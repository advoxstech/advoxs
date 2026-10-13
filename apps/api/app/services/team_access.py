"""Office membership and object-level authorization (also used by downloads)."""

from fastapi import Depends, HTTPException
from sqlalchemy import and_, or_, select, text, true

from app.api.deps import TenantContext, get_current_tenant
from app.models import Conversation


async def require_admin(ctx: TenantContext = Depends(get_current_tenant)) -> TenantContext:
    if ctx.role != "admin":
        raise HTTPException(403, "Acesso exclusivo do administrador.")
    return ctx


def conversation_scope(ctx):
    if ctx.role == "admin":
        return true()
    return and_(
        Conversation.is_test.is_(False),
        or_(
            Conversation.assigned_user_id == ctx.user_id,
            text("""EXISTS (SELECT 1 FROM agent_lawyers al
          WHERE al.tenant_id=conversations.tenant_id AND al.user_id=:team_user
          AND al.agent_id=COALESCE(conversations.current_agent_id,
            (SELECT id FROM agents WHERE tenant_id=conversations.tenant_id
             AND is_entry_point LIMIT 1)))""").bindparams(team_user=ctx.user_id),
        ),
    )


async def audit(session, ctx, conversation_id, action, target_name=None):
    from app.models.attendance_event import AttendanceEvent

    session.add(
        AttendanceEvent(
            tenant_id=ctx.tenant_id,
            conversation_id=conversation_id,
            actor_id=ctx.user_id,
            actor_name=ctx.name,
            action=action,
            target_name=target_name,
        )
    )
    return ctx.name


async def require_document_access(session, ctx, file_id):
    if ctx.role == "admin":
        return
    # Only documents actually cited in a conversation this lawyer can access.
    permitted = (
        select(Conversation.id)
        .where(
            Conversation.tenant_id == ctx.tenant_id,
            Conversation.deletion_requested_at.is_(None),
            Conversation.is_test.is_(False),
            conversation_scope(ctx),
            text("""EXISTS (SELECT 1 FROM messages m,
          jsonb_array_elements(COALESCE(m.response_sources->'sources','[]'::jsonb)) source
          WHERE m.conversation_id=conversations.id
          AND source->>'document_id'=:team_file)""").bindparams(team_file=str(file_id)),
        )
        .limit(1)
    )
    if await session.scalar(permitted) is None:
        raise HTTPException(404, "Documento não encontrado.")
