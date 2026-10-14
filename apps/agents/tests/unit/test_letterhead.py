import uuid

from services import letterhead


def test_letterhead_uses_only_the_conversation_office(tmp_path, monkeypatch):
    monkeypatch.setattr(letterhead, "LETTERHEAD_DIR", str(tmp_path))
    first, second = uuid.uuid4(), uuid.uuid4()
    (tmp_path / f"{first}.pdf").write_bytes(b"%PDF-1.4 first")
    assert letterhead.letterhead_for_conversation(f"{first}:5511999999999") == b"%PDF-1.4 first"
    assert letterhead.letterhead_for_conversation(f"{second}:5511999999999") is None
    assert letterhead.letterhead_for_conversation("../wrong:5511999999999") is None
