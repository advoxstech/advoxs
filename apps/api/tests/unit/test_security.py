import jwt
import pytest

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


class TestPassword:
    def test_hash_e_verify(self) -> None:
        hashed = hash_password("senha-secreta")

        assert hashed != "senha-secreta"
        assert verify_password("senha-secreta", hashed)
        assert not verify_password("senha-errada", hashed)

    def test_hash_invalido_nao_explode(self) -> None:
        assert not verify_password("qualquer", "hash-invalido")


class TestTokens:
    def test_access_token_roundtrip(self) -> None:
        token = create_access_token("user-1", "tenant-1", "admin")
        payload = decode_token(token)

        assert payload["sub"] == "user-1"
        assert payload["tenant_id"] == "tenant-1"
        assert payload["role"] == "admin"
        assert payload["type"] == "access"

    def test_refresh_token_tem_jti_unico(self) -> None:
        payload_a = decode_token(create_refresh_token("user-1"))
        payload_b = decode_token(create_refresh_token("user-1"))

        assert payload_a["type"] == "refresh"
        assert payload_a["jti"] != payload_b["jti"]

    def test_assinatura_invalida_rejeitada(self) -> None:
        token = jwt.encode(
            {"sub": "user-1"}, "outro-segredo-tambem-com-32-bytes-ou-mais", algorithm="HS256"
        )

        with pytest.raises(jwt.PyJWTError):
            decode_token(token)

    def test_token_expirado_rejeitado(self, monkeypatch) -> None:
        from app.core.config import settings

        monkeypatch.setattr(settings, "jwt_access_token_expires_minutes", -1)
        token = create_access_token("user-1", "tenant-1", "admin")

        with pytest.raises(jwt.ExpiredSignatureError):
            decode_token(token)
