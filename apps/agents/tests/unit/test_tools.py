from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests
from langgraph.types import Command

from agents.tools import (
    DOCUMENT_GENERATION_CREDIT_COST,
    bucar_base_conhecimento_usuario,
    buscar_base_conhecimento_agente,
    enviar_aviso,
    enviar_documento,
    enviar_edital_convocacao,
    fazer_advertencia,
    fazer_contrato,
    fazer_multa,
    fazer_oficio,
    transfer_to_agent,
)
from clients.document_generation import DocumentGenerationError

# ──────────────────────────────────────────────
# transfer_to_agent
# ──────────────────────────────────────────────


def test_transfer_retorna_command():
    result = transfer_to_agent.invoke({"agent_id": "agent-2", "valid_agent_ids": ["agent-2"]})
    assert isinstance(result, Command)


def test_transfer_atualiza_current_agent_id():
    result = transfer_to_agent.invoke({"agent_id": "agent-2", "valid_agent_ids": ["agent-2"]})
    assert result.update["current_agent_id"] == "agent-2"


def test_transfer_ativa_receptive_message():
    result = transfer_to_agent.invoke({"agent_id": "agent-2", "valid_agent_ids": ["agent-2"]})
    assert result.update["receptive_message_specialist"] is True


def test_transfer_agent_id_fora_da_lista_recusa():
    result = transfer_to_agent.invoke({"agent_id": "agent-forjado", "valid_agent_ids": ["agent-2"]})
    assert isinstance(result, str)
    assert "recusada" in result.lower()


def test_transfer_sem_valid_agent_ids_recusa():
    result = transfer_to_agent.invoke({"agent_id": "agent-2"})
    assert isinstance(result, str)
    assert "recusada" in result.lower()


# ──────────────────────────────────────────────
# buscar_base_conhecimento_agente
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_buscar_agente_chama_retrieval_com_doc_ids():
    mock_retrieval = AsyncMock(return_value="resultado")
    with patch("agents.tools.retrieval_escritorio", new=mock_retrieval) as mock_fn:
        result = await buscar_base_conhecimento_agente.ainvoke(
            {
                "query": "regimento",
                "conversation_id": "tenant-1:5511999998888",
                "knowledge_base_file_ids": ["f1", "f2"],
            }
        )

        mock_fn.assert_called_once_with("tenant-1:5511999998888", "regimento", doc_ids=["f1", "f2"])
        assert result == "resultado"


@pytest.mark.asyncio
async def test_buscar_agente_sem_arquivos_nao_chama_retrieval():
    with patch("agents.tools.retrieval_escritorio", new=AsyncMock()) as mock_fn:
        result = await buscar_base_conhecimento_agente.ainvoke(
            {
                "query": "regimento",
                "conversation_id": "tenant-1:5511999998888",
                "knowledge_base_file_ids": [],
            }
        )

        mock_fn.assert_not_called()
        assert "não tem" in result.lower()


@pytest.mark.asyncio
async def test_buscar_agente_sem_knowledge_base_file_ids_nao_chama_retrieval():
    with patch("agents.tools.retrieval_escritorio", new=AsyncMock()) as mock_fn:
        result = await buscar_base_conhecimento_agente.ainvoke(
            {
                "query": "regimento",
                "conversation_id": "tenant-1:5511999998888",
            }
        )

        mock_fn.assert_not_called()
        assert "não tem" in result.lower()


# ──────────────────────────────────────────────
# bucar_base_conhecimento_usuario
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_buscar_base_usuario_chama_retrieval_usuario():
    mock_retrieval = AsyncMock(return_value="doc do usuário")
    with patch("agents.tools.retrieval_usuario", new=mock_retrieval) as mock_fn:
        result = await bucar_base_conhecimento_usuario.ainvoke(
            {
                "query": "meu contrato",
                "conversation_id": "conv-99",
            }
        )
        mock_fn.assert_called_once_with("conv-99", "meu contrato")
        assert result == "doc do usuário"


@pytest.mark.asyncio
async def test_buscar_base_usuario_repassa_conversation_id():
    with patch("agents.tools.retrieval_usuario", new=AsyncMock(return_value="")) as mock_fn:
        await bucar_base_conhecimento_usuario.ainvoke(
            {
                "query": "busca",
                "conversation_id": "conv-especifica-123",
            }
        )
        assert mock_fn.call_args[0][0] == "conv-especifica-123"


# ──────────────────────────────────────────────
# enviar_documento
# ──────────────────────────────────────────────


def test_enviar_documento_url_invalida():
    result = enviar_documento.invoke({"url": "nao-e-uma-url", "conversation_id": "conv-1"})
    assert "Falha" in result
    assert "URL inválida" in result


def test_enviar_documento_conexao_falha():
    with patch("agents.tools.requests.get") as mock_get:
        mock_get.side_effect = requests.exceptions.ConnectionError()
        result = enviar_documento.invoke(
            {"url": "http://host-inexistente.test/doc.pdf", "conversation_id": "conv-1"}
        )
        assert "Falha" in result
        assert "conectar" in result.lower()


