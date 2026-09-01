"""Conversas de teste: o tenant conversa com os próprios agentes sem WhatsApp.

Diferente do playground do admin (efêmero), aqui tudo persiste em
conversations/messages e o consumo debita créditos normalmente — teste gasta
token real de LLM.
"""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import UploadFile
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.agents import send_playground_message
from app.models import Conversation, CreditTransaction, Message, Tenant
from app.services.agents_engine import load_agents_for_engine
from app.services.pricing import (
    DOCUMENT_GENERATION_CREDIT_COST,
    calcular_creditos,
    get_current_pricing_config,
)
from app.services.test_attachments import process_test_attachment


async def send_test_message(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    conversation: Conversation,
    content: str,
    file: UploadFile | None = None,
) -> tuple[list[Message], bool]:
    """Persiste a mensagem do usuário (como contato), roda o agente síncrono e
    persiste/debita as respostas. Retorna (mensagens novas, grouped).

    `file`: anexo opcional (PDF/DOCX/TXT) — simula, na conversa de teste, o
    que o worker faz numa conversa real via WhatsApp (ver
    apps/worker/app/tasks/attachments.py): ingere na base de conhecimento
    pessoal da conversa ANTES de chamar o agents, e anexa uma nota de
    sucesso/falha à mensagem mandada pro agente (nunca ao texto persistido
    do contato, que reflete só o que ele de fato escreveu/anexou)."""
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
    # Commit ANTES da chamada ao agents: se ele falhar, a mensagem do usuário
    # sobrevive no histórico (mesma filosofia do fluxo real via webhook).
    await session.commit()
    await session.refresh(contact_message)

    message_for_agent = content
    if file is not None:
        thread_id = f"{tenant_id}:{conversation.contact_phone_number}"
        note = await process_test_attachment(
            file,
            tenant_id=str(tenant_id),
            conversation_id=thread_id,
            message_id=str(contact_message.id),
        )
        message_for_agent = f"{content}\n{note}".strip() if content else note

    result = await send_playground_message(
        tenant_id=str(tenant_id),
        contact_phone_number=conversation.contact_phone_number,
        message=message_for_agent,
        agents=await load_agents_for_engine(session, tenant_id),
    )
    if result is None:
        # 202: debounce agrupou numa execução em andamento — as respostas
        # serão persistidas pela requisição que está rodando.
        return [contact_message], True

    responses: list[str] = result["responses"]
    tokens_used = result["tokens_used"] or 0
    tokens_input = result.get("tokens_input", 0)
    tokens_output = result.get("tokens_output", 0)
    current_agent_id = result.get("current_agent_id")
    documents: list[dict] = result.get("documents", [])
    if current_agent_id:
        # Pra exibir "{nome do agente} respondendo" no painel — mesma coluna
        # usada pelas conversas reais (worker), atualizada aqui pro caminho
        # síncrono das conversas de teste.
        conversation.current_agent_id = uuid.UUID(current_agent_id)
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
    return [contact_message, *agent_messages], False
