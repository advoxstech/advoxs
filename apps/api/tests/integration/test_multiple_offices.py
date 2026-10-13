"""Real PostgreSQL checks for shared identities and independent office boundaries."""

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select, text

from app.api.deps import get_current_tenant
from app.api.v1 import offices, team
from app.core.security import create_access_token, decode_token
from app.models import OfficeMembership, OfficeSession, User
from app.services.auth import refresh, start_office_session
from tests.integration.test_office_team import seed

pytest_plugins = ["tests.integration.test_office_team"]


def bearer(token):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


async def add_second_office(engine, ids, role="admin"):
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO office_memberships VALUES (:other,:l1,:role,true)"),
            {**ids, "role": role},
        )


async def test_shared_identity_roles_switch_stale_tab_and_refresh(db):
    engine, factory, _ = db
    ids = await seed(engine)
    await add_second_office(engine, ids)
    async with factory() as session:
        user = await session.get(User, ids["l1"])
        tokens = await start_office_session(user, session)
        assert decode_token(tokens[0])["tenant_id"] == ""
        with pytest.raises(HTTPException) as error:
            await get_current_tenant(bearer(tokens[0]), session)
        assert error.value.detail == "office_selection_required"
        identity = (user, decode_token(tokens[0]))
        selected = await offices.select_office(
            offices.OfficeSelection(tenant_id=ids["t"]), identity, session
        )
        original = selected["access_token"]
        assert (await get_current_tenant(bearer(original), session)).role == "lawyer"
        selected = await offices.select_office(
            offices.OfficeSelection(tenant_id=ids["other"]), (user, decode_token(original)), session
        )
        assert (await get_current_tenant(bearer(selected["access_token"]), session)).role == "admin"
        with pytest.raises(HTTPException) as error:
            await get_current_tenant(bearer(original), session)
        assert error.value.detail == "office_context_changed"
        with pytest.raises(HTTPException):
            await get_current_tenant(bearer(selected["access_token"]), session, str(ids["t"]))
        redis = AsyncMock()
        redis.set.return_value = True
        # Even the refresh issued before switching resolves the authoritative new context.
        renewed = await refresh(tokens[1], session, redis)
        assert decode_token(renewed[0])["tenant_id"] == str(ids["other"])
        assert (await get_current_tenant(bearer(renewed[0]), session)).role == "admin"


async def test_revocation_does_not_disable_other_membership_or_change_password(db):
    engine, factory, _ = db
    ids = await seed(engine)
    await add_second_office(engine, ids)
    from app.api.deps import TenantContext

    async with factory() as session:
        user = await session.get(User, ids["l1"])
        before = user.password_hash
        ctx = TenantContext(user_id=ids["admin"], tenant_id=ids["t"], role="admin")
        await team.update_member(
            ids["l1"], team.MemberUpdate(role="lawyer", active=False), ctx, session
        )
        assert user.active and user.session_version == 0 and user.password_hash == before
        assert (await session.get(OfficeMembership, (ids["other"], user.id))).active
        token = create_access_token(str(user.id), str(ids["t"]), "lawyer")
        with pytest.raises(HTTPException):
            await get_current_tenant(bearer(token), session)
        other = create_access_token(str(user.id), str(ids["other"]), "lawyer")
        assert (await get_current_tenant(bearer(other), session)).role == "admin"
        session.add(OfficeSession(user_id=user.id, tenant_id=ids["other"]))
        await session.flush()


async def test_existing_account_invitation_requires_matching_identity_and_is_single_use(db):
    engine, factory, _ = db
    ids = await seed(engine)
    token = "multi-office-invitation-" + "x" * 32
    async with engine.begin() as conn:
        await conn.execute(
            text("""INSERT INTO team_invitations
          (tenant_id,email,name,role,token_hash,expires_at)
          VALUES (:other,'l1@example.com','Ana','lawyer',:hash,now()+interval '1 day')"""),
            {**ids, "hash": team.digest(token)},
        )
    async with factory() as session:
        with pytest.raises(HTTPException) as error:
            await team.accept(team.Accept(token=token, password="must-not-reset"), session, None)
        assert error.value.status_code == 401
        await session.rollback()
        wrong = bearer(create_access_token(str(ids["l2"]), str(ids["t"]), "lawyer"))
        with pytest.raises(HTTPException) as error:
            await team.accept(team.Accept(token=token), session, wrong)
        assert error.value.status_code == 403
        await session.rollback()

    correct = bearer(create_access_token(str(ids["l1"]), str(ids["t"]), "lawyer"))

    async def accept_once():
        async with factory() as session:
            try:
                return await team.accept(team.Accept(token=token), session, correct)
            except HTTPException as error:
                return error.status_code

    results = await asyncio.gather(accept_once(), accept_once())
    assert results.count({"status": "accepted"}) == 1 and results.count(400) == 1
    async with factory() as session:
        user = await session.get(User, ids["l1"])
        assert user.password_hash == "hash"
        assert (
            len(
                list(
                    await session.scalars(
                        select(OfficeMembership).where(OfficeMembership.user_id == user.id)
                    )
                )
            )
            == 2
        )


