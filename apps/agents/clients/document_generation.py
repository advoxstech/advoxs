"""Cliente do serviço externo de geração de documentos jurídicos
(api_advoxs.rootlab.com.br) — antes orquestrado por um fluxo n8n (draft via
LLM -> LaTeX -> PDF -> Google Drive -> Chatvolt); aqui a mesma cadeia de 3
chamadas é feita direto, sem n8n/Drive/Chatvolt no meio. A entrega ao contato
usa o WhatsApp real do tenant (ver agents/tools.py + services/document_storage.py)."""

import os
from urllib.parse import urlsplit, urlunsplit

import httpx
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

DOCUMENT_API_BASE_URL = os.getenv("DOCUMENT_API_BASE_URL", "https://api_advoxs.rootlab.com.br")
DOCUMENT_API_KEY = os.getenv("DOCUMENT_API_KEY", "")

# ──────────────────────────────────────────────────────────────────────────
# GAMBIARRA (deliberada, documentada): o hostname padrão acima
# ("api_advoxs.rootlab.com.br") usa underscore, que não é um caractere válido
# de hostname (RFC 952/1123). Isso NÃO impede o DNS de resolver nem o
# Cloudflare de servir o certificado — mas quebra a validação de hostname do
# OpenSSL (usado pelo Python/httpx aqui, e por qualquer cliente TLS "correto"
# em Linux, que é onde este serviço roda de verdade em produção): o matching
# de wildcard da RFC 6125 recusa comparar "*.rootlab.com.br" contra um label
# com "_", mesmo o certificado sendo genuinamente válido pra zona inteira.
# Confirmado na prática (2026-08-03): falha só em clientes baseados em
# OpenSSL — o schannel do Windows (curl.exe, navegador) é mais permissivo e
# não acusa nada, o que mascarou o problema até testarmos de dentro do
# container Linux.
#
# Isso NÃO é uma redução de segurança: continuamos validando um certificado
# real e confiável, só não o comparamos contra o hostname com underscore.
# Em vez disso, fazemos o handshake TLS/SNI contra um hostname IRMÃO válido
# na MESMA zona Cloudflare (a raiz "rootlab.com.br", coberta pelo mesmo
# certificado wildcard) e sobrescrevemos o header HTTP "Host" pro hostname
# real de underscore — a Cloudflare roteia pro serviço de origem certo com
# base no header Host, não no SNI usado no handshake. Testado manualmente e
# confirmado que chega no serviço certo (resposta real de make_latex/
# make_*_llm/compile_latex).
#
# Isso só entra em ação se o hostname configurado tiver underscore — se um
# dia DOCUMENT_API_BASE_URL apontar pra um hostname válido (o conserto de
# verdade, do lado da infra), esse código some da jogada sozinho, sem precisar
# tocar aqui.
_parsed_base_url = urlsplit(DOCUMENT_API_BASE_URL)
_REAL_HOSTNAME = _parsed_base_url.hostname or ""
_TLS_SNI_ANCHOR_HOSTNAME = "rootlab.com.br"
_NEEDS_SNI_HOST_WORKAROUND = "_" in _REAL_HOSTNAME


def _request_base_url() -> str:
    """URL usada pra conectar/validar TLS — troca o hostname com underscore
    pelo hostname-âncora válido quando necessário (ver gambiarra acima)."""
    if not _NEEDS_SNI_HOST_WORKAROUND:
        return DOCUMENT_API_BASE_URL
    anchor = _parsed_base_url._replace(netloc=_TLS_SNI_ANCHOR_HOSTNAME)
    return urlunsplit(anchor)


_REQUEST_BASE_URL = _request_base_url()

# Nome do campo devolvido por cada endpoint make_{tipo}_llm — confirmado a
# partir do fluxo n8n original (cada tipo usa um nome de campo diferente na
# resposta, sem padrão único).
_DRAFT_RESPONSE_FIELD = {
    "contrato": "contract",
    "multa": "multa",
    "advertencia": "advertencia",
    "oficio": "oficio",
    "edital_convocacao": "edital",
    "aviso": "aviso",
}

