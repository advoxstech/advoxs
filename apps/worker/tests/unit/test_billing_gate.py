import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.billing_gate import MAX_RETRIES, handle_billing_gate, maybe_enter_gate
from app.tasks.messages import InboundContext

TENANT_ID = str(uuid.uuid4())
CONVERSATION_ID = str(uuid.uuid4())

PACKAGES = [
    {"id": "pkg-1", "name": "Básico", "price_brl": "49.90", "credits_granted": 500},
    {"id": "pkg-2", "name": "Premium", "price_brl": "99.90", "credits_granted": 1200},
]


def _inbound(**overrides) -> InboundContext:
    base = InboundContext(
        conversation_state="agent",
        contact_phone_number="5511999998888",
        message_content="oi",
        phone_number_id="PNID",
        access_token_encrypted="cifrado",
        credit_balance=Decimal(1000),
        end_customer_billing_enabled=True,
        end_customer_balance=Decimal(0),
        end_customer_packages=PACKAGES,
        agents=[],
        insufficient_balance_policy="deterministic_gate",
        billing_gate_step=None,
        billing_gate_retries=0,
        billing_gate_checkout_url=None,
        billing_gate_welcome_text=None,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


@pytest.fixture(autouse=True)
def crypto(monkeypatch):
    monkeypatch.setattr("app.billing_gate.decrypt_access_token", lambda v: "token-claro")


class TestMaybeEnterGate:
    async def test_entra_no_gate_quando_policy_deterministic_e_sem_saldo(self) -> None:
        session = AsyncMock()
        inbound = _inbound(conversation_state="agent", end_customer_balance=Decimal(0))

        entered = await maybe_enter_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert entered is True
        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once()

    async def test_nao_entra_quando_policy_e_block_with_message(self) -> None:
        session = AsyncMock()
        inbound = _inbound(insufficient_balance_policy="block_with_message")

        entered = await maybe_enter_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert entered is False
        session.execute.assert_not_called()

    async def test_nao_entra_com_saldo_positivo(self) -> None:
        session = AsyncMock()
        inbound = _inbound(end_customer_balance=Decimal(500))

        entered = await maybe_enter_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert entered is False

    async def test_ja_em_billing_gate_retorna_true_sem_reprocessar_entrada(self) -> None:
        session = AsyncMock()
        inbound = _inbound(
            conversation_state="billing_gate", billing_gate_step="aguardando_pagamento"
        )

        entered = await maybe_enter_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert entered is True
        session.execute.assert_not_called()

    async def test_nao_entra_quando_contato_esta_isento(self) -> None:
        session = AsyncMock()
        inbound = _inbound(end_customer_balance=Decimal(0), end_customer_billing_exempt=True)

        entered = await maybe_enter_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert entered is False
        session.execute.assert_not_called()


class TestHandleBillingGateAbertura:
    async def test_abre_o_gate_manda_boas_vindas_e_lista(self, monkeypatch) -> None:
        session = AsyncMock()
        send_text = AsyncMock()
        send_list = AsyncMock()
        monkeypatch.setattr("app.billing_gate.send_text_message", send_text)
        monkeypatch.setattr("app.billing_gate.send_interactive_list_message", send_list)
        inbound = _inbound(billing_gate_step=None)

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        send_text.assert_awaited_once()
        send_list.assert_awaited_once()
        sections = send_list.await_args.kwargs["sections"]
        assert sections[0]["rows"][0]["title"] == "Básico"
        session.execute.assert_awaited_once()
        update_values = session.execute.await_args.args[0]
        compiled = str(update_values.compile(compile_kwargs={"literal_binds": True}))
        assert "aguardando_selecao_pacote" in compiled

    async def test_primeira_compra_usa_texto_institucional(self, monkeypatch) -> None:
        session = AsyncMock()
        session.scalar = AsyncMock(return_value=None)  # nunca comprou
        send_text = AsyncMock()
        monkeypatch.setattr("app.billing_gate.send_text_message", send_text)
        monkeypatch.setattr("app.billing_gate.send_interactive_list_message", AsyncMock())
        inbound = _inbound(billing_gate_step=None, billing_gate_welcome_text=None)

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert "Escolha um pacote" in send_text.await_args_list[0].kwargs["text"]

    async def test_texto_configurado_pelo_tenant_tem_prioridade(self, monkeypatch) -> None:
        session = AsyncMock()
        send_text = AsyncMock()
        monkeypatch.setattr("app.billing_gate.send_text_message", send_text)
        monkeypatch.setattr("app.billing_gate.send_interactive_list_message", AsyncMock())
        inbound = _inbound(billing_gate_step=None, billing_gate_welcome_text="Bem-vindo à Advoxs!")

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert send_text.await_args_list[0].kwargs["text"] == "Bem-vindo à Advoxs!"


class TestHandleBillingGateSelecaoPacote:
    async def test_selecao_valida_gera_link_e_avanca_step(self, monkeypatch) -> None:
        session = AsyncMock()
        send_text = AsyncMock()
        checkout = AsyncMock(return_value="https://checkout.stripe.com/xyz")
        monkeypatch.setattr("app.billing_gate.send_text_message", send_text)
        monkeypatch.setattr("app.billing_gate.create_end_customer_checkout", checkout)
        inbound = _inbound(billing_gate_step="aguardando_selecao_pacote", message_content="Básico")

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        checkout.assert_awaited_once_with(
            tenant_id=TENANT_ID, contact_phone_number="5511999998888", package_id="pkg-1"
        )
        assert "https://checkout.stripe.com/xyz" in send_text.await_args.kwargs["text"]
        update_values = session.execute.await_args.args[0]
        compiled = str(update_values.compile(compile_kwargs={"literal_binds": True}))
        assert "aguardando_pagamento" in compiled

    async def test_selecao_nao_reconhecida_reenvia_lista_e_incrementa_retry(
        self, monkeypatch
    ) -> None:
        session = AsyncMock()
        send_text = AsyncMock()
        send_list = AsyncMock()
        monkeypatch.setattr("app.billing_gate.send_text_message", send_text)
        monkeypatch.setattr("app.billing_gate.send_interactive_list_message", send_list)
        inbound = _inbound(
            billing_gate_step="aguardando_selecao_pacote",
            message_content="não sei escolher",
            billing_gate_retries=0,
        )

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        send_list.assert_awaited_once()
        update_values = session.execute.await_args.args[0]
        compiled = str(update_values.compile(compile_kwargs={"literal_binds": True}))
        assert "billing_gate_retries=1" in compiled

    async def test_ultima_tentativa_escala_pra_human(self, monkeypatch) -> None:
        session = AsyncMock()
        monkeypatch.setattr("app.billing_gate.send_text_message", AsyncMock())
        monkeypatch.setattr("app.billing_gate.send_interactive_list_message", AsyncMock())
        inbound = _inbound(
            billing_gate_step="aguardando_selecao_pacote",
            message_content="não sei escolher",
            billing_gate_retries=MAX_RETRIES - 1,
        )

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        update_values = session.execute.await_args.args[0]
        compiled = str(update_values.compile(compile_kwargs={"literal_binds": True}))
        assert "state='human'" in compiled


class TestHandleBillingGateAguardandoPagamento:
    async def test_reenvia_o_link_ja_gerado_sem_chamar_checkout_de_novo(self, monkeypatch) -> None:
        session = AsyncMock()
        send_text = AsyncMock()
        checkout = AsyncMock()
        monkeypatch.setattr("app.billing_gate.send_text_message", send_text)
        monkeypatch.setattr("app.billing_gate.create_end_customer_checkout", checkout)
        inbound = _inbound(
            billing_gate_step="aguardando_pagamento",
            billing_gate_checkout_url="https://checkout.stripe.com/xyz",
            billing_gate_retries=0,
        )

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        checkout.assert_not_called()
        assert "https://checkout.stripe.com/xyz" in send_text.await_args.kwargs["text"]

    async def test_ultima_tentativa_aguardando_pagamento_escala_pra_human(
        self, monkeypatch
    ) -> None:
        session = AsyncMock()
        monkeypatch.setattr("app.billing_gate.send_text_message", AsyncMock())
        inbound = _inbound(
            billing_gate_step="aguardando_pagamento",
            billing_gate_checkout_url="https://checkout.stripe.com/xyz",
            billing_gate_retries=MAX_RETRIES - 1,
        )

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        update_values = session.execute.await_args.args[0]
        compiled = str(update_values.compile(compile_kwargs={"literal_binds": True}))
        assert "state='human'" in compiled
