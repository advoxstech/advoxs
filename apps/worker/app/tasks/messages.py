import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from arq.worker import Retry
from sqlalchemy import delete, func, insert, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import tables
from app.billing_gate import handle_billing_gate, maybe_enter_gate
from app.clients.agents import (
    replace_context_to_agents,
    send_message_to_agents,
    sync_context_to_agents,
)
from app.crypto import decrypt_access_token
from app.db import open_system_session, open_tenant_session
from app.email_notifications import send_tenant_out_of_credits_notification
from app.pricing import (
    DOCUMENT_GENERATION_CREDIT_COST,
    calcular_creditos,
    get_current_pricing_config,
)
from app.safe_logging import safe_error
from app.tasks.attachments import process_inbound_attachment
from app.tasks.inbound_context import InboundContext
from app.tasks.outbound_outbox import enqueue_outbound_message_jobs

logger = logging.getLogger(__name__)

# Na última tentativa, vira a conversa pra humano em vez de reagendar (o
# default de max_tries do Arq também é 5 — manter em sincronia, mesmo padrão
# já usado em apps/worker/app/tasks/knowledge_base.py).
MAX_TRIES = 5
PROCESSING_LEASE = timedelta(minutes=15)


async def _load_agents(session: AsyncSession, tenant_id: str) -> list[dict]:
    """Carrega os agentes do tenant + os ids dos arquivos de KB anexados a
    cada um — nunca lido pelo agents service diretamente do Postgres
    principal, só propagado por aqui em cada POST /messages. Sempre faz as
    duas queries (mesmo com 0 agentes) — o contrato da API garante que todo
    tenant tem ao menos 1 agente, então o caso vazio é só defensivo."""
    agents_result = await session.execute(
        select(
            tables.agents.c.id,
            tables.agents.c.name,
            tables.agents.c.instructions,
            tables.agents.c.is_entry_point,
        ).where(tables.agents.c.tenant_id == uuid.UUID(tenant_id))
    )
    agents_rows = agents_result.all()

    links_result = await session.execute(
        select(
            tables.agent_knowledge_base_files.c.agent_id,
            tables.agent_knowledge_base_files.c.knowledge_base_file_id,
        ).where(tables.agent_knowledge_base_files.c.agent_id.in_([row.id for row in agents_rows]))
    )
    kb_by_agent: dict[uuid.UUID, list[str]] = {}
    for agent_id, file_id in links_result.all():
        kb_by_agent.setdefault(agent_id, []).append(str(file_id))

    return [
        {
            "id": str(row.id),
            "name": row.name,
            "instructions": row.instructions,
            "is_entry_point": row.is_entry_point,
            "knowledge_base_file_ids": kb_by_agent.get(row.id, []),
        }
        for row in agents_rows
    ]


async def _sync_context(
    http: httpx.AsyncClient, tenant_id: str, contact_phone_number: str, content: str
) -> None:
    """Best-effort: falha no sync não pode quebrar o processamento."""
    try:
        await sync_context_to_agents(
            http,
            tenant_id=tenant_id,
            contact_phone_number=contact_phone_number,
            role="contact",
            content=content,
        )
    except Exception as exc:
        logger.warning(
            "Falha ao sincronizar contexto do takeover | tenant=%s error_type=%s",
            tenant_id,
            safe_error(exc),
        )


async def _claim_inbound_job(ctx: dict, tenant_id: str, job_id: str) -> bool:
    """Reserva o turno mais antigo de uma conversa, uma única vez.

    Redis pode entregar o mesmo job mais de uma vez e diversos workers podem
    receber mensagens do mesmo contato. O bloqueio persistente e a checagem
    de precedência mantêm uma execução de IA por conversa, em ordem.
    """
    now = datetime.now(UTC)
    tenant_uuid = uuid.UUID(tenant_id)
    job_uuid = uuid.UUID(job_id)
    async with open_system_session(ctx["system_session_factory"]) as session:
        candidate = (
            await session.execute(
                select(
                    tables.inbound_message_jobs.c.conversation_id,
                    tables.inbound_message_jobs.c.created_at,
                ).where(
                    tables.inbound_message_jobs.c.id == job_uuid,
                    tables.inbound_message_jobs.c.tenant_id == tenant_uuid,
                    tables.inbound_message_jobs.c.status == "pending",
                    tables.inbound_message_jobs.c.available_at <= now,
                )
            )
        ).one_or_none()
        if candidate is None:
            return False

        lock = await session.execute(
            pg_insert(tables.conversation_processing_locks)
            .values(
                conversation_id=candidate.conversation_id,
                tenant_id=tenant_uuid,
                job_id=job_uuid,
                locked_at=now,
            )
            .on_conflict_do_update(
                index_elements=[tables.conversation_processing_locks.c.conversation_id],
                set_={"tenant_id": tenant_uuid, "job_id": job_uuid, "locked_at": now},
                where=(tables.conversation_processing_locks.c.locked_at < now - PROCESSING_LEASE),
            )
            .returning(tables.conversation_processing_locks.c.conversation_id)
        )
        conversation_id = lock.scalar_one_or_none()
        if conversation_id is None:
            await session.rollback()
            return False

        candidate = (
            await session.execute(
                select(
                    tables.inbound_message_jobs.c.conversation_id,
                    tables.inbound_message_jobs.c.created_at,
                ).where(
                    tables.inbound_message_jobs.c.id == job_uuid,
                    tables.inbound_message_jobs.c.tenant_id == tenant_uuid,
                    tables.inbound_message_jobs.c.status == "pending",
                    tables.inbound_message_jobs.c.available_at <= now,
                )
            )
        ).one_or_none()
        if candidate is None:
            await session.execute(
                delete(tables.conversation_processing_locks).where(
                    tables.conversation_processing_locks.c.conversation_id == conversation_id,
                    tables.conversation_processing_locks.c.job_id == job_uuid,
                )
            )
            await session.commit()
            return False

        predecessor = await session.execute(
            select(tables.inbound_message_jobs.c.id)
            .where(
                tables.inbound_message_jobs.c.conversation_id == candidate.conversation_id,
                tables.inbound_message_jobs.c.status.in_(("pending", "processing")),
                tuple_(
                    tables.inbound_message_jobs.c.created_at,
                    tables.inbound_message_jobs.c.id,
                )
                < tuple_(candidate.created_at, job_uuid),
            )
            .limit(1)
        )
        if predecessor.scalar_one_or_none() is not None:
            await session.execute(
                delete(tables.conversation_processing_locks).where(
                    tables.conversation_processing_locks.c.conversation_id == conversation_id,
                    tables.conversation_processing_locks.c.job_id == job_uuid,
                )
            )
            await session.commit()
            return False

        claimed = await session.execute(
            update(tables.inbound_message_jobs)
            .where(
                tables.inbound_message_jobs.c.id == job_uuid,
                tables.inbound_message_jobs.c.tenant_id == tenant_uuid,
                tables.inbound_message_jobs.c.status == "pending",
                tables.inbound_message_jobs.c.available_at <= now,
            )
            .values(
                status="processing",
                attempts=tables.inbound_message_jobs.c.attempts + 1,
                locked_at=now,
                last_error=None,
            )
            .returning(tables.inbound_message_jobs.c.id)
        )
        if claimed.scalar_one_or_none() is None:
            await session.execute(
                delete(tables.conversation_processing_locks).where(
                    tables.conversation_processing_locks.c.conversation_id == conversation_id,
                    tables.conversation_processing_locks.c.job_id == job_uuid,
                )
            )
            await session.commit()
            return False
        await session.commit()
    return True