def test_enviar_documento_timeout():
    with patch("agents.tools.requests.get") as mock_get:
        mock_get.side_effect = requests.exceptions.Timeout()
        result = enviar_documento.invoke(
            {"url": "http://lento.test/doc.pdf", "conversation_id": "conv-1"}
        )
        assert "Falha" in result
        assert "tempo" in result.lower() or "limite" in result.lower()


def test_enviar_documento_http_error():
    with patch("agents.tools.requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError()
        mock_get.return_value = mock_response
        result = enviar_documento.invoke(
            {"url": "http://example.com/inexistente.pdf", "conversation_id": "conv-1"}
        )
        assert "Falha" in result


def test_enviar_documento_sucesso_200():
    with (
        patch("agents.tools.requests.get") as mock_get,
        patch("agents.tools.requests.post") as mock_post,
    ):
        download = MagicMock()
        download.raise_for_status.return_value = None
        download.content = b"conteudo do pdf"
        download.headers = {"Content-Type": "application/pdf"}
        mock_get.return_value = download

        insert = MagicMock()
        insert.status_code = 200
        insert.json.return_value = {"message": "inserido com sucesso"}
        mock_post.return_value = insert

        result = enviar_documento.invoke(
            {"url": "http://example.com/contrato.pdf", "conversation_id": "conv-1"}
        )
        assert "sucesso" in result.lower()


def test_enviar_documento_servidor_retorna_401():
    with (
        patch("agents.tools.requests.get") as mock_get,
        patch("agents.tools.requests.post") as mock_post,
    ):
        download = MagicMock()
        download.raise_for_status.return_value = None
        download.content = b"arquivo"
        download.headers = {"Content-Type": "application/pdf"}
        mock_get.return_value = download

        insert = MagicMock()
        insert.status_code = 401
        mock_post.return_value = insert

        result = enviar_documento.invoke(
            {"url": "http://example.com/doc.pdf", "conversation_id": "conv-1"}
        )
        assert "autorizado" in result.lower() or "Falha" in result


def test_enviar_documento_servidor_retorna_500():
    with (
        patch("agents.tools.requests.get") as mock_get,
        patch("agents.tools.requests.post") as mock_post,
    ):
        download = MagicMock()
        download.raise_for_status.return_value = None
        download.content = b"arquivo"
        download.headers = {"Content-Type": "application/pdf"}
        mock_get.return_value = download

        insert = MagicMock()
        insert.status_code = 500
        mock_post.return_value = insert

        result = enviar_documento.invoke(
            {"url": "http://example.com/doc.pdf", "conversation_id": "conv-1"}
        )
        assert "500" in result or "interno" in result.lower()


def test_enviar_documento_infere_extensao_pelo_content_type():
    with (
        patch("agents.tools.requests.get") as mock_get,
        patch("agents.tools.requests.post") as mock_post,
    ):
        download = MagicMock()
        download.raise_for_status.return_value = None
        download.content = b"arquivo"
        download.headers = {"Content-Type": "image/png"}
        mock_get.return_value = download

        insert = MagicMock()
        insert.status_code = 200
        insert.json.return_value = {"message": "ok"}
        mock_post.return_value = insert

        enviar_documento.invoke({"url": "http://example.com/imagem", "conversation_id": "conv-1"})

        filename = mock_post.call_args[1]["files"]["file"][0]
        assert filename.endswith(".png")


# ──────────────────────────────────────────────
# Tools de geração de documento (fazer_contrato/fazer_multa/etc)
# ──────────────────────────────────────────────

_MULTA_ARGS = {
    "nome_condominio": "Edifício Alfa",
    "unidade": "101",
    "nome_morador": "Fulano de Tal",
    "tipo_infracao": "barulho",
    "descricao_ocorrencia": "festa após as 22h",
    "data_ocorrencia": "2026-01-01",
    "hora_ocorrencia": "23:00",
    "local_ocorrencia": "unidade 101",
    "houve_advertencia_previa": True,
    "nome_responsavel": "Síndico",
    "cidade_data_emissao": "São Paulo, 02/01/2026",
    "valor_multa": "R$ 500,00",
    "percentual_taxa_condominial": "50%",
    "forma_cobranca": "boleto",
    "data_vencimento": "2026-02-01",
    "prazo_recurso": "10 dias",
    "destino_recurso": "síndico",
}

_CONTRATO_ARGS = {
    "tipo_contrato": "prestação de serviço",
    "objetivo_contrato": "consultoria",
    "dados_contratante": "Empresa X",
    "dados_contratado": "Empresa Y",
    "servico_ou_objeto": "consultoria jurídica",
    "valor_acordado": "R$ 1000,00",
    "forma_pagamento": "à vista",
    "prazo_contrato": "12 meses",
    "multa_ou_penalidade": "10% sobre o valor",
    "regras_importantes": "confidencialidade",
    "foro_contrato": "São Paulo",
}

_ADVERTENCIA_ARGS = {
    "nome_condominio": "Edifício Alfa",
    "unidade": "101",
    "nome_morador": "Fulano de Tal",
    "tipo_infracao": "barulho",
    "descricao_ocorrencia": "festa após as 22h",
    "data_ocorrencia": "2026-01-01",
    "hora_ocorrencia": "23:00",
    "nome_responsavel": "Síndico",
    "cidade_data_emissao": "São Paulo, 02/01/2026",
}

_OFICIO_ARGS = {
    "numero_oficio": "014/2026",
    "destinatario": "Secretaria Municipal",
    "assunto": "manutenção",
    "objetivo_oficio": "solicitar manutenção",
    "descricao_detalhada": "computadores com defeito",
    "nome_responsavel": "Diretor",
    "cargo_responsavel": "Diretor Escolar",
    "cidade_data": "Aracaju, 14/05/2026",
}

_EDITAL_ARGS = {
    "nome_condominio": "Edifício Alfa",
    "cnpj_condominio": "00.000.000/0001-00",
    "endereco_condominio": "Rua X, 123",
    "tipo_assembleia": "ordinária",
    "data_assembleia": "2026-03-01",
    "local_plataforma": "salão de festas",
    "horario_primeira_convocacao": "19h",
    "horario_segunda_convocacao": "19h30",
    "ordem_dia": "eleição de síndico",
    "nome_responsavel": "Síndico",
    "cargo_responsavel": "Síndico",
    "cidade_data_emissao": "São Paulo, 01/02/2026",
}

_AVISO_ARGS = {
    "titulo_comunicado": "Manutenção da piscina",
    "data": "2026-01-10",
    "local_setor": "piscina",
    "assunto": "manutenção",
    "descricao_comunicado": "piscina fechada para manutenção",
    "impactos_alteracoes": "piscina indisponível",
    "orientacoes": "evitar a área",
    "prazo_periodo": "3 dias",
    "responsavel_comunicado": "Síndico",
    "tom_comunicado": "informativo",
}

_DOCUMENT_TOOLS_CASES = [
    (fazer_contrato, "contrato", "Contrato.pdf", _CONTRATO_ARGS),
    (fazer_multa, "multa", "Multa.pdf", _MULTA_ARGS),
    (fazer_advertencia, "advertencia", "Advertência.pdf", _ADVERTENCIA_ARGS),
    (fazer_oficio, "oficio", "Ofício.pdf", _OFICIO_ARGS),
    (enviar_edital_convocacao, "edital_convocacao", "Edital de Convocação.pdf", _EDITAL_ARGS),
    (enviar_aviso, "aviso", "Aviso.pdf", _AVISO_ARGS),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, tipo, filename, args", _DOCUMENT_TOOLS_CASES)
async def test_tool_documento_sucesso_devolve_command_com_link(tool, tipo, filename, args):
    with (
        patch("agents.tools.generate_pdf", new=AsyncMock(return_value=b"%PDF-1.4")) as mock_gen,
        patch("agents.tools.save_pdf", return_value="doc-id-123") as mock_save,
        patch(
            "agents.tools.build_public_url",
            return_value="https://agents.exemplo.com/generated-documents/doc-id-123",
        ),
    ):
        result = await tool.ainvoke(args)

    assert isinstance(result, Command)
    docs = result.update["generated_documents"]
    assert len(docs) == 1
    assert docs[0]["filename"] == filename
    assert docs[0]["link"] == "https://agents.exemplo.com/generated-documents/doc-id-123"
    assert docs[0]["credit_cost"] == DOCUMENT_GENERATION_CREDIT_COST
    mock_gen.assert_awaited_once()
    assert mock_gen.await_args.args[0] == tipo
    mock_save.assert_called_once_with(b"%PDF-1.4")


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, tipo, filename, args", _DOCUMENT_TOOLS_CASES)
async def test_tool_documento_falha_na_geracao_devolve_string_de_erro(tool, tipo, filename, args):
    with patch(
        "agents.tools.generate_pdf",
        new=AsyncMock(side_effect=DocumentGenerationError("Falha ao gerar o documento.")),
    ):
        result = await tool.ainvoke(args)

    assert isinstance(result, str)
    assert "Falha" in result


@pytest.mark.asyncio
async def test_fazer_multa_repassa_todos_os_campos_no_payload():
    with (
        patch("agents.tools.generate_pdf", new=AsyncMock(return_value=b"%PDF-1.4")) as mock_gen,
        patch("agents.tools.save_pdf", return_value="doc-id"),
        patch("agents.tools.build_public_url", return_value="https://exemplo.com/x"),
    ):
        await fazer_multa.ainvoke(_MULTA_ARGS)

    text_payload = mock_gen.await_args.args[1]
    assert "Edifício Alfa" in text_payload
    assert "Fulano de Tal" in text_payload
    assert "R$ 500,00" in text_payload
