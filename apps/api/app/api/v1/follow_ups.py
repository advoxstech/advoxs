"""Follow-up manual: o administrador envia um modelo aprovado da Meta (ou um
texto, na Z-API) para contatos escolhidos, inclusive fora da janela de 24h.
O envio em si é feito pelo worker (`deliver_follow_up_batch`), um contato por vez."""

import logging
import re
import uuid
from types import SimpleNamespace

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_tenant_session
from app.clients.omni import OmniApiError, OmniNetworkError, list_waba_templates
from app.clients.whatsapp import WhatsAppApiError, WhatsAppNetworkError, fetch_message_templates
from app.core.crypto import decrypt_access_token
from app.core.queue import get_arq_pool
from app.models import (
    Contact,
    ContactTagLink,
    Conversation,
    FollowUpBatch,
    FollowUpRecipient,
    WhatsAppNumber,
)
from app.schemas.follow_ups import (
    MAX_RECIPIENTS,
    FollowUpBatchDetailOut,
    FollowUpBatchOut,
    FollowUpCreate,
    FollowUpRecipientOut,
    FollowUpTemplatesOut,
)
from app.services.follow_ups import parse_template
from app.services.team_access import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/follow-ups", tags=["follow-ups"])

_PHONE = re.compile(r"[1-9][0-9]{7,14}")


async def _connected_number(session: AsyncSession, tenant_id: uuid.UUID) -> WhatsAppNumber:
    number = await session.scalar(
        select(WhatsAppNumber).where(
            WhatsAppNumber.tenant_id == tenant_id, WhatsAppNumber.status == "connected"
        )
    )
    if number is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Conecte um número de WhatsApp antes de enviar follow-ups."
        )
    return number


