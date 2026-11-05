"""Conversas de teste: o tenant conversa com os próprios agentes sem WhatsApp.

Diferente do playground do admin (efêmero), aqui tudo persiste em
conversations/messages e o consumo debita créditos normalmente — teste gasta
token real de LLM.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, UploadFile
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import open_tenant_session
from app.clients.agents import send_playground_message
from app.models import Conversation, CreditTransaction, Message, Tenant
from app.services.agent_versions import test_snapshot
from app.services.agents_engine import load_agents_for_engine
from app.services.pricing import (
    DOCUMENT_GENERATION_CREDIT_COST,
    calcular_creditos,
    get_current_pricing_config,
)
from app.services.test_attachments import ingest_test_attachment, read_test_attachment
from app.services.urgency import flag_contact_message, mark_urgent

logger = logging.getLogger(__name__)

# Mesmo critério do worker (apps/worker/app/tasks/attachments.py): leitura que
# passa disso vira aviso + resposta depois.
ATTACHMENT_NOTICE_AFTER_SECONDS = 45
READING_NOTICE = (
    "Recebi seu documento. Ele é grande e vou levar alguns minutos para ler; "
    "assim que terminar, respondo sua mensagem."
)
# Mesmo texto de apps/worker/app/tasks/attachments.py: o agente não viu o aviso
# (texto fixo, fora do LLM) e precisa saber que ficou devendo a resposta.
READING_NOTICE_AGENT_NOTE = (
    "[A leitura do documento demorou e o cliente recebeu o aviso automático: "
    f'"{READING_NOTICE}" Responda agora ao que ele pediu junto com o documento, '
    "considerando também as mensagens que ele mandou enquanto esperava.]"
)
BACKGROUND_FAILURE_NOTICE = (
    "Não foi possível concluir a resposta depois da leitura do documento. Envie a mensagem "
    "novamente."
)

_background_turns: set[asyncio.Task] = set()


@dataclass
class TestTurn:
    messages: list[Message]
    grouped: bool = False
    # True quando o documento ainda está sendo lido e a resposta virá depois.
    reading: bool = False

    __test__ = False  # não é uma classe de teste do pytest


async def send_test_message(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    conversation: Conversation,
    content: str,
    file: UploadFile | None = None,
) -> TestTurn:
    """Persiste a mensagem do usuário (como contato), roda o agente e
    persiste/debita as respostas.

    `file`: anexo opcional (PDF/DOCX/TXT) — simula, na conversa de teste, o
    que o worker faz numa conversa real via WhatsApp (ver
    apps/worker/app/tasks/attachments.py): ingere na base de conhecimento
    pessoal da conversa ANTES de chamar o agents, e anexa uma nota de
    sucesso/falha à mensagem mandada pro agente (nunca ao texto persistido
    do contato, que reflete só o que ele de fato escreveu/anexou). Se a
    leitura passar de ATTACHMENT_NOTICE_AFTER_SECONDS, devolve a mensagem do
    contato e um aviso (`reading=True`); a resposta do agente é gravada
    quando a leitura terminar e aparece pela atualização periódica do painel."""
    snapshot = (
        await test_snapshot(session, tenant_id, conversation.id)
        if conversation.contact_phone_number.startswith("rascunho-")
        else None
    )
    # A deleted agent cascades its snapshots. Never switch a draft test to live config.
    if snapshot is None and conversation.contact_phone_number.startswith("rascunho-"):
        raise HTTPException(409, "Este teste não está mais disponível. Inicie um novo teste.")
    agents = (
        snapshot.agents_snapshot
        if snapshot is not None
        else await load_agents_for_engine(session, tenant_id)
    )
    now = datetime.now(UTC)
    contact_message = Message(
        conversation_id=conversation.id,
        tenant_id=tenant_id,
        sender_type="contact",
        content=content or (f"📎 {file.filename}" if file is not None else ""),
        created_at=now,
    )
    session.add(contact_message)
    conversation.last_message_at = now
    await flag_contact_message(
        session,
        conversation,
        content,
        **({"agent_id": snapshot.agent_id} if snapshot is not None else {}),
    )
    # Commit ANTES da chamada ao agents: se ele falhar, a mensagem do usuário
    # sobrevive no histórico (mesma filosofia do fluxo real via webhook).
    await session.commit()
    await session.refresh(contact_message)

    start_agent_id = str(snapshot.agent_id) if snapshot is not None else None
    message_for_agent = content
    if file is not None:
        attachment = await read_test_attachment(file, tenant_id=str(tenant_id))
        if isinstance(attachment, str):
            note = attachment
        else:
            # conversation_id aqui precisa ser só o contact_phone_number, NUNCA o
            # thread_id composto "{tenant_id}:{contact_phone_number}" — o agents
            # (apps/agents/clients/retrieval.py::retrieval_usuario) recebe esse
            # thread_id completo e faz .partition(":") ele mesmo antes de
            # consultar o api_rag; a ingestão precisa gravar sob a MESMA metade
            # (o contato) que sobra depois desse split, senão a busca nunca
            # encontra o documento (bug real encontrado em teste manual).
            ingestion = asyncio.ensure_future(
                ingest_test_attachment(
                    attachment,
                    tenant_id=str(tenant_id),
                    conversation_id=conversation.contact_phone_number,
                    message_id=str(contact_message.id),
                )
            )
            done, _ = await asyncio.wait({ingestion}, timeout=ATTACHMENT_NOTICE_AFTER_SECONDS)
            if not done:
                # Documento grande: responde já com o aviso e termina depois. A
                # Cloudflare corta requisições em ~100 s, então não dá para
                # esperar a leitura aqui dentro.
                notice = await _start_background_turn(
                    session, tenant_id, conversation, content, ingestion, agents, start_agent_id
                )
                return TestTurn([contact_message, notice], grouped=False, reading=True)
            note = ingestion.result()
        message_for_agent = f"{content}\n{note}".strip() if content else note

    turn = await _run_agent_turn(
        session, tenant_id, conversation, content, message_for_agent, agents, start_agent_id
    )
    return TestTurn([contact_message, *turn.messages], grouped=turn.grouped)


async def _start_background_turn(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    conversation: Conversation,
    content: str,
    ingestion: asyncio.Future,
    agents: list[dict],
    start_agent_id: str | None,
) -> Message:
    """Registra o aviso de leitura e agenda o turno do agente para depois dela."""
    notice = Message(
        conversation_id=conversation.id,
        tenant_id=tenant_id,
        sender_type="system",
        content=READING_NOTICE,
        created_at=datetime.now(UTC),
    )
    session.add(notice)
    conversation.automation_status = "processing"
    await session.commit()
    await session.refresh(notice)
    task = asyncio.create_task(
        _finish_turn_after_reading(
            tenant_id, conversation.id, content, ingestion, agents, start_agent_id
        )
    )
    # Referência forte: o event loop só guarda referência fraca das tasks.
    _background_turns.add(task)
    task.add_done_callback(_background_turns.discard)
    return notice


async def _finish_turn_after_reading(
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    content: str,
    ingestion: asyncio.Future,
    agents: list[dict],
    start_agent_id: str | None,
) -> None:
    """Espera a leitura terminar e roda o turno do agente numa sessão própria.

    Roda dentro do processo do api: se ele reiniciar no meio (deploy), o turno
    se perde e a conversa de teste fica sem resposta — aceitável para teste.
    """
    note = await ingestion
    message_for_agent = "\n".join(
        part for part in (content, note, READING_NOTICE_AGENT_NOTE) if part
    )
    async with open_tenant_session(tenant_id) as session:
        conversation = await session.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.tenant_id == tenant_id,
                Conversation.deletion_requested_at.is_(None),
            )
        )
        if conversation is None:
            return
        try:
            await _run_agent_turn(
                session,
                tenant_id,
                conversation,
                content,
                message_for_agent,
                agents,
                start_agent_id,
            )
            conversation.automation_status = "idle"
            await session.commit()
        except Exception:
            logger.exception("Falha no turno de teste depois da leitura | tenant=%s", tenant_id)
            await session.rollback()
            conversation = await session.get(Conversation, conversation_id)
            if conversation is not None:
                session.add(
                    Message(
                        conversation_id=conversation_id,
                        tenant_id=tenant_id,
                        sender_type="system",
                        content=BACKGROUND_FAILURE_NOTICE,
                        created_at=datetime.now(UTC),
                    )
                )
                conversation.automation_status = "idle"
                await session.commit()


async def _run_agent_turn(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    conversation: Conversation,
    content: str,
    message_for_agent: str,
    agents: list[dict],
    start_agent_id: str | None,
) -> TestTurn:
    """Chama o agente e persiste/debita as respostas (sem a mensagem do contato)."""
    result = await send_playground_message(
        tenant_id=str(tenant_id),
        contact_phone_number=conversation.contact_phone_number,
        message=message_for_agent,
        agents=agents,
        **({"test_start_agent_id": start_agent_id} if start_agent_id is not None else {}),
    )
    if result is None:
        # 202: debounce agrupou numa execução em andamento — as respostas
        # serão persistidas pela requisição que está rodando.
        return TestTurn([], grouped=True)

    responses: list[str] = result["responses"]
    tokens_used = result["tokens_used"] or 0
    tokens_input = result.get("tokens_input", 0)
    tokens_output = result.get("tokens_output", 0)
    current_agent_id = result.get("current_agent_id")
    documents: list[dict] = result.get("documents", [])
    if current_agent_id:
        previous_agent_id = getattr(conversation, "current_agent_id", None)
        # Pra exibir "{nome do agente} respondendo" no painel — mesma coluna
        # usada pelas conversas reais (worker), atualizada aqui pro caminho
        # síncrono das conversas de teste.
        conversation.current_agent_id = uuid.UUID(current_agent_id)
        if previous_agent_id != conversation.current_agent_id:
            await flag_contact_message(
                session,
                conversation,
                content,
                agent_id=conversation.current_agent_id,
            )
    if result.get("urgency"):
        mark_urgent(conversation, result["urgency"], "agent")
    config = await get_current_pricing_config(session)
    # Custo de tokens + custo fixo de cada documento gerado nesta execução
    # (ver agents/tools.py) — mesma fórmula do worker (conversas reais).
    credits = (
        calcular_creditos(tokens_input, tokens_output, tokens_used, config)
        + len(documents) * DOCUMENT_GENERATION_CREDIT_COST
    )

    now = datetime.now(UTC)
    agent_messages: list[Message] = []
    index = 0
    for text in responses:
        message = Message(
            conversation_id=conversation.id,
            tenant_id=tenant_id,
            sender_type="agent",
            content=text,
            response_sources=(
                result.get("response_sources", [])[index]
                if index < len(result.get("response_sources", []))
                else None
            ),
            # Mesma execução pode gerar várias respostas/documentos (ex:
            # despedida da secretária + saudação do especialista) — sem
            # offset por índice, todas cravam o mesmo instante e a ordenação
            # por created_at não tem como desempatar a ordem real de geração.
            created_at=now + timedelta(microseconds=index),
            tokens_used=tokens_used if index == 0 else None,
            credits_consumed=credits if index == 0 else None,
        )
        session.add(message)
        agent_messages.append(message)
        index += 1
    for doc in documents:
        message = Message(
            conversation_id=conversation.id,
            tenant_id=tenant_id,
            sender_type="agent",
            content=f"📄 {doc['filename']}",
            media_url=doc["link"],
            media_type="application/pdf",
            created_at=now + timedelta(microseconds=index),
            tokens_used=tokens_used if index == 0 else None,
            credits_consumed=credits if index == 0 else None,
        )
        session.add(message)
        agent_messages.append(message)
        index += 1
    conversation.last_message_at = now
    await session.flush()

    if credits and agent_messages:
        # Ledger + saldo na mesma transação das mensagens (fórmula do worker).
        # Lock da linha do tenant: serializa débitos concorrentes do saldo.
        await session.execute(
            select(Tenant.credit_balance).where(Tenant.id == tenant_id).with_for_update()
        )
        session.add(
            CreditTransaction(
                tenant_id=tenant_id,
                type="consumption",
                amount_credits=-credits,
                related_message_id=agent_messages[0].id,
                tokens_input=tokens_input or None,
                tokens_output=tokens_output or None,
                pricing_config_id=config.id,
                description="Consumo do agente em conversa de teste",
            )
        )
        await session.execute(
            update(Tenant)
            .where(Tenant.id == tenant_id)
            .values(credit_balance=Tenant.credit_balance - credits)
        )
    await session.commit()
    for message in agent_messages:
        await session.refresh(message)
    return TestTurn(agent_messages, grouped=False)
