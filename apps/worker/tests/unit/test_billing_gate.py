import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.billing_gate import (
    MAX_RETRIES,
    _packages_to_sections,
    handle_billing_gate,
    maybe_enter_gate,
)
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
        whatsapp_provider="meta",
        phone_number_id="PNID",
        access_token_encrypted="cifrado",
        zapi_instance_id=None,
        zapi_instance_token_encrypted=None,
        zapi_client_token_encrypted=None,
        credit_balance=Decimal(1000),
        end_customer_billing_enabled=True,
        end_customer_balance=Decimal(0),
        end_customer_packages=PACKAGES,
        agents=[],
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
    async def test_entra_no_gate_quando_habilitado_e_sem_saldo(self) -> None:
        session = AsyncMock()
        inbound = _inbound(conversation_state="agent", end_customer_balance=Decimal(0))

        entered = await maybe_enter_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert entered is True
        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once()

    async def test_nao_entra_com_saldo_positivo(self) -> None:
        session = AsyncMock()
        inbound = _inbound(end_customer_balance=Decimal(500))

        entered = await maybe_enter_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert entered is False

    async def test_entra_no_gate_para_tenant_zapi_sem_saldo(self) -> None:
        """Paridade de provedor: Z-API entra no gate exatamente como Meta —
        o gate manda mensagem de lista via send-option-list da Z-API em vez
        da Cloud API da Meta (ver TestHandleBillingGateAbertura mais abaixo
        pro envio de fato)."""
        session = AsyncMock()
        inbound = _inbound(
            whatsapp_provider="zapi", conversation_state="agent", end_customer_balance=Decimal(0)
        )

        entered = await maybe_enter_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert entered is True
        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once()

    async def test_ja_em_billing_gate_com_provider_zapi_retorna_true_sem_reprocessar(self) -> None:
        """Espelha test_ja_em_billing_gate_retorna_true_sem_reprocessar_entrada
        pro provider Z-API — o curto-circuito de reentrada não depende do
        provedor."""
        session = AsyncMock()
        inbound = _inbound(
            whatsapp_provider="zapi",
            conversation_state="billing_gate",
            billing_gate_step="aguardando_pagamento",
        )

        entered = await maybe_enter_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert entered is True
        session.execute.assert_not_called()

    async def test_ja_em_billing_gate_retorna_true_sem_reprocessar_entrada(self) -> None:
        session = AsyncMock()
        inbound = _inbound(
            conversation_state="billing_gate", billing_gate_step="aguardando_pagamento"
        )

        entered = await maybe_enter_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert entered is True
        session.execute.assert_not_called()

    async def test_gate_ativo_mas_ja_isento_sai_do_gate_e_libera_o_turno(self) -> None:
        session = AsyncMock()
        inbound = _inbound(
            conversation_state="billing_gate",
            billing_gate_step="aguardando_pagamento",
            end_customer_billing_exempt=True,
        )

        entered = await maybe_enter_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert entered is False
        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once()

    async def test_nao_entra_quando_contato_esta_isento(self) -> None:
        session = AsyncMock()
        inbound = _inbound(end_customer_balance=Decimal(0), end_customer_billing_exempt=True)

        entered = await maybe_enter_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert entered is False
        session.execute.assert_not_called()

    async def test_gate_ativo_mas_assinante_ativo_sai_do_gate_e_libera_o_turno(self) -> None:
        """Espelha test_gate_ativo_mas_ja_isento_sai_do_gate_e_libera_o_turno:
        uma conversa presa em billing_gate (ex: webhook de notificação de
        cancelamento/renovação falhou em disparar a saída de estado, ou uma
        mensagem chega na corrida entre a assinatura ser commitada e o gate
        sair) precisa se auto-recuperar quando o contato já é assinante
        ativo — sem isso, o cliente é cobrado de novo até escalar pra human."""
        session = AsyncMock()
        inbound = _inbound(
            conversation_state="billing_gate",
            billing_gate_step="aguardando_pagamento",
            end_customer_has_active_subscription=True,
        )

        entered = await maybe_enter_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert entered is False
        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once()

    async def test_nao_entra_no_gate_com_assinatura_ativa(self) -> None:
        session = AsyncMock()
        inbound = _inbound(
            end_customer_balance=Decimal(0), end_customer_has_active_subscription=True
        )

        entered = await maybe_enter_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert entered is False


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


