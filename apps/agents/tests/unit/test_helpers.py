import pytest
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from agents.helpers import strip_messages


def _ai_with_tool(tool_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": tool_id, "name": "alguma_tool", "args": {}, "type": "tool_call"}],
    )


def test_strip_apenas_human():
    messages = [HumanMessage(content="oi")]
    result = strip_messages(messages, last_n=10)
    assert len(result) == 1
    assert result[0].type == "human"


def test_strip_last_n_zero_retorna_vazio():
    messages = [HumanMessage(content="oi"), HumanMessage(content="tudo bem")]
    result = strip_messages(messages, last_n=0)
    assert result == []


def test_strip_last_n_limita_historico():
    messages = [HumanMessage(content=str(i)) for i in range(10)]
    result = strip_messages(messages, last_n=3)
    assert len(result) == 3
    assert result[-1].content == "9"


def test_strip_fecha_tool_pendente_antes_de_human():
    """AIMessage com tool_call sem ToolMessage correspondente deve receber placeholder."""
    ai_msg = _ai_with_tool("id1")
    messages = [ai_msg, HumanMessage(content="proxima")]
    result = strip_messages(messages, last_n=10)

    tool_msgs = [m for m in result if m.type == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].tool_call_id == "id1"
    assert tool_msgs[0].content == ""


def test_strip_tool_message_correspondente_nao_gera_placeholder():
    """Quando ToolMessage existe para o tool_call, não deve haver placeholder."""
    ai_msg = _ai_with_tool("id1")
    tool_msg = ToolMessage(content="resultado real", tool_call_id="id1")
    messages = [ai_msg, tool_msg]
    result = strip_messages(messages, last_n=10)

    tool_msgs = [m for m in result if m.type == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].content == "resultado real"


def test_strip_fecha_pendencias_restantes_ao_fim():
    """Se o histórico terminar com AIMessage com tool_call sem resposta, fecha ao fim."""
    ai_msg = _ai_with_tool("id_fim")
    messages = [HumanMessage(content="pergunta"), ai_msg]
    result = strip_messages(messages, last_n=10)

    tool_msgs = [m for m in result if m.type == "tool"]
    assert any(t.tool_call_id == "id_fim" for t in tool_msgs)


def test_strip_nao_comeca_no_meio_de_tool():
    """Quando o corte last_n cai em uma ToolMessage, deve recuar até o AIMessage pai."""
    ai_msg = _ai_with_tool("id1")
    tool_msg = ToolMessage(content="ok", tool_call_id="id1")
    messages = [
        HumanMessage(content="1"),
        HumanMessage(content="2"),
        ai_msg,
        tool_msg,
        HumanMessage(content="3"),
    ]
    # last_n=3 cortaria: ai_msg, tool_msg, human("3")
    # mas o código recua se o start_index apontar para um tool — o ai_msg já está incluído
    result = strip_messages(messages, last_n=3)
    assert result[0].type != "tool", "Historico não pode começar com ToolMessage"


def test_strip_multiplos_tool_calls_parcialmente_respondidos():
    """AIMessage com dois tool_calls onde só um tem resposta."""
    ai_msg = AIMessage(
        content="",
        tool_calls=[
            {"id": "a", "name": "t1", "args": {}, "type": "tool_call"},
            {"id": "b", "name": "t2", "args": {}, "type": "tool_call"},
        ],
    )
    tool_msg = ToolMessage(content="resposta de a", tool_call_id="a")
    messages = [ai_msg, tool_msg, HumanMessage(content="proxima")]
    result = strip_messages(messages, last_n=10)

    tool_msgs = [m for m in result if m.type == "tool"]
    tool_ids = {t.tool_call_id for t in tool_msgs}
    assert "a" in tool_ids
    assert "b" in tool_ids  # placeholder gerado para b


def test_strip_preserva_conteudo_das_mensagens():
    """Conteúdo das mensagens deve ser preservado após strip."""
    messages = [
        HumanMessage(content="pergunta do usuário"),
        AIMessage(content="resposta do modelo"),
        HumanMessage(content="segunda pergunta"),
    ]
    result = strip_messages(messages, last_n=10)
    contents = [m.content for m in result]
    assert "pergunta do usuário" in contents
    assert "resposta do modelo" in contents
    assert "segunda pergunta" in contents
