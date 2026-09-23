import uuid

from app.safe_logging import safe_error, safe_identifier, safe_url


def test_safe_identifier_preserves_internal_uuid() -> None:
    value = str(uuid.uuid4())

    assert safe_identifier(value) == value


def test_safe_identifier_hides_external_identifier() -> None:
    value = "5511999999999"

    result = safe_identifier(value)

    assert result.startswith("ref:")
    assert value not in result


def test_safe_error_does_not_include_exception_message() -> None:
    assert safe_error(RuntimeError("token-secreto")) == "RuntimeError"


def test_safe_url_removes_credentials_path_and_query() -> None:
    result = safe_url("https://usuario:segredo@example.com:9443/token/abc?key=xyz")

    assert result == "https://example.com:9443/[redigido]"
    assert "segredo" not in result
    assert "abc" not in result
    assert "xyz" not in result


def test_safe_url_accepts_malformed_value_without_raising() -> None:
    assert safe_url("https://[") == "[url-redigida]"
