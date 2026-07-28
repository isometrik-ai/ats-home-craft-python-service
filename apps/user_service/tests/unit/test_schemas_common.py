"""Unit tests for shared common schema validators."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.user_service.app.schemas.common import (
    AddressesUpdate,
    AddressInput,
    NoteItem,
    PhoneInput,
    PhonesUpdate,
    WebsiteInput,
    WebsitesUpdate,
)
from libs.shared_utils.http_exceptions import ValidationException


def test_note_item_strips_whitespace():
    """Note title and content are stripped on validation."""
    note = NoteItem(title="  Title  ", content="  Body  ")
    assert note.title == "Title"
    assert note.content == "Body"


def test_note_item_rejects_empty_after_strip():
    """Blank note fields fail validation after stripping."""
    with pytest.raises(ValidationError):
        NoteItem(title="   ", content="Body")
    with pytest.raises(ValidationError):
        NoteItem(title="Title", content="")


def test_address_input_coerces_null_address_data():
    """Explicit null address_data becomes an empty dict."""
    address = AddressInput.model_validate({"country": "US", "address_data": None})
    assert address.address_data == {}


def test_phones_update_rejects_multiple_primary_on_add():
    """Only one primary phone may be added in a batch."""
    with pytest.raises(ValidationException) as exc_info:
        PhonesUpdate(
            add=[
                PhoneInput(phone_number="111", phone_isd_code="+1", is_primary=True),
                PhoneInput(phone_number="222", phone_isd_code="+1", is_primary=True),
            ]
        )
    assert exc_info.value.message_key == "clients.errors.only_one_primary_phone"


def test_phones_update_allows_single_primary_on_add():
    """Single primary phone in add batch passes validation."""
    payload = PhonesUpdate(
        add=[PhoneInput(phone_number="111", phone_isd_code="+1", is_primary=True)]
    )
    assert payload.add is not None
    assert payload.add[0].is_primary is True


def test_phones_update_allows_empty_update_list():
    """Empty or omitted update lists pass primary validation."""
    assert PhonesUpdate(update=None) is not None
    assert PhonesUpdate(update=[]) is not None


def test_phones_update_rejects_multiple_primary_on_update():
    """Only one primary phone may be marked on update."""
    from apps.user_service.app.schemas.common import PhoneUpdateItem

    with pytest.raises(ValidationException) as exc_info:
        PhonesUpdate(
            update=[
                PhoneUpdateItem(id="p1", is_primary=True),
                PhoneUpdateItem(id="p2", is_primary=True),
            ]
        )
    assert exc_info.value.message_key == "clients.errors.only_one_primary_phone"


def test_addresses_update_rejects_multiple_primary_across_add_and_update():
    """Only one primary address is allowed across add and update."""
    from apps.user_service.app.schemas.common import AddressUpdateItem

    with pytest.raises(ValidationException) as exc_info:
        AddressesUpdate(
            add=[AddressInput(country="US", is_primary=True)],
            update=[AddressUpdateItem(id="a1", is_primary=True)],
        )
    assert exc_info.value.message_key == "clients.errors.only_one_primary_address"


def test_websites_update_rejects_multiple_primary_on_add():
    """Only one primary website may be added in a batch."""
    with pytest.raises(ValidationException) as exc_info:
        WebsitesUpdate(
            add=[
                WebsiteInput(url="https://a.example", type="work", is_primary=True),
                WebsiteInput(url="https://b.example", type="home", is_primary=True),
            ]
        )
    assert exc_info.value.message_key == "clients.errors.only_one_primary_website"


def test_addresses_update_allows_single_primary():
    """Single primary address across add/update passes validation."""
    payload = AddressesUpdate(add=[AddressInput(country="US", is_primary=True)])
    assert payload.add[0].is_primary is True


def test_websites_update_allows_single_primary_on_add():
    """Single primary website in add batch passes validation."""
    payload = WebsitesUpdate(
        add=[WebsiteInput(url="https://a.example", type="work", is_primary=True)]
    )
    assert payload.add[0].is_primary is True


def test_websites_update_allows_empty_update_list():
    """Empty or omitted website update lists pass primary validation."""
    assert WebsitesUpdate(update=None) is not None
    assert WebsitesUpdate(update=[]) is not None


def test_websites_update_rejects_multiple_primary_on_update():
    """Only one primary website may be marked on update."""
    from apps.user_service.app.schemas.common import WebsiteUpdateItem

    with pytest.raises(ValidationException) as exc_info:
        WebsitesUpdate(
            update=[
                WebsiteUpdateItem(id="w1", is_primary=True),
                WebsiteUpdateItem(id="w2", is_primary=True),
            ]
        )
    assert exc_info.value.message_key == "clients.errors.only_one_primary_website"
