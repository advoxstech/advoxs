import pytest
import requests
from unittest.mock import AsyncMock, patch, MagicMock
from langgraph.types import Command
from agents.tools import (
    transfer_to_agent,
    buscar_base_conhecimento_agente,
    bucar_base_conhecimento_usuario,
    enviar_documento,
)


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
    with patch("agents.tools.retrieval_escritorio", new=AsyncMock(return_value="resultado")) as mock_fn:
        result = await buscar_base_conhecimento_agente.ainvoke({
            "query": "regimento",
            "conversation_id": "tenant-1:5511999998888",
            "knowledge_base_file_ids": ["f1", "f2"],
        })

        mock_fn.assert_called_once_with(
            "tenant-1:5511999998888", "regimento", doc_ids=["f1", "f2"]
        )
        assert result == "resultado"


@pytest.mark.asyncio
async def test_buscar_agente_sem_arquivos_nao_chama_retrieval():
    with patch("agents.tools.retrieval_escritorio", new=AsyncMock()) as mock_fn:
        result = await buscar_base_conhecimento_agente.ainvoke({
            "query": "regimento",
            "conversation_id": "tenant-1:5511999998888",
            "knowledge_base_file_ids": [],
        })

        mock_fn.assert_not_called()
        assert "não tem" in result.lower()


@pytest.mark.asyncio
async def test_buscar_agente_sem_knowledge_base_file_ids_nao_chama_retrieval():
    with patch("agents.tools.retrieval_escritorio", new=AsyncMock()) as mock_fn:
        result = await buscar_base_conhecimento_agente.ainvoke({
            "query": "regimento",
            "conversation_id": "tenant-1:5511999998888",
        })

        mock_fn.assert_not_called()
        assert "não tem" in result.lower()


# ──────────────────────────────────────────────
# bucar_base_conhecimento_usuario
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_buscar_base_usuario_chama_retrieval_usuario():
    with patch("agents.tools.retrieval_usuario", new=AsyncMock(return_value="doc do usuário")) as mock_fn:
        result = await bucar_base_conhecimento_usuario.ainvoke({
            "query": "meu contrato",
            "conversation_id": "conv-99",
        })
        mock_fn.assert_called_once_with("conv-99", "meu contrato")
        assert result == "doc do usuário"


@pytest.mark.asyncio
async def test_buscar_base_usuario_repassa_conversation_id():
    with patch("agents.tools.retrieval_usuario", new=AsyncMock(return_value="")) as mock_fn:
        await bucar_base_conhecimento_usuario.ainvoke({
            "query": "busca",
            "conversation_id": "conv-especifica-123",
        })
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
        result = enviar_documento.invoke({"url": "http://host-inexistente.test/doc.pdf", "conversation_id": "conv-1"})
        assert "Falha" in result
        assert "conectar" in result.lower()


def test_enviar_documento_timeout():
    with patch("agents.tools.requests.get") as mock_get:
        mock_get.side_effect = requests.exceptions.Timeout()
        result = enviar_documento.invoke({"url": "http://lento.test/doc.pdf", "conversation_id": "conv-1"})
        assert "Falha" in result
        assert "tempo" in result.lower() or "limite" in result.lower()


def test_enviar_documento_http_error():
    with patch("agents.tools.requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError()
        mock_get.return_value = mock_response
        result = enviar_documento.invoke({"url": "http://example.com/inexistente.pdf", "conversation_id": "conv-1"})
        assert "Falha" in result


def test_enviar_documento_sucesso_200():
    with patch("agents.tools.requests.get") as mock_get, \
         patch("agents.tools.requests.post") as mock_post:
        download = MagicMock()
        download.raise_for_status.return_value = None
        download.content = b"conteudo do pdf"
        download.headers = {"Content-Type": "application/pdf"}
        mock_get.return_value = download

        insert = MagicMock()
        insert.status_code = 200
        insert.json.return_value = {"message": "inserido com sucesso"}
        mock_post.return_value = insert

        result = enviar_documento.invoke({"url": "http://example.com/contrato.pdf", "conversation_id": "conv-1"})
        assert "sucesso" in result.lower()


def test_enviar_documento_servidor_retorna_401():
    with patch("agents.tools.requests.get") as mock_get, \
         patch("agents.tools.requests.post") as mock_post:
        download = MagicMock()
        download.raise_for_status.return_value = None
        download.content = b"arquivo"
        download.headers = {"Content-Type": "application/pdf"}
        mock_get.return_value = download

        insert = MagicMock()
        insert.status_code = 401
        mock_post.return_value = insert

        result = enviar_documento.invoke({"url": "http://example.com/doc.pdf", "conversation_id": "conv-1"})
        assert "autorizado" in result.lower() or "Falha" in result


def test_enviar_documento_servidor_retorna_500():
    with patch("agents.tools.requests.get") as mock_get, \
         patch("agents.tools.requests.post") as mock_post:
        download = MagicMock()
        download.raise_for_status.return_value = None
        download.content = b"arquivo"
        download.headers = {"Content-Type": "application/pdf"}
        mock_get.return_value = download

        insert = MagicMock()
        insert.status_code = 500
        mock_post.return_value = insert

        result = enviar_documento.invoke({"url": "http://example.com/doc.pdf", "conversation_id": "conv-1"})
        assert "500" in result or "interno" in result.lower()


def test_enviar_documento_infere_extensao_pelo_content_type():
    with patch("agents.tools.requests.get") as mock_get, \
         patch("agents.tools.requests.post") as mock_post:
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