_TIMEOUT_SECONDS = 90.0


class DocumentGenerationError(Exception):
    """Falha em qualquer etapa da cadeia draft -> LaTeX -> PDF."""


def _headers() -> dict:
    headers = {"x-api-key": DOCUMENT_API_KEY}
    if _NEEDS_SNI_HOST_WORKAROUND:
        # Host: real (com underscore) — ver gambiarra documentada no topo do
        # arquivo. A conexão/SNI usa _REQUEST_BASE_URL (hostname-âncora), mas
        # o roteamento de origem na Cloudflare segue este header.
        headers["Host"] = _REAL_HOSTNAME
    return headers


async def _draft_document(client: httpx.AsyncClient, tipo: str, text_payload: str) -> str:
    campo = _DRAFT_RESPONSE_FIELD[tipo]
    url = f"{_REQUEST_BASE_URL}/make_{tipo}_llm"
    try:
        response = await client.post(url, headers=_headers(), json={"text": text_payload})
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Falha ao redigir documento | tipo={} erro={}", tipo, exc)
        raise DocumentGenerationError(
            f"Falha ao redigir o documento (etapa de redação, tipo={tipo})."
        ) from exc

    body = response.json()
    texto = body.get(campo)
    if not texto:
        logger.error("Resposta de redação sem o campo esperado | tipo={} campo={}", tipo, campo)
        raise DocumentGenerationError(
            f"Falha ao redigir o documento: resposta inesperada do serviço (tipo={tipo})."
        )
    return texto


async def _draft_to_latex(client: httpx.AsyncClient, texto: str) -> str:
    url = f"{_REQUEST_BASE_URL}/make_latex"
    try:
        response = await client.post(url, headers=_headers(), json={"text": texto})
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Falha ao gerar LaTeX | erro={}", exc)
        raise DocumentGenerationError("Falha ao gerar o documento (etapa de formatação).") from exc

    # Diferente de make_{tipo}_llm (JSON), este endpoint devolve a fonte
    # LaTeX como texto puro no corpo (Content-Type: text/plain) — confirmado
    # testando ao vivo (2026-08-03). O `$json.data` usado no fluxo n8n
    # original era só a representação interna do n8n pra uma resposta de
    # texto (não um campo "data" de verdade devolvido pelo serviço).
    latex = response.text
    if not latex:
        logger.error("Resposta de make_latex veio vazia")
        raise DocumentGenerationError(
            "Falha ao gerar o documento: resposta inesperada do serviço (formatação)."
        )
    return latex


async def _compile_pdf(client: httpx.AsyncClient, latex: str) -> bytes:
    url = f"{_REQUEST_BASE_URL}/compile_latex"
    headers = {**_headers(), "Content-Type": "text/plain"}
    try:
        response = await client.post(url, headers=headers, content=latex.encode("utf-8"))
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Falha ao compilar PDF | erro={}", exc)
        raise DocumentGenerationError("Falha ao gerar o documento (etapa de compilação).") from exc

    if not response.content:
        logger.error("compile_latex devolveu corpo vazio")
        raise DocumentGenerationError(
            "Falha ao gerar o documento: o serviço devolveu um PDF vazio."
        )
    return response.content


async def generate_pdf(tipo: str, text_payload: str) -> bytes:
    """Encadeia as 3 chamadas (draft -> LaTeX -> PDF) e devolve os bytes do PDF.

    `tipo` deve ser uma chave de `_DRAFT_RESPONSE_FIELD` (contrato, multa,
    advertencia, oficio, edital_convocacao, aviso).
    """
    logger.info("Gerando documento | tipo={}", tipo)
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        texto = await _draft_document(client, tipo, text_payload)
        latex = await _draft_to_latex(client, texto)
        pdf_bytes = await _compile_pdf(client, latex)
    logger.info("Documento gerado com sucesso | tipo={} tamanho_bytes={}", tipo, len(pdf_bytes))
    return pdf_bytes
