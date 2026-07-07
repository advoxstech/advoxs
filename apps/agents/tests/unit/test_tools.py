import pytest
import requests
from unittest.mock import AsyncMock, patch, MagicMock
from langgraph.types import Command
from agents.tools import (
    transfer_to_specialist,
    bucar_base_conhecimento_condominial,
    bucar_base_conhecimento_contratos,
    bucar_base_conhecimento_direito_consumidor,
    bucar_base_conhecimento_usuario,
    enviar_documento,
)


# ──────────────────────────────────────────────
# transfer_to_specialist
# ──────────────────────────────────────────────

def test_transfer_retorna_command():
    result = transfer_to_specialist.invoke({"current_specialist": "agente_condominial"})
    assert isinstance(result, Command)


def test_transfer_atualiza_current_specialist():
    result = transfer_to_specialist.invoke({"current_specialist": "agente_condominial"})
    assert result.update["current_specialist"] == "agente_condominial"


def test_transfer_ativa_receptive_message():
    result = transfer_to_specialist.invoke({"current_specialist": "agente_condominial"})
    assert result.update["receptive_message_specialist"] is True


@pytest.mark.parametrize("specialist", [
    "agente_condominial",
    "agente_contratos",
    "agente_direito_consumidor",
])
def test_transfer_todos_especialistas_validos(specialist):
    result = transfer_to_specialist.invoke({"current_specialist": specialist})
    assert result.update["current_specialist"] == specialist


# ──────────────────────────────────────────────
# bucar_base_conhecimento_condominial
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_buscar_condominial_chama_base_correta():
    with patch("agents.tools.retrieval_sistema", new=AsyncMock(return_value="resultado")) as mock_fn:
        result = await bucar_base_conhecimento_condominial.ainvoke({"query": "taxa condominial"})
        mock_fn.assert_called_once_with("condominial", "taxa condominial")
        assert result == "resultado"


@pytest.mark.asyncio
async def test_buscar_condominial_nao_chama_outra_base():
    with patch("agents.tools.retrieval_sistema", new=AsyncMock(return_value="")) as mock_fn:
        await bucar_base_conhecimento_condominial.ainvoke({"query": "qualquer"})
        base_usada = mock_fn.call_args[0][0]
        assert base_usada == "condominial"


# ──────────────────────────────────────────────
# bucar_base_conhecimento_contratos
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_buscar_contratos_chama_base_correta():
    with patch("agents.tools.retrieval_sistema", new=AsyncMock(return_value="resultado")) as mock_fn:
        result = await bucar_base_conhecimento_contratos.ainvoke({"query": "multa contratual"})
        mock_fn.assert_called_once_with("contratos", "multa contratual")
        assert result == "resultado"


@pytest.mark.asyncio
async def test_buscar_contratos_nao_chama_outra_base():
    with patch("agents.tools.retrieval_sistema", new=AsyncMock(return_value="")) as mock_fn:
        await bucar_base_conhecimento_contratos.ainvoke({"query": "qualquer"})
        base_usada = mock_fn.call_args[0][0]
        assert base_usada == "contratos"


# ──────────────────────────────────────────────
# bucar_base_conhecimento_direito_consumidor
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_buscar_direito_consumidor_chama_base_correta():
    with patch("agents.tools.retrieval_sistema", new=AsyncMock(return_value="resultado")) as mock_fn:
        result = await bucar_base_conhecimento_direito_consumidor.ainvoke({"query": "prazo de garantia"})
        mock_fn.assert_called_once_with("direito_consumidor", "prazo de garantia")
        assert result == "resultado"


@pytest.mark.asyncio
async def test_buscar_direito_consumidor_nao_chama_outra_base():
    with patch("agents.tools.retrieval_sistema", new=AsyncMock(return_value="")) as mock_fn:
        await bucar_base_conhecimento_direito_consumidor.ainvoke({"query": "qualquer"})
        base_usada = mock_fn.call_args[0][0]
        assert base_usada == "direito_consumidor"


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
