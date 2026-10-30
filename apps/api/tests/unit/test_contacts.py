import pytest
from pydantic import ValidationError

from app.schemas.contacts import ContactInput, TagInput


@pytest.mark.parametrize("phone", ["+55 (79) 9602-8480", "557996028480", "00557996028480"])
def test_phone_formats_match(phone):
    assert ContactInput(phone_number=phone).phone_number == "557996028480"


@pytest.mark.parametrize("phone", ["", "123", "phone12345678", "+0000", "1" * 16])
def test_invalid_phone_rejected(phone):
    with pytest.raises(ValidationError):
        ContactInput(phone_number=phone)


def test_no_silent_ninth_digit_insertion():
    assert ContactInput(phone_number="557996028480").phone_number != "5579996028480"


def test_blank_tag_rejected():
    with pytest.raises(ValidationError):
        TagInput(name="   ")
