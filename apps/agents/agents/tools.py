from langchain.tools import tool
from langgraph.types import Command
from clients.retrieval import retrieval_usuario, retrieval_escritorio
from loguru import logger
import requests
import tempfile
import os
from urllib.parse import urlparse

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

@tool("buscar_base_conhecimento_agente")
async def buscar_base_conhecimento_agente(
    query: str,
    conversation_id: str,
    knowledge_base_file_ids: list[str] | None = None,
) -> str:
    """Busca na base de conhecimento anexada a este agente.

    Use quando a pergunta envolver documentos, materiais, modelos ou
    orientações que você tenha na sua própria base de conhecimento — cada
    agente só tem acesso aos arquivos que foram anexados especificamente a
    ele, nunca à base de outro agente.

    Args:
        query: Pergunta ou tema a ser pesquisado.
        conversation_id: preenchido automaticamente pelo sistema.
        knowledge_base_file_ids: preenchido automaticamente pelo sistema.
    """
    if not knowledge_base_file_ids:
        return "Este agente não tem nenhuma base de conhecimento anexada."
    return await retrieval_escritorio(conversation_id, query, doc_ids=knowledge_base_file_ids)


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


@tool("transfer_to_agent")
def transfer_to_agent(agent_id: str, valid_agent_ids: list[str] | None = None) -> str:
    """
    Transfere a conversa para outro agente do escritório.

    Args:
        agent_id: id do agente de destino — escolha entre os agentes
            disponíveis no seu contexto, nunca invente um id.
        valid_agent_ids: preenchido automaticamente pelo sistema.
    """
    if agent_id not in (valid_agent_ids or []):
        return (
            "Transferência recusada: agent_id inválido — escolha um dos agentes "
            "disponíveis no seu contexto."
        )
    return Command(
        update={
            "current_agent_id": agent_id,
            "receptive_message_specialist": True,
        }
    )


tools = [
    buscar_base_conhecimento_agente,
    bucar_base_conhecimento_usuario,
    transfer_to_agent,
]
