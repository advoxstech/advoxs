"""Offline regression coverage for all independently built service policies."""

import base64
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ("api", "worker", "agents", "api_rag")


def load_policy(service):
    spec = importlib.util.spec_from_file_location(
        f"policy_{service}", ROOT / "apps" / service / "config_validation.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProductionConfigTests(unittest.TestCase):
    def setUp(self):
        self.values = {
            "APP_ENV": "production",
            "JWT_SECRET": "a" * 48,
            "PLATFORM_JWT_SECRET": "b" * 48,
            "AGENTS_API_KEY": "c" * 48,
            "INTERNAL_SERVICE_KEY": "d" * 48,
            "RAG_API_KEY": "e" * 48,
            "API_KEY": "e" * 48,
            "WHATSAPP_TOKEN_ENCRYPTION_KEY": base64.urlsafe_b64encode(
                b"f" * 32
            ).decode(),
            "TENANT_STRIPE_KEY_ENCRYPTION_KEY": base64.urlsafe_b64encode(
                b"g" * 32
            ).decode(),
            "META_APP_SECRET": "h" * 32,
            "META_VERIFY_TOKEN": "i" * 32,
            "STRIPE_SECRET_KEY": "sk_live_" + "j" * 32,
            "STRIPE_WEBHOOK_SECRET": "whsec_" + "k" * 32,
            "STRIPE_CONNECT_SECRET_KEY": "rk_live_" + "l" * 32,
            "STRIPE_CONNECT_WEBHOOK_SECRET": "whsec_" + "m" * 32,
        }

    def test_valid_config_all_services(self):
        for service in SERVICES:
            load_policy(service).validate_config(self.values, service)

    def test_each_required_secret_missing_empty_or_placeholder(self):
        for service in SERVICES:
            policy = load_policy(service)
            required = list(policy.REQUIRED[service])
            if service == "api":
                required += [
                    key for keys in policy.INTEGRATIONS.values() for key in keys
                ]
            for key in required:
                for bad in (None, "", "  ", "changeme", "changeme-platform"):
                    values = {**self.values, key: bad}
                    with self.subTest(service=service, key=key, bad=bad):
                        with self.assertRaisesRegex(RuntimeError, key):
                            policy.validate_config(values, service)

    def test_invalid_fernet_and_equal_jwt(self):
        policy = load_policy("api")
        for key in (
            "WHATSAPP_TOKEN_ENCRYPTION_KEY",
            "TENANT_STRIPE_KEY_ENCRYPTION_KEY",
        ):
            for bad in ("malformed", base64.urlsafe_b64encode(b"short").decode()):
                with self.assertRaisesRegex(RuntimeError, key):
                    policy.validate_config({**self.values, key: bad}, "api")
        with self.assertRaisesRegex(RuntimeError, "JWT_SECRET"):
            policy.validate_config(
                {**self.values, "PLATFORM_JWT_SECRET": self.values["JWT_SECRET"]}, "api"
            )

    def test_disabled_integrations_do_not_require_their_keys(self):
        policy = load_policy("api")
        for flag, keys in policy.INTEGRATIONS.items():
            values = {**self.values, flag: "false"}
            for key in keys:
                values.pop(key)
            policy.validate_config(values, "api")
            values[flag] = "true"
            with self.assertRaises(RuntimeError):
                policy.validate_config(values, "api")

    def test_bad_environment_and_flags_rejected(self):
        for service in SERVICES:
            policy = load_policy(service)
            for bad in ("prod", "", " production", "PRODUCTION"):
                with self.assertRaisesRegex(RuntimeError, "APP_ENV"):
                    policy.validate_config({**self.values, "APP_ENV": bad}, service)
            with self.assertRaisesRegex(RuntimeError, "META_ENABLED"):
                policy.validate_config({**self.values, "META_ENABLED": "typo"}, service)

    def test_dev_and_test_allow_missing_security_secrets(self):
        for service in SERVICES:
            for environment in ("development", "test"):
                load_policy(service).validate_config({"APP_ENV": environment}, service)

    def test_failure_never_contains_values(self):
        values = {**self.values, "JWT_SECRET": "private-value"}
        with self.assertRaises(RuntimeError) as exc:
            load_policy("api").validate_config(values, "api")
        self.assertIn("JWT_SECRET", str(exc.exception))
        for value in values.values():
            if value != "production":
                self.assertNotIn(value, str(exc.exception))

    def test_policies_are_identical(self):
        contents = [
            (ROOT / "apps" / service / "config_validation.py").read_bytes()
            for service in SERVICES
        ]
        self.assertTrue(all(content == contents[0] for content in contents))


if __name__ == "__main__":
    unittest.main()
