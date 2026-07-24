"""Additional mocked unit tests to close high-impact coverage gaps."""

from __future__ import annotations

import time
import types
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.auth import CompanyData
from apps.user_service.app.schemas.common import Phone
from apps.user_service.app.schemas.contacts import CreateContactRequest
from apps.user_service.app.schemas.enums import (
    ContactType,
    DeleteRequestStatus,
    OrganizationStatus,
)
from apps.user_service.app.schemas.organizations import (
    NewOrganizationBody,
    OrganizationAdminUpdate,
)
from apps.user_service.app.schemas.verification_codes import (
    SendVerificationCodeRequest,
    VerificationType,
    VerifyVerificationCodeRequest,
)
from apps.user_service.app.services.companies_service import CompaniesService
from apps.user_service.app.services.contacts_service import ContactsService
from apps.user_service.app.services.organization_service import OrganizationService
from apps.user_service.app.services.verification_code_service import (
    VerificationCodeService,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ConflictException,
    InternalServerErrorException,
    NotFoundException,
    ServiceUnavailableException,
    UnauthorizedException,
    ValidationException,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
CONTACT_ID = "660e8400-e29b-41d4-a716-446655440001"
REQUEST_ID = "770e8400-e29b-41d4-a716-446655440002"


def _org_ctx() -> UserContext:
    return UserContext(user_id="admin-1", email="admin@example.com", organization_id=ORG_ID)


def _org_service() -> OrganizationService:
    svc = OrganizationService(user_context=_org_ctx(), db_connection=MagicMock())
    svc.organization_repository = MagicMock()
    svc.organization_member_repository = MagicMock()
    svc.delete_request_repository = MagicMock()
    svc.team_repository = MagicMock()
    svc.role_repository = MagicMock()
    svc.permissions_repository = MagicMock()
    return svc


def _new_org_body() -> NewOrganizationBody:
    return NewOrganizationBody(
        company_data=CompanyData(
            company_name="Acme Legal",
            website_url="https://acme.example.com",
            primary_practice_areas=["Litigation"],
        )
    )


@pytest.mark.asyncio
async def test_approve_delete_request_full_flow(monkeypatch):
    """_approve_delete_request deletes org data and notifies members."""
    svc = _org_service()
    svc.organization_member_repository.get_all_members_by_organization_id = AsyncMock(
        return_value=[{"email": "a@example.com"}, {"email": None}, {"email": "b@example.com"}]
    )
    svc.organization_member_repository.delete_all_members_by_organization_id = AsyncMock()
    svc.team_repository.delete_all_teams_by_organization_id = AsyncMock()
    svc.role_repository.delete_all_roles_by_organization_id = AsyncMock()
    svc.permissions_repository.delete_all_permissions_by_organization_id = AsyncMock()
    svc.organization_repository.delete_organization = AsyncMock()
    svc.delete_request_repository.approve_delete_request = AsyncMock(
        return_value={
            "id": REQUEST_ID,
            "organization_id": ORG_ID,
            "status": DeleteRequestStatus.APPROVED.value,
            "review_reason": "Approved",
            "reviewed_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        }
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.organization_service.revoke_organization_sessions_everywhere",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.organization_service.send_organization_deletion_approved_email",
        lambda **kwargs: True,
    )

    result = await svc._approve_delete_request(
        REQUEST_ID,
        ORG_ID,
        "Acme Legal",
        "Approved",
    )

    assert result["status"] == DeleteRequestStatus.APPROVED.value
    svc.organization_repository.delete_organization.assert_awaited_once_with(ORG_ID)


@pytest.mark.asyncio
async def test_reject_delete_request_full_flow(monkeypatch):
    """_reject_delete_request notifies requester on rejection."""
    svc = _org_service()
    svc.organization_member_repository.get_user_profile_by_id = AsyncMock(
        return_value={"email": "requester@example.com"}
    )
    svc.delete_request_repository.reject_delete_request = AsyncMock(
        return_value={
            "id": REQUEST_ID,
            "organization_id": ORG_ID,
            "status": DeleteRequestStatus.REJECTED.value,
            "review_reason": "No",
            "reviewed_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        }
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.organization_service.send_organization_deletion_rejected_email",
        lambda **kwargs: True,
    )

    result = await svc._reject_delete_request(
        REQUEST_ID,
        ORG_ID,
        "Acme Legal",
        {"requester_id": "req-1"},
        "No",
    )

    assert result["status"] == DeleteRequestStatus.REJECTED.value


@pytest.mark.asyncio
async def test_build_organization_payload_invalid_isometrik_project_id():
    """Invalid isometrik project id raises InternalServerErrorException."""
    svc = _org_service()
    with pytest.raises(InternalServerErrorException):
        svc._build_organization_payload(
            organization_id=ORG_ID,
            resolved_slug="business-acme",
            body=_new_org_body(),
            subscription={},
            settings={},
            isometrik_details={"projectId": "   "},
        )


@pytest.mark.asyncio
async def test_build_organization_payload_with_isometrik():
    """_build_organization_payload embeds isometrik details in settings."""
    svc = _org_service()
    payload = svc._build_organization_payload(
        organization_id=ORG_ID,
        resolved_slug="business-acme",
        body=_new_org_body(),
        subscription={"plan_type": "trial"},
        settings={"website_url": "https://acme.example.com"},
        isometrik_details={"projectId": "proj-1", "userSecret": "secret"},
    )

    assert payload["isometrik_project_id"] == "proj-1"
    assert "isometrik_application_details" in payload["settings"]


@pytest.mark.asyncio
async def test_create_super_admin_role_without_permissions():
    """_create_super_admin_role skips permission assignment when empty."""
    svc = _org_service()
    svc.role_repository.create_role = AsyncMock(return_value={"id": "role-1"})
    svc.role_repository.assign_permissions_to_role = AsyncMock()

    role_id = await svc._create_super_admin_role(ORG_ID, [])

    assert role_id == "role-1"
    svc.role_repository.assign_permissions_to_role.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_delete_request_not_found():
    """process_delete_request raises when request missing."""
    svc = _org_service()
    svc.delete_request_repository.get_delete_request_by_id = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc.process_delete_request(REQUEST_ID, is_accepted=True, reason="Yes")


@pytest.mark.asyncio
async def test_update_organization_ai_overview_patch():
    """update_organization merges ai_overview_settings into settings JSON."""
    org_row = {
        "id": ORG_ID,
        "name": "Acme",
        "slug": "business-acme",
        "status": OrganizationStatus.ACTIVE.value,
        "settings": '{"website_url":"https://old.example.com"}',
        "subscription": "{}",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    svc = _org_service()
    svc.organization_repository.get_organization_by_id = AsyncMock(return_value=org_row)
    svc.organization_repository.check_slug_unique = AsyncMock(return_value=True)
    svc.organization_repository.update_organization = AsyncMock(
        side_effect=lambda organization_id, update_data: {**org_row, **update_data}
    )

    body = OrganizationAdminUpdate(
        ai_overview_settings={"business_overview": "Updated overview"},
    )
    result = await svc.update_organization(ORG_ID, body)

    assert result["organization_id"] == ORG_ID
    svc.organization_repository.update_organization.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_supabase_client_with_token_missing_config(monkeypatch):
    """_get_supabase_client_with_token fails when Supabase config missing."""
    svc = VerificationCodeService(db_connection=None, sb_client=MagicMock())
    monkeypatch.setattr(
        "apps.user_service.app.services.verification_code_service.app_settings.shared_settings.supabase.url",
        "",
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.verification_code_service.app_settings.shared_settings.supabase.anon_key",
        "",
    )
    with pytest.raises(InternalServerErrorException):
        await svc._get_supabase_client_with_token("token")


@pytest.mark.asyncio
async def test_validate_and_set_session_expired_token(monkeypatch):
    """_validate_and_set_session rejects expired JWT."""
    svc = VerificationCodeService(db_connection=None, sb_client=MagicMock())
    fake_client = MagicMock()
    fake_client.auth.get_user = AsyncMock(
        return_value=types.SimpleNamespace(user=types.SimpleNamespace(id="u1"))
    )
    monkeypatch.setattr(svc, "_get_supabase_client_with_token", AsyncMock(return_value=fake_client))
    monkeypatch.setattr(
        "apps.user_service.app.services.verification_code_service.get_claims_from_token",
        AsyncMock(return_value={"exp": int(time.time()) - 10}),
    )

    with pytest.raises(UnauthorizedException):
        await svc._validate_and_set_session("expired-token")


@pytest.mark.asyncio
async def test_validate_and_set_session_invalid_user(monkeypatch):
    """_validate_and_set_session rejects when auth.get_user returns no user."""
    svc = VerificationCodeService(db_connection=None, sb_client=MagicMock())
    fake_client = MagicMock()
    fake_client.auth.get_user = AsyncMock(return_value=types.SimpleNamespace(user=None))
    monkeypatch.setattr(svc, "_get_supabase_client_with_token", AsyncMock(return_value=fake_client))

    with pytest.raises(UnauthorizedException):
        await svc._validate_and_set_session("bad-token")


@pytest.mark.asyncio
async def test_update_user_phone_success():
    """_update_user_phone updates metadata via Supabase admin API."""
    svc = VerificationCodeService(db_connection=None, sb_client=MagicMock())
    admin = MagicMock()
    admin.get_user_by_id = AsyncMock(
        return_value=types.SimpleNamespace(
            user=types.SimpleNamespace(
                user_metadata={"phone_number": "111", "phone_isd_code": "+1"}
            )
        )
    )
    admin.update_user_by_id = AsyncMock(
        return_value=types.SimpleNamespace(
            user=types.SimpleNamespace(
                user_metadata={"phone_number": "2222222222", "phone_isd_code": "+1"},
            )
        )
    )
    svc.supabase_client = types.SimpleNamespace(auth=types.SimpleNamespace(admin=admin))
    svc.organization_member_repository.update_user_phone_by_user_id = AsyncMock(return_value=1)

    updated = await svc._update_user_phone("u1", "2222222222", "+1")

    assert updated is True


@pytest.mark.asyncio
async def test_create_verification_code_inserts_row(monkeypatch):
    """create_verification_code persists OTP row and returns expiry metadata."""
    svc = VerificationCodeService(db_connection=None, sb_client=MagicMock())
    svc.verification_code_repository.insert_verification_code = AsyncMock(
        return_value={"id": "ver-1", "expiry_at": 9999999999999}
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.verification_code_service.app_settings.two_fa_settings",
        types.SimpleNamespace(
            verification_code_expiry_minutes=10,
            email_default_otp="123456",
            phone_default_otp="654321",
            email_otp_enabled=False,
            phone_otp_enabled=False,
        ),
    )

    record = await svc.create_verification_code(
        type_text="EMAIL",
        given_input="user@example.com",
        user_id="u1",
        triggered_text="EMAIL_UPDATE",
        ip_address="1.2.3.4",
    )

    assert record["id"] == "ver-1"
    svc.verification_code_repository.insert_verification_code.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_verification_code_authenticated_email(monkeypatch):
    """Authenticated send_verification_code uses update triggers."""
    svc = VerificationCodeService(db_connection=None, sb_client=MagicMock())
    svc.user_repository = MagicMock()
    svc.user_repository.get_auth_user_by_email = AsyncMock(return_value=None)
    svc.verification_code_repository = MagicMock()
    svc.verification_code_repository.get_recent_verification_codes = AsyncMock(return_value=[])
    svc.verification_code_repository.insert_verification_code = AsyncMock(
        return_value={"id": "ver-1", "expiry_at": 9999999999999}
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.verification_code_service.get_user_by_id",
        AsyncMock(
            return_value=types.SimpleNamespace(
                user=types.SimpleNamespace(email="old@example.com"),
            )
        ),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.verification_code_service.send_verification_code_email",
        lambda **kwargs: True,
    )
    req = types.SimpleNamespace(
        headers={"X-Forwarded-For": "1.2.3.4"},
        client=types.SimpleNamespace(host="5.6.7.8"),
    )
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="new@example.com")

    result = await svc.send_verification_code(req, data, current_user={"sub": "u1"})

    assert result["verification_id"] == "ver-1"


@pytest.mark.asyncio
async def test_verify_verification_code_updates_phone(monkeypatch):
    """Phone update verification delegates to _update_email_or_phone."""
    svc = VerificationCodeService(db_connection=None, sb_client=MagicMock())
    verification_repo = MagicMock()
    verification_repo.get_verification_code_by_id = AsyncMock(
        return_value={
            "id": "ver-1",
            "verified": False,
            "expiry_at": int(datetime(2099, 1, 1, tzinfo=timezone.utc).timestamp() * 1000),
            "given_input": "+12222222222",
            "verification_code": "123456",
            "triggered_text": "PHONE_NUMBER_UPDATE",
            "attempts": [],
            "user_id": "u1",
        }
    )
    verification_repo.update_verification_code = AsyncMock()
    svc.verification_code_repository = verification_repo
    req = types.SimpleNamespace(state=types.SimpleNamespace(access_token="token-abc"))
    data = VerifyVerificationCodeRequest(
        type=VerificationType.PHONE_NUMBER,
        verification_id="ver-1",
        verification_code="123456",
        phone_number="2222222222",
        phone_isd_code="+1",
    )
    monkeypatch.setattr(svc, "_update_email_or_phone", AsyncMock(return_value=(False, True)))

    result = await svc.verify_verification_code(req, data, current_user={"sub": "u1"})

    assert result["verified"] is True
    svc._update_email_or_phone.assert_awaited_once()


@pytest.mark.asyncio
async def test_provision_auth_for_existing_contact_updates_contact(monkeypatch):
    """provision_auth_for_existing_contact persists user and isometrik ids."""
    repo = MagicMock()
    repo.get_contact_for_update = AsyncMock(
        return_value={
            "id": CONTACT_ID,
            "user_id": None,
            "isometrik_user_id": None,
            "phones": [{"is_primary": True, "phone_isd_code": "+91", "phone_number": "9876543210"}],
            "emails": [{"is_primary": True, "email": "jane@example.com"}],
            "first_name": "Jane",
            "last_name": "Doe",
        }
    )
    repo.update_contact = AsyncMock(return_value={"id": CONTACT_ID, "user_id": "auth-1"})
    service = ContactsService(
        db_connection=MagicMock(),
        user_context=_org_ctx(),
        supabase_client=MagicMock(),
    )
    service.contacts_repo = repo
    monkeypatch.setattr(
        service,
        "_provision_contact_auth_identity",
        AsyncMock(return_value=("auth-1", "iso-1", "pass")),
    )

    updated = await service.provision_auth_for_existing_contact(
        contact_id=CONTACT_ID,
        password="Secret@123",
    )

    assert updated["user_id"] == "auth-1"
    repo.update_contact.assert_awaited_once()


@pytest.mark.asyncio
async def test_provision_auth_missing_primary_phone():
    """provision_auth_for_existing_contact requires a primary phone."""
    repo = MagicMock()
    repo.get_contact_for_update = AsyncMock(
        return_value={
            "id": CONTACT_ID,
            "user_id": None,
            "phones": [],
            "emails": [],
        }
    )
    service = ContactsService(db_connection=MagicMock(), user_context=_org_ctx())
    service.contacts_repo = repo

    with pytest.raises(ValidationException):
        await service.provision_auth_for_existing_contact(contact_id=CONTACT_ID)


# --- CompaniesService coverage push ---


def _companies_service(**kwargs) -> CompaniesService:
    svc = CompaniesService(db_connection=MagicMock(), user_context=_org_ctx())
    svc.companies_repo = kwargs.get("companies_repo", MagicMock())
    svc.contacts_repo = kwargs.get("contacts_repo", MagicMock())
    svc.cc_repo = kwargs.get("cc_repo", MagicMock())
    svc.org_repo = kwargs.get("org_repo", MagicMock())
    return svc


@pytest.mark.asyncio
async def test_companies_provision_contact_identity_no_email():
    """_provision_contact_identity returns empty tuple when email missing."""
    svc = _companies_service()
    create_contact = CreateContactRequest.model_construct(
        contact_type=ContactType.OWNER,
        email=None,
        phones=[Phone(phone_number="1", phone_isd_code="+1", is_primary=True)],
    )
    result = await svc._provision_contact_identity(create_contact=create_contact)
    assert result == (None, None, None, None)


@pytest.mark.asyncio
async def test_companies_provision_contact_identity_with_email(monkeypatch):
    """_provision_contact_identity delegates identity provisioning to ContactsService."""
    svc = _companies_service()
    contacts_service = MagicMock()
    contacts_service._provision_identity = AsyncMock(return_value=("u1", "iso-1", "pass"))
    monkeypatch.setattr(svc, "_contacts_service", lambda: contacts_service)

    email, user_id, iso_id, password = await svc._provision_contact_identity(
        create_contact=CreateContactRequest(
            email="jane@example.com",
            phones=[Phone(phone_number="1", phone_isd_code="+1", is_primary=True)],
        ),
    )

    assert email == "jane@example.com"
    assert user_id == "u1"
    assert iso_id == "iso-1"
    assert password == "pass"


@pytest.mark.asyncio
async def test_companies_create_contact_for_association(monkeypatch):
    """_create_contact_for_company_association inserts contact row."""
    contacts_repo = MagicMock()
    contacts_repo.create_contacts = AsyncMock(
        return_value=[{"id": CONTACT_ID, "email": "jane@example.com"}],
    )
    svc = _companies_service(contacts_repo=contacts_repo)
    monkeypatch.setattr(svc, "_validate_custom_fields_for_create", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        svc,
        "_provision_contact_identity",
        AsyncMock(return_value=("jane@example.com", "u1", "iso-1", None)),
    )

    contact_id, row = await svc._create_contact_for_company_association(
        create_contact=CreateContactRequest(
            email="jane@example.com",
            first_name="Jane",
            phones=[Phone(phone_number="9876543210", phone_isd_code="+91", is_primary=True)],
        ),
    )

    assert contact_id == CONTACT_ID
    assert row["email"] == "jane@example.com"


@pytest.mark.asyncio
async def test_process_delete_request_approve_integration(monkeypatch):
    """process_delete_request approve path runs full deletion workflow."""
    svc = _org_service()
    svc.delete_request_repository.get_delete_request_by_id = AsyncMock(
        return_value={
            "id": REQUEST_ID,
            "organization_id": ORG_ID,
            "requester_id": "req-1",
            "status": DeleteRequestStatus.PENDING.value,
        }
    )
    svc.organization_repository.get_organization_by_id = AsyncMock(
        return_value={"name": "Acme Legal"}
    )
    monkeypatch.setattr(
        svc, "_approve_delete_request", AsyncMock(return_value={"status": "approved"})
    )

    result = await svc.process_delete_request(REQUEST_ID, is_accepted=True, reason="Yes")

    assert result["status"] == "approved"
    svc._approve_delete_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_contact_routes_to_property_flow(monkeypatch):
    """create_contact delegates to property flow when contact_type is set."""
    svc = ContactsService(db_connection=MagicMock(), user_context=_org_ctx())
    monkeypatch.setattr(
        svc,
        "_create_property_contact",
        AsyncMock(return_value={"contact_id": CONTACT_ID, "new_data": {"id": CONTACT_ID}}),
    )

    result = await svc.create_contact(
        CreateContactRequest(
            contact_type=ContactType.OWNER,
            first_name="Jane",
            phones=[Phone(phone_number="1", phone_isd_code="+1", is_primary=True)],
        )
    )

    assert result["contact_id"] == CONTACT_ID


@pytest.mark.asyncio
async def test_provision_identity_reuses_existing_auth_user(monkeypatch):
    """_provision_identity reuses a single matching auth user."""
    org_repo = MagicMock()
    org_repo.get_organization_by_id = AsyncMock(return_value={"id": ORG_ID, "settings": "{}"})
    service = ContactsService(
        db_connection=MagicMock(),
        user_context=_org_ctx(),
        supabase_client=MagicMock(),
    )
    service.org_repo = org_repo
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.UserRepository",
        lambda db_connection=None: types.SimpleNamespace(
            get_auth_users_by_phone_or_email=AsyncMock(return_value=[{"id": "auth-existing"}])
        ),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.get_isometrik_data_from_settings",
        lambda settings: {"projectId": "p1"},
    )
    monkeypatch.setattr(
        service,
        "_create_or_reuse_isometrik_user",
        AsyncMock(return_value="iso-1"),
    )

    user_id, iso_id, password = await service._provision_identity(
        contact_id=CONTACT_ID,
        first_name="Jane",
        last_name="Doe",
        prefix=None,
        phone="+911234567890",
        email="jane@example.com",
    )

    assert user_id == "auth-existing"
    assert iso_id == "iso-1"
    assert password is None


@pytest.mark.asyncio
async def test_provision_identity_requires_email_or_phone():
    """_provision_identity rejects when both email and phone are missing."""
    service = ContactsService(
        db_connection=MagicMock(),
        user_context=_org_ctx(),
        supabase_client=MagicMock(),
    )

    with pytest.raises(ValidationException):
        await service._provision_identity(
            contact_id=CONTACT_ID,
            first_name="Jane",
            last_name="Doe",
            prefix=None,
            phone=None,
            email=None,
        )


@pytest.mark.asyncio
async def test_provision_identity_auth_mismatch_raises(monkeypatch):
    """Conflicting auth matches raise ConflictException."""
    org_repo = MagicMock()
    org_repo.get_organization_by_id = AsyncMock(return_value={"id": ORG_ID, "settings": "{}"})
    service = ContactsService(
        db_connection=MagicMock(),
        user_context=_org_ctx(),
        supabase_client=MagicMock(),
    )
    service.org_repo = org_repo
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.UserRepository",
        lambda db_connection=None: types.SimpleNamespace(
            get_auth_users_by_phone_or_email=AsyncMock(
                return_value=[{"id": "auth-1"}, {"id": "auth-2"}]
            )
        ),
    )

    with pytest.raises(ConflictException):
        await service._provision_identity(
            contact_id=CONTACT_ID,
            first_name="Jane",
            last_name="Doe",
            prefix=None,
            phone="+911234567890",
            email="jane@example.com",
        )


@pytest.mark.asyncio
async def test_provision_identity_without_supabase_raises():
    """Missing Supabase client raises ServiceUnavailableException."""
    service = ContactsService(db_connection=MagicMock(), user_context=_org_ctx())

    with pytest.raises(ServiceUnavailableException):
        await service._provision_identity(
            contact_id=CONTACT_ID,
            first_name="Jane",
            last_name="Doe",
            prefix=None,
            phone="+911234567890",
            email="jane@example.com",
        )


@pytest.mark.asyncio
async def test_provision_identity_creates_new_auth_user(monkeypatch):
    """_provision_identity creates Supabase user when no auth match exists."""
    org_repo = MagicMock()
    org_repo.get_organization_by_id = AsyncMock(return_value={"id": ORG_ID, "settings": "{}"})
    service = ContactsService(
        db_connection=MagicMock(),
        user_context=_org_ctx(),
        supabase_client=MagicMock(),
    )
    service.org_repo = org_repo
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.UserRepository",
        lambda db_connection=None: types.SimpleNamespace(
            get_auth_users_by_phone_or_email=AsyncMock(return_value=[])
        ),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.create_user",
        AsyncMock(return_value={"id": "auth-new"}),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.get_isometrik_data_from_settings",
        lambda settings: {"projectId": "p1"},
    )
    monkeypatch.setattr(
        service,
        "_create_or_reuse_isometrik_user",
        AsyncMock(return_value="iso-new"),
    )

    user_id, iso_id, password = await service._provision_identity(
        contact_id=CONTACT_ID,
        first_name="Jane",
        last_name="Doe",
        prefix="Ms",
        phone="+911234567890",
        email="jane@example.com",
        password="Secret@123",
    )

    assert user_id == "auth-new"
    assert iso_id == "iso-new"
    assert password == "Secret@123"


@pytest.mark.asyncio
async def test_provision_identity_org_not_found(monkeypatch):
    """_provision_identity raises when organization is missing."""
    service = ContactsService(
        db_connection=MagicMock(),
        user_context=_org_ctx(),
        supabase_client=MagicMock(),
    )
    service.org_repo = MagicMock()
    service.org_repo.get_organization_by_id = AsyncMock(return_value=None)

    with pytest.raises(NotFoundException):
        await service._provision_identity(
            contact_id=CONTACT_ID,
            first_name="Jane",
            last_name="Doe",
            prefix=None,
            phone="+911234567890",
            email="jane@example.com",
        )