async def _finish_inbound_job(
    ctx: dict, job_id: str, *, failed: bool = False, error: str | None = None
) -> None:
    async with open_system_session(ctx["system_session_factory"]) as session:
        await session.execute(
            update(tables.inbound_message_jobs)
            .where(tables.inbound_message_jobs.c.id == uuid.UUID(job_id))
            .values(
                status="failed" if failed else "completed",
                completed_at=datetime.now(UTC),
                locked_at=None,
                last_error=(error or "")[:1000] if failed else None,
            )
        )
        await session.commit()


async def _release_inbound_job(
    ctx: dict, job_id: str, error: Exception, *, defer_seconds: int
) -> None:
    async with open_system_session(ctx["system_session_factory"]) as session:
        await session.execute(
            update(tables.inbound_message_jobs)
            .where(tables.inbound_message_jobs.c.id == uuid.UUID(job_id))
            .values(
                status="pending",
                locked_at=None,
                available_at=datetime.now(UTC) + timedelta(seconds=defer_seconds),
                last_error=str(error)[:1000],
            )
        )
        await session.commit()


async def _release_conversation_lock(ctx: dict, conversation_id: str, job_id: str) -> None:
    """Libera somente o bloqueio que pertence a este job.

    A condição pelo job protege contra um worker antigo apagar um bloqueio já
    recuperado e assumido por outra execução.
    """
    async with open_system_session(ctx["system_session_factory"]) as session:
        await session.execute(
            delete(tables.conversation_processing_locks).where(
                tables.conversation_processing_locks.c.conversation_id
                == uuid.UUID(conversation_id),
                tables.conversation_processing_locks.c.job_id == uuid.UUID(job_id),
            )
        )
        await session.commit()