async def test_rls_and_foreign_keys_require_membership_in_correct_office(db):
    engine, factory, role = db
    ids = await seed(engine)
    async with engine.begin() as conn:
        await conn.execute(text(f'SET LOCAL ROLE "{role}"'))
        await conn.execute(
            text("SELECT set_config('app.tenant_id',:tid,true)"), {"tid": str(ids["other"])}
        )
        assert await conn.scalar(text("SELECT count(*) FROM users")) == 0
        assert await conn.scalar(text("SELECT count(*) FROM office_memberships")) == 0
    await add_second_office(engine, ids)
    async with engine.begin() as conn:
        await conn.execute(text(f'SET LOCAL ROLE "{role}"'))
        await conn.execute(
            text("SELECT set_config('app.tenant_id',:tid,true)"), {"tid": str(ids["other"])}
        )
        assert await conn.scalar(text("SELECT count(*) FROM users")) == 1
        assert await conn.scalar(text("SELECT count(*) FROM office_memberships")) == 1
    async with factory() as session:
        user = await session.get(User, ids["l1"])
        session.add(OfficeSession(user_id=user.id))
        await session.flush()
        tokens = await start_office_session(user, session)
        with pytest.raises(HTTPException) as error:
            await offices.select_office(
                offices.OfficeSelection(tenant_id=uuid.uuid4()),
                (user, decode_token(tokens[0])),
                session,
            )
        assert error.value.status_code == 403


async def test_migration_preserves_existing_password_roles_disabled_members_and_history(db):
    from pathlib import Path

    from app.models import Base
    from tests.integration.test_office_team import migrate

    engine, _, _ = db
    schema = "membership_backfill_" + uuid.uuid4().hex
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        await conn.run_sync(Base.metadata.create_all)
        for table in ("office_sessions", "office_memberships", "attendance_events"):
            await conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
        for table, columns in {
            "users": ["active"],
            "conversations": ["assigned_user_id", "assigned_user_name", "urgency_episode_id"],
            "messages": ["sender_user_id", "sender_user_name"],
        }.items():
            for column in columns:
                await conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
        await conn.run_sync(migrate)
        tenant, user = uuid.uuid4(), uuid.uuid4()
        await conn.execute(
            text(
                "INSERT INTO tenants(id,name,email_contato) VALUES (:id,'Legacy','a@example.com')"
            ),
            {"id": tenant},
        )
        await conn.execute(
            text("""INSERT INTO users
            (id,tenant_id,name,email,password_hash,role,active,session_version)
            VALUES (:id,:tid,'Legacy','old@example.com','preserved-hash','lawyer',false,3)"""),
            {"id": user, "tid": tenant},
        )
        migration = Path(__file__).parents[2] / "alembic/versions/0046_multiple_offices.sql"
        for statement in migration.read_text().split("\n-- statement\n"):
            await conn.execute(text(statement))
        member = (await conn.execute(text("SELECT * FROM office_memberships"))).mappings().one()
        assert member["user_id"] == user and member["tenant_id"] == tenant
        assert member["role"] == "lawyer" and not member["active"]
        identity = (await conn.execute(text("SELECT * FROM users"))).mappings().one()
        assert identity["password_hash"] == "preserved-hash" and identity["active"]
        assert identity["session_version"] == 4
        await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


async def test_suspended_office_and_disabled_membership_do_not_receive_alerts(db):
    engine, factory, _ = db
    ids = await seed(engine)
    await add_second_office(engine, ids, "lawyer")
    async with engine.begin() as conn:
        # Deactivating in another office does not silence this office's assigned lawyer.
        await conn.execute(
            text("UPDATE office_memberships SET active=false WHERE tenant_id=:other"), ids
        )
        await conn.execute(text("UPDATE conversations SET urgent_since=now() WHERE id=:c"), ids)
        assert (
            await conn.scalar(
                text("SELECT count(*) FROM team_emails WHERE user_id=:l1 AND tenant_id=:t"), ids
            )
            == 1
        )
        assert (
            await conn.scalar(text("SELECT count(*) FROM team_emails WHERE tenant_id=:other"), ids)
            == 0
        )
        await conn.execute(text("UPDATE tenants SET status='suspended' WHERE id=:t"), ids)
    async with factory() as session:
        user = await session.get(User, ids["l1"])
        tokens = await start_office_session(user, session)
        assert decode_token(tokens[0])["tenant_id"] == ""
        with pytest.raises(HTTPException):
            await offices.select_office(
                offices.OfficeSelection(tenant_id=ids["t"]),
                (user, decode_token(tokens[0])),
                session,
            )


async def test_cross_office_assignment_is_rejected_by_database(db):
    from sqlalchemy.exc import IntegrityError

    engine, _, _ = db
    ids = await seed(engine)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM agent_lawyers WHERE user_id=:l1"), ids)
        await conn.execute(
            text("DELETE FROM office_memberships WHERE user_id=:l1 AND tenant_id=:t"), ids
        )
        await conn.execute(
            text("INSERT INTO office_memberships VALUES (:other,:l1,'lawyer',true)"), ids
        )
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE conversations SET assigned_user_id=:l1 WHERE id=:c"), ids
            )


async def test_concurrent_office_switch_rejects_stale_selection(db):
    engine, factory, _ = db
    ids = await seed(engine)
    await add_second_office(engine, ids)
    async with factory() as session:
        user = await session.get(User, ids["l1"])
        tokens = await start_office_session(user, session)

    async def switch(tenant_id):
        async with factory() as session:
            user = await session.get(User, ids["l1"])
            # Keep the identity lookup cached to reproduce concurrent requests.
            cached = await session.get(OfficeSession, uuid.UUID(decode_token(tokens[0])["sid"]))
            assert cached.version == 0 or cached.version == 1
            try:
                result = await offices.select_office(
                    offices.OfficeSelection(tenant_id=tenant_id),
                    (user, decode_token(tokens[0])),
                    session,
                )
                return decode_token(result["access_token"])["tenant_id"]
            except HTTPException as error:
                assert error.detail == "office_context_changed"
                return None

    results = await asyncio.gather(switch(ids["t"]), switch(ids["other"]))
    assert sum(value is not None for value in results) == 1
