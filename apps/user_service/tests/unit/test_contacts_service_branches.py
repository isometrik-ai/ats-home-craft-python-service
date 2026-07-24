"""Branch-focused unit tests for ContactsService remaining coverage gaps."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks

from apps.user_service.app.schemas.common import (
    AddressesUpdate,
    AddressUpdateItem,
    EducationalHistoryUpdate,
    EducationalHistoryUpdateItem,
    Phone,
)
from apps.user_service.app.schemas.contacts import (
    CommunicationPreferences,
    ContactCompanyAssociationCreate,
    ContactCompanyUpdate,
    UpdateContactRequest,
)
from apps.user_service.app.schemas.enums import (
    ClientStatus,
    ContactBloodGroup,
    ContactGender,
    ContactType,
)
from apps.user_service.app.services.contacts_service import ContactsService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ServiceUnavailableException,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
CONTACT_ID = "660e8400-e29b-41d4-a716-446655440001"
USER_ID = "770e8400-e29b-41d4-a716-446655440002"
COMPANY_ID = "880e8400-e29b-41d4-a716-446655440003"


def _ctx() -> UserContext:
    return UserContext(user_id="admin-1", email="admin@example.com", organization_id=ORG_ID)


def _service(**kwargs: Any) -> ContactsService:
    svc = ContactsService(
        db_connection=MagicMock(),
        user_context=kwargs.pop("user_context", _ctx()),
        supabase_client=kwargs.pop("supabase_client", MagicMock()),
    )
    for key, value in kwargs.items():
        setattr(svc, key, value)
    return svc


# --- static helpers / inference ---


@pytest.mark.parametrize(
    ("email", "expected_domain"),
    [
        ("not-an-email", None),
        ("user@localhost", None),
        ("user@gmail.com", "gmail.com"),
    ],
)
def test_extract_email_domain_branches(email: str, expected_domain: str | None) -> None:
    """Cover domain parsing edge cases."""
    assert ContactsService._extract_email_domain(email) == expected_domain


@pytest.mark.parametrize(
    ("email", "expected_company"),
    [
        ("user@gmail.com", None),
        ("user@mail.yahoo.co.in", None),
        ("rohit@appscrip.co", "appscrip"),
    ],
)
def test_infer_company_name_branches(email: str, expected_company: str | None) -> None:
    """Cover consumer-provider and registrable-domain inference branches."""
    assert ContactsService._infer_company_name_from_email(email) == expected_company


def test_isometrik_user_id_from_response_keys() -> None:
    """_isometrik_user_id_from_response checks userId, user_id, and id."""
    assert ContactsService._isometrik_user_id_from_response(None) is None
    assert ContactsService._isometrik_user_id_from_response({}) is None
    assert ContactsService._isometrik_user_id_from_response({"userId": "a"}) == "a"
    assert ContactsService._isometrik_user_id_from_response({"user_id": "b"}) == "b"
    assert ContactsService._isometrik_user_id_from_response({"id": "c"}) == "c"


def test_typesense_lazy_property() -> None:
    """typesense property lazily constructs TypesenseService."""
    svc = _service()
    with patch(
        "apps.user_service.app.services.contacts_service.TypesenseService.from_settings",
        return_value=MagicMock(name="typesense"),
    ) as from_settings:
        first = svc.typesense
        second = svc.typesense
    assert first is second
    from_settings.assert_called_once()


# --- isometrik / auth provisioning ---


@pytest.mark.asyncio
async def test_create_or_reuse_isometrik_conflict_login_success() -> None:
    """Conflict on create falls back to login response."""
    svc = _service()
    with (
        patch(
            "apps.user_service.app.services.contacts_service.create_isometrik_user",
            AsyncMock(side_effect=ConflictException(message_key="x")),
        ),
        patch(
            "apps.user_service.app.services.contacts_service.login_to_isometrik",
            AsyncMock(return_value={"userId": "iso-login"}),
        ),
    ):
        iso_id = await svc._create_or_reuse_isometrik_user(
            contact_id=CONTACT_ID,
            isometrik_payload={},
            isometrik_credentials={},
        )
    assert iso_id == "iso-login"


@pytest.mark.asyncio
async def test_create_or_reuse_isometrik_conflict_login_missing_id() -> None:
    """Login without user id raises ServiceUnavailableException."""
    svc = _service()
    with (
        patch(
            "apps.user_service.app.services.contacts_service.create_isometrik_user",
            AsyncMock(side_effect=ConflictException(message_key="x")),
        ),
        patch(
            "apps.user_service.app.services.contacts_service.login_to_isometrik",
            AsyncMock(return_value={}),
        ),
    ):
        with pytest.raises(ServiceUnavailableException):
            await svc._create_or_reuse_isometrik_user(
                contact_id=CONTACT_ID,
                isometrik_payload={},
                isometrik_credentials={},
            )


@pytest.mark.asyncio
async def test_create_or_reuse_isometrik_create_missing_id() -> None:
    """Create response without user id raises ServiceUnavailableException."""
    svc = _service()
    with patch(
        "apps.user_service.app.services.contacts_service.create_isometrik_user",
        AsyncMock(return_value={"status": "ok"}),
    ):
        with pytest.raises(ServiceUnavailableException):
            await svc._create_or_reuse_isometrik_user(
                contact_id=CONTACT_ID,
                isometrik_payload={},
                isometrik_credentials={},
            )


@pytest.mark.asyncio
async def test_create_or_reuse_isometrik_reuses_existing() -> None:
    """Existing isometrik user id is returned without API calls."""
    svc = _service()
    result = await svc._create_or_reuse_isometrik_user(
        contact_id=CONTACT_ID,
        isometrik_payload={},
        isometrik_credentials={},
        existing_isometrik_user_id="iso-existing",
    )
    assert result == "iso-existing"


@pytest.mark.asyncio
@patch("apps.user_service.app.services.contacts_service.UserRepository")
@patch("apps.user_service.app.services.contacts_service.create_user", new_callable=AsyncMock)
@patch("apps.user_service.app.services.contacts_service.get_isometrik_data_from_settings")
async def test_provision_auth_create_user_failure(
    mock_iso_settings: MagicMock,
    mock_create_user: AsyncMock,
    mock_user_repo_cls: MagicMock,
) -> None:
    """Auth user creation failure raises ServiceUnavailableException."""
    mock_user_repo_cls.return_value.get_auth_users_by_phone_or_email = AsyncMock(return_value=[])
    mock_create_user.return_value = None
    mock_iso_settings.return_value = {}
    org_repo = MagicMock()
    org_repo.get_organization_by_id = AsyncMock(return_value={"id": ORG_ID, "settings": "{}"})
    svc = _service(org_repo=org_repo)

    with pytest.raises(ServiceUnavailableException):
        await svc._provision_contact_auth_identity(
            contact_id=CONTACT_ID,
            first_name="Jane",
            last_name="Doe",
            prefix=None,
            phone="+911234567890",
            email="jane@example.com",
        )


@pytest.mark.asyncio
@patch("apps.user_service.app.services.contacts_service.UserRepository")
@patch("apps.user_service.app.services.contacts_service.get_user_by_id", new_callable=AsyncMock)
async def test_sync_auth_phone_missing_auth_user(
    mock_get_user: AsyncMock,
    mock_user_repo_cls: MagicMock,
) -> None:
    """Sync auth phone fails when Supabase user lookup returns nothing."""
    mock_user_repo_cls.return_value.get_auth_user_by_phone = AsyncMock(return_value=None)
    mock_get_user.return_value = None
    svc = _service()

    with pytest.raises(ServiceUnavailableException):
        await svc._sync_contact_auth_phone(
            user_id=USER_ID,
            phone=Phone(phone_number="9876543210", phone_isd_code="+91", is_primary=True),
        )


@pytest.mark.asyncio
@patch("apps.user_service.app.services.contacts_service.UserRepository")
@patch("apps.user_service.app.services.contacts_service.get_user_by_id", new_callable=AsyncMock)
@patch("apps.user_service.app.services.contacts_service.update_phone", new_callable=AsyncMock)
async def test_sync_auth_phone_update_failure(
    mock_update_phone: AsyncMock,
    mock_get_user: AsyncMock,
    mock_user_repo_cls: MagicMock,
) -> None:
    """Sync auth phone fails when Supabase update returns False."""
    mock_user_repo_cls.return_value.get_auth_user_by_phone = AsyncMock(return_value=None)
    mock_get_user.return_value = {"user_metadata": {}}
    mock_update_phone.return_value = False
    svc = _service()

    with pytest.raises(ServiceUnavailableException):
        await svc._sync_contact_auth_phone(
            user_id=USER_ID,
            phone=Phone(phone_number="9876543210", phone_isd_code="+91", is_primary=True),
        )


@pytest.mark.asyncio
async def test_sync_auth_phone_without_supabase() -> None:
    """Sync auth phone requires Supabase client."""
    svc = _service(supabase_client=None)
    with pytest.raises(ServiceUnavailableException):
        await svc._sync_contact_auth_phone(
            user_id=USER_ID,
            phone=Phone(phone_number="1", phone_isd_code="+1", is_primary=True),
        )


# --- phones / email / scheduling ---


@pytest.mark.asyncio
async def test_add_phones_skips_empty_and_duplicate() -> None:
    """add_phones_to_contact_if_missing ignores blank numbers and duplicates."""
    repo = MagicMock()
    repo.get_contact_phones_for_update = AsyncMock(
        return_value=[{"phone_number": "111", "phone_isd_code": "+1", "is_primary": False}]
    )
    repo.update_contact = AsyncMock(return_value={"id": CONTACT_ID})
    svc = _service(contacts_repo=repo)

    changed = await svc.add_phones_to_contact_if_missing(
        contact_id=CONTACT_ID,
        phones=[
            Phone(phone_number="   ", phone_isd_code="+1", is_primary=True),
            Phone(phone_number="111", phone_isd_code="+1", is_primary=False),
            Phone(phone_number="222", phone_isd_code="+1", is_primary=True),
        ],
    )

    assert changed is True
    repo.update_contact.assert_awaited_once()
    phones_arg = repo.update_contact.await_args.kwargs["update_data"]["phones"]
    assert "222" in str(phones_arg)


@pytest.mark.asyncio
async def test_add_phones_no_new_numbers_returns_false() -> None:
    """add_phones_to_contact_if_missing returns False when all numbers already exist."""
    repo = MagicMock()
    repo.get_contact_phones_for_update = AsyncMock(
        return_value=[{"phone_number": "111", "phone_isd_code": "+1", "is_primary": True}]
    )
    svc = _service(contacts_repo=repo)
    assert (
        await svc.add_phones_to_contact_if_missing(
            contact_id=CONTACT_ID,
            phones=[Phone(phone_number="111", phone_isd_code="+1", is_primary=False)],
        )
        is False
    )


@pytest.mark.asyncio
async def test_validate_custom_fields_skips_without_org() -> None:
    """Custom field validation is skipped without organization context."""
    svc = _service(user_context=UserContext(user_id="u1", email="x@y.com", organization_id=""))
    result = await svc._validate_custom_fields_for_create([{"field_id": "f1"}])
    assert result == []


def test_maybe_send_contact_creation_email_swallows_errors(monkeypatch) -> None:
    """Email send failures are logged but not raised."""
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.send_client_creation_email",
        MagicMock(side_effect=RuntimeError("smtp down")),
    )
    svc = _service()
    svc._maybe_send_contact_creation_email(
        portal_access=True,
        email="jane@example.com",
        organization_name="Acme",
        password="pass",
    )


@pytest.mark.asyncio
async def test_create_lifecycle_event_skips_none_event() -> None:
    """Lifecycle helper ignores entities whose event creation returns None."""
    event_service = MagicMock()
    event_service.create_lifecycle_event = AsyncMock(return_value=None)
    created = await ContactsService.create_lifecycle_events_for_created_entities(
        event_service=event_service,
        created_entities=[
            {"entity_id": CONTACT_ID, "entity_table": "contacts", "action": "create"}
        ],
        organization_id=ORG_ID,
        actor_user_id="admin",
    )
    assert created == []


def test_schedule_typesense_company_create_branch() -> None:
    """schedule_typesense_indexing schedules company indexing for create_company."""
    from apps.user_service.app.services.contacts_service import (
        index_companies_background,
    )

    bg = BackgroundTasks()
    ContactsService.schedule_typesense_indexing_for_created_entities(
        background_tasks=bg,
        created_entities=[
            {"entity_id": COMPANY_ID, "entity_table": "companies", "action": "create_company"}
        ],
        organization_id=ORG_ID,
    )
    assert len(bg.tasks) == 1
    assert bg.tasks[0].func is index_companies_background


def test_schedule_typesense_skips_missing_entity_id() -> None:
    """schedule_typesense_indexing ignores rows without entity_id."""
    bg = BackgroundTasks()
    ContactsService.schedule_typesense_indexing_for_created_entities(
        background_tasks=bg,
        created_entities=[{"entity_table": "contacts", "action": "create"}],
        organization_id=ORG_ID,
    )
    assert bg.tasks == []


# --- normalization / audit ---


def test_normalize_contact_audit_branches() -> None:
    """Audit snapshot normalization covers sales_intel and communication_preferences."""
    row = {
        "id": uuid.uuid4(),
        "organization_id": ORG_ID,
        "sales_intelligence": '{"score": 1}',
        "communication_preferences": '{"email": true}',
        "additional_data": "{}",
        "phones": "[]",
        "tags": '["vip"]',
        "companies": [],
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    normalized = ContactsService._normalize_contact_audit_snapshot(row)
    assert normalized is not None
    assert normalized["sales_intelligence"]["score"] == 1
    assert normalized["communication_preferences"]["email"] is True

    assert ContactsService._normalize_contact_audit_snapshot(None) is None

    bad_intel = {"sales_intelligence": 42}
    ContactsService._normalize_contact_sales_intelligence(bad_intel)
    assert bad_intel["sales_intelligence"] is None


def test_normalize_contact_details_coercion_branches() -> None:
    """Detail normalization covers string JSON, UUID coercion, and list rows."""
    svc = _service()
    details = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "phones": "[]",
        "notes": '[{"title":"T","content":"C"}]',
        "communication_preferences": '{"sms": true}',
        "additional_data": '{"k": "v"}',
        "tags": None,
        "skills": "[]",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
    }
    normalized = svc._normalize_contact_details(details)
    assert isinstance(normalized["id"], str)
    assert normalized["tags"] == []
    prefs = normalized["communication_preferences"]
    assert isinstance(prefs, dict)
    assert prefs["sms"] is True

    list_row = {
        "company_names": '["Acme"]',
        "phones": None,
        "tags": ("vip",),
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
    }
    ContactsService._normalize_contact_list_row(list_row)
    assert list_row["company_names"] == ["Acme"]
    assert list_row["phones"] == []
    assert list_row["tags"] == ["vip"]


# --- update_contact branches ---


@pytest.mark.asyncio
async def test_update_contact_all_scalar_fields_and_no_row(monkeypatch) -> None:
    """Scalar update builder and missing updated_row raise NotFound."""
    repo = MagicMock()
    repo.get_contact_for_update = AsyncMock(
        return_value={
            "id": CONTACT_ID,
            "organization_id": ORG_ID,
            "user_id": USER_ID,
            "phones": [],
            "addresses": [],
            "custom_fields": [],
        }
    )
    repo.update_contact = AsyncMock(return_value=None)
    svc = _service(contacts_repo=repo)
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.CustomFieldService",
        lambda **kwargs: MagicMock(merge_for_update=AsyncMock(return_value=[])),
    )

    body = UpdateContactRequest(
        contact_type=ContactType.OWNER,
        portal_access=True,
        additional_data={"k": "v"},
        sales_intelligence={"score": 1},
        gender=ContactGender.FEMALE,
        blood_group=ContactBloodGroup.A_POSITIVE,
        communication_preferences=CommunicationPreferences(),
        skills=["python"],
    )

    with pytest.raises(NotFoundException):
        await svc.update_contact(contact_id=CONTACT_ID, body=body)


@pytest.mark.asyncio
async def test_apply_jsonb_list_update_not_found() -> None:
    """Updating a missing JSONB list item raises NotFoundException."""
    svc = _service()
    update_obj = EducationalHistoryUpdate(
        update=[EducationalHistoryUpdateItem(id="missing-id", university="School")]
    )
    with pytest.raises(NotFoundException):
        await svc._apply_jsonb_list_changes(
            update_obj,
            current={"educational_history": []},
            payload={},
            field_name="educational_history",
            not_found_message_key="contacts.errors.educational_history_item_not_found",
        )


@pytest.mark.asyncio
async def test_apply_contact_addresses_update_appends_new_row() -> None:
    """Address update appends row when id was not in snapshot."""
    repo = MagicMock()
    repo.update_contact_address = AsyncMock(
        return_value={"id": "addr-new", "city": "Mumbai", "is_primary": False}
    )
    svc = _service(contacts_repo=repo)
    addresses = AddressesUpdate(update=[AddressUpdateItem(id="addr-new", city="Mumbai")])
    result = await svc._apply_contact_addresses_update(
        contact_id=CONTACT_ID,
        addresses=addresses,
        result_list=[],
    )
    assert len(result) == 1
    assert result[0]["city"] == "Mumbai"


@pytest.mark.asyncio
async def test_apply_companies_delta_without_snapshot_fetch() -> None:
    """Delta with no effective changes uses repo companies snapshot directly."""
    from types import SimpleNamespace

    repo = MagicMock()
    repo.get_contact_for_update = AsyncMock(return_value={"id": CONTACT_ID})
    cc_repo = MagicMock()
    cc_repo.apply_companies_update_delta = AsyncMock(
        return_value={"companies": [{"company_id": COMPANY_ID, "name": "Acme"}]}
    )
    svc = _service(contacts_repo=repo, cc_repo=cc_repo)

    delta = SimpleNamespace(
        remove_associations=[],
        add_associations=[],
        update_associations=[],
        create_and_associate=None,
    )
    result = await svc.apply_companies_update_delta(contact_id=CONTACT_ID, delta=delta)

    assert result["companies"][0]["name"] == "Acme"
    cc_repo.get_contact_companies_snapshot.assert_not_called()


# --- enrichment / background tasks ---


@pytest.mark.asyncio
async def test_trigger_enrichment_builds_payload_from_details(monkeypatch) -> None:
    """trigger_enrichment uses primary phone and country-bearing addresses."""
    svc = _service()
    svc.get_contact_details = AsyncMock(
        return_value={
            "organization_id": ORG_ID,
            "first_name": "Jane",
            "last_name": "Doe",
            "email": "jane@example.com",
            "addresses": [{"country": " IN "}, {"country": ""}, "bad"],
            "phones": [
                {"phone_number": "111", "phone_isd_code": "+1", "is_primary": False},
                {"phone_number": "222", "phone_isd_code": "+91", "is_primary": True},
            ],
        }
    )
    enrichment = MagicMock()
    enrichment.run_client_enrichment = AsyncMock()
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ClientEnrichmentService.from_settings",
        lambda: enrichment,
    )

    await svc.trigger_enrichment(contact_id=CONTACT_ID, organization_id=ORG_ID)

    payload = enrichment.run_client_enrichment.await_args.kwargs["payload_data"]
    assert payload["phone_number"] == "222"
    assert payload["addresses"] == [{"country": "IN"}]


@pytest.mark.asyncio
async def test_trigger_enrichment_falls_back_to_first_phone(monkeypatch) -> None:
    """trigger_enrichment uses first phone when none marked primary."""
    svc = _service()
    svc.get_contact_details = AsyncMock(
        return_value={
            "organization_id": ORG_ID,
            "first_name": "Jane",
            "phones": [{"phone_number": "999", "phone_isd_code": "+1"}],
            "addresses": [],
        }
    )
    enrichment = MagicMock()
    enrichment.run_client_enrichment = AsyncMock()
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ClientEnrichmentService.from_settings",
        lambda: enrichment,
    )

    await svc.trigger_enrichment(contact_id=CONTACT_ID, organization_id=ORG_ID)

    payload = enrichment.run_client_enrichment.await_args.kwargs["payload_data"]
    assert payload["phone_number"] == "999"


def test_schedule_contact_update_background_tasks_all_paths(monkeypatch) -> None:
    """Post-update scheduler publishes events, indexes, and enriches."""
    bg = BackgroundTasks()
    enrichment = MagicMock()
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ClientEnrichmentService.from_settings",
        lambda: enrichment,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.index_contacts_background",
        MagicMock(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.index_companies_background",
        MagicMock(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.EventService.publish_event_background",
        MagicMock(),
    )

    body = UpdateContactRequest(
        first_name="Updated",
        company_association=ContactCompanyUpdate(
            create_and_associate=ContactCompanyAssociationCreate(name="Acme", is_primary=False),
        ),
    )

    ContactsService.schedule_contact_update_background_tasks(
        background_tasks=bg,
        contact_id=CONTACT_ID,
        organization_id=ORG_ID,
        body=body,
        update_result={
            "companies_delta": {"affected_company_ids": [COMPANY_ID]},
            "created_company_id": COMPANY_ID,
        },
        update_event={"id": "evt-1"},
        event_key=CONTACT_ID,
        event_topics=[],
        related_lifecycle_events=[({"id": "evt-2"}, "key-2")],
    )

    assert len(bg.tasks) >= 4


@pytest.mark.asyncio
async def test_search_contacts_vector_with_distance_threshold(monkeypatch) -> None:
    """search_contacts adds distance_threshold to vector_query when configured."""
    mock_typesense = MagicMock()
    mock_typesense.embed_query_text = AsyncMock(return_value=[0.1, 0.2])
    mock_typesense.search = AsyncMock(return_value={"hits": [], "found": 0})
    svc = _service()
    svc._typesense = mock_typesense

    settings = MagicMock()
    settings.typesense.vector_distance_threshold = 0.25
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.shared_settings",
        settings,
    )

    await svc.search_contacts(query="Jane Doe", page=1, page_size=10, status=None)

    params = mock_typesense.search.await_args.args[0]
    assert "distance_threshold:0.25" in params["vector_query"]


@pytest.mark.asyncio
async def test_apply_inferred_company_existing_match() -> None:
    """Inferred company links to existing company by name."""
    companies_repo = MagicMock()
    companies_repo.get_company_ids_by_names = AsyncMock(return_value={"acme": COMPANY_ID})
    svc = _service(companies_repo=companies_repo)

    (
        company_id,
        company_data,
        addresses,
        make_primary,
    ) = await svc._apply_inferred_company_assoc_on_create(
        organization_id=ORG_ID,
        email_norm="user@acme.com",
        company_id=None,
        company_data=None,
        company_addresses=None,
        make_primary=True,
    )

    assert company_id == COMPANY_ID
    assert company_data is None
    assert make_primary is False


@pytest.mark.asyncio
async def test_apply_inferred_company_creates_payload() -> None:
    """Inferred company creates minimal company_data when no match exists."""
    companies_repo = MagicMock()
    companies_repo.get_company_ids_by_names = AsyncMock(return_value={})
    svc = _service(companies_repo=companies_repo)

    (
        company_id,
        company_data,
        addresses,
        make_primary,
    ) = await svc._apply_inferred_company_assoc_on_create(
        organization_id=ORG_ID,
        email_norm="user@newcorp.io",
        company_id=None,
        company_data=None,
        company_addresses=None,
        make_primary=True,
    )

    assert company_id is None
    assert company_data is not None
    assert company_data["status"] == ClientStatus.ACTIVE.value
    assert addresses == []
    assert make_primary is False
