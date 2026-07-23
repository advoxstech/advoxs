import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from arq.worker import Retry
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import tables
from app.billing_gate import _escalate_to_human, handle_billing_gate, maybe_enter_gate
from app.clients.agents import send_message_to_agents, sync_context_to_agents
from app.config import settings
from app.crypto import decrypt_access_token
from app.db import open_tenant_session
from app.pricing import calcular_creditos, get_current_pricing_config
from app.tasks.inbound_context import InboundContext

logger = logging.getLogger(__name__)

# Na última tentativa, vira a conversa pra humano em vez de reagendar (o
# default de max_tries do Arq também é 5 — manter em sincronia, mesmo padrão
# já usado em apps/worker/app/tasks/knowledge_base.py).
MAX_TRIES = 5


def _takeover_expirado(human_last_seen_at: datetime | None) -> bool:
    """Sem heartbeat recente do painel, a presença expirou (NULL = expirado)."""
    if human_last_seen_at is None:
        return True
    idade = (datetime.now(UTC) - human_last_seen_at).total_seconds()
    return idade > settings.human_takeover_timeout_seconds


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
        ).where(
            tables.agent_knowledge_base_files.c.agent_id.in_([row.id for row in agents_rows])
        )
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
            "Falha ao sincronizar contexto do takeover | tenant=%s erro=%s", tenant_id, exc
        )


