"""Office team. Public acceptance never provisions a tenant or a checkout."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.api.deps import get_current_tenant, get_tenant_session
from app.core.crypto import encrypt_access_token
from app.core.db import get_system_session
from app.core.security import hash_password
from app.models import Agent, Tenant, User
from app.services.team_access import require_admin

router = APIRouter(prefix="/team", tags=["team"])
public_router = APIRouter(prefix="/team-invitations", tags=["team"])


class Invite(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    email: EmailStr
    role: Literal["admin", "lawyer"] = "lawyer"

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError("Informe o nome.")
        return value.strip()


class MemberUpdate(BaseModel):
    role: Literal["admin", "lawyer"]
    active: bool


class Bindings(BaseModel):
    user_ids: list[uuid.UUID] = Field(max_length=100)


class Accept(BaseModel):
    token: str = Field(min_length=32, max_length=200)
    password: str = Field(min_length=8, max_length=72)

    @field_validator("password")
    @classmethod
    def password_bytes(cls, value):
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Senha excede o limite de 72 bytes.")
        return value


async def refresh_urgencies(session, tenant_id):
    # Reevaluate recipients after membership or agent assignments change.
    await session.execute(
        text("""
        UPDATE conversations SET urgent_since=urgent_since
        WHERE tenant_id=:tid AND urgent_since IS NOT NULL
          AND deletion_requested_at IS NULL AND NOT is_test
    """),
        {"tid": tenant_id},
    )


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


async def lock_office(session, tenant_id):
    await session.execute(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())


@router.get("/me")
async def me(ctx=Depends(get_current_tenant), session=Depends(get_tenant_session)):
    user = await session.get(User, ctx.user_id)
    return dict(id=user.id, name=user.name, email=user.email, role=user.role)


@router.get("/agents")
async def my_agents(ctx=Depends(get_current_tenant), session=Depends(get_tenant_session)):
    query = select(Agent.id, Agent.name).where(Agent.tenant_id == ctx.tenant_id)
    if ctx.role != "admin":
        query = query.where(
            text("""EXISTS (SELECT 1 FROM agent_lawyers a
          WHERE a.agent_id=agents.id AND a.tenant_id=agents.tenant_id AND a.user_id=:uid)
            """).bindparams(uid=ctx.user_id)
        )
    return [dict(r) for r in (await session.execute(query.order_by(Agent.name))).mappings()]


@router.get("")
async def members(ctx=Depends(require_admin), session=Depends(get_tenant_session)):
    rows = (
        await session.execute(
            select(User).where(User.tenant_id == ctx.tenant_id).order_by(User.name)
        )
    ).scalars()
    return [dict(id=u.id, name=u.name, email=u.email, role=u.role, active=u.active) for u in rows]


@router.get("/invitations")
async def invitations(ctx=Depends(require_admin), session=Depends(get_tenant_session)):
    return [
        dict(r)
        for r in (
            await session.execute(
                text("""SELECT id,name,email,role,
      expires_at,accepted_at,cancelled_at,created_at FROM team_invitations
      WHERE tenant_id=:tid ORDER BY created_at DESC LIMIT 200"""),
                {"tid": ctx.tenant_id},
            )
        ).mappings()
    ]


@router.post("/invitations", status_code=202)
async def invite(body: Invite, ctx=Depends(require_admin), session=Depends(get_tenant_session)):
    await lock_office(session, ctx.tenant_id)
    email = str(body.email).lower()
    recent = await session.scalar(
        text("""SELECT count(*) FROM team_invitations
      WHERE tenant_id=:tid AND created_at>now()-interval '1 hour'"""),
        {"tid": ctx.tenant_id},
    )
    if recent >= 30:
        raise HTTPException(429, "Limite de convites atingido. Tente novamente em uma hora.")
    pending = (
        (
            await session.execute(
                text("""SELECT id,created_at FROM team_invitations
      WHERE tenant_id=:tid AND lower(email)=:email AND accepted_at IS NULL
      AND cancelled_at IS NULL FOR UPDATE"""),
                dict(tid=ctx.tenant_id, email=email),
            )
        )
        .mappings()
        .first()
    )
    if pending:
        if datetime.now(UTC) - pending["created_at"] < timedelta(minutes=1):
            raise HTTPException(429, "Aguarde um minuto antes de reenviar.")
        await session.execute(
            text("UPDATE team_invitations SET cancelled_at=now() WHERE id=:id"),
            {"id": pending["id"]},
        )
    # Existing accounts are never silently reassigned (including at acceptance).
    existing = await session.scalar(select(User.id).where(func.lower(User.email) == email))
    if existing:
        raise HTTPException(409, "Este membro já possui uma conta neste escritório.")
    token = secrets.token_urlsafe(32)
    invitation_id = uuid.uuid4()
    await session.execute(
        text("""INSERT INTO team_invitations
      (id,tenant_id,email,name,role,token_hash,expires_at)
      VALUES (:id,:tid,:email,:name,:role,:hash,:expires)"""),
        dict(
            id=invitation_id,
            tid=ctx.tenant_id,
            email=email,
            name=body.name.strip(),
            role=body.role,
            hash=digest(token),
            expires=datetime.now(UTC) + timedelta(hours=48),
        ),
    )
    await session.execute(
        text("""INSERT INTO team_emails
      (tenant_id,kind,invitation_id,secret_encrypted) VALUES (:tid,'invitation',:id,:secret)"""),
        dict(tid=ctx.tenant_id, id=invitation_id, secret=encrypt_access_token(token)),
    )
    await session.commit()
    return {"status": "queued"}


@router.delete("/invitations/{invitation_id}", status_code=204)
async def cancel(
    invitation_id: uuid.UUID, ctx=Depends(require_admin), session=Depends(get_tenant_session)
):
    await session.execute(
        text("""UPDATE team_invitations SET cancelled_at=now()
      WHERE id=:id AND tenant_id=:tid AND accepted_at IS NULL"""),
        dict(id=invitation_id, tid=ctx.tenant_id),
    )
    await session.commit()


@public_router.post("/accept")
async def accept(body: Accept, session=Depends(get_system_session)):
    invitation = (
        (
            await session.execute(
                text("""SELECT * FROM team_invitations
      WHERE token_hash=:hash FOR UPDATE"""),
                {"hash": digest(body.token)},
            )
        )
        .mappings()
        .first()
    )
    if (
        not invitation
        or invitation["cancelled_at"]
        or invitation["accepted_at"]
        or invitation["expires_at"] <= datetime.now(UTC)
    ):
        raise HTTPException(400, "Convite inválido ou expirado. Solicite outro ao administrador.")
    tenant = await session.get(Tenant, invitation["tenant_id"])
    if tenant is None or tenant.status != "active":
        raise HTTPException(403, "Escritório indisponível.")
    # Serialize acceptance by email across different offices as well as signup.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:email,0))"),
        {"email": invitation["email"]},
    )
    existing = await session.scalar(
        select(User.id).where(func.lower(User.email) == invitation["email"])
    )
    if existing:
        raise HTTPException(409, "E-mail já possui conta. Peça orientação ao administrador.")
    session.add(
        User(
            tenant_id=invitation["tenant_id"],
            email=invitation["email"],
            name=invitation["name"],
            role=invitation["role"],
            active=True,
            password_hash=hash_password(body.password),
        )
    )
    await session.execute(
        text("UPDATE team_invitations SET accepted_at=now() WHERE id=:id"), {"id": invitation["id"]}
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "E-mail já possui conta.") from None
    return {"status": "accepted"}


@router.patch("/members/{user_id}")
async def update_member(
    user_id: uuid.UUID,
    body: MemberUpdate,
    ctx=Depends(require_admin),
    session=Depends(get_tenant_session),
):
    await lock_office(session, ctx.tenant_id)
    user = await session.scalar(
        select(User).where(User.id == user_id, User.tenant_id == ctx.tenant_id).with_for_update()
    )
    if user is None:
        raise HTTPException(404, "Membro não encontrado.")
    if user.role == "admin" and user.active and (not body.active or body.role != "admin"):
        count = await session.scalar(
            select(func.count(User.id)).where(
                User.tenant_id == ctx.tenant_id, User.active.is_(True), User.role == "admin"
            )
        )
        if count <= 1:
            raise HTTPException(409, "O escritório precisa de pelo menos um administrador ativo.")
    user.role, user.active = body.role, body.active
    user.session_version += 1
    if not body.active:
        await session.execute(
            text("""INSERT INTO attendance_events
          (tenant_id,conversation_id,actor_id,actor_name,action,target_name)
          SELECT :tid,id,:actor,(SELECT name FROM users WHERE id=:actor),'member_deactivated',:name
          FROM conversations WHERE tenant_id=:tid AND assigned_user_id=:uid"""),
            dict(tid=ctx.tenant_id, actor=ctx.user_id, uid=user_id, name=user.name),
        )
        await session.execute(
            text("""UPDATE conversations SET assigned_user_id=NULL,assigned_user_name=NULL
          WHERE tenant_id=:tid AND assigned_user_id=:uid"""),
            dict(tid=ctx.tenant_id, uid=user_id),
        )
    await session.flush()
    await refresh_urgencies(session, ctx.tenant_id)
    await session.commit()
    return {"status": "updated"}


@router.get("/agents/{agent_id}/lawyers")
async def lawyers(
    agent_id: uuid.UUID, ctx=Depends(require_admin), session=Depends(get_tenant_session)
):
    return list(
        (
            await session.execute(
                text("SELECT user_id FROM agent_lawyers WHERE tenant_id=:tid AND agent_id=:aid"),
                dict(tid=ctx.tenant_id, aid=agent_id),
            )
        ).scalars()
    )


@router.put("/agents/{agent_id}/lawyers")
async def bind(
    agent_id: uuid.UUID,
    body: Bindings,
    ctx=Depends(require_admin),
    session=Depends(get_tenant_session),
):
    await lock_office(session, ctx.tenant_id)
    agent = await session.scalar(
        select(Agent.id).where(Agent.id == agent_id, Agent.tenant_id == ctx.tenant_id)
    )
    if agent is None:
        raise HTTPException(404, "Agente não encontrado.")
    ids = set(body.user_ids)
    users = set(
        (
            await session.scalars(
                select(User.id).where(
                    User.tenant_id == ctx.tenant_id,
                    User.id.in_(ids),
                    User.active.is_(True),
                    User.role == "lawyer",
                )
            )
        ).all()
    )
    if ids != users:
        raise HTTPException(400, "Selecione somente advogados ativos deste escritório.")
    await session.execute(
        text("DELETE FROM agent_lawyers WHERE tenant_id=:tid AND agent_id=:aid"),
        dict(tid=ctx.tenant_id, aid=agent_id),
    )
    for uid in ids:
        await session.execute(
            text("INSERT INTO agent_lawyers(tenant_id,agent_id,user_id) VALUES (:tid,:aid,:uid)"),
            dict(tid=ctx.tenant_id, aid=agent_id, uid=uid),
        )
    await session.flush()
    await refresh_urgencies(session, ctx.tenant_id)
    await session.commit()
    return {"status": "updated"}


@router.get("/notifications")
async def notifications(ctx=Depends(require_admin), session=Depends(get_tenant_session)):
    return [
        dict(r)
        for r in (
            await session.execute(
                text("""SELECT e.id,e.kind,e.status,e.attempts,
      e.last_error,e.created_at,e.sent_at,u.name AS recipient_name,i.email AS invitation_email
      FROM team_emails e LEFT JOIN users u ON u.id=e.user_id
      LEFT JOIN team_invitations i ON i.id=e.invitation_id
      WHERE e.tenant_id=:tid ORDER BY e.created_at DESC LIMIT 100"""),
                {"tid": ctx.tenant_id},
            )
        ).mappings()
    ]


@router.post("/notifications/{email_id}/retry", status_code=202)
async def retry(
    email_id: uuid.UUID, ctx=Depends(require_admin), session=Depends(get_tenant_session)
):
    await session.execute(
        text("""UPDATE team_emails SET status='pending',attempts=0,available_at=now()
      WHERE id=:id AND tenant_id=:tid AND status='failed'"""),
        dict(id=email_id, tid=ctx.tenant_id),
    )
    await session.commit()
    return {"status": "queued"}
