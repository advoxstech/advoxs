"""Finalização real da exclusão em PostgreSQL, inclusive mensagem concorrente."""

import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.tasks.conversation_cleanup import _finalize_cleanup, recover_conversation_cleanup_jobs


@pytest.fixture
async def database():
    url = os.getenv("CONVERSATION_CLEANUP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires an explicitly configured disposable PostgreSQL database")
    schema = f"worker_cleanup_test_{uuid.uuid4().hex}"
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(
                text(
                    "CREATE TABLE conversations ("
                    "id UUID PRIMARY KEY, tenant_id UUID NOT NULL, contact_phone_number TEXT, "
                    "state TEXT, automation_status TEXT, is_test BOOLEAN, "
                    "last_message_at TIMESTAMPTZ, summary TEXT, "
                    "summary_generated_at TIMESTAMPTZ, human_last_seen_at TIMESTAMPTZ, "
                    "billing_gate_step TEXT, billing_gate_retries INTEGER, "
                    "billing_gate_checkout_url TEXT, end_customer_billing_exempt BOOLEAN, "
                    "current_agent_id UUID, deletion_requested_at TIMESTAMPTZ)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE messages (id UUID PRIMARY KEY, conversation_id UUID, "
                    "tenant_id UUID, sender_type TEXT, content TEXT, delivery_status TEXT, "
                    "response_sources JSONB, media_url TEXT, media_type TEXT, tokens_used INTEGER, "
                    "credits_consumed NUMERIC, created_at TIMESTAMPTZ DEFAULT now())"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE conversation_cleanup_jobs ("
                    "id UUID PRIMARY KEY, tenant_id UUID, conversation_id UUID, message_ids JSONB, "
                    "attachment_document_ids JSONB, status TEXT, attempts INTEGER, "
                    "available_at TIMESTAMPTZ, last_enqueued_at TIMESTAMPTZ, "
                    "locked_at TIMESTAMPTZ, last_error TEXT, created_at TIMESTAMPTZ)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE conversation_processing_locks (conversation_id UUID PRIMARY KEY, "
                    "tenant_id UUID, job_id UUID, locked_at TIMESTAMPTZ)"
                )
            )
            for table_name in ("inbound_message_jobs", "outbound_message_jobs"):
                await conn.execute(
                    text(
                        f"CREATE TABLE {table_name} (id UUID PRIMARY KEY, tenant_id UUID, "
                        "conversation_id UUID, message_id UUID, status TEXT, attempts INTEGER, "
                        "available_at TIMESTAMPTZ, last_enqueued_at TIMESTAMPTZ, "
                        "locked_at TIMESTAMPTZ, completed_at TIMESTAMPTZ, "
                        "last_error TEXT, created_at TIMESTAMPTZ)"
                    )
                )
            await conn.execute(
                text(
                    "CREATE TABLE credit_transactions (id UUID PRIMARY KEY, tenant_id UUID, "
                    "type TEXT, amount_credits NUMERIC, tokens_input INTEGER, "
                    "tokens_output INTEGER, "
                    "pricing_config_id UUID, related_message_id UUID, description TEXT, "
                    "created_at TIMESTAMPTZ)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE end_customer_credit_transactions ("
                    "id UUID PRIMARY KEY, tenant_id UUID, contact_phone_number TEXT, type TEXT, "
                    "amount_credits NUMERIC, tokens_input INTEGER, tokens_output INTEGER, "
                    "pricing_config_id UUID, related_message_id UUID, description TEXT, "
                    "created_at TIMESTAMPTZ)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE usage_records (id UUID PRIMARY KEY, tenant_id UUID, "
                    "conversation_id UUID, related_message_id UUID, contact_phone_number TEXT, "
                    "funding_source TEXT, operational_credits NUMERIC, billed_credits NUMERIC, "
                    "shortfall_credits NUMERIC, document_credits NUMERIC, tokens_input INTEGER, "
                    "tokens_output INTEGER, pricing_config_id UUID, "
                    "end_customer_subscription_id UUID, created_at TIMESTAMPTZ)"
                )
            )
        yield engine
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


async def test_preserva_mensagem_nova_e_reabre_conversa_sem_contexto_antigo(database) -> None:
    tenant_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    job_id = uuid.uuid4()
    old_message_id, new_message_id = uuid.uuid4(), uuid.uuid4()
    inbound_old, inbound_new = uuid.uuid4(), uuid.uuid4()
    usage_id = uuid.uuid4()
    async with database.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO conversations VALUES "
                "(:id, :tenant, '5511', 'agent', 'processing', false, now(), 'resumo', now(), "
                "now(), 'gate', 2, 'url', false, NULL, now())"
            ),
            {"id": conversation_id, "tenant": tenant_id},
        )
        await conn.execute(
            text(
                "INSERT INTO messages (id, conversation_id, tenant_id, sender_type, content) "
                "VALUES (:old, :conversation, :tenant, 'contact', 'antiga'), "
                "(:new, :conversation, :tenant, 'contact', 'nova')"
            ),
            {
                "old": old_message_id,
                "new": new_message_id,
                "conversation": conversation_id,
                "tenant": tenant_id,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO inbound_message_jobs "
                "(id, tenant_id, conversation_id, message_id, status) VALUES "
                "(:old_job, :tenant, :conversation, :old, 'failed'), "
                "(:new_job, :tenant, :conversation, :new, 'pending')"
            ),
            {
                "old_job": inbound_old,
                "new_job": inbound_new,
                "tenant": tenant_id,
                "conversation": conversation_id,
                "old": old_message_id,
                "new": new_message_id,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO usage_records "
                "(id, tenant_id, conversation_id, related_message_id) "
                "VALUES (:id, :tenant, :conversation, :message)"
            ),
            {
                "id": usage_id,
                "tenant": tenant_id,
                "conversation": conversation_id,
                "message": old_message_id,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO conversation_cleanup_jobs "
                "(id, tenant_id, conversation_id, message_ids, attachment_document_ids, status) "
                "VALUES (:id, :tenant, :conversation, :messages, '[]'::jsonb, 'processing')"
            ),
            {
                "id": job_id,
                "tenant": tenant_id,
                "conversation": conversation_id,
                "messages": f'["{old_message_id}"]',
            },
        )

    ctx = {"system_session_factory": async_sessionmaker(database, expire_on_commit=False)}
    cleanup = SimpleNamespace(conversation_id=conversation_id, message_ids=[str(old_message_id)])
    resumed = await _finalize_cleanup(ctx, str(tenant_id), str(job_id), cleanup)

    assert resumed == str(conversation_id)
    async with database.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM messages")) == 1
        assert (
            await conn.scalar(
                text("SELECT count(*) FROM messages WHERE id=:id"), {"id": new_message_id}
            )
            == 1
        )
        row = (
            await conn.execute(
                text(
                    "SELECT deletion_requested_at, state, automation_status, summary "
                    "FROM conversations WHERE id=:id"
                ),
                {"id": conversation_id},
            )
        ).one()
        assert tuple(row) == (None, "agent", "processing", None)
        assert (
            await conn.scalar(
                text("SELECT related_message_id FROM usage_records WHERE id=:id"), {"id": usage_id}
            )
            is None
        )
        assert await conn.scalar(text("SELECT count(*) FROM conversation_cleanup_jobs")) == 0


