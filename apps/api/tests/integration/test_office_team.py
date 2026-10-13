"""Real PostgreSQL: migration, notifications, invitations and exclusive ownership."""

import asyncio
import importlib.util
import os
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.deps import TenantContext
from app.api.v1 import conversations as routes
from app.api.v1 import team
from app.core.config import settings
from app.core.crypto import decrypt_access_token
from app.models import Base, Conversation, User
from app.schemas.conversations import ConversationStateUpdate
from app.services.team_access import conversation_scope

path = Path(__file__).parents[2] / "alembic/versions/0045_office_team.py"
spec = importlib.util.spec_from_file_location("office_team_migration", path)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def migrate(conn, direction="upgrade"):
    with Operations.context(
        MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
    ):
        getattr(migration, direction)()


@pytest.fixture
async def db():
    url = os.getenv("AGENT_VERSIONS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires explicitly configured disposable PostgreSQL")
    schema = "team_test_" + uuid.uuid4().hex
    role = "team_reader_" + uuid.uuid4().hex
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.run_sync(Base.metadata.create_all)
            # Reconstruct pre-0045 columns, then exercise the real migration.
            await conn.execute(text("DROP TABLE IF EXISTS attendance_events"))
            for table, columns in {
                "users": ["active"],
                "conversations": ["assigned_user_id", "assigned_user_name", "urgency_episode_id"],
                "messages": ["sender_user_id", "sender_user_name"],
            }.items():
                for column in columns:
                    await conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
            await conn.run_sync(migrate)
            await conn.execute(text(f'CREATE ROLE "{role}"'))
            await conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"'))
            await conn.execute(
                text(
                    f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA "{schema}" '
                    f'TO "{role}"'
                )
            )
        yield engine, async_sessionmaker(engine, expire_on_commit=False), role
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        await engine.dispose()


async def seed(engine):
    ids = {k: uuid.uuid4() for k in ("t", "other", "admin", "l1", "l2", "a", "c", "test")}
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants(id,name,email_contato) "
                "VALUES (:t,'Office','office@example.com'),"
                "(:other,'Other','other@example.com')"
            ),
            ids,
        )
        for key, role in (("admin", "admin"), ("l1", "lawyer"), ("l2", "lawyer")):
            await conn.execute(
                text(
                    "INSERT INTO users(id,tenant_id,name,email,password_hash,role)"
                    " VALUES (:id,:t,:name,:email,'hash',:role)"
                ),
                dict(id=ids[key], t=ids["t"], name=key, email=f"{key}@example.com", role=role),
            )
        await conn.execute(
            text(
                "INSERT INTO agents(id,tenant_id,name,instructions,is_entry_point)"
                " VALUES (:a,:t,'Consumer','Instructions',true)"
            ),
            ids,
        )
        await conn.execute(
            text(
                "INSERT INTO conversations(id,tenant_id,contact_phone_number,"
                "current_agent_id,is_test)"
                " VALUES (:c,:t,'5511',:a,false),(:test,:t,'5522',:a,true)"
            ),
            ids,
        )
        await conn.execute(text("INSERT INTO agent_lawyers VALUES (:t,:a,:l1),(:t,:a,:l2)"), ids)
    return ids


async def test_urgency_dedup_fallback_test_exclusion_and_rls(db):
    engine, _, role = db
    ids = await seed(engine)
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE conversations SET urgent_since=now()"))
        assert await conn.scalar(text("SELECT count(*) FROM team_emails")) == 2
        assert (
            await conn.scalar(text("SELECT state FROM conversations WHERE id=:c"), ids) == "agent"
        )
        await conn.execute(text("UPDATE conversations SET urgent_since=urgent_since"))
        assert await conn.scalar(text("SELECT count(*) FROM team_emails")) == 2
        await conn.execute(text("UPDATE users SET active=false WHERE role='lawyer'"))
        await team.refresh_urgencies(conn, ids["t"])
        assert await conn.scalar(text("SELECT count(*) FROM team_emails")) == 3
        await conn.execute(text(f'SET LOCAL ROLE "{role}"'))
        await conn.execute(
            text("SELECT set_config('app.tenant_id',:tid,true)"), {"tid": str(ids["other"])}
        )
        assert await conn.scalar(text("SELECT count(*) FROM team_emails")) == 0
        assert await conn.scalar(text("SELECT count(*) FROM agent_lawyers")) == 0
        await conn.execute(text("RESET ROLE"))
        await conn.execute(text("UPDATE conversations SET urgent_since=NULL WHERE id=:c"), ids)
        await conn.execute(text("UPDATE conversations SET urgent_since=now() WHERE id=:c"), ids)
        assert await conn.scalar(text("SELECT count(*) FROM team_emails")) == 4
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: migrate(c, "downgrade"))
        await conn.run_sync(migrate)