async def _enqueue_next_inbound_message_job(ctx: dict, conversation_id: str) -> None:
    """Dispara sem espera o próximo turno já disponível da conversa."""
    if "redis" not in ctx:
        return
    now = datetime.now(UTC)
    async with open_system_session(ctx["system_session_factory"]) as session:
        row = (
            await session.execute(
                select(
                    tables.inbound_message_jobs.c.id,
                    tables.inbound_message_jobs.c.tenant_id,
                    tables.inbound_message_jobs.c.message_id,
                )
                .where(
                    tables.inbound_message_jobs.c.conversation_id == uuid.UUID(conversation_id),
                    tables.inbound_message_jobs.c.status == "pending",
                    tables.inbound_message_jobs.c.available_at <= now,
                )
                .order_by(
                    tables.inbound_message_jobs.c.created_at,
                    tables.inbound_message_jobs.c.id,
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).one_or_none()
        if row is not None:
            await session.execute(
                update(tables.inbound_message_jobs)
                .where(tables.inbound_message_jobs.c.id == row.id)
                .values(last_enqueued_at=now)
            )
        await session.commit()
    if row is not None:
        await ctx["redis"].enqueue_job(
            "process_inbound_message",
            tenant_id=str(row.tenant_id),
            conversation_id=conversation_id,
            message_id=str(row.message_id),
            job_id=str(row.id),
        )


async def _has_newer_contact_message(
    session: AsyncSession, conversation_id: str, message_id: str
) -> bool:
    marker = (
        await session.execute(
            select(tables.messages.c.created_at).where(
                tables.messages.c.id == uuid.UUID(message_id)
            )
        )
    ).scalar_one_or_none()
    if marker is None:
        return False
    newer = await session.execute(
        select(tables.messages.c.id)
        .where(
            tables.messages.c.conversation_id == uuid.UUID(conversation_id),
            tables.messages.c.sender_type == "contact",
            tables.messages.c.created_at > marker,
        )
        .limit(1)
    )
    return newer.scalar_one_or_none() is not None


async def _conversation_allows_automation(
    session: AsyncSession, conversation_id: str, *, lock: bool = False
) -> bool:
    """Lê a fonte de verdade; com lock, serializa com o takeover da API."""
    statement = select(tables.conversations.c.state).where(
        tables.conversations.c.id == uuid.UUID(conversation_id)
    )
    if lock:
        statement = statement.with_for_update()
    state = (await session.execute(statement)).scalar_one_or_none()
    return state == "agent"


async def _restore_agent_context_before_stale_response(
    session: AsyncSession,
    http: httpx.AsyncClient,
    tenant_id: str,
    conversation_id: str,
    message_id: str,
    contact_phone_number: str,
) -> None:
    """Remove da memória do agente a resposta que não será entregue."""
    marker = (
        await session.execute(
            select(tables.messages.c.created_at).where(
                tables.messages.c.id == uuid.UUID(message_id)
            )
        )
    ).scalar_one_or_none()
    if marker is None:
        raise RuntimeError("mensagem de referência não encontrada para restaurar contexto")
    rows = (
        await session.execute(
            select(tables.messages.c.sender_type, tables.messages.c.content)
            .where(
                tables.messages.c.conversation_id == uuid.UUID(conversation_id),
                tables.messages.c.created_at <= marker,
                tables.messages.c.sender_type.in_(("contact", "agent", "human")),
            )
            .order_by(tables.messages.c.created_at, tables.messages.c.id)
        )
    ).all()
    history = [
        {
            "role": "contact" if row.sender_type == "contact" else "attendant",
            "content": row.content,
        }
        for row in rows
    ]
    await replace_context_to_agents(
        http,
        tenant_id=tenant_id,
        contact_phone_number=contact_phone_number,
        messages=history,
    )


async def process_inbound_message(
    ctx: dict, tenant_id: str, conversation_id: str, message_id: str, job_id: str | None = None
) -> None:
    """Processa o turno e mantém o estado operacional consistente em falhas."""
    if job_id is not None and not await _claim_inbound_job(ctx, tenant_id, job_id):
        return
    try:
        await _process_inbound_message(ctx, tenant_id, conversation_id, message_id)
    except Retry:
        if job_id is not None:
            await _release_inbound_job(
                ctx,
                job_id,
                RuntimeError("tentativa reagendada"),
                defer_seconds=ctx.get("job_try", 1) * 10,
            )
            await _release_conversation_lock(ctx, conversation_id, job_id)
        raise
    except Exception as exc:
        if ctx.get("job_try", 1) < MAX_TRIES:
            logger.error(
                "Falha inesperada no turno, reagendando | tenant=%s conversation=%s error_type=%s",
                tenant_id,
                conversation_id,
                safe_error(exc),
            )
            if job_id is not None:
                await _release_inbound_job(
                    ctx, job_id, exc, defer_seconds=ctx.get("job_try", 1) * 10
                )
                await _release_conversation_lock(ctx, conversation_id, job_id)
            raise Retry(defer=ctx.get("job_try", 1) * 10) from exc

        logger.error(
            "Falha inesperada definitiva no turno | tenant=%s conversation=%s error_type=%s",
            tenant_id,
            conversation_id,
            safe_error(exc),
        )
        session_factory = ctx["session_factory"]
        async with open_tenant_session(session_factory, tenant_id) as session:
            await session.execute(
                update(tables.conversations)
                .where(tables.conversations.c.id == uuid.UUID(conversation_id))
                .values(state="human", automation_status="failed")
            )
            await session.commit()
        if job_id is not None:
            await _finish_inbound_job(ctx, job_id, failed=True, error=safe_error(exc))
            await _release_conversation_lock(ctx, conversation_id, job_id)
            await _enqueue_next_inbound_message_job(ctx, conversation_id)
    else:
        if job_id is not None:
            await _finish_inbound_job(ctx, job_id)
            await _release_conversation_lock(ctx, conversation_id, job_id)
            await _enqueue_next_inbound_message_job(ctx, conversation_id)


async def _process_inbound_message(
    ctx: dict, tenant_id: str, conversation_id: str, message_id: str
) -> None:
    """Verifica o estado da conversa (agent|human) e repassa para o agents service.

    Enfileirado pelo `api` depois de persistir a mensagem do contato. As respostas
    do agente voltam na chamada e são persistidas em `messages` (sender_type=agent).
    """
    session_factory = ctx["session_factory"]
    http: httpx.AsyncClient = ctx["http"]
    rag_http: httpx.AsyncClient = ctx["rag_http"]

    async with open_tenant_session(session_factory, tenant_id) as session:
        inbound = await _load_context(session, tenant_id, conversation_id, message_id)

    if inbound is None:
        return

    async with open_tenant_session(session_factory, tenant_id) as session:
        entrou_no_gate = await maybe_enter_gate(session, tenant_id, conversation_id, inbound)
    if entrou_no_gate:
        try:
            async with open_tenant_session(session_factory, tenant_id) as session:
                await handle_billing_gate(session, tenant_id, conversation_id, inbound)
        except Exception as exc:
            # Qualquer chamada externa dentro do billing gate (envio de texto/
            # lista via WhatsApp, criação do checkout) pode falhar — se a
            # exceção subisse incapturada, o job do arq morreria (depois das
            # tentativas do próprio arq) e a conversa ficaria travada em
            # state=billing_gate pra sempre: a válvula de MAX_RETRIES do gate
            # só dispara numa RESPOSTA não reconhecida, nunca numa falha de
            # ENVIO. Mesmo princípio da escalada em send_message_to_agents
            # abaixo: silêncio nunca é melhor que qualquer erro transiente de
            # rede. Sessão nova (não a que pode ter ficado com a transação
            # suja/abortada) — garante app.tenant_id setado de novo pra RLS.
            logger.error(
                "Falha ao processar o billing gate, virando conversa pra human | "
                "tenant=%s conversation=%s error_type=%s",
                tenant_id,
                conversation_id,
                safe_error(exc),
            )
            async with open_tenant_session(session_factory, tenant_id) as session:
                await session.execute(
                    update(tables.conversations)
                    .where(tables.conversations.c.id == uuid.UUID(conversation_id))
                    .values(
                        state="human",
                        automation_status="failed",
                        billing_gate_step=None,
                        billing_gate_retries=0,
                        billing_gate_checkout_url=None,
                    )
                )
                await session.commit()
        return

    if inbound.conversation_state != "agent":
        # O takeover persiste até o atendente devolver a conversa à IA.
        logger.info(
            "Automação pausada, agente não acionado | tenant=%s conversation=%s state=%s",
            tenant_id,
            conversation_id,
            inbound.conversation_state,
        )
        await _sync_context(http, tenant_id, inbound.contact_phone_number, inbound.message_content)
        return

    # Moeda única: turno custeado pelo cliente final (cobrança habilitada e
    # saldo positivo) roda mesmo com o estoque do tenant zerado — esse crédito
    # já saiu do estoque na revenda. Silêncio total só quando o turno seria
    # custeado pelo tenant E o saldo dele esgotou.
    customer_funded = (
        not inbound.end_customer_billing_exempt
        and inbound.end_customer_billing_enabled
        and inbound.end_customer_balance > 0
    )
    if (
        inbound.credit_balance <= 0
        and not customer_funded
        and not inbound.end_customer_has_active_subscription
    ):
        logger.info(
            "Saldo esgotado, agente não acionado | tenant=%s conversation=%s saldo=%s",
            tenant_id,
            conversation_id,
            inbound.credit_balance,
        )
        await _sync_context(http, tenant_id, inbound.contact_phone_number, inbound.message_content)
        async with open_tenant_session(session_factory, tenant_id) as session:
            await session.execute(
                update(tables.conversations)
                .where(tables.conversations.c.id == uuid.UUID(conversation_id))
                .values(automation_status="idle")
            )
            await session.commit()
        return

    meta_access_token: str | None = None
    zapi_client_token: str | None = None
    if inbound.whatsapp_provider == "zapi":
        zapi_client_token = (
            decrypt_access_token(inbound.zapi_client_token_encrypted)
            if inbound.zapi_client_token_encrypted
            else ""
        )
    else:
        meta_access_token = decrypt_access_token(inbound.access_token_encrypted)

    # Baixa e ingere um eventual anexo (PDF/DOCX/TXT) na base de conhecimento
    # pessoal do contato ANTES de chamar o agents — só assim o documento já
    # fica pesquisável na mesma resposta (ver app/tasks/attachments.py).
    # Best-effort: nunca levanta, só devolve uma nota anexada à mensagem.
    #
    # conversation_id aqui precisa ser só o contact_phone_number, NUNCA o
    # `conversation_id` (uuid de `conversations`, parâmetro desta função) nem
    # o thread_id composto do checkpoint — retrieval_usuario, do lado do
    # agents (apps/agents/clients/retrieval.py), recebe o thread_id completo
    # "{tenant_id}:{contact_phone_number}" e faz .partition(":") ele mesmo
    # antes de consultar o api_rag, então a ingestão precisa gravar sob a
    # MESMA metade (o contato) que sobra depois desse split, senão a busca
    # nunca encontra o documento (bug real encontrado em teste manual).
    attachment_note = await process_inbound_attachment(
        rag_http,
        tenant_id=tenant_id,
        conversation_id=inbound.contact_phone_number,
        message_id=message_id,
        media_ref=inbound.media_url,
        media_type=inbound.media_type,
        whatsapp_provider=inbound.whatsapp_provider,
        access_token=meta_access_token,
        zapi_client_token=zapi_client_token,
    )
    message_content = inbound.message_content
    if attachment_note:
        message_content = f"{message_content}\n{attachment_note}".strip()

    # Se outra mensagem já chegou antes de iniciarmos a IA, preserva a atual
    # no checkpoint e deixa apenas o último turno pendente gerar a resposta.
    # Isso reduz respostas intermediárias e custo em rajadas maiores que o
    # debounce, sem perder a ordem do histórico.
    async with open_tenant_session(session_factory, tenant_id) as session:
        newer_message_already_saved = await _has_newer_contact_message(
            session, conversation_id, message_id
        )
    if newer_message_already_saved:
        await sync_context_to_agents(
            http,
            tenant_id=tenant_id,
            contact_phone_number=inbound.contact_phone_number,
            role="contact",
            content=message_content,
        )
        logger.info(
            "Mensagem incorporada sem gerar resposta; há turno mais recente | "
            "tenant=%s conversation=%s",
            tenant_id,
            conversation_id,
        )
        return

    # O estado carregado no início do job pode ter mudado enquanto o anexo
    # era processado. Revalidamos antes de iniciar uma execução cobrada da IA.
    async with open_tenant_session(session_factory, tenant_id) as session:
        automation_allowed = await _conversation_allows_automation(session, conversation_id)
    if not automation_allowed:
        await _sync_context(http, tenant_id, inbound.contact_phone_number, message_content)
        logger.info(
            "Automação pausada antes da chamada à IA | tenant=%s conversation=%s",
            tenant_id,
            conversation_id,
        )
        return

    try:
        result = await send_message_to_agents(
            http,
            tenant_id=tenant_id,
            contact_phone_number=inbound.contact_phone_number,
            message=message_content,
            agents=inbound.agents,
        )
    except Exception as exc:
        # Qualquer falha ao chamar o agents (rede, 5xx, ou um bug — ex: um
        # TypeError de serialização já aconteceu em produção) precisa cair
        # aqui, não só httpx.HTTPError: sem isso, a exceção sobe incapturada,
        # o Arq esgota as tentativas em silêncio, e a conversa fica travada
        # sem resposta e sem alertar o escritório — pior do que qualquer
        # erro transiente de rede.
        if ctx.get("job_try", 1) < MAX_TRIES:
            # Erro transiente (rede, 5xx): reagenda com backoff crescente.
            logger.warning(
                "Falha ao chamar agents, reagendando | tenant=%s conversation=%s error_type=%s",
                tenant_id,
                conversation_id,
                safe_error(exc),
            )
            raise Retry(defer=ctx.get("job_try", 1) * 10)
        # Última tentativa: o agente não conseguiu processar. Diferente do
        # bloqueio por saldo esgotado (que só retorna em silêncio, sem mudar
        # o estado), aqui vira a conversa pra humano de propósito — alerta o
        # escritório, em vez de deixar o job desaparecer em silêncio depois
        # do TTL do resultado.
        logger.error(
            "Esgotadas as tentativas de chamar agents, virando conversa pra human | "
            "tenant=%s conversation=%s error_type=%s",
            tenant_id,
            conversation_id,
            safe_error(exc),
        )
        async with open_tenant_session(session_factory, tenant_id) as session:
            await session.execute(
                update(tables.conversations)
                .where(tables.conversations.c.id == uuid.UUID(conversation_id))
                .values(state="human", automation_status="failed")
            )
            await session.commit()
        return

    if result is None:
        # 202: debounce agrupou em execução já em andamento.
        logger.info(
            "Mensagem agrupada pelo debounce do agents | tenant=%s conversation=%s",
            tenant_id,
            conversation_id,
        )
        return

    # O contato pode enviar informação nova enquanto o LLM ainda responde.
    # A resposta atual não é gravada, cobrada nem enviada. Como o agents
    # service já gravou aquela resposta no próprio checkpoint, reconstituímos
    # a memória somente com o histórico que o cliente realmente recebeu.
    async with open_tenant_session(session_factory, tenant_id) as session:
        response_became_stale = await _has_newer_contact_message(
            session, conversation_id, message_id
        )
        if response_became_stale:
            await _restore_agent_context_before_stale_response(
                session,
                http,
                tenant_id,
                conversation_id,
                message_id,
                inbound.contact_phone_number,
            )
    if response_became_stale:
        logger.info(
            "Resposta descartada porque chegou uma nova mensagem | tenant=%s conversation=%s",
            tenant_id,
            conversation_id,
        )
        return

    responses = result["responses"]
    tokens_used = result.get("tokens_used", 0)
    tokens_input = result.get("tokens_input", 0)
    tokens_output = result.get("tokens_output", 0)
    current_agent_id = result.get("current_agent_id")
    documents = result.get("documents", [])

    async with open_tenant_session(session_factory, tenant_id) as session:
        # Este lock usa a mesma linha bloqueada pelo PATCH de takeover. Assim,
        # uma resposta pronta não pode ser persistida e cobrada depois que o
        # atendimento humano foi confirmado.
        automation_allowed = await _conversation_allows_automation(
            session, conversation_id, lock=True
        )
        if not automation_allowed:
            await session.rollback()
            await _restore_agent_context_before_stale_response(
                session,
                http,
                tenant_id,
                conversation_id,
                message_id,
                inbound.contact_phone_number,
            )
            logger.info(
                "Resposta descartada após takeover humano | tenant=%s conversation=%s",
                tenant_id,
                conversation_id,
            )
            return

        # Tokens ponderados -> créditos fracionados, pela config vigente, mais
        # o custo fixo de cada documento gerado nesta execução (ver
        # agents/tools.py) — a cobrança independe da entrega do documento ter
        # funcionado, o custo da geração já ocorreu.
        config = await get_current_pricing_config(session)
        credits = (
            calcular_creditos(tokens_input, tokens_output, tokens_used, config)
            + len(documents) * DOCUMENT_GENERATION_CREDIT_COST
        )

        first_message_id, outbound_job_ids = await _persist_agent_responses(
            session,
            tenant_id,
            conversation_id,
            responses,
            documents,
            tokens_used,
            credits,
            response_sources=result.get("response_sources", []),
        )

        await session.execute(
            update(tables.conversations)
            .where(tables.conversations.c.id == uuid.UUID(conversation_id))
            .values(automation_status="processing" if outbound_job_ids else "idle")
        )

        if current_agent_id:
            # Permite identificar o agente atual no status do painel; atualiza
            # mesmo se `responses` veio vazio.
            await session.execute(
                update(tables.conversations)
                .where(tables.conversations.c.id == uuid.UUID(conversation_id))
                .values(current_agent_id=uuid.UUID(current_agent_id))
            )
        saldo_tenant_zerou = False
        billed_credits = Decimal(0)
        funding_source = (
            "end_customer_subscription"
            if inbound.end_customer_has_active_subscription
            else "end_customer_credits"
            if customer_funded
            else "tenant"
        )
        if first_message_id is not None:
            # Moeda única: quem custeia o turno é a wallet do cliente final
            # (quando a cobrança está habilitada e havia saldo antes da
            # chamada) OU o estoque do tenant — nunca os dois. Ledger + saldo
            # na mesma transação das mensagens.
            if inbound.end_customer_has_active_subscription:
                # Ilimitado: nenhum dos dois lados é debitado enquanto a
                # assinatura estiver ativa — o tenant absorve o custo do LLM,
                # sem teto automático nesta v1 (ver design doc).
                pass
            elif customer_funded and credits:
                billed_credits = await _debitar_creditos_cliente_final(
                    session,
                    tenant_id,
                    inbound.contact_phone_number,
                    first_message_id,
                    tokens_used,
                    credits,
                    tokens_input,
                    tokens_output,
                    config.id,
                )
            elif credits:
                saldo_tenant_zerou, billed_credits = await _debitar_creditos(
                    session,
                    tenant_id,
                    first_message_id,
                    tokens_used,
                    credits,
                    tokens_input,
                    tokens_output,
                    config.id,
                )

            shortfall_credits = (
                Decimal(0)
                if funding_source == "end_customer_subscription"
                else max(Decimal(0), credits - billed_credits)
            )
            await session.execute(
                pg_insert(tables.usage_records)
                .values(
                    tenant_id=uuid.UUID(tenant_id),
                    conversation_id=uuid.UUID(conversation_id),
                    related_message_id=first_message_id,
                    contact_phone_number=inbound.contact_phone_number,
                    funding_source=funding_source,
                    operational_credits=credits,
                    billed_credits=billed_credits,
                    shortfall_credits=shortfall_credits,
                    document_credits=(Decimal(len(documents)) * DOCUMENT_GENERATION_CREDIT_COST),
                    tokens_input=tokens_input,
                    tokens_output=tokens_output,
                    pricing_config_id=config.id,
                    end_customer_subscription_id=(
                        uuid.UUID(inbound.end_customer_subscription_id)
                        if inbound.end_customer_subscription_id
                        else None
                    ),
                    created_at=datetime.now(UTC),
                )
                .on_conflict_do_nothing(index_elements=[tables.usage_records.c.related_message_id])
            )

        await session.commit()

        # A resposta, o débito e a pendência de entrega já estão no banco.
        # Se Redis estiver indisponível neste ponto, a recuperação periódica
        # encontra o job pendente; não chamamos a IA nem cobramos novamente.
        try:
            await enqueue_outbound_message_jobs(ctx, outbound_job_ids)
        except Exception as exc:
            logger.error(
                "Falha ao enfileirar entrega da resposta; recuperação assumirá | "
                "tenant=%s error_type=%s",
                tenant_id,
                safe_error(exc),
            )

        if saldo_tenant_zerou:
            # Best-effort, depois do commit — nunca deve atrapalhar o
            # processamento da mensagem (ver email_notifications.py).
            tenant_name = (
                await session.execute(
                    select(tables.tenants.c.name).where(tables.tenants.c.id == uuid.UUID(tenant_id))
                )
            ).scalar_one_or_none()
            if tenant_name:
                await send_tenant_out_of_credits_notification(tenant_name, datetime.now(UTC))


async def _load_context(
    session: AsyncSession, tenant_id: str, conversation_id: str, message_id: str
) -> InboundContext | None:
    conversation = (
        await session.execute(
            select(
                tables.conversations.c.state,
                tables.conversations.c.contact_phone_number,
                tables.conversations.c.human_last_seen_at,
                tables.conversations.c.billing_gate_step,
                tables.conversations.c.billing_gate_retries,
                tables.conversations.c.billing_gate_checkout_url,
                tables.conversations.c.end_customer_billing_exempt,
            ).where(tables.conversations.c.id == uuid.UUID(conversation_id))
        )
    ).one_or_none()
    if conversation is None:
        logger.warning("Conversa não encontrada | conversation=%s", conversation_id)
        return None

    message_row = (
        await session.execute(
            select(
                tables.messages.c.content,
                tables.messages.c.media_url,
                tables.messages.c.media_type,
            ).where(tables.messages.c.id == uuid.UUID(message_id))
        )
    ).one_or_none()
    if message_row is None:
        logger.warning("Mensagem não encontrada | message=%s", message_id)
        return None
    content = message_row.content

    number = (
        await session.execute(
            select(
                tables.whatsapp_numbers.c.provider,
                tables.whatsapp_numbers.c.phone_number_id,
                tables.whatsapp_numbers.c.access_token_encrypted,
                tables.whatsapp_numbers.c.zapi_instance_id,
                tables.whatsapp_numbers.c.zapi_instance_token_encrypted,
                tables.whatsapp_numbers.c.zapi_client_token_encrypted,
            ).where(
                tables.whatsapp_numbers.c.tenant_id == uuid.UUID(tenant_id),
                tables.whatsapp_numbers.c.status == "connected",
            )
        )
    ).one_or_none()
    if number is None:
        logger.warning("Tenant sem número WhatsApp conectado | tenant=%s", tenant_id)
        return None

    credit_balance = (
        await session.execute(
            select(tables.tenants.c.credit_balance).where(
                tables.tenants.c.id == uuid.UUID(tenant_id)
            )
        )
    ).scalar_one()

    billing_settings = (
        await session.execute(
            select(
                tables.tenant_billing_settings.c.enabled,
                tables.tenant_billing_settings.c.billing_gate_welcome_text,
            ).where(tables.tenant_billing_settings.c.tenant_id == uuid.UUID(tenant_id))
        )
    ).one_or_none()

    agents = await _load_agents(session, tenant_id)

    end_customer_billing_enabled = bool(billing_settings and billing_settings.enabled)
    end_customer_balance = Decimal(0)
    end_customer_packages: list[dict] = []
    active_subscription = None

    if end_customer_billing_enabled:
        balance = (
            await session.execute(
                select(tables.end_customer_balances.c.credit_balance).where(
                    tables.end_customer_balances.c.tenant_id == uuid.UUID(tenant_id),
                    tables.end_customer_balances.c.contact_phone_number
                    == conversation.contact_phone_number,
                )
            )
        ).scalar_one_or_none()
        end_customer_balance = balance if balance is not None else Decimal(0)

        packages_result = await session.execute(
            select(
                tables.end_customer_credit_packages.c.id,
                tables.end_customer_credit_packages.c.name,
                tables.end_customer_credit_packages.c.price_brl,
                tables.end_customer_credit_packages.c.kind,
                tables.end_customer_credit_packages.c.credits_granted,
            ).where(
                tables.end_customer_credit_packages.c.tenant_id == uuid.UUID(tenant_id),
                tables.end_customer_credit_packages.c.active.is_(True),
            )
        )
        end_customer_packages = [
            {
                "id": str(row.id),
                "name": row.name,
                "price_brl": str(row.price_brl),
                "kind": row.kind,
                "credits_granted": row.credits_granted,
            }
            for row in packages_result
        ]

        active_subscription = (
            await session.execute(
                select(tables.end_customer_subscriptions.c.id).where(
                    tables.end_customer_subscriptions.c.tenant_id == uuid.UUID(tenant_id),
                    tables.end_customer_subscriptions.c.contact_phone_number
                    == conversation.contact_phone_number,
                    tables.end_customer_subscriptions.c.status == "active",
                    or_(
                        tables.end_customer_subscriptions.c.current_period_end.is_(None),
                        tables.end_customer_subscriptions.c.current_period_end >= func.now(),
                    ),
                )
            )
        ).scalar_one_or_none()

    return InboundContext(
        conversation_state=conversation.state,
        contact_phone_number=conversation.contact_phone_number,
        message_content=content,
        whatsapp_provider=number.provider,
        phone_number_id=number.phone_number_id,
        access_token_encrypted=number.access_token_encrypted,
        zapi_instance_id=number.zapi_instance_id,
        zapi_instance_token_encrypted=number.zapi_instance_token_encrypted,
        zapi_client_token_encrypted=number.zapi_client_token_encrypted,
        credit_balance=credit_balance,
        end_customer_billing_enabled=end_customer_billing_enabled,
        end_customer_balance=end_customer_balance,
        end_customer_packages=end_customer_packages,
        agents=agents,
        human_last_seen_at=conversation.human_last_seen_at,
        billing_gate_step=conversation.billing_gate_step,
        billing_gate_retries=conversation.billing_gate_retries,
        billing_gate_checkout_url=conversation.billing_gate_checkout_url,
        billing_gate_welcome_text=(
            billing_settings.billing_gate_welcome_text if billing_settings is not None else None
        ),
        end_customer_billing_exempt=conversation.end_customer_billing_exempt,
        end_customer_has_active_subscription=active_subscription is not None,
        end_customer_subscription_id=(str(active_subscription) if active_subscription else None),
        media_url=message_row.media_url,
        media_type=message_row.media_type,
    )


async def _persist_agent_responses(
    session: AsyncSession,
    tenant_id: str,
    conversation_id: str,
    responses: list[str],
    documents: list[dict] | None = None,
    tokens_used: int = 0,
    credits: Decimal | int = 0,
    delivery_failures: set[int] | None = None,
    response_sources: list[dict | None] | None = None,
) -> tuple[uuid.UUID | None, list[uuid.UUID]]:
    """Insere as respostas de texto do agente + uma mensagem por documento
    gerado (fazer_contrato/fazer_multa/etc, ver agents/tools.py) e retorna o
    id da primeira mensagem inserida (texto ou documento, o que vier
    primeiro) e os ids das entregas pendentes.

    O consumo da execução inteira (tokens/créditos, já incluindo o custo
    fixo de eventuais documentos) fica registrado só nessa primeira mensagem
    — é a ela que o lançamento do ledger se vincula. A entrega ocorre depois
    do commit por uma caixa de saída independente; portanto toda nova
    mensagem do agente começa como `pending`, inclusive documentos.
    """
    del delivery_failures  # Compatibilidade temporária com chamadas antigas.
    documents = documents or []
    now = datetime.now(UTC)
    first_message_id: uuid.UUID | None = None
    outbound_job_ids: list[uuid.UUID] = []
    index = 0

    for response in responses:
        values: dict = {
            "response_sources": (
                response_sources[index]
                if response_sources and index < len(response_sources)
                else None
            ),
            "conversation_id": uuid.UUID(conversation_id),
            "tenant_id": uuid.UUID(tenant_id),
            "sender_type": "agent",
            "content": response,
            "delivery_status": "pending",
            # Mesma execução pode gerar várias respostas/documentos (ex:
            # despedida da secretária + saudação do especialista) — sem um
            # offset por índice, todas cravam o mesmo instante e o ORDER BY
            # created_at não tem como desempatar a ordem real de geração.
            "created_at": now + timedelta(microseconds=index),
        }
        if index == 0:
            values["tokens_used"] = tokens_used or None
            values["credits_consumed"] = credits or None
        result = await session.execute(
            insert(tables.messages).values(**values).returning(tables.messages.c.id)
        )
        message_id = result.scalar_one()
        if index == 0:
            first_message_id = message_id
        job_id = uuid.uuid4()
        await session.execute(
            insert(tables.outbound_message_jobs).values(
                id=job_id,
                tenant_id=uuid.UUID(tenant_id),
                conversation_id=uuid.UUID(conversation_id),
                message_id=message_id,
                status="pending",
                available_at=now,
                created_at=now,
            )
        )
        outbound_job_ids.append(job_id)
        index += 1

    for doc in documents:
        values = {
            "conversation_id": uuid.UUID(conversation_id),
            "tenant_id": uuid.UUID(tenant_id),
            "sender_type": "agent",
            "content": f"📄 {doc['filename']}",
            "delivery_status": "pending",
            "media_url": doc["link"],
            "media_type": "application/pdf",
            "created_at": now + timedelta(microseconds=index),
        }
        if index == 0:
            values["tokens_used"] = tokens_used or None
            values["credits_consumed"] = credits or None
        result = await session.execute(
            insert(tables.messages).values(**values).returning(tables.messages.c.id)
        )
        message_id = result.scalar_one()
        if index == 0:
            first_message_id = message_id
        job_id = uuid.uuid4()
        await session.execute(
            insert(tables.outbound_message_jobs).values(
                id=job_id,
                tenant_id=uuid.UUID(tenant_id),
                conversation_id=uuid.UUID(conversation_id),
                message_id=message_id,
                status="pending",
                available_at=now,
                created_at=now,
            )
        )
        outbound_job_ids.append(job_id)
        index += 1

    if responses or documents:
        await session.execute(
            update(tables.conversations)
            .where(tables.conversations.c.id == uuid.UUID(conversation_id))
            .values(last_message_at=now)
        )
    return first_message_id, outbound_job_ids


async def _debitar_creditos(
    session: AsyncSession,
    tenant_id: str,
    message_id: uuid.UUID,
    tokens_used: int,
    credits: Decimal,
    tokens_input: int = 0,
    tokens_output: int = 0,
    pricing_config_id: uuid.UUID | None = None,
) -> tuple[bool, Decimal]:
    """Lança o consumo no ledger e atualiza o cache de saldo do tenant.

    O SELECT ... FOR UPDATE serializa débitos concorrentes do mesmo tenant
    (várias mensagens simultâneas) — o update relativo em seguida nunca perde
    escrita nem lê saldo obsoleto.

    Devolve se este débito zerou o saldo e quanto foi efetivamente cobrado.
    O valor cobrado nunca ultrapassa o saldo travado, portanto concorrência
    não produz saldo negativo.

    True representa a transição de positivo para zero — usado pra notificar
    a Advoxs uma única vez por
    "episódio" de saldo esgotado, nunca a cada mensagem enquanto já
    está zerado (ver send_tenant_out_of_credits_notification)."""
    saldo_antes = (
        await session.execute(
            select(tables.tenants.c.credit_balance)
            .where(tables.tenants.c.id == uuid.UUID(tenant_id))
            .with_for_update()
        )
    ).scalar_one()
    saldo_disponivel = max(Decimal(0), saldo_antes)
    credits_cobrados = min(saldo_disponivel, credits)
    if credits_cobrados:
        await session.execute(
            insert(tables.credit_transactions).values(
                tenant_id=uuid.UUID(tenant_id),
                type="consumption",
                amount_credits=-credits_cobrados,
                related_message_id=message_id,
                tokens_input=tokens_input or None,
                tokens_output=tokens_output or None,
                pricing_config_id=pricing_config_id,
                description="Consumo do agente",
                created_at=datetime.now(UTC),
            )
        )
        await session.execute(
            update(tables.tenants)
            .where(tables.tenants.c.id == uuid.UUID(tenant_id))
            .values(credit_balance=tables.tenants.c.credit_balance - credits_cobrados)
        )
    return saldo_antes > 0 and credits_cobrados == saldo_disponivel, credits_cobrados


async def _debitar_creditos_cliente_final(
    session: AsyncSession,
    tenant_id: str,
    contact_phone_number: str,
    message_id: uuid.UUID,
    tokens_used: int,
    credits: Decimal,
    tokens_input: int = 0,
    tokens_output: int = 0,
    pricing_config_id: uuid.UUID | None = None,
) -> Decimal:
    """Débito do saldo do CLIENTE FINAL com o tenant — moeda única: quando o
    turno é custeado pelo cliente, SÓ esta wallet é debitada (o estoque do
    tenant já foi debitado na revenda). FOR UPDATE serializa débitos
    concorrentes do mesmo contato."""
    saldo_antes = (
        await session.execute(
            select(tables.end_customer_balances.c.credit_balance)
            .where(
                tables.end_customer_balances.c.tenant_id == uuid.UUID(tenant_id),
                tables.end_customer_balances.c.contact_phone_number == contact_phone_number,
            )
            .with_for_update()
        )
    ).scalar_one()
    saldo_disponivel = max(Decimal(0), saldo_antes)
    credits_cobrados = min(saldo_disponivel, credits)
    if not credits_cobrados:
        return Decimal(0)
    await session.execute(
        insert(tables.end_customer_credit_transactions).values(
            tenant_id=uuid.UUID(tenant_id),
            contact_phone_number=contact_phone_number,
            type="consumption",
            amount_credits=-credits_cobrados,
            related_message_id=message_id,
            tokens_input=tokens_input or None,
            tokens_output=tokens_output or None,
            pricing_config_id=pricing_config_id,
            description="Consumo do agente",
            created_at=datetime.now(UTC),
        )
    )
    await session.execute(
        update(tables.end_customer_balances)
        .where(
            tables.end_customer_balances.c.tenant_id == uuid.UUID(tenant_id),
            tables.end_customer_balances.c.contact_phone_number == contact_phone_number,
        )
        .values(credit_balance=tables.end_customer_balances.c.credit_balance - credits_cobrados)
    )
    return credits_cobrados