class TestPackagesToSections:
    def test_sem_assinatura_mantem_1_secao(self) -> None:
        packages = [
            {
                "id": "p1",
                "name": "Básico",
                "price_brl": "49.90",
                "kind": "one_time",
                "credits_granted": 500,
            },
        ]

        sections = _packages_to_sections(packages)

        assert len(sections) == 1
        assert sections[0]["title"] == "Pacotes disponíveis"
        assert sections[0]["rows"][0]["description"] == "R$ 49.90 = 500 créditos"

    def test_com_assinatura_gera_2_secoes(self) -> None:
        packages = [
            {
                "id": "p1",
                "name": "Básico",
                "price_brl": "49.90",
                "kind": "one_time",
                "credits_granted": 500,
            },
            {
                "id": "p2",
                "name": "Ilimitado",
                "price_brl": "99.90",
                "kind": "subscription",
                "credits_granted": None,
            },
        ]

        sections = _packages_to_sections(packages)

        assert len(sections) == 2
        assert sections[0]["title"] == "Pacotes de créditos"
        assert sections[0]["rows"][0]["description"] == "R$ 49.90 = 500 créditos"
        assert sections[1]["title"] == "Assinatura mensal"
        assert sections[1]["rows"][0]["description"] == "R$ 99.90/mês — conversas ilimitadas"

    def test_so_assinatura_sem_pacote_avulso(self) -> None:
        packages = [
            {
                "id": "p2",
                "name": "Ilimitado",
                "price_brl": "99.90",
                "kind": "subscription",
                "credits_granted": None,
            },
        ]

        sections = _packages_to_sections(packages)

        assert len(sections) == 1
        assert sections[0]["title"] == "Assinatura mensal"