async def test_sem_mensagem_nova_remove_conversa_e_preserva_auditoria(database) -> None:
    tenant_id, conversation_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    old_message_id, usage_id = uuid.uuid4(), uuid.uuid4()
    async with database.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO conversations "
                "(id, tenant_id, contact_phone_number, state, automation_status, is_test, "
                "end_customer_billing_exempt, deletion_requested_at) "
                "VALUES (:id, :tenant, '5511', 'agent', 'idle', false, false, now())"
            ),
            {"id": conversation_id, "tenant": tenant_id},
        )
        await conn.execute(
            text(
                "INSERT INTO messages (id, conversation_id, tenant_id, sender_type, content) "
                "VALUES (:id, :conversation, :tenant, 'contact', 'antiga')"
            ),
            {"id": old_message_id, "conversation": conversation_id, "tenant": tenant_id},
        )
        await conn.execute(
            text(
                "INSERT INTO usage_records "
                "(id, tenant_id, conversation_id, related_message_id) "
                "VALUES (:id, :tenant, :conversation, :message)"
            ),
            {
                "id": usage_id,
                "tenant": tenant_id,
                "conversation": conversation_id,
                "message": old_message_id,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO conversation_cleanup_jobs "
                "(id, tenant_id, conversation_id, message_ids, attachment_document_ids, status) "
                "VALUES (:id, :tenant, :conversation, :messages, '[]'::jsonb, 'processing')"
            ),
            {
                "id": job_id,
                "tenant": tenant_id,
                "conversation": conversation_id,
                "messages": f'["{old_message_id}"]',
            },
        )

    ctx = {"system_session_factory": async_sessionmaker(database, expire_on_commit=False)}
    cleanup = SimpleNamespace(conversation_id=conversation_id, message_ids=[str(old_message_id)])
    resumed = await _finalize_cleanup(ctx, str(tenant_id), str(job_id), cleanup)

    assert resumed is None
    async with database.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM conversations")) == 0
        usage = (
            await conn.execute(
                text("SELECT conversation_id, related_message_id FROM usage_records WHERE id=:id"),
                {"id": usage_id},
            )
        ).one()
        assert tuple(usage) == (None, None)


async def test_recupera_job_abandonado_e_recoloca_na_fila(database) -> None:
    tenant_id, conversation_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with database.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO conversations "
                "(id, tenant_id, contact_phone_number, state, automation_status, is_test, "
                "end_customer_billing_exempt, deletion_requested_at) "
                "VALUES (:id, :tenant, '5511', 'agent', 'idle', false, false, now())"
            ),
            {"id": conversation_id, "tenant": tenant_id},
        )
        await conn.execute(
            text(
                "INSERT INTO conversation_cleanup_jobs "
                "(id, tenant_id, conversation_id, message_ids, attachment_document_ids, status, "
                "available_at, locked_at) "
                "VALUES (:id, :tenant, :conversation, '[]'::jsonb, '[]'::jsonb, 'processing', "
                "now() - interval '1 hour', now() - interval '1 hour')"
            ),
            {"id": job_id, "tenant": tenant_id, "conversation": conversation_id},
        )

    redis = SimpleNamespace(enqueue_job=AsyncMock())
    ctx = {
        "system_session_factory": async_sessionmaker(database, expire_on_commit=False),
        "redis": redis,
    }
    await recover_conversation_cleanup_jobs(ctx)

    redis.enqueue_job.assert_awaited_once_with(
        "process_conversation_cleanup",
        tenant_id=str(tenant_id),
        job_id=str(job_id),
    )
    async with database.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT status, locked_at, last_enqueued_at "
                    "FROM conversation_cleanup_jobs WHERE id=:id"
                ),
                {"id": job_id},
            )
        ).one()
        assert row.status == "pending"
        assert row.locked_at is None
        assert row.last_enqueued_at is not None