async def _meta_templates(number: WhatsAppNumber) -> list[dict]:
    """Modelos aprovados da conta de WhatsApp Business (Meta direta ou Omni).

    O Omni devolve os modelos no mesmo formato da Graph API."""
    if number.provider == "omni":
        if not number.waba_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Peça à Advoxs para informar o WABA ID deste número para usar os modelos.",
            )
        try:
            raw = await list_waba_templates(number.waba_id)
        except (OmniApiError, OmniNetworkError) as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        return [parse_template(item) for item in raw]
    if not number.waba_id or not number.access_token_encrypted:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Reconecte o WhatsApp oficial informando o WABA ID para usar os modelos.",
        )
    try:
        raw = await fetch_message_templates(
            number.waba_id, decrypt_access_token(number.access_token_encrypted)
        )
    except (WhatsAppApiError, WhatsAppNetworkError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return [parse_template(item) for item in raw]


@router.get("/templates", response_model=FollowUpTemplatesOut)
async def list_templates(
    ctx: TenantContext = Depends(require_admin),
    session: AsyncSession = Depends(get_tenant_session),
):
    number = await _connected_number(session, ctx.tenant_id)
    if number.provider == "zapi":
        return FollowUpTemplatesOut(provider="zapi", templates=[])
    return FollowUpTemplatesOut(provider=number.provider, templates=await _meta_templates(number))


async def _resolve_template(number: WhatsAppNumber, body: FollowUpCreate) -> dict:
    if not body.template_name or not body.language:
        raise HTTPException(422, "Escolha um modelo aprovado.")
    template = next(
        (
            t
            for t in await _meta_templates(number)
            if t["name"] == body.template_name and t["language"] == body.language
        ),
        None,
    )
    if template is None or template["status"] != "APPROVED":
        raise HTTPException(
            422,
            "Esse modelo não está aprovado na Meta. Escolha um modelo ativo.",
        )
    if not template["supported"]:
        raise HTTPException(422, template["unsupported_reason"])
    if len(body.params) != len(template["param_names"]):
        raise HTTPException(
            422,
            f"Preencha as {len(template['param_names'])} variáveis do modelo.",
        )
    if any(p.source == "text" and not p.value for p in body.params):
        raise HTTPException(422, "Preencha o texto de todas as variáveis.")
    return {
        key: template[key]
        for key in (
            "name",
            "language",
            "parameter_format",
            "header_text",
            "body_text",
            "footer_text",
            "param_names",
        )
    }


async def _collect_recipients(
    session: AsyncSession, tenant_id: uuid.UUID, body: FollowUpCreate
) -> list[dict]:
    """Junta contatos, contatos das tags e conversas, sem repetir números."""
    recipients: dict[str, dict] = {}

    def add(phone: str, name: str, contact_id: uuid.UUID | None) -> None:
        current = recipients.get(phone)
        if current is None:
            recipients[phone] = {"phone": phone, "contact_name": name, "contact_id": contact_id}
        elif not current["contact_name"] and name:
            current["contact_name"] = name

    contact_filters = []
    if body.contact_ids:
        contact_filters.append(Contact.id.in_(set(body.contact_ids)))
    if body.tag_ids:
        contact_filters.append(
            Contact.id.in_(
                select(ContactTagLink.contact_id).where(
                    ContactTagLink.tenant_id == tenant_id,
                    ContactTagLink.tag_id.in_(set(body.tag_ids)),
                )
            )
        )
    if contact_filters:
        contacts = (
            await session.scalars(
                select(Contact)
                .where(Contact.tenant_id == tenant_id, or_(*contact_filters))
                .order_by(Contact.name, Contact.id)
            )
        ).all()
        found = {c.id for c in contacts}
        if set(body.contact_ids) - found:
            raise HTTPException(422, "Selecione contatos deste escritório.")
        for contact in contacts:
            add(contact.phone_number, contact.name, contact.id)

    if body.conversation_ids:
        rows = (
            await session.execute(
                select(Conversation, Contact.name)
                .outerjoin(Contact, Contact.id == Conversation.contact_id)
                .where(
                    Conversation.tenant_id == tenant_id,
                    Conversation.id.in_(set(body.conversation_ids)),
                    Conversation.is_test.is_(False),
                    Conversation.deletion_requested_at.is_(None),
                )
            )
        ).all()
        if len(rows) != len(set(body.conversation_ids)):
            raise HTTPException(
                422,
                "Selecione conversas reais deste escritório.",
            )
        for conversation, name in rows:
            phone = re.sub(r"\D", "", conversation.contact_phone_number).removeprefix("00")
            if not _PHONE.fullmatch(phone):
                raise HTTPException(
                    422,
                    "Uma das conversas não tem um número de WhatsApp válido.",
                )
            add(phone, name or "", conversation.contact_id)

    if not recipients:
        raise HTTPException(422, "Nenhum contato encontrado para o envio.")
    if len(recipients) > MAX_RECIPIENTS:
        raise HTTPException(
            422,
            f"Máximo de {MAX_RECIPIENTS} destinatários por envio ({len(recipients)} selecionados).",
        )
    return list(recipients.values())


_COUNTS = (
    func.count(FollowUpRecipient.id).label("total"),
    func.count(case((FollowUpRecipient.status == "sent", 1))).label("sent"),
    func.count(case((FollowUpRecipient.status == "failed", 1))).label("failed"),
    func.count(case((FollowUpRecipient.status == "skipped", 1))).label("skipped"),
    func.count(case((FollowUpRecipient.status.in_(("pending", "processing")), 1))).label("pending"),
)


def _batch_out(batch: FollowUpBatch, counts) -> dict:
    return {
        "id": batch.id,
        "created_at": batch.created_at,
        "created_by_name": batch.created_by_name,
        "provider": batch.provider,
        "template_name": (batch.template or {}).get("name"),
        "text": batch.message_text,
        "total": counts.total,
        "sent": counts.sent,
        "failed": counts.failed,
        "skipped": counts.skipped,
        "pending": counts.pending,
    }


@router.post("", response_model=FollowUpBatchOut, status_code=status.HTTP_201_CREATED)
async def create_follow_up(
    body: FollowUpCreate,
    ctx: TenantContext = Depends(require_admin),
    session: AsyncSession = Depends(get_tenant_session),
    arq: ArqRedis = Depends(get_arq_pool),
):
    number = await _connected_number(session, ctx.tenant_id)
    batch = FollowUpBatch(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        created_by_user_id=ctx.user_id,
        created_by_name=ctx.name,
        provider=number.provider,
    )
    if number.provider == "zapi":
        if not (body.text or "").strip():
            raise HTTPException(422, "Escreva a mensagem.")
        batch.message_text = body.text.strip()
    else:
        batch.template = await _resolve_template(number, body)
        batch.params = [p.model_dump() for p in body.params]

    recipients = await _collect_recipients(session, ctx.tenant_id, body)
    session.add(batch)
    await session.flush()
    session.add_all(
        FollowUpRecipient(tenant_id=ctx.tenant_id, batch_id=batch.id, **item) for item in recipients
    )
    await session.commit()
    await session.refresh(batch)

    try:
        await arq.enqueue_job(
            "deliver_follow_up_batch",
            tenant_id=str(ctx.tenant_id),
            batch_id=str(batch.id),
            _job_id=f"follow-up:{batch.id}",
        )
    except Exception:  # noqa: BLE001 — o cron de recuperação reenfileira o lote
        logger.warning("Falha ao enfileirar follow-up | batch=%s", batch.id)

    counts = SimpleNamespace(
        total=len(recipients), sent=0, failed=0, skipped=0, pending=len(recipients)
    )
    return _batch_out(batch, counts)


@router.get("", response_model=list[FollowUpBatchOut])
async def list_follow_ups(
    limit: int = Query(default=20, ge=1, le=100),
    ctx: TenantContext = Depends(require_admin),
    session: AsyncSession = Depends(get_tenant_session),
):
    rows = (
        await session.execute(
            select(FollowUpBatch, *_COUNTS)
            .outerjoin(FollowUpRecipient, FollowUpRecipient.batch_id == FollowUpBatch.id)
            .where(FollowUpBatch.tenant_id == ctx.tenant_id)
            .group_by(FollowUpBatch.id)
            .order_by(FollowUpBatch.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [_batch_out(row[0], row) for row in rows]


@router.get("/{batch_id}", response_model=FollowUpBatchDetailOut)
async def get_follow_up(
    batch_id: uuid.UUID,
    ctx: TenantContext = Depends(require_admin),
    session: AsyncSession = Depends(get_tenant_session),
):
    row = (
        await session.execute(
            select(FollowUpBatch, *_COUNTS)
            .outerjoin(FollowUpRecipient, FollowUpRecipient.batch_id == FollowUpBatch.id)
            .where(FollowUpBatch.tenant_id == ctx.tenant_id, FollowUpBatch.id == batch_id)
            .group_by(FollowUpBatch.id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Envio não encontrado.")
    recipients = (
        await session.scalars(
            select(FollowUpRecipient)
            .where(
                FollowUpRecipient.tenant_id == ctx.tenant_id,
                FollowUpRecipient.batch_id == batch_id,
            )
            .order_by(FollowUpRecipient.created_at, FollowUpRecipient.phone)
        )
    ).all()
    return {
        **_batch_out(row[0], row),
        "recipients": [
            FollowUpRecipientOut(
                id=r.id,
                phone=r.phone,
                contact_name=r.contact_name,
                status=r.status,
                error=r.error,
                sent_at=r.sent_at,
                conversation_id=r.conversation_id,
            )
            for r in recipients
        ],
    }
