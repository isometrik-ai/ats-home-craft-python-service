"""Additional unit tests to close remaining coverage gaps toward 90%."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from asyncpg import UniqueViolationError

from apps.user_service.app.schemas.common import Phone
from apps.user_service.app.schemas.companies import CreateCompanyRequest
from apps.user_service.app.schemas.contacts import (
    ContactCompaniesCreate,
    ContactCompanyAssociationAdd,
    ContactCompanyAssociationCreateInline,
    CreateContactRequest,
)
from apps.user_service.app.schemas.enums import DealType, Priority
from apps.user_service.app.schemas.leads import (
    CreateLeadCompany,
    CreateLeadRequest,
    LeadCompaniesUpdate,
    LeadCompanyAssociationUpdate,
    LeadCompanyCreate,
    UpdateLeadRequest,
)
from apps.user_service.app.services.auth_service import AuthService
from apps.user_service.app.services.contacts_service import ContactsService
from apps.user_service.app.services.email_notification_service import (
    EmailNotificationService,
    _extract_body,
    _first_normalized_address,
    _parse_attachments,
    _parse_recipients,
    _parse_reference_time,
    _resolve_thread_id,
    build_inbound_email_record,
    extract_sender_email,
)
from apps.user_service.app.services.lead_service import LeadService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ConflictException,
    DuplicateValueException,
    NotFoundException,
    ValidationException,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
STAGE_ID = "990e8400-e29b-41d4-a716-446655440004"
LEAD_ID = "66666666-6666-6666-6666-666666666666"
CONTACT_ID = "660e8400-e29b-41d4-a716-446655440001"
COMPANY_ID = "880e8400-e29b-41d4-a716-446655440003"
OWNER_ID = "770e8400-e29b-41d4-a716-446655440002"


def _ctx() -> UserContext:
    return UserContext(user_id=OWNER_ID, email="admin@example.com", organization_id=ORG_ID)


def _auth_service() -> AuthService:
    svc = AuthService.__new__(AuthService)
    svc.db_connection = MagicMock()
    svc.user_repository = MagicMock()
    svc.organization_repository = MagicMock()
    svc.supabase_client = MagicMock()
    svc.supabase_admin_client = MagicMock()
    return svc


# --- auth_service ---


def test_validate_password_strength_empty():
    """Empty password raises ValidationException."""
    with pytest.raises(ValidationException):
        _auth_service()._validate_password_strength("   ")  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_validate_verification_code_for_signup_not_found(monkeypatch):
    """Signup verification fails when record is missing."""
    svc = _auth_service()
    fake_repo = MagicMock()
    fake_repo.get_verification_code_by_id = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.VerificationCodeService",
        lambda db_connection=None, sb_client=None: SimpleNamespace(
            verification_code_repository=fake_repo
        ),
    )

    with pytest.raises(NotFoundException):
        await svc._validate_verification_code_for_signup("ver-1", "a@b.com", "123456")


@pytest.mark.asyncio
async def test_validate_verification_code_for_signup_not_verified(monkeypatch):
    """Signup verification fails when code is not verified."""
    svc = _auth_service()
    fake_repo = MagicMock()
    fake_repo.get_verification_code_by_id = AsyncMock(
        return_value={"verified": False, "given_input": "a@b.com"}
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.VerificationCodeService",
        lambda db_connection=None, sb_client=None: SimpleNamespace(
            verification_code_repository=fake_repo
        ),
    )

    with pytest.raises(BadRequestException):
        await svc._validate_verification_code_for_signup("ver-1", "a@b.com", "123456")


@pytest.mark.asyncio
async def test_get_and_validate_verification_record_missing_input(monkeypatch):
    """Verification record without given_input is rejected."""
    svc = _auth_service()
    fake_repo = MagicMock()
    fake_repo.get_verification_code_by_id = AsyncMock(return_value={"given_input": None})
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.VerificationCodeService",
        lambda db_connection=None, sb_client=None: SimpleNamespace(
            verification_code_repository=fake_repo
        ),
    )

    with pytest.raises(BadRequestException):
        await svc._get_and_validate_verification_record("ver-1")


def test_validate_phone_match_mismatch():
    """Phone mismatch raises BadRequestException."""
    with pytest.raises(BadRequestException):
        AuthService._validate_phone_match("+911111111111", "+922222222222")


def test_validate_phone_match_normalized_equal():
    """Phone match accepts equivalent numbers with different plus prefixes."""
    AuthService._validate_phone_match("911111111111", "+911111111111")


# --- email_notification helpers ---


def test_first_normalized_address_list_skips_invalid():
    """Address list skips entries without @."""
    assert _first_normalized_address(["invalid", "user@example.com"]) == "user@example.com"
    assert _first_normalized_address(["invalid"]) is None


def test_extract_sender_email_from_key():
    """Sender can be resolved from message.from."""
    assert (
        extract_sender_email({"message": {"from": "User <user@example.com>"}}) == "user@example.com"
    )


def test_extract_body_truncates_long_text():
    """Body extraction truncates very long messages."""
    long_text = "x" * 200_000
    body = _extract_body({"extracted_text": long_text})
    assert len(body) == 100_000


def test_parse_recipients_string_and_list():
    """Recipient parser handles string and list forms."""
    assert _parse_recipients(["a@b.com", ""]) == ("a@b.com",)
    assert _parse_recipients("a@b.com") == ("a@b.com",)


def test_parse_attachments_skips_invalid():
    """Attachment parser ignores invalid entries."""
    assert _parse_attachments({"attachments": [None, {"attachment_id": ""}]}) == ()


def test_resolve_thread_id_from_message():
    """Thread id falls back to message.thread_id."""
    assert _resolve_thread_id({"thread": {}}, {"thread_id": "t-1"}) == "t-1"


def test_parse_reference_time_iso_and_fallback():
    """Reference time parser handles ISO strings and defaults."""
    dt = _parse_reference_time("2026-04-15T05:40:57.000Z")
    assert dt.tzinfo is not None
    assert _parse_reference_time(None) <= datetime.now(timezone.utc)


def test_build_record_from_smtp_id():
    """Record builder accepts smtp_id when message_id missing."""
    record = build_inbound_email_record(
        webhook_body={"message": {"smtp_id": "smtp-1", "extracted_text": "hi"}},
        sender_email="user@example.com",
        contact_id=CONTACT_ID,
    )
    assert record is not None
    assert record.message_id == "smtp-1"


# --- lead_service ---


def _lead_service(lead_repo: MagicMock) -> LeadService:
    return LeadService(
        db_connection=MagicMock(),
        user_context=_ctx(),
        lead_repository=lead_repo,
        user_repository=MagicMock(get_user_details_by_id=AsyncMock(return_value={"id": OWNER_ID})),
    )


@pytest.mark.asyncio
async def test_create_lead_duplicate_company_violation(monkeypatch):
    """UniqueViolation on lead_companies maps to duplicate company error."""
    lead_repo = MagicMock()
    lead_repo.fetch_lead_reference_validation = AsyncMock(return_value=(True, set(), set()))
    exc = UniqueViolationError("duplicate")
    exc.constraint_name = "uq_lead_company_x"
    lead_repo.create_lead = AsyncMock(side_effect=exc)
    monkeypatch.setattr(
        "apps.user_service.app.services.lead_service.CustomFieldService",
        lambda db_connection=None, user_context=None: SimpleNamespace(
            validate_for_create=AsyncMock(return_value=[])
        ),
    )

    with pytest.raises(DuplicateValueException):
        await _lead_service(lead_repo).create_lead(
            CreateLeadRequest(name="Lead", stage_id=STAGE_ID)
        )


@pytest.mark.asyncio
async def test_create_lead_external_owner_none(monkeypatch):
    """External create resolves owner_id to None."""
    lead_repo = MagicMock()
    lead_repo.fetch_lead_reference_validation = AsyncMock(return_value=(True, set(), set()))
    lead_repo.create_lead = AsyncMock(return_value={"id": LEAD_ID})
    monkeypatch.setattr(
        "apps.user_service.app.services.lead_service.CustomFieldService",
        lambda db_connection=None, user_context=None: SimpleNamespace(
            validate_for_create=AsyncMock(return_value=[])
        ),
    )

    await _lead_service(lead_repo).create_lead(
        CreateLeadRequest(name="Lead", stage_id=STAGE_ID, owner_id=OWNER_ID),
        external=True,
    )

    assert lead_repo.create_lead.await_args.args[0]["owner_id"] is None


def test_apply_company_delta_update_and_duplicate():
    """Company delta update label and duplicate detection."""
    svc = LeadService(db_connection=MagicMock(), user_context=_ctx())
    current = {"companies": [{"company_id": COMPANY_ID, "label": "old"}]}

    payload, ids = svc._apply_company_delta(  # pylint: disable=protected-access
        current=current,
        delta=LeadCompaniesUpdate(
            update_associations=[LeadCompanyAssociationUpdate(company_id=COMPANY_ID, label="new")]
        ),
    )
    assert payload[0]["label"] == "new"
    assert COMPANY_ID in ids

    with pytest.raises(ValidationException):
        svc._apply_company_delta(  # pylint: disable=protected-access
            current=current,
            delta=LeadCompaniesUpdate(
                add_associations=[LeadCompanyCreate(company_id=COMPANY_ID, label="dup")]
            ),
        )


@pytest.mark.asyncio
async def test_update_lead_scalar_deal_type_and_priority():
    """Scalar PATCH maps deal_type and priority enums to values."""
    lead_repo = MagicMock()
    lead_repo.get_lead_detail_by_id = AsyncMock(
        return_value={"id": LEAD_ID, "contacts": [], "companies": [], "custom_fields": []}
    )
    lead_repo.update_lead_with_associations = AsyncMock(
        return_value={"id": LEAD_ID, "deal_type": "new_business"}
    )
    svc = _lead_service(lead_repo)

    await svc.update_lead(
        LEAD_ID,
        UpdateLeadRequest(deal_type=DealType.NEW_BUSINESS, priority=Priority.HIGH),
    )

    update_data = lead_repo.update_lead_with_associations.await_args.args[2]
    assert update_data["deal_type"] == DealType.NEW_BUSINESS.value
    assert update_data["priority"] == Priority.HIGH.value


# --- contacts_service ---


@pytest.mark.asyncio
async def test_prepare_company_association_add_existing(monkeypatch):
    """_prepare_optional_contact_company_association handles add_association."""
    svc = ContactsService(db_connection=MagicMock(), user_context=_ctx())
    body = CreateContactRequest(
        first_name="Jane",
        email="jane@example.com",
        phones=[Phone(phone_number="9876543210", phone_isd_code="+91", is_primary=True)],
        company_association=ContactCompaniesCreate(
            add_association=ContactCompanyAssociationAdd(
                company_id=COMPANY_ID,
                is_primary=True,
            )
        ),
    )

    (
        company_id,
        company_data,
        addresses,
        make_primary,
    ) = await svc._prepare_optional_contact_company_association(body=body)

    assert company_id == COMPANY_ID
    assert company_data is None
    assert make_primary is True


@pytest.mark.asyncio
async def test_prepare_company_association_inline_create(monkeypatch):
    """Inline company create validates custom fields and builds payload."""
    svc = ContactsService(db_connection=MagicMock(), user_context=_ctx())
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.CustomFieldService",
        lambda db_connection=None, user_context=None: SimpleNamespace(
            validate_for_create=AsyncMock(return_value=[{"field_id": "f1", "value": "x"}])
        ),
    )
    body = CreateContactRequest(
        first_name="Jane",
        email="jane@example.com",
        phones=[Phone(phone_number="9876543210", phone_isd_code="+91", is_primary=True)],
        company_association=ContactCompaniesCreate(
            create_and_associate=ContactCompanyAssociationCreateInline(
                is_primary=False,
                company=CreateCompanyRequest(name="Acme Corp"),
            )
        ),
    )

    (
        company_id,
        company_data,
        _,
        make_primary,
    ) = await svc._prepare_optional_contact_company_association(body=body)

    assert company_id is None
    assert company_data is not None
    assert company_data["name"] == "Acme Corp"
    assert make_primary is False


@pytest.mark.asyncio
async def test_prepare_company_association_invalid():
    """Missing inline company payload raises ValidationException."""
    svc = ContactsService(db_connection=MagicMock(), user_context=_ctx())
    body = CreateContactRequest.model_construct(
        first_name="Jane",
        phones=[],
        company_association=ContactCompaniesCreate.model_construct(
            add_association=None,
            create_and_associate=ContactCompanyAssociationCreateInline.model_construct(
                is_primary=False,
                company=None,
            ),
        ),
    )

    with pytest.raises(ValidationException):
        await svc._prepare_optional_contact_company_association(body=body)


# --- audit decorator error handlers ---


@pytest.mark.asyncio
async def test_maybe_log_audit_on_error_json_decode(monkeypatch):
    """JSONDecodeError during error audit is swallowed."""
    from starlette.requests import Request

    from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
        maybe_log_audit_on_error,
    )

    req = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    req.state.audit_user_context = {
        "organization_id": ORG_ID,
        "user_id": OWNER_ID,
        "user_email": "admin@example.com",
    }

    with patch(
        "apps.user_service.app.dependencies.audit_logs.audit_decorator._extract_request_body",
        AsyncMock(side_effect=ValueError("bad data")),
    ):
        await maybe_log_audit_on_error(req, description="fail")


@pytest.mark.asyncio
async def test_process_skips_org_memory_disabled():
    """Organization memory disabled skips inbound email ingestion."""
    service = EmailNotificationService(db_connection=MagicMock(), graphiti=MagicMock())
    with (
        patch(
            "apps.user_service.app.services.email_notification_service.is_graphiti_configured",
            return_value=True,
        ),
        patch(
            "apps.user_service.app.services.email_notification_service.is_organization_memory_enabled",
            new=AsyncMock(return_value=False),
        ),
    ):
        result = await service.process_message_received(
            organization_id=ORG_ID,
            webhook_body={"event_type": "message.received", "message": {"from_": "a@b.com"}},
        )
    assert result.skipped_reason == "organization_memory_disabled"


@pytest.mark.asyncio
async def test_fetch_attachment_blocks_downloads_bytes():
    """Configured AgentMail downloads attachment bytes."""
    from apps.user_service.app.services.email_notification_service import (
        InboundEmailRecord,
    )

    agentmail = MagicMock()
    agentmail.is_configured = True
    agentmail.fetch_message_attachment = AsyncMock(return_value=b"hello")

    service = EmailNotificationService(db_connection=MagicMock(), agentmail=agentmail)
    record = InboundEmailRecord(
        message_id="m1",
        contact_id=CONTACT_ID,
        from_email="user@example.com",
        body="hello",
        subject="Hi",
        from_header=None,
        to=(),
        thread_id=None,
        inbox_id="inbox-1",
        received_at=None,
        attachments=({"attachment_id": "att-1", "filename": "a.txt"},),
    )

    with patch(
        "apps.user_service.app.services.email_notification_service.format_attachment_block",
        return_value="ATT:block",
    ):
        blocks = await service._fetch_attachment_blocks(record)

    assert blocks == ["ATT:block"]
    agentmail.fetch_message_attachment.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_custom_fields_skips_without_org():
    """Lead custom fields are skipped when organization context is absent."""
    svc = LeadService(
        db_connection=MagicMock(),
        user_context=UserContext(user_id=OWNER_ID, email="x@y.com", organization_id=""),
        lead_repository=MagicMock(),
    )
    body = CreateLeadRequest(name="Lead", stage_id=STAGE_ID)
    lead_row: dict = {}
    await svc._apply_custom_fields_if_needed(lead_row, body)  # pylint: disable=protected-access
    assert lead_row == {}


# --- contacts_service module helpers (branch coverage) ---


def test_contacts_module_helpers_branches():
    """Cover JSON/phone helper branches in contacts_service."""
    from apps.user_service.app.schemas.common import Phone
    from apps.user_service.app.services.contacts_service import (
        _contact_phone_sync_info,
        _get_primary_phone_identity,
        _normalize_phone_item,
        _primary_phone_changed,
        _serialize_jsonb_list,
    )

    assert _serialize_jsonb_list(None) == []
    assert (
        _serialize_jsonb_list([Phone(phone_number="1", phone_isd_code="+1")])[0]["phone_number"]
        == "1"
    )
    assert _serialize_jsonb_list([{"a": 1}]) == [{"a": 1}]

    assert (
        _normalize_phone_item(Phone(phone_number="1", phone_isd_code="+1"))["phone_number"] == "1"
    )
    assert _normalize_phone_item({"phone_number": "2"})["phone_number"] == "2"
    assert _normalize_phone_item("bad") == {}

    phones = [Phone(phone_number="9", phone_isd_code="+1", is_primary=True)]
    assert _get_primary_phone_identity(phones) == ("+1", "9")
    assert _get_primary_phone_identity([]) is None

    old = [{"phone_isd_code": "+1", "phone_number": "1", "is_primary": True}]
    new = [Phone(phone_number="2", phone_isd_code="+1", is_primary=True)]
    assert _primary_phone_changed(old, new) is True
    assert _primary_phone_changed(old, old) is False

    sync, primary = _contact_phone_sync_info(
        current={"user_id": "u1", "phones": old},
        phones=new,
    )
    assert sync is True
    assert primary is not None

    sync2, primary2 = _contact_phone_sync_info(
        current={"phones": old},
        phones=new,
    )
    assert sync2 is False
    assert primary2 is None


@pytest.mark.asyncio
async def test_validate_verification_code_email_and_code_mismatch(monkeypatch):
    """Signup verification rejects email and code mismatches."""
    svc = _auth_service()
    fake_repo = MagicMock()
    fake_repo.get_verification_code_by_id = AsyncMock(
        return_value={
            "verified": True,
            "given_input": "other@example.com",
            "verification_code": "999999",
        }
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.VerificationCodeService",
        lambda db_connection=None, sb_client=None: SimpleNamespace(
            verification_code_repository=fake_repo
        ),
    )

    with pytest.raises(BadRequestException):
        await svc._validate_verification_code_for_signup("ver-1", "a@b.com", "123456")

    fake_repo.get_verification_code_by_id = AsyncMock(
        return_value={
            "verified": True,
            "given_input": "a@b.com",
            "verification_code": "999999",
        }
    )
    with pytest.raises(BadRequestException):
        await svc._validate_verification_code_for_signup("ver-1", "a@b.com", "123456")


def test_auth_extract_session_branches():
    """_extract_session returns session only when access_token exists."""
    assert AuthService._extract_session(None) is None
    assert AuthService._extract_session(object()) is None
    assert AuthService._extract_session(SimpleNamespace(access_token="tok")).access_token == "tok"


@pytest.mark.asyncio
async def test_create_lead_missing_company_raises(monkeypatch):
    """Missing linked company raises NotFoundException."""
    lead_repo = MagicMock()
    lead_repo.fetch_lead_reference_validation = AsyncMock(
        return_value=(True, set(), {"missing-co"})
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.lead_service.CustomFieldService",
        lambda db_connection=None, user_context=None: SimpleNamespace(
            validate_for_create=AsyncMock(return_value=[])
        ),
    )

    with pytest.raises(NotFoundException):
        await _lead_service(lead_repo).create_lead(
            CreateLeadRequest(
                name="Lead",
                stage_id=STAGE_ID,
                company=CreateLeadCompany(company_id=COMPANY_ID),
            )
        )


@pytest.mark.asyncio
async def test_unit_configs_unique_violation_on_create():
    """Duplicate config code raises ConflictException."""
    from asyncpg import UniqueViolationError

    from apps.user_service.app.schemas.enums import PlotType, UnitConfigKind
    from apps.user_service.app.schemas.project_inventory import CreateUnitConfigRequest
    from apps.user_service.app.services.unit_configs_service import UnitConfigsService

    svc = UnitConfigsService(db_connection=MagicMock(), user_context=_ctx())
    svc.configs_repo = MagicMock()
    svc.configs_repo.insert_config = AsyncMock(side_effect=UniqueViolationError("dup"))
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock(return_value={"id": "p1"})

    with pytest.raises(ConflictException):
        await svc.create_config(
            project_id="p1",
            body=CreateUnitConfigRequest(
                config_kind=UnitConfigKind.PLOT,
                name="Plot",
                code="P1",
                plot_type=PlotType.RESIDENTIAL,
            ),
        )


@pytest.mark.asyncio
async def test_audit_error_handlers_json_and_os(monkeypatch):
    """maybe_log_audit_on_error handles JSON and OS errors gracefully."""
    from starlette.requests import Request

    from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
        maybe_log_audit_on_error,
    )

    req = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    req.state.audit_user_context = {
        "organization_id": ORG_ID,
        "user_id": OWNER_ID,
        "user_email": "admin@example.com",
    }

    with patch(
        "apps.user_service.app.dependencies.audit_logs.audit_logger.audit_logger.log_audit_event",
        AsyncMock(side_effect=json.JSONDecodeError("x", "y", 0)),
    ):
        await maybe_log_audit_on_error(req, description="json fail")

    with patch(
        "apps.user_service.app.dependencies.audit_logs.audit_logger.audit_logger.log_audit_event",
        AsyncMock(side_effect=OSError("network")),
    ):
        await maybe_log_audit_on_error(req, description="os fail")
