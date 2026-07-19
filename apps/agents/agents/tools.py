from langchain.tools import tool
from langgraph.types import Command
from clients.retrieval import retrieval_sistema, retrieval_usuario, retrieval_escritorio
from clients.billing import criar_link_pagamento
from loguru import logger
import requests
import tempfile
import os
from urllib.parse import urlparse
from typing import Literal

ENDPOINT_URL = "http://localhost:8000/documents/users/insert"  # ajuste a URL base
API_KEY = "LASKDJFLK234LWAK"  # ajuste conforme necessário
CONVERSATION_ID = "1"  # ajuste conforme necessário

@tool("enviar_documento")
def enviar_documento(url: str, conversation_id: str) -> str:
    """
    Baixa um documento a partir de uma URL e envia para o endpoint de inserção.

    Args:
        documento: URL do documento a ser enviado.
        conversation_id: ID da conversa atual.

    Returns:
        Mensagem indicando sucesso ou falha na inserção do documento.
    """
    logger.info("Enviando documento | url={}", url)

    # 1. Baixar o documento
    try:
        logger.info("Baixando documento | url={}", url)
        download_response = requests.get(url, timeout=30)
        download_response.raise_for_status()
    except requests.exceptions.MissingSchema:
        logger.error("URL inválida | url={}", url)
        return "Falha ao enviar documento: URL inválida. Verifique se a URL está correta."
    except requests.exceptions.ConnectionError:
        logger.error("Erro de conexão ao baixar documento | url={}", url)
        return "Falha ao enviar documento: não foi possível conectar à URL fornecida."
    except requests.exceptions.Timeout:
        logger.error("Timeout ao baixar documento | url={}", url)
        return "Falha ao enviar documento: tempo limite excedido ao baixar o arquivo."
    except requests.exceptions.HTTPError as e:
        logger.error("Erro HTTP ao baixar documento | error={}", e)
        return f"Falha ao enviar documento: erro ao baixar o arquivo (HTTP {download_response.status_code})."
    except Exception as e:
        logger.error("Erro inesperado ao baixar documento | error={}", e)
        return "Falha ao enviar documento: erro inesperado ao baixar o arquivo."

    # 2. Inferir nome e extensão do arquivo
    parsed_url = urlparse(url)
    filename = os.path.basename(parsed_url.path) or "documento"
    if "." not in filename:
        content_type = download_response.headers.get("Content-Type", "")
        ext_map = {
            "application/pdf": ".pdf",
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "text/plain": ".txt",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        }
        filename += ext_map.get(content_type.split(";")[0].strip(), ".bin")

    # 3. Salvar em arquivo temporário e enviar ao endpoint
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{filename}") as tmp_file:
            tmp_file.write(download_response.content)
            tmp_path = tmp_file.name

        logger.info("Arquivo temporário criado | path={}", tmp_path)

        with open(tmp_path, "rb") as f:
            files = {"file": (filename, f, download_response.headers.get("Content-Type", "application/octet-stream"))}
            data = {"convesation_id": conversation_id}  # typo mantido igual ao endpoint
            headers = {"Authorization": f"{API_KEY}"}  # ajuste conforme seu esquema de auth

            logger.info("Enviando para o endpoint | url={}", ENDPOINT_URL)
            insert_response = requests.post(
                ENDPOINT_URL,
                files=files,
                data=data,
                headers=headers,
                timeout=100,
            )

    except Exception as e:
        logger.error("Erro ao enviar documento ao endpoint | error={}", e)
        return "Falha ao enviar documento: erro ao comunicar com o servidor de inserção."
    finally:
        # Limpar arquivo temporário
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
            logger.info("Arquivo temporário removido | path={}", tmp_path)

    # 4. Tratar resposta
    logger.info("Resposta do endpoint | status={}", insert_response.status_code)

    if insert_response.status_code in (200, 201):
        try:
            body = insert_response.json()
            msg = body.get("message") or body.get("detail") or "Documento inserido com sucesso."
            logger.info("Documento inserido com sucesso | message={}", msg)
            return f"Documento inserido com sucesso. Resposta do servidor: {msg}"
        except Exception:
            return "Documento inserido com sucesso."

    elif insert_response.status_code == 401:
        logger.error("Não autorizado ao inserir documento")
        return "Falha ao inserir documento: acesso não autorizado. Verifique a API key."

    elif insert_response.status_code == 422:
        logger.error("Erro de validação | response={}", insert_response.text)
        return "Falha ao inserir documento: dados inválidos enviados ao servidor (erro de validação)."

    elif insert_response.status_code >= 500:
        logger.error("Erro interno do servidor | status={}", insert_response.status_code)
        return f"Falha ao inserir documento: erro interno no servidor (HTTP {insert_response.status_code}). Tente novamente mais tarde."

    else:
        logger.error("Resposta inesperada | status={} | response={}", insert_response.status_code, insert_response.text)
        return f"Falha ao inserir documento: resposta inesperada do servidor (HTTP {insert_response.status_code})."

@tool("bucar_base_conhecimento_condominial")
async def bucar_base_conhecimento_condominial(query: str) -> str:
    """Busca na base de conhecimento jurídico geral do sistema.

    Use esta ferramenta na maioria das situações: sempre que precisar de embasamento
    jurídico, legislação, jurisprudência, conceitos legais ou orientações sobre
    direito condominial.
    Prefira esta ferramenta por padrão antes de recorrer à base pessoal do usuário.

    Args:
        query: Pergunta ou tema jurídico a ser pesquisado.
    """
    return await retrieval_sistema("condominial", query)


