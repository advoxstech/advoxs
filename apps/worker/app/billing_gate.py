"""Máquina de estados do billing gate determinístico — conduz o diálogo
mecânico (sem LLM) de "sem saldo -> escolher pacote -> pagar -> liberado"
pro cliente final, sempre que tenant_billing_settings.enabled = true — é o
único mecanismo de cobrança do cliente final que existe (ver
docs/superpowers/specs/2026-07-23-gate-unico-deterministico-design.md)."""

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import tables
from app.clients.billing import create_end_customer_checkout
from app.clients.whatsapp import send_interactive_list_message, send_text_message
from app.crypto import decrypt_access_token
from app.tasks.inbound_context import InboundContext

MAX_RETRIES = 3


async def maybe_enter_gate(
    session: AsyncSession, tenant_id: str, conversation_id: str, inbound: InboundContext
) -> bool:
    """Transiciona a conversa pra billing_gate se o tenant estiver migrado e
    o contato sem saldo. Retorna True se a conversa está (ou acabou de
    entrar) em billing_gate — nesse caso, process_inbound_message não deve
    seguir pro fluxo normal de chamar o agents.

    Bloqueado pra qualquer provedor diferente de "meta": o gate usa mensagens
    interativas (`interactive`/`list`) nativas da Cloud API da Meta, sem
    equivalente testado na Z-API nesta v1 — ver `PATCH
    /end-customer-billing/settings`, que já recusa HABILITAR a cobrança pra
    um tenant Z-API. Essa checagem aqui cobre o caso de um tenant que
    habilitou via Meta e depois trocou pra Z-API: sem ela, o gate seria
    acionado em runtime e quebraria ao tentar descriptografar um
    access_token_encrypted=None (campos Meta ficam NULL num tenant Z-API).
    Retornar False aqui faz o agente responder normalmente, custeado pelo
    estoque do próprio tenant — mesma filosofia de fallback seguro já usada
    em outros lugares deste código."""
    if inbound.whatsapp_provider != "meta":
        return False
    if inbound.conversation_state == "billing_gate":
        if inbound.end_customer_billing_exempt or inbound.end_customer_has_active_subscription:
            await session.execute(
                update(tables.conversations)
                .where(tables.conversations.c.id == uuid.UUID(conversation_id))
                .values(state="agent", billing_gate_step=None, billing_gate_retries=0)
            )
            await session.commit()
            return False
        return True
    if (
        inbound.conversation_state == "agent"
        and inbound.end_customer_billing_enabled
        and not inbound.end_customer_billing_exempt
        and not inbound.end_customer_has_active_subscription
        and inbound.end_customer_balance <= 0
    ):
        await session.execute(
            update(tables.conversations)
            .where(tables.conversations.c.id == uuid.UUID(conversation_id))
            .values(state="billing_gate", billing_gate_step=None, billing_gate_retries=0)
        )
        await session.commit()
        return True
    return False


async def handle_billing_gate(
    session: AsyncSession, tenant_id: str, conversation_id: str, inbound: InboundContext
) -> None:
    access_token = decrypt_access_token(inbound.access_token_encrypted)

    if inbound.billing_gate_step is None:
        await _open_gate(session, tenant_id, conversation_id, inbound, access_token)
    elif inbound.billing_gate_step == "aguardando_selecao_pacote":
        await _handle_package_selection(session, tenant_id, conversation_id, inbound, access_token)
    elif inbound.billing_gate_step == "aguardando_pagamento":
        await _handle_awaiting_payment(session, conversation_id, inbound, access_token)


async def _welcome_text(
    session: AsyncSession, tenant_id: str, contact_phone_number: str, configured: str | None
) -> str:
    if configured:
        return configured
    ja_comprou = await session.scalar(
        select(tables.end_customer_credit_transactions.c.id)
        .where(
            tables.end_customer_credit_transactions.c.tenant_id == uuid.UUID(tenant_id),
            tables.end_customer_credit_transactions.c.contact_phone_number == contact_phone_number,
            tables.end_customer_credit_transactions.c.type == "purchase",
        )
        .limit(1)
    )
    if ja_comprou:
        return "Seus créditos acabaram! Escolha um pacote pra continuar:"
    return "Olá! Escolha um pacote de créditos pra começar o atendimento:"


def _package_row(package: dict) -> dict:
    if package.get("kind") == "subscription":
        description = f"R$ {package['price_brl']}/mês — conversas ilimitadas"
    else:
        description = f"R$ {package['price_brl']} = {package['credits_granted']} créditos"
    return {"id": package["name"], "title": package["name"], "description": description}


