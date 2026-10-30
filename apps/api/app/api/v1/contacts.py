import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.models.contact import Contact, ContactTag, ContactTagLink
from app.schemas.contacts import ContactInput, ContactOut, TagInput, TagOut
from app.services.team_access import require_admin

router = APIRouter(tags=["contacts"])


async def commit_changes(session):
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            409, "Já existe um contato com esse número ou uma tag com esse nome."
        ) from exc


@router.get("/contact-tags", response_model=list[TagOut])
async def list_tags(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
):
    return (
        await session.scalars(
            select(ContactTag)
            .where(ContactTag.tenant_id == ctx.tenant_id)
            .order_by(ContactTag.name)
        )
    ).all()


@router.post("/contact-tags", response_model=TagOut, status_code=201)
async def create_tag(
    body: TagInput,
    ctx=Depends(require_admin),
    session=Depends(get_tenant_session),
):
    tag = ContactTag(tenant_id=ctx.tenant_id, **body.model_dump())
    session.add(tag)
    await commit_changes(session)
    return tag


async def owned(session, model, item_id, tenant_id):
    item = await session.scalar(
        select(model).where(model.id == item_id, model.tenant_id == tenant_id)
    )
    if item is None:
        raise HTTPException(404, "Cadastro não encontrado.")
    return item


@router.put("/contact-tags/{tag_id}", response_model=TagOut)
async def update_tag(
    tag_id: uuid.UUID,
    body: TagInput,
    ctx=Depends(require_admin),
    session=Depends(get_tenant_session),
):
    tag = await owned(session, ContactTag, tag_id, ctx.tenant_id)
    tag.name, tag.color = body.name, body.color
    await commit_changes(session)
    return tag


@router.delete("/contact-tags/{tag_id}", status_code=204)
async def delete_tag(
    tag_id: uuid.UUID, ctx=Depends(require_admin), session=Depends(get_tenant_session)
):
    await session.delete(await owned(session, ContactTag, tag_id, ctx.tenant_id))
    await session.commit()
    return Response(status_code=204)


@router.get("/contacts", response_model=list[ContactOut])
async def list_contacts(
    q: str = Query(default="", max_length=120),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    ctx=Depends(require_admin),
    session=Depends(get_tenant_session),
):
    statement = select(Contact).where(Contact.tenant_id == ctx.tenant_id)
    if q.strip():
        term = q.strip()
        statement = statement.where(
            or_(
                Contact.name.icontains(term, autoescape=True),
                Contact.phone_number.contains(term.lstrip("+"), autoescape=True),
            )
        )
    return (
        await session.scalars(
            statement.order_by(Contact.name, Contact.id).limit(limit).offset(offset)
        )
    ).all()


async def save_contact(session, ctx, body, contact=None):
    ids = set(body.tag_ids)
    tags = (
        (
            await session.scalars(
                select(ContactTag).where(
                    ContactTag.tenant_id == ctx.tenant_id, ContactTag.id.in_(ids)
                )
            )
        ).all()
        if ids
        else []
    )
    if len(tags) != len(ids):
        raise HTTPException(422, "Selecione tags deste escritório.")
    if contact is None:
        contact = Contact(id=uuid.uuid4(), tenant_id=ctx.tenant_id)
        session.add(contact)
    contact.name = body.name.strip()
    contact.phone_number = body.phone_number
    contact.human_only = body.human_only
    try:
        await session.flush()
        await session.execute(delete(ContactTagLink).where(ContactTagLink.contact_id == contact.id))
        session.add_all(
            [ContactTagLink(tenant_id=ctx.tenant_id, contact_id=contact.id, tag_id=t) for t in ids]
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, "Já existe um contato com esse número neste escritório.") from exc
    await session.refresh(contact, ["tags"])
    return contact


@router.post("/contacts", response_model=ContactOut, status_code=201)
async def create_contact(
    body: ContactInput,
    ctx=Depends(require_admin),
    session=Depends(get_tenant_session),
):
    return await save_contact(session, ctx, body)


@router.get("/contacts/{contact_id}", response_model=ContactOut)
async def get_contact(
    contact_id: uuid.UUID,
    ctx=Depends(require_admin),
    session=Depends(get_tenant_session),
):
    return await owned(session, Contact, contact_id, ctx.tenant_id)


@router.put("/contacts/{contact_id}", response_model=ContactOut)
async def update_contact(
    contact_id: uuid.UUID,
    body: ContactInput,
    ctx=Depends(require_admin),
    session=Depends(get_tenant_session),
):
    contact = await owned(session, Contact, contact_id, ctx.tenant_id)
    return await save_contact(session, ctx, body, contact)


@router.delete("/contacts/{contact_id}", status_code=204)
async def delete_contact(
    contact_id: uuid.UUID,
    ctx=Depends(require_admin),
    session=Depends(get_tenant_session),
):
    await session.delete(await owned(session, Contact, contact_id, ctx.tenant_id))
    await session.commit()
    return Response(status_code=204)