async def test_scope_concurrent_takeover_and_revocation(db, monkeypatch):
    engine, factory, _ = db
    ids = await seed(engine)
    for name, value in (
        ("_end_customer_balances_by_phone", {}),
        ("_end_customer_cycles_by_phone", {}),
        ("_is_end_customer_billing_enabled", False),
        ("_agent_names_by_id", {}),
    ):
        monkeypatch.setattr(routes, name, AsyncMock(return_value=value))

    def ctx(key):
        return TenantContext(
            tenant_id=ids["t"],
            user_id=ids[key],
            role="admin" if key == "admin" else "lawyer",
            name=key,
        )

    async with factory() as session:
        visible = list(
            await session.scalars(select(Conversation.id).where(conversation_scope(ctx("l1"))))
        )
        assert visible == [ids["c"]]

    async def assume(key):
        async with factory() as session:
            try:
                await routes.update_state(
                    ids["c"], ConversationStateUpdate(state="human"), ctx(key), session
                )
                return key
            except HTTPException as exc:
                assert exc.status_code == 409
                return None

    winners = [v for v in await asyncio.gather(assume("l1"), assume("l2")) if v]
    assert len(winners) == 1
    async with factory() as session:
        await team.update_member(
            ids[winners[0]], team.MemberUpdate(role="lawyer", active=False), ctx("admin"), session
        )
        conv = await session.get(Conversation, ids["c"])
        assert conv.state == "human" and conv.assigned_user_id is None
        user = await session.get(User, ids[winners[0]])
        assert not user.active and user.session_version == 1
        with pytest.raises(HTTPException) as error:
            await team.update_member(
                ids["admin"], team.MemberUpdate(role="admin", active=False), ctx("admin"), session
            )
        assert error.value.status_code == 409


async def test_invitation_acceptance_single_use_expiration_and_no_new_office(db):
    engine, factory, _ = db
    ids = await seed(engine)
    token = "a" * 43
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO team_invitations(tenant_id,email,name,role,token_hash,expires_at)"
                " VALUES (:t,'new@example.com','New','lawyer',:hash,now()+interval '1 day')"
            ),
            {**ids, "hash": team.digest(token)},
        )
    async with factory() as session:
        await team.accept(team.Accept(token=token, password="secure-password"), session)
        user = await session.scalar(select(User).where(User.email == "new@example.com"))
        assert user.tenant_id == ids["t"] and user.role == "lawyer"
        assert await session.scalar(text("SELECT count(*) FROM tenants")) == 2
        with pytest.raises(HTTPException) as error:
            await team.accept(team.Accept(token=token, password="secure-password"), session)
        assert error.value.status_code == 400


async def test_invite_is_encrypted_cancelled_and_expired_links_are_rejected(db, monkeypatch):
    engine, factory, _ = db
    ids = await seed(engine)
    monkeypatch.setattr(settings, "whatsapp_token_encryption_key", Fernet.generate_key().decode())
    context = TenantContext(tenant_id=ids["t"], user_id=ids["admin"], role="admin")
    async with factory() as session:
        body = team.Invite(name="New", email="new@example.com")
        assert await team.invite(body, context, session) == {"status": "queued"}
        row = (
            (
                await session.execute(
                    text(
                        "SELECT e.secret_encrypted,i.id,i.token_hash FROM team_emails e "
                        "JOIN team_invitations i ON i.id=e.invitation_id"
                    )
                )
            )
            .mappings()
            .one()
        )
        token = decrypt_access_token(row["secret_encrypted"])
        assert token not in row["secret_encrypted"]
        assert team.digest(token) == row["token_hash"]
        with pytest.raises(HTTPException) as cooldown:
            await team.invite(body, context, session)
        assert cooldown.value.status_code == 429
        await session.rollback()
        await team.cancel(row["id"], context, session)
        with pytest.raises(HTTPException) as cancelled:
            await team.accept(team.Accept(token=token, password="secure-password"), session)
        assert cancelled.value.status_code == 400
        await session.rollback()
        await session.execute(
            text(
                "UPDATE team_invitations SET cancelled_at=NULL,expires_at=now()-interval '1 second'"
            )
        )
        await session.commit()
        with pytest.raises(HTTPException) as expired:
            await team.accept(team.Accept(token=token, password="secure-password"), session)
        assert expired.value.status_code == 400