async def process_inbound_message(
    ctx: dict, tenant_id: str, conversation_id: str, message_id: str
) -> None:
    """Verifica o estado da conversa (agent|human) e repassa para o agents service.

    Enfileirado pelo `api` depois de persistir a mensagem do contato. As respostas
    do agente voltam na chamada e são persistidas em `messages` (sender_type=agent).
    """
    session_factory = ctx["session_factory"]
    http: httpx.AsyncClient = ctx["http"]

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
                "tenant=%s conversation=%s erro=%s",
                tenant_id,
                conversation_id,
                exc,
            )
            async with open_tenant_session(session_factory, tenant_id) as session:
                await _escalate_to_human(session, conversation_id)
        return

    if inbound.conversation_state != "agent":
        if not _takeover_expirado(inbound.human_last_seen_at):
            # Takeover ativo: a mensagem aparece no painel e entra no
            # checkpoint do agente (memória do takeover) — mas a IA não responde.
            logger.info(
                "Conversa em modo humano, agente não acionado | tenant=%s conversation=%s",
                tenant_id,
                conversation_id,
            )
            await _sync_context(
                http, tenant_id, inbound.contact_phone_number, inbound.message_content
            )
            return
        # Presença do atendente expirou: a IA reassume nesta mesma execução.
        logger.info(
            "Takeover expirado, IA reassume | tenant=%s conversation=%s",
            tenant_id,
            conversation_id,
        )
        async with open_tenant_session(session_factory, tenant_id) as session:
            await session.execute(
                update(tables.conversations)
                .where(tables.conversations.c.id == uuid.UUID(conversation_id))
                .values(state="agent", human_last_seen_at=None)
            )
            await session.commit()

    # Moeda única: turno custeado pelo cliente final (cobrança habilitada e
    # saldo positivo) roda mesmo com o estoque do tenant zerado — esse crédito
    # já saiu do estoque na revenda. Silêncio total só quando o turno seria
    # custeado pelo tenant E o saldo dele esgotou.
    customer_funded = (
        not inbound.end_customer_billing_exempt
        and inbound.end_customer_billing_enabled
        and inbound.end_customer_balance > 0
    )
    if inbound.credit_balance <= 0 and not customer_funded:
        logger.info(
            "Saldo esgotado, agente não acionado | tenant=%s conversation=%s saldo=%s",
            tenant_id,
            conversation_id,
            inbound.credit_balance,
        )
        await _sync_context(http, tenant_id, inbound.contact_phone_number, inbound.message_content)
        return

    access_token = decrypt_access_token(inbound.access_token_encrypted)

    extra_kwargs: dict = {}
    if inbound.end_customer_billing_enabled and not inbound.end_customer_billing_exempt:
        extra_kwargs["end_customer_billing"] = {
            "enabled": True,
            "balance": inbound.end_customer_balance,
            "packages": inbound.end_customer_packages,
        }

    try:
        result = await send_message_to_agents(
            http,
            tenant_id=tenant_id,
            contact_phone_number=inbound.contact_phone_number,
            message=inbound.message_content,
            phone_number_id=inbound.phone_number_id,
            access_token=access_token,
            agents=inbound.agents,
            **extra_kwargs,
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
                "Falha ao chamar agents, reagendando | tenant=%s conversation=%s erro=%s",
                tenant_id,
                conversation_id,
                exc,
            )
            raise Retry(defer=ctx.get("job_try", 1) * 10)
        # Última tentativa: o agente não conseguiu processar. Diferente do
        # bloqueio por saldo esgotado (que só retorna em silêncio, sem mudar
        # o estado), aqui vira a conversa pra humano de propósito — alerta o
        # escritório, em vez de deixar o job desaparecer em silêncio depois
        # do TTL do resultado.
        logger.error(
            "Esgotadas as tentativas de chamar agents, virando conversa pra human | "
            "tenant=%s conversation=%s erro=%s",
            tenant_id,
            conversation_id,
            exc,
        )
        async with open_tenant_session(session_factory, tenant_id) as session:
            await session.execute(
                update(tables.conversations)
                .where(tables.conversations.c.id == uuid.UUID(conversation_id))
                .values(state="human")
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

    responses = result["responses"]
    tokens_used = result.get("tokens_used", 0)
    tokens_input = result.get("tokens_input", 0)
    tokens_output = result.get("tokens_output", 0)
    delivery_failures = set(result.get("delivery_failures", []))

    async with open_tenant_session(session_factory, tenant_id) as session:
        # Tokens ponderados -> créditos fracionados, pela config vigente.
        config = await get_current_pricing_config(session)
        credits = calcular_creditos(tokens_input, tokens_output, tokens_used, config)

        first_message_id = await _persist_agent_responses(
            session, tenant_id, conversation_id, responses, tokens_used, credits, delivery_failures
        )
        if credits and first_message_id is not None:
            # Moeda única: quem custeia o turno é a wallet do cliente final
            # (quando a cobrança está habilitada e havia saldo antes da
            # chamada) OU o estoque do tenant — nunca os dois. Ledger + saldo
            # na mesma transação das mensagens.
            if customer_funded:
                await _debitar_creditos_cliente_final(
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
            else:
                await _debitar_creditos(
                    session,
                    tenant_id,
                    first_message_id,
                    tokens_used,
                    credits,
                    tokens_input,
                    tokens_output,
                    config.id,
                )

        await session.commit()


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

    content = (
        await session.execute(
            select(tables.messages.c.content).where(tables.messages.c.id == uuid.UUID(message_id))
        )
    ).scalar_one_or_none()
    if content is None:
        logger.warning("Mensagem não encontrada | message=%s", message_id)
        return None

    number = (
        await session.execute(
            select(
                tables.whatsapp_numbers.c.phone_number_id,
                tables.whatsapp_numbers.c.access_token_encrypted,
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
                tables.tenant_billing_settings.c.insufficient_balance_policy,
                tables.tenant_billing_settings.c.billing_gate_welcome_text,
            ).where(tables.tenant_billing_settings.c.tenant_id == uuid.UUID(tenant_id))
        )
    ).one_or_none()

    agents = await _load_agents(session, tenant_id)

    end_customer_billing_enabled = bool(billing_settings and billing_settings.enabled)
    end_customer_balance = Decimal(0)
    end_customer_packages: list[dict] = []

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
                "credits_granted": row.credits_granted,
            }
            for row in packages_result
        ]

    return InboundContext(
        conversation_state=conversation.state,
        contact_phone_number=conversation.contact_phone_number,
        message_content=content,
        phone_number_id=number.phone_number_id,
        access_token_encrypted=number.access_token_encrypted,
        credit_balance=credit_balance,
        end_customer_billing_enabled=end_customer_billing_enabled,
        end_customer_balance=end_customer_balance,
        end_customer_packages=end_customer_packages,
        agents=agents,
        human_last_seen_at=conversation.human_last_seen_at,
        billing_gate_step=conversation.billing_gate_step,
        billing_gate_retries=conversation.billing_gate_retries,
        billing_gate_checkout_url=conversation.billing_gate_checkout_url,
        insufficient_balance_policy=(
            billing_settings.insufficient_balance_policy
            if billing_settings is not None
            else "block_with_message"
        ),
        billing_gate_welcome_text=(
            billing_settings.billing_gate_welcome_text if billing_settings is not None else None
        ),
        end_customer_billing_exempt=conversation.end_customer_billing_exempt,
    )