class TestEnvioPorProvedorZApi:
    async def test_abertura_do_gate_usa_zapi_quando_provider_e_zapi(self, monkeypatch) -> None:
        session = AsyncMock()
        send_text = AsyncMock()
        send_list = AsyncMock()
        monkeypatch.setattr("app.billing_gate.send_zapi_text_message", send_text)
        monkeypatch.setattr("app.billing_gate.send_zapi_option_list", send_list)
        inbound = _inbound(
            whatsapp_provider="zapi",
            zapi_instance_id="inst-1",
            zapi_instance_token_encrypted="cifrado-token",
            zapi_client_token_encrypted=None,
            billing_gate_step=None,
        )

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        send_text.assert_awaited_once()
        assert send_text.await_args.kwargs["instance_id"] == "inst-1"
        assert send_text.await_args.kwargs["client_token"] is None
        send_list.assert_awaited_once()
        options = send_list.await_args.kwargs["options"]
        assert options[0]["title"] == "Básico"
        assert options[1]["title"] == "Premium"

    async def test_lista_zapi_achata_avulso_e_assinatura(self, monkeypatch) -> None:
        session = AsyncMock()
        monkeypatch.setattr("app.billing_gate.send_zapi_text_message", AsyncMock())
        send_list = AsyncMock()
        monkeypatch.setattr("app.billing_gate.send_zapi_option_list", send_list)
        packages = [
            {
                "id": "p1",
                "name": "Básico",
                "price_brl": "49.90",
                "kind": "one_time",
                "credits_granted": 500,
            },
            {
                "id": "p2",
                "name": "Ilimitado",
                "price_brl": "99.90",
                "kind": "subscription",
                "credits_granted": None,
            },
        ]
        inbound = _inbound(
            whatsapp_provider="zapi",
            zapi_instance_id="inst-1",
            zapi_instance_token_encrypted="cifrado-token",
            end_customer_packages=packages,
            billing_gate_step=None,
        )

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        options = send_list.await_args.kwargs["options"]
        assert len(options) == 2
        assert options[0]["title"] == "Básico"
        assert options[1]["title"] == "Ilimitado"

    async def test_selecao_valida_via_zapi_gera_link(self, monkeypatch) -> None:
        session = AsyncMock()
        send_text = AsyncMock()
        checkout = AsyncMock(return_value="https://checkout.stripe.com/xyz")
        monkeypatch.setattr("app.billing_gate.send_zapi_text_message", send_text)
        monkeypatch.setattr("app.billing_gate.create_end_customer_checkout", checkout)
        inbound = _inbound(
            whatsapp_provider="zapi",
            zapi_instance_id="inst-1",
            zapi_instance_token_encrypted="cifrado-token",
            billing_gate_step="aguardando_selecao_pacote",
            message_content="Básico",
        )

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        checkout.assert_awaited_once_with(
            tenant_id=TENANT_ID, contact_phone_number="5511999998888", package_id="pkg-1"
        )
        assert "https://checkout.stripe.com/xyz" in send_text.await_args.kwargs["text"]

    async def test_client_token_zapi_e_descriptografado_quando_presente(self, monkeypatch) -> None:
        session = AsyncMock()
        send_text = AsyncMock()
        monkeypatch.setattr("app.billing_gate.send_zapi_text_message", send_text)
        monkeypatch.setattr("app.billing_gate.send_zapi_option_list", AsyncMock())
        inbound = _inbound(
            whatsapp_provider="zapi",
            zapi_instance_id="inst-1",
            zapi_instance_token_encrypted="cifrado-token",
            zapi_client_token_encrypted="cifrado-client-token",
            billing_gate_step=None,
        )

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert send_text.await_args.kwargs["client_token"] == "token-claro"

    async def test_selecao_nao_reconhecida_via_zapi_reenvia_lista(self, monkeypatch) -> None:
        session = AsyncMock()
        send_text = AsyncMock()
        send_list = AsyncMock()
        monkeypatch.setattr("app.billing_gate.send_zapi_text_message", send_text)
        monkeypatch.setattr("app.billing_gate.send_zapi_option_list", send_list)
        inbound = _inbound(
            whatsapp_provider="zapi",
            zapi_instance_id="inst-1",
            zapi_instance_token_encrypted="cifrado-token",
            billing_gate_step="aguardando_selecao_pacote",
            message_content="não sei escolher",
            billing_gate_retries=0,
        )

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        send_text.assert_awaited_once()
        send_list.assert_awaited_once()

    async def test_aguardando_pagamento_via_zapi_reenvia_o_link(self, monkeypatch) -> None:
        session = AsyncMock()
        send_text = AsyncMock()
        monkeypatch.setattr("app.billing_gate.send_zapi_text_message", send_text)
        inbound = _inbound(
            whatsapp_provider="zapi",
            zapi_instance_id="inst-1",
            zapi_instance_token_encrypted="cifrado-token",
            billing_gate_step="aguardando_pagamento",
            billing_gate_checkout_url="https://checkout.stripe.com/xyz",
            billing_gate_retries=0,
        )

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert "https://checkout.stripe.com/xyz" in send_text.await_args.kwargs["text"]


class TestPackagesToFlatOptions:
    def test_achata_avulso_e_assinatura_avulso_primeiro(self) -> None:
        from app.billing_gate import _packages_to_flat_options

        packages = [
            {
                "id": "p2",
                "name": "Ilimitado",
                "price_brl": "99.90",
                "kind": "subscription",
                "credits_granted": None,
            },
            {
                "id": "p1",
                "name": "Básico",
                "price_brl": "49.90",
                "kind": "one_time",
                "credits_granted": 500,
            },
        ]

        options = _packages_to_flat_options(packages)

        assert [o["title"] for o in options] == ["Básico", "Ilimitado"]
        assert options[0]["description"] == "R$ 49.90 = 500 créditos"
        assert options[1]["description"] == "R$ 99.90/mês — conversas ilimitadas"

    def test_so_avulso(self) -> None:
        from app.billing_gate import _packages_to_flat_options

        packages = [
            {
                "id": "p1",
                "name": "Básico",
                "price_brl": "49.90",
                "kind": "one_time",
                "credits_granted": 500,
            },
        ]

        options = _packages_to_flat_options(packages)

        assert len(options) == 1
        assert options[0]["title"] == "Básico"