@tool("bucar_base_conhecimento_contratos")
async def bucar_base_conhecimento_contratos(query: str) -> str:
    """Busca na base de conhecimento jurídico geral do sistema.

    Use esta ferramenta na maioria das situações: sempre que precisar de embasamento
    jurídico, legislação, jurisprudência, conceitos legais ou orientações sobre
    direito de contratos.
    Prefira esta ferramenta por padrão antes de recorrer à base pessoal do usuário.

    Args:
        query: Pergunta ou tema jurídico a ser pesquisado.
    """
    return await retrieval_sistema("contratos", query)



@tool("bucar_base_conhecimento_direito_consumidor")
async def bucar_base_conhecimento_direito_consumidor(query: str) -> str:
    """Busca na base de conhecimento jurídico geral do sistema.

    Use esta ferramenta na maioria das situações: sempre que precisar de embasamento
    jurídico, legislação, jurisprudência, conceitos legais ou orientações sobre
    direito do consumidor.
    Prefira esta ferramenta por padrão antes de recorrer à base pessoal do usuário.

    Args:
        query: Pergunta ou tema jurídico a ser pesquisado.
    """
    return await retrieval_sistema("direito_consumidor", query)



@tool("bucar_base_conhecimento_usuario")
async def bucar_base_conhecimento_usuario(query: str, conversation_id: str) -> str:
    """Busca na base de documentos pessoais enviados pelo próprio usuário.

    Use esta ferramenta apenas quando o usuário indicar explicitamente que quer
    verificar algo nos documentos que ele mesmo enviou — por exemplo: "você tem
    meu contrato?", "busca no que te mandei", "verifica nos meus documentos",
    "analisa o arquivo que te enviei". Não use por padrão; espere uma indicação
    clara de que a busca deve ser feita na base pessoal do usuário.

    Args:
        query: Trecho ou tema a ser localizado nos documentos do usuário.
        conversation_id: ID da conversa/usuário.
    """
    return await retrieval_usuario(conversation_id, query)


@tool("buscar_base_conhecimento_escritorio")
async def buscar_base_conhecimento_escritorio(query: str, conversation_id: str) -> str:
    """Busca na base de conhecimento própria do escritório de advocacia.

    Use quando a pergunta envolver documentos, materiais, modelos ou
    orientações internas do próprio escritório — por exemplo regimentos,
    políticas de atendimento, modelos de contrato do escritório ou qualquer
    material institucional que o escritório tenha cadastrado na plataforma.

    Args:
        query: Pergunta ou tema a ser pesquisado nos documentos do escritório.
        conversation_id: ID da conversa (preenchido automaticamente pelo sistema).
    """
    return await retrieval_escritorio(conversation_id, query)

@tool("gerar_link_pagamento_cliente")
async def gerar_link_pagamento_cliente(package_id: str, conversation_id: str) -> str:
    """Gera o link de pagamento (Stripe) pro cliente comprar um pacote de créditos.

    Use quando o cliente não tiver saldo suficiente pra continuar sendo
    atendido por um especialista, ou quando ele pedir explicitamente pra
    comprar mais créditos. Escolha o package_id entre os pacotes informados
    no seu contexto — nunca invente um id.

    Args:
        package_id: id do pacote escolhido (vem da lista de pacotes disponíveis).
        conversation_id: preenchido automaticamente pelo sistema.
    """
    tenant_id, _, contact_phone_number = str(conversation_id).partition(":")
    checkout_url = await criar_link_pagamento(tenant_id, contact_phone_number, package_id)
    if checkout_url is None:
        return (
            "Não foi possível gerar o link de pagamento agora — peça pro cliente "
            "tentar de novo em instantes."
        )
    return f"Link de pagamento gerado: {checkout_url}"

def is_billing_blocked(enabled: bool, balance: float) -> bool:
    """Bloqueia oferta/transferência quando a cobrança do cliente final está
    ativa e o saldo está zerado — usada tanto pelo gate técnico em
    transfer_to_specialist quanto pela decisão de injetar os pacotes/pular a
    despedida em agente_secretaria, pra nunca divergir entre os dois."""
    return bool(enabled) and balance <= 0


@tool("transfer_to_specialist")
def transfer_to_specialist(
    current_specialist: Literal["agente_condominial", "agente_contratos", "agente_direito_consumidor"],
    end_customer_billing_enabled: bool = False,
    end_customer_balance: float = 0,
) -> str:
    """
    Atualiza o estado do agente para transferir a conversa para um especialista.

    Args:
        current_specialist: Nome do especialista a ser transferido.
        end_customer_billing_enabled: preenchido automaticamente pelo sistema.
        end_customer_balance: preenchido automaticamente pelo sistema.
    """
    if is_billing_blocked(end_customer_billing_enabled, end_customer_balance):
        return (
            "Transferência bloqueada: o cliente ainda não tem créditos disponíveis. "
            "Ofereça os pacotes de crédito e gere o link de pagamento antes de "
            "transferir para um especialista."
        )
    return Command(
        update={
            "current_specialist": current_specialist,
            "receptive_message_specialist": True,
        }
    )



tools = [
    bucar_base_conhecimento_condominial,
    bucar_base_conhecimento_contratos,
    bucar_base_conhecimento_direito_consumidor,
    bucar_base_conhecimento_usuario,
    buscar_base_conhecimento_escritorio,
    gerar_link_pagamento_cliente,
    transfer_to_specialist,
]