def _packages_to_sections(packages: list[dict]) -> list[dict]:
    # .get("kind", "one_time") — compatibilidade com qualquer chamador que
    # ainda não propague "kind" (nenhum hoje, mas evita um KeyError silencioso
    # se um teste/fixture mais antigo não tiver esse campo).
    avulsos = [p for p in packages if p.get("kind", "one_time") != "subscription"]
    assinaturas = [p for p in packages if p.get("kind") == "subscription"]

    if not assinaturas:
        return [{"title": "Pacotes disponíveis", "rows": [_package_row(p) for p in avulsos]}]

    sections = []
    if avulsos:
        sections.append(
            {"title": "Pacotes de créditos", "rows": [_package_row(p) for p in avulsos]}
        )
    sections.append(
        {"title": "Assinatura mensal", "rows": [_package_row(p) for p in assinaturas]}
    )
    return sections


async def _send_package_list(inbound: InboundContext, access_token: str) -> None:
    await send_interactive_list_message(
        phone_number_id=inbound.phone_number_id,
        access_token=access_token,
        to=inbound.contact_phone_number,
        header="Pacotes de créditos",
        body="Escolha uma opção:",
        sections=_packages_to_sections(inbound.end_customer_packages),
    )


async def _open_gate(
    session: AsyncSession,
    tenant_id: str,
    conversation_id: str,
    inbound: InboundContext,
    access_token: str,
) -> None:
    text = await _welcome_text(
        session, tenant_id, inbound.contact_phone_number, inbound.billing_gate_welcome_text
    )
    await send_text_message(
        phone_number_id=inbound.phone_number_id,
        access_token=access_token,
        to=inbound.contact_phone_number,
        text=text,
    )
    await _send_package_list(inbound, access_token)
    await session.execute(
        update(tables.conversations)
        .where(tables.conversations.c.id == uuid.UUID(conversation_id))
        .values(billing_gate_step="aguardando_selecao_pacote", billing_gate_retries=0)
    )
    await session.commit()


def _resolve_package_by_title(packages: list[dict], title: str) -> dict | None:
    for package in packages:
        if package["name"] == title:
            return package
    return None


async def _escalate_to_human(session: AsyncSession, conversation_id: str) -> None:
    await session.execute(
        update(tables.conversations)
        .where(tables.conversations.c.id == uuid.UUID(conversation_id))
        .values(state="human", billing_gate_step=None, billing_gate_retries=0)
    )
    await session.commit()


async def _handle_package_selection(
    session: AsyncSession,
    tenant_id: str,
    conversation_id: str,
    inbound: InboundContext,
    access_token: str,
) -> None:
    package = _resolve_package_by_title(inbound.end_customer_packages, inbound.message_content)
    if package is None:
        retries = inbound.billing_gate_retries + 1
        if retries >= MAX_RETRIES:
            await _escalate_to_human(session, conversation_id)
            return
        await send_text_message(
            phone_number_id=inbound.phone_number_id,
            access_token=access_token,
            to=inbound.contact_phone_number,
            text="Não entendi — escolha uma opção da lista abaixo:",
        )
        await _send_package_list(inbound, access_token)
        await session.execute(
            update(tables.conversations)
            .where(tables.conversations.c.id == uuid.UUID(conversation_id))
            .values(billing_gate_retries=retries)
        )
        await session.commit()
        return

    checkout_url = await create_end_customer_checkout(
        tenant_id=tenant_id,
        contact_phone_number=inbound.contact_phone_number,
        package_id=package["id"],
    )
    await send_text_message(
        phone_number_id=inbound.phone_number_id,
        access_token=access_token,
        to=inbound.contact_phone_number,
        text=f"Aqui está o link de pagamento: {checkout_url}",
    )
    await session.execute(
        update(tables.conversations)
        .where(tables.conversations.c.id == uuid.UUID(conversation_id))
        .values(
            billing_gate_step="aguardando_pagamento",
            billing_gate_checkout_url=checkout_url,
            billing_gate_retries=0,
        )
    )
    await session.commit()


async def _handle_awaiting_payment(
    session: AsyncSession, conversation_id: str, inbound: InboundContext, access_token: str
) -> None:
    retries = inbound.billing_gate_retries + 1
    if retries >= MAX_RETRIES:
        await _escalate_to_human(session, conversation_id)
        return
    await send_text_message(
        phone_number_id=inbound.phone_number_id,
        access_token=access_token,
        to=inbound.contact_phone_number,
        text=(
            "Ainda aguardando a confirmação do pagamento. Aqui está o link de novo: "
            f"{inbound.billing_gate_checkout_url}"
        ),
    )
    await session.execute(
        update(tables.conversations)
        .where(tables.conversations.c.id == uuid.UUID(conversation_id))
        .values(billing_gate_retries=retries)
    )
    await session.commit()
