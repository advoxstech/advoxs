import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import get_current_identity
from app.core.db import get_system_session
from app.models import OfficeMembership, OfficeSession, Tenant
from app.services.auth import available_offices, office_tokens

router = APIRouter(prefix="/offices", tags=["offices"])


class OfficeSelection(BaseModel):
    tenant_id: uuid.UUID


@router.get("")
async def list_offices(identity=Depends(get_current_identity), session=Depends(get_system_session)):
    user, payload = identity
    current = payload.get("tenant_id") or None
    if payload.get("sid"):
        current = (await session.get(OfficeSession, uuid.UUID(payload["sid"]))).tenant_id
    return {"offices": await available_offices(user.id, session), "current_id": current}


@router.post("/select")
async def select_office(
    body: OfficeSelection,
    identity=Depends(get_current_identity),
    session=Depends(get_system_session),
):
    user, payload = identity
    membership = await session.get(OfficeMembership, (body.tenant_id, user.id))
    tenant = await session.get(Tenant, body.tenant_id)
    if not membership or not membership.active or not tenant or tenant.status != "active":
        raise HTTPException(403, "Você não tem acesso a este escritório.")
    if payload.get("sid"):
        office_session = await session.scalar(
            select(OfficeSession)
            .where(OfficeSession.id == uuid.UUID(payload["sid"]), OfficeSession.user_id == user.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if not office_session:
            raise HTTPException(401, "Sessão expirada.")
        if office_session.version != payload.get("context_version", 0):
            raise HTTPException(409, "office_context_changed")
        office_session.version += 1
        office_session.tenant_id = body.tenant_id
    else:
        office_session = OfficeSession(user_id=user.id, tenant_id=body.tenant_id)
        session.add(office_session)
    await session.flush()
    tokens = await office_tokens(user, office_session, session)
    await session.commit()
    return dict(access_token=tokens[0], refresh_token=tokens[1])
