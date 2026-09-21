"""Production configuration policy, mirrored across independently built services.

Keep the four copies identical; tests/test_production_config.py checks parity.
Validation is offline and never prints secret values.
"""

import base64
import binascii
import os
import sys
from collections.abc import Mapping

REQUIRED = {
    "api": (
        "JWT_SECRET",
        "PLATFORM_JWT_SECRET",
        "AGENTS_API_KEY",
        "INTERNAL_SERVICE_KEY",
        "RAG_API_KEY",
        "WHATSAPP_TOKEN_ENCRYPTION_KEY",
        "TENANT_STRIPE_KEY_ENCRYPTION_KEY",
    ),
    "worker": (
        "AGENTS_API_KEY",
        "INTERNAL_SERVICE_KEY",
        "RAG_API_KEY",
        "WHATSAPP_TOKEN_ENCRYPTION_KEY",
    ),
    "agents": ("AGENTS_API_KEY", "RAG_API_KEY"),
    "api_rag": ("API_KEY",),
}
INTEGRATIONS = {
    "STRIPE_ENABLED": ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"),
    "STRIPE_CONNECT_ENABLED": ("STRIPE_CONNECT_SECRET_KEY", "STRIPE_CONNECT_WEBHOOK_SECRET"),
}


def production_enforcement_enabled(values: Mapping) -> bool:
    """Retorna se a exigência de segredos em produção está ativa.

    O bloqueio fica desligado temporariamente por padrão enquanto o servidor
    ainda não tem os segredos configurados. Ao preenchê-los, habilite
    ENFORCE_PRODUCTION_CONFIG=true para restaurar a política completa.
    """
    value = str(values.get("ENFORCE_PRODUCTION_CONFIG", "false")).strip().lower()
    if value in {"true", "1"}:
        return True
    if value in {"false", "0"}:
        return False
    raise RuntimeError("Configuração inválida: ENFORCE_PRODUCTION_CONFIG (use true ou false)")


def integration_enabled(values: Mapping, name: str) -> bool:
    value = str(values.get(name, "true")).strip().lower()
    if value in {"true", "1"}:
        return True
    if value in {"false", "0"}:
        return False
    raise RuntimeError(f"Configuração inválida: {name} (use true ou false)")


def validate_config(values: Mapping, service: str) -> None:
    environment = str(values.get("APP_ENV", "development"))
    if environment not in {"development", "test", "production"}:
        raise RuntimeError("Configuração inválida: APP_ENV")
    enabled = {name: integration_enabled(values, name) for name in INTEGRATIONS}
    if environment != "production" or not production_enforcement_enabled(values):
        return

    required = list(REQUIRED[service])
    if service == "api":
        for name, keys in INTEGRATIONS.items():
            if enabled[name]:
                required.extend(keys)

    invalid = set()
    for name in required:
        value = str(values.get(name) or "")
        if (
            not value.strip()
            or value != value.strip()
            or value.lower().startswith(("changeme", "replace-me", "your-"))
        ):
            invalid.add(name)
        if name.endswith("ENCRYPTION_KEY"):
            try:
                decoded = base64.b64decode(value, altchars=b"-_", validate=True)
                if len(decoded) != 32:
                    invalid.add(name)
            except (ValueError, binascii.Error):
                invalid.add(name)
        if name in {"JWT_SECRET", "PLATFORM_JWT_SECRET"} and len(value) < 32:
            invalid.add(name)

    if service == "api" and values.get("JWT_SECRET") == values.get("PLATFORM_JWT_SECRET"):
        invalid.update(("JWT_SECRET", "PLATFORM_JWT_SECRET"))
    if invalid:
        raise RuntimeError("Configuração de produção inválida: " + ", ".join(sorted(invalid)))


def validate_environment(service: str) -> None:
    from dotenv import load_dotenv

    load_dotenv()
    validate_config(os.environ, service)


if __name__ == "__main__":
    try:
        validate_environment(sys.argv[1])
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print("Configuração de segurança válida")