async def _persist_agent_responses(
    session: AsyncSession,
    tenant_id: str,
    conversation_id: str,
    responses: list[str],
    tokens_used: int = 0,
    credits: Decimal | int = 0,
    delivery_failures: set[int] | None = None,
) -> uuid.UUID | None:
    """Insere as respostas do agente e retorna o id da primeira.

    O consumo da execução inteira (tokens/créditos) fica registrado na
    primeira mensagem — é a ela que o lançamento do ledger se vincula.
    `delivery_failures` marca, por índice, quais respostas falharam ao
    entregar ao WhatsApp — a cobrança acontece independente disso, porque o
    custo do LLM já ocorreu.
    """
    delivery_failures = delivery_failures or set()
    now = datetime.now(UTC)
    first_message_id: uuid.UUID | None = None
    for i, response in enumerate(responses):
        values: dict = {
            "conversation_id": uuid.UUID(conversation_id),
            "tenant_id": uuid.UUID(tenant_id),
            "sender_type": "agent",
            "content": response,
            "delivery_status": "failed" if i in delivery_failures else "sent",
            # Mesma execução pode gerar várias respostas (ex: despedida da
            # secretária + saudação do especialista) — sem um offset por
            # índice, todas cravam o mesmo instante e o ORDER BY created_at
            # não tem como desempatar a ordem real de geração.
            "created_at": now + timedelta(microseconds=i),
        }
        if i == 0:
            values["tokens_used"] = tokens_used or None
            values["credits_consumed"] = credits or None
        result = await session.execute(
            insert(tables.messages).values(**values).returning(tables.messages.c.id)
        )
        if i == 0:
            first_message_id = result.scalar_one()
    if responses:
        await session.execute(
            update(tables.conversations)
            .where(tables.conversations.c.id == uuid.UUID(conversation_id))
            .values(last_message_at=now)
        )
    return first_message_id


async def _debitar_creditos(
    session: AsyncSession,
    tenant_id: str,
    message_id: uuid.UUID,
    tokens_used: int,
    credits: Decimal,
    tokens_input: int = 0,
    tokens_output: int = 0,
    pricing_config_id: uuid.UUID | None = None,
) -> None:
    """Lança o consumo no ledger e atualiza o cache de saldo do tenant.

    O SELECT ... FOR UPDATE serializa débitos concorrentes do mesmo tenant
    (várias mensagens simultâneas) — o update relativo em seguida nunca perde
    escrita nem lê saldo obsoleto."""
    await session.execute(
        select(tables.tenants.c.credit_balance)
        .where(tables.tenants.c.id == uuid.UUID(tenant_id))
        .with_for_update()
    )
    await session.execute(
        insert(tables.credit_transactions).values(
            tenant_id=uuid.UUID(tenant_id),
            type="consumption",
            amount_credits=-credits,
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
        .values(credit_balance=tables.tenants.c.credit_balance - credits)
    )


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
) -> None:
    """Débito do saldo do CLIENTE FINAL com o tenant — moeda única: quando o
    turno é custeado pelo cliente, SÓ esta wallet é debitada (o estoque do
    tenant já foi debitado na revenda). FOR UPDATE serializa débitos
    concorrentes do mesmo contato."""
    await session.execute(
        select(tables.end_customer_balances.c.credit_balance)
        .where(
            tables.end_customer_balances.c.tenant_id == uuid.UUID(tenant_id),
            tables.end_customer_balances.c.contact_phone_number == contact_phone_number,
        )
        .with_for_update()
    )
    await session.execute(
        insert(tables.end_customer_credit_transactions).values(
            tenant_id=uuid.UUID(tenant_id),
            contact_phone_number=contact_phone_number,
            type="consumption",
            amount_credits=-credits,
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
        .values(credit_balance=tables.end_customer_balances.c.credit_balance - credits)
    )
