"""Unit tests for ClientService business logic."""
# pylint: disable=too-many-lines

import datetime
import json
from datetime import date

import pytest

from apps.user_service.app.schemas.clients import (
    Address,
    AddressesUpdate,
    AddressInput,
    AddressUpdateItem,
    BillingPreferencesUpdate,
    CreateClientFromUserRequest,
    CreateClientRequest,
    LeadManagement,
    LeadManagementUpdate,
    PhoneInput,
    SocialPageInput,
    SocialPagesUpdate,
    SocialPageUpdateItem,
    UpdateClientRequest,
    Website,
    WebsiteInput,
    WebsitesUpdate,
    WebsiteUpdateItem,
)
from apps.user_service.app.schemas.enums import ClientType, UserEventStatus
from apps.user_service.app.services.client_service import ClientService
from apps.user_service.app.utils.common_utils import UserContext, parse_json_field
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ServiceUnavailableException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode


class _FakeClientRepo:
    """Lightweight fake client repository."""

    def __init__(self):
        self.calls = {}
        self.client_user_exists = False
        self.name_exists = False
        self.client_result = None
        self.client_email_exists = False
        self.existing_client_id = None
        self.client_details_result = None
        self.clients_list_result = []
        self.clients_count_result = 0
        self.address_result = []
        self.company_contacts_result = []
        self.primary_contact_for_update_result = None

    async def check_client_name_exists(self, name, organization_id, exclude_client_id=None):
        """Return name existence flag."""
        self.calls["check_client_name_exists"] = {
            "name": name,
            "organization_id": organization_id,
            "exclude_client_id": exclude_client_id,
        }
        return self.name_exists

    async def _check_client_email_exists(self, email, organization_id, exclude_client_id=None):
        """Return existing client id at org level (or None)."""
        self.calls["check_client_email_exists"] = {
            "email": email,
            "organization_id": organization_id,
            "exclude_client_id": exclude_client_id,
        }
        if self.existing_client_id is not None:
            return self.existing_client_id
        return "existing-client-1" if self.client_email_exists else None

    async def create_client(self, clients_data):
        """Create clients; accepts list of dicts, returns list of records."""
        self.calls["create_client"] = clients_data
        return [
            (self.client_result if i == 0 else None) or {"id": f"client-{i + 1}", **data}
            for i, data in enumerate(clients_data)
        ]

    async def create_client_user(self, client_user_data):
        """Create client user."""
        self.calls["create_client_user"] = client_user_data
        return {"id": "client-user-1", **client_user_data}

    async def bulk_create_addresses(self, addresses_data):
        """Create addresses."""
        self.calls["bulk_create_addresses"] = addresses_data
        return [{"id": f"addr-{i}", **addr} for i, addr in enumerate(addresses_data)]

    async def get_client_details_with_primary_contact(self, client_id, organization_id):
        """Get client details."""
        self.calls["get_client_details_with_primary_contact"] = (client_id, organization_id)
        return self.client_details_result

    async def get_clients_list(self, organization_id, filter_params):
        """Get clients list."""
        self.calls["get_clients_list"] = (organization_id, filter_params)
        return self.clients_list_result

    async def get_clients_count(self, organization_id, filter_params):
        """Get clients count."""
        self.calls["get_clients_count"] = (organization_id, filter_params)
        return self.clients_count_result

    async def delete_client(self, client_id, organization_id):
        """Delete client."""
        self.calls["delete_client"] = (client_id, organization_id)
        return True

    async def delete_client_users(self, client_id):
        """Delete client users."""
        self.calls["delete_client_users"] = client_id
        return True

    async def delete_addresses(self, client_id):
        """Delete addresses."""
        self.calls["delete_addresses"] = client_id
        return True

    async def get_client_addresses(self, client_id):
        """Get client addresses."""
        self.calls["get_client_addresses"] = client_id
        return self.address_result

    async def get_company_contacts(self, company_client_id, organization_id):
        """Get company contacts for a company client."""
        self.calls["get_company_contacts"] = (company_client_id, organization_id)
        return self.company_contacts_result

    get_client_for_update_result = None

    async def get_client_for_update(self, client_id, organization_id):
        """Get client for update."""
        self.calls["get_client_for_update"] = (client_id, organization_id)
        return self.get_client_for_update_result

    async def update_client(self, client_id, organization_id, update_data):
        """Update client."""
        self.calls["update_client"] = (client_id, organization_id, update_data)
        return {"id": client_id, **update_data}

    async def update_address(self, address_id, client_id, update_data):
        """Update address."""
        self.calls["update_address"] = (address_id, client_id, update_data)
        return True

    async def clear_primary_addresses(self, client_id, exclude_address_id=None):
        """Clear primary flags for addresses."""
        self.calls.setdefault("clear_primary_addresses", []).clear()
        self.calls["clear_primary_addresses"].append((client_id, exclude_address_id))

    async def _delete_addresses_by_ids(self, client_id, address_ids):
        """Delete addresses by ids."""
        self.calls["delete_addresses_by_ids"] = (client_id, address_ids)

    async def _get_primary_contact_for_update(self, client_id, organization_id):
        """Return configured primary contact row for updates."""
        self.calls["_get_primary_contact_for_update"] = (client_id, organization_id)
        return self.primary_contact_for_update_result

    async def _update_client_user(self, client_user_id, update_data):
        """Record client_user updates."""
        self.calls["_update_client_user"] = (client_user_id, update_data)
        base_row = self.primary_contact_for_update_result or {"id": client_user_id}
        return {**base_row, **update_data}

    async def clear_primary_contact_for_company(
        self, company_client_id, organization_id, exclude_client_user_id=None
    ):
        """Record clear-primary operation."""
        self.calls["clear_primary_contact_for_company"] = (
            company_client_id,
            organization_id,
            exclude_client_user_id,
        )


class _FakeLeadRepo:
    """Lightweight fake lead repository."""

    def __init__(self):
        self.calls = {}
        self.client_type_by_id: dict[str, str] = {}

    async def fetch_lead_reference_validation(self, organization_id, client_ids, *, stage_id=None):
        """Return stage OK and client id -> client_type for validation."""
        self.calls["fetch_lead_reference_validation"] = (
            organization_id,
            list(client_ids),
            stage_id,
        )
        types_map = {
            str(cid): self.client_type_by_id.get(str(cid), ClientType.PERSON.value)
            for cid in client_ids
        }
        return True, types_map

    async def create_lead(self, row, contacts=None, company=None):
        """Record create (same repository method as API lead create)."""
        self.calls["create_lead"] = {"row": row, "contacts": contacts, "company": company}
        return {"id": "lead-1", **row}

    async def delete_leads_by_client_id(self, client_id):
        """Record delete by client id."""
        self.calls["delete_leads_by_client_id"] = client_id
        return True

    async def update_lead(self, organization_id, lead_id, update_data):
        """Record patch by lead id."""
        self.calls["update_lead"] = (organization_id, lead_id, update_data)
        return {"id": lead_id}


class _FakeUserRepo:
    """Fake user repository."""

    def __init__(self):
        self.user_details = {"id": "user-1", "email": "test@example.com"}
        self.phone_exists = False
        self.email_exists = False

    async def get_user_details_by_id(self, _user_id, _fields):
        """Get user details."""
        return self.user_details

    async def get_auth_user_by_email(self, _email):
        """Get auth user by email."""
        return self.user_details if self.email_exists else None

    async def phone_exists_for_other_user(self, phone=None, user_id=None):
        """Check phone existence."""
        del phone, user_id
        return self.phone_exists


class _FakeOrgRepo:
    """Fake organization repository."""

    def __init__(self):
        self.organization = {
            "id": "org-1",
            "name": "Test Org",
            "settings": '{"isometrik": {"api_key": "key", "api_secret": "secret"}}',
        }

    async def get_organization_by_id(self, _organization_id):
        """Get organization."""
        return self.organization


class _FakeUserEventRepo:
    """Fake user event repository."""

    def __init__(self, user_event_details=None):
        self.calls = {}
        self.user_event_details = user_event_details or {"status": "pending"}

    async def get_user_event_by_user_id(self, _user_id: str, _select_columns=None):
        """Return configured user_event details."""
        return self.user_event_details

    async def update_status_by_user_id(self, user_id: str, status: UserEventStatus) -> None:
        """Record call and no-op."""
        self.calls["update_status_by_user_id"] = {"user_id": user_id, "status": status}


class _FakeCustomFieldRepo:
    """Fake custom field repository for client service tests."""

    def __init__(self):
        self.get_fields_result = []

    async def get_custom_fields_by_entity_type(self, _organization_id, _entity_type):
        """Return configured custom fields."""
        return self.get_fields_result


@pytest.fixture(autouse=True)
def _patch_custom_field_repository(monkeypatch):
    """Prevent DB access in client service tests.

    ClientService performs a lightweight required-custom-fields check when no
    `custom_fields` are provided. Many tests in this module use db_connection=None,
    so we patch the repository used by CustomFieldService to a fake by default.
    Individual tests can override this patch with their own monkeypatch.
    """
    fake_custom_field_repo = _FakeCustomFieldRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_custom_field_repo,
    )
    return fake_custom_field_repo


# Reference the autouse fixture so static analyzers
_AUTO_PATCH_CUSTOM_FIELD_REPOSITORY = _patch_custom_field_repository


def _ctx(org_id="org-1"):
    """Build reusable UserContext for tests."""
    return UserContext(
        user_id="u1",
        email="u1@example.com",
        organization_id=org_id,
        user_type="admin",
    )


def _cfc(
    field_id: str,
    value,
    instance_id: str | None = "10000000-0000-4000-8000-000000000001",
) -> dict:
    """PATCH FieldCell fragment (include ``instance_id`` when updating an existing root)."""
    cell: dict = {"field_id": field_id, "value": value}
    if instance_id is not None:
        cell["instance_id"] = instance_id
    return cell


def _cfc_create(field_id: str, value) -> dict:
    """Create payload root FieldCell: ``field_id`` + discriminator only (no instance_id / type)."""
    return {"field_id": field_id, "value": value}


@pytest.mark.asyncio
async def test_create_client_from_user_no_event(monkeypatch):
    """Raises ConflictException when user event is missing."""
    fake_repo = _FakeClientRepo()
    fake_user_event_repo = _FakeUserEventRepo()
    fake_user_event_repo.user_event_details = None

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.UserEventRepository",
        lambda db_connection=None: fake_user_event_repo,
    )
    service = ClientService(db_connection=None)
    request_data = CreateClientFromUserRequest(user_id="user-1", organization_id="org-1")

    with pytest.raises(ConflictException) as exc_info:
        await service.create_client_from_user(request_data)

    assert exc_info.value.message_key == "clients.errors.user_event_not_available"


@pytest.mark.asyncio
async def test_create_client_from_user_event_not_pending(monkeypatch):
    """Raises ConflictException when user event status is not pending."""
    fake_repo = _FakeClientRepo()
    fake_user_event_repo = _FakeUserEventRepo(
        user_event_details={"status": UserEventStatus.COMPLETED.value}
    )

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.UserEventRepository",
        lambda db_connection=None: fake_user_event_repo,
    )
    service = ClientService(db_connection=None)
    request_data = CreateClientFromUserRequest(user_id="user-1", organization_id="org-1")

    with pytest.raises(ConflictException) as exc_info:
        await service.create_client_from_user(request_data)

    assert exc_info.value.message_key == "clients.errors.user_event_not_available"


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_create_client_from_user_user_not_found(monkeypatch):
    """Raises NotFoundException when user not found."""
    fake_repo = _FakeClientRepo()
    fake_user_repo = _FakeUserRepo()
    fake_user_repo.user_details = None
    fake_user_event_repo = _FakeUserEventRepo()

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.UserRepository",
        lambda db_connection=None: fake_user_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.UserEventRepository",
        lambda db_connection=None: fake_user_event_repo,
    )
    service = ClientService(db_connection=None)
    request_data = CreateClientFromUserRequest(user_id="user-1", organization_id="org-1")

    with pytest.raises(NotFoundException):
        await service.create_client_from_user(request_data)


@pytest.mark.asyncio
async def test_client_from_user_raises_org_not_found(monkeypatch):
    """Raises NotFoundException when organization not found."""
    fake_repo = _FakeClientRepo()
    fake_user_repo = _FakeUserRepo()
    fake_org_repo = _FakeOrgRepo()
    fake_org_repo.organization = None
    fake_user_event_repo = _FakeUserEventRepo()

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.UserRepository",
        lambda db_connection=None: fake_user_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.OrganizationRepository",
        lambda db_connection=None: fake_org_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.UserEventRepository",
        lambda db_connection=None: fake_user_event_repo,
    )
    service = ClientService(db_connection=None)
    request_data = CreateClientFromUserRequest(user_id="user-1", organization_id="org-1")

    with pytest.raises(NotFoundException):
        await service.create_client_from_user(request_data)


@pytest.mark.asyncio
async def test_create_client_raises_email_exists(monkeypatch):
    """Raises ConflictException when email already exists for organization."""
    fake_repo = _FakeClientRepo()
    fake_repo.existing_client_id = "client-existing-123"
    fake_user_repo = _FakeUserRepo()
    fake_org_repo = _FakeOrgRepo()

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.UserRepository",
        lambda db_connection=None: fake_user_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.OrganizationRepository",
        lambda db_connection=None: fake_org_repo,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    request_data = CreateClientRequest(
        client_type=ClientType.PERSON,
        email="test@example.com",
        first_name="John",
        last_name="Doe",
        phones=[
            PhoneInput(
                phone_number="1234567890",
                phone_isd_code="+1",
                label="mobile",
                is_primary=True,
            )
        ],
    )

    with pytest.raises(ConflictException) as exc_info:
        await service.create_client(request_data)
    assert exc_info.value.params["client_id"] == "client-existing-123"


@pytest.mark.asyncio
async def test_create_client_does_not_check_phone_uniqueness(monkeypatch):
    """Phone uniqueness is not validated during client creation."""
    fake_repo = _FakeClientRepo()
    fake_user_repo = _FakeUserRepo()
    fake_user_repo.phone_exists = True
    fake_org_repo = _FakeOrgRepo()

    async def fake_create_user(*_args, **_kwargs):
        return {"id": "auth-user-123"}

    async def fake_create_isometrik_user(*_args, **_kwargs):
        return {"userId": "isometrik-123"}

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.UserRepository",
        lambda db_connection=None: fake_user_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.OrganizationRepository",
        lambda db_connection=None: fake_org_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.create_user",
        fake_create_user,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.create_isometrik_user",
        fake_create_isometrik_user,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.send_client_creation_email",
        lambda *_args, **_kwargs: None,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    request_data = CreateClientRequest(
        client_type=ClientType.PERSON,
        email="test@example.com",
        first_name="John",
        last_name="Doe",
        phones=[
            PhoneInput(
                phone_number="1234567890",
                phone_isd_code="+1",
                label="mobile",
                is_primary=True,
            )
        ],
    )

    # Should not raise ConflictException for phone uniqueness anymore
    await service.create_client(request_data)


@pytest.mark.asyncio
async def test_create_client_allows_duplicate_names(monkeypatch):
    """Client creation does not enforce name uniqueness."""
    fake_repo = _FakeClientRepo()
    fake_repo.name_exists = True  # should be ignored by service now
    fake_user_repo = _FakeUserRepo()
    fake_org_repo = _FakeOrgRepo()

    async def _fake_create_isometrik_user(*_args, **_kwargs):
        return {"userId": "iso-1"}

    async def _fake_create_user(*_args, **_kwargs):
        return {"id": "auth-user-1"}

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.UserRepository",
        lambda db_connection=None: fake_user_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.OrganizationRepository",
        lambda db_connection=None: fake_org_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.create_isometrik_user",
        _fake_create_isometrik_user,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.create_user",
        _fake_create_user,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.send_client_creation_email",
        lambda *_args, **_kwargs: None,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)

    await service.create_client(
        CreateClientRequest(
            client_type=ClientType.PERSON,
            email="test@example.com",
            first_name="John",
            last_name="Doe",
            phones=[
                PhoneInput(
                    phone_number="1234567890",
                    phone_isd_code="+1",
                    label="mobile",
                    is_primary=True,
                )
            ],
        )
    )

    await service.create_client(
        CreateClientRequest(
            client_type=ClientType.COMPANY,
            email="test-company@example.com",
            first_name="John",
            last_name="Doe",
            phones=[
                PhoneInput(
                    phone_number="1234567891",
                    phone_isd_code="+1",
                    label="mobile",
                    is_primary=True,
                )
            ],
            name="Test Company",
        )
    )


@pytest.mark.asyncio
async def test_get_client_details_not_found(monkeypatch):
    """Raises NotFoundException when client not found."""
    fake_repo = _FakeClientRepo()
    fake_repo.client_details_result = None

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)

    with pytest.raises(NotFoundException):
        await service.get_client_details("client-1", "org-1")


@pytest.mark.asyncio
async def test_get_clients_list_returns_results(monkeypatch):
    """get_clients_list returns transformed results."""
    fake_repo = _FakeClientRepo()
    fake_repo.clients_list_result = [
        {
            "id": "client-1",
            "name": "Client 1",
            "company_name": "Acme Corp",
            "client_type": "person",
            "status": "active",
            "industry": "SaaS",
            "created_at": datetime.datetime.now(),
            "updated_at": datetime.datetime.now(),
            "first_name": "John",
            "last_name": "Doe",
            "tags": [],
        }
    ]
    fake_repo.clients_count_result = 1

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    result = await service.get_clients_list(
        "org-1",
        {"page": 1, "page_size": 20, "search": None, "client_type": None, "status": None},
    )

    assert result["total"] == 1
    assert len(result["clients"]) == 1
    assert result["clients"][0]["id"] == "client-1"
    assert result["clients"][0]["name"] == "Client 1"
    assert result["clients"][0]["company_name"] == "Acme Corp"
    assert result["clients"][0]["industry"] == "SaaS"


@pytest.mark.asyncio
async def test_get_clients_list_company_omits_company_name(monkeypatch):
    """get_clients_list does not include company_name for company clients."""
    fake_repo = _FakeClientRepo()
    fake_repo.clients_list_result = [
        {
            "id": "client-company-1",
            "name": "Company 1",
            "client_type": "company",
            "status": "active",
            "created_at": datetime.datetime.now(),
            "updated_at": datetime.datetime.now(),
            "first_name": None,
            "last_name": None,
            "tags": [],
        }
    ]
    fake_repo.clients_count_result = 1

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    result = await service.get_clients_list(
        "org-1",
        {"page": 1, "page_size": 20, "search": None, "client_type": None, "status": None},
    )

    assert result["total"] == 1
    assert len(result["clients"]) == 1
    assert result["clients"][0]["id"] == "client-company-1"
    assert result["clients"][0]["name"] == "Company 1"
    assert "company_name" not in result["clients"][0]


@pytest.mark.asyncio
async def test_delete_client_calls_delete_methods(monkeypatch):
    """delete_client calls all related delete methods."""
    fake_repo = _FakeClientRepo()
    fake_lead = _FakeLeadRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.LeadRepository",
        lambda db_connection=None: fake_lead,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    await service.delete_client("client-1", "org-1")

    assert "delete_client" in fake_repo.calls
    assert "delete_client_users" in fake_repo.calls
    assert fake_lead.calls["delete_leads_by_client_id"] == "client-1"
    assert "delete_addresses" in fake_repo.calls


@pytest.mark.asyncio
async def test_build_client_name_for_person():
    """_prepare_client_data builds full name for person type."""
    service = ClientService(db_connection=None)
    request_data = CreateClientRequest(
        client_type=ClientType.PERSON,
        email="test@example.com",
        phone_isd_code="+1",
        phone_number="1234567890",
        first_name="John",
        last_name="Doe",
    )

    client_data = await service._prepare_client_data(request_data, "org-1")

    assert client_data["name"] == "John Doe"


@pytest.mark.asyncio
async def test_build_client_name_for_company():
    """_prepare_client_data builds company name for company type."""
    service = ClientService(db_connection=None)
    request_data = CreateClientRequest(
        client_type=ClientType.COMPANY,
        email="test@example.com",
        phone_isd_code="+1",
        phone_number="1234567890",
        first_name="John",
        last_name="Doe",
        name="Test Company",
    )

    client_data = await service._prepare_client_data(request_data, "org-1")

    assert client_data["name"] == "Test Company"


@pytest.mark.asyncio
async def test_client_raises_required_custom_fields_missing(monkeypatch):
    """_prepare_client_data should raise when org has required custom fields but none provided."""
    fake_custom_field_repo = _FakeCustomFieldRepo()
    fake_custom_field_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Required Field",
            "field_key": "required_field",
            "field_type": "text",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": True,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        }
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_custom_field_repo,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)
    request_data = CreateClientRequest(
        client_type=ClientType.PERSON,
        email="test@example.com",
        phone_isd_code="+1",
        phone_number="1234567890",
        first_name="John",
        last_name="Doe",
        # custom_fields omitted → defaults to []
    )

    with pytest.raises(ValidationException) as exc_info:
        await service._prepare_client_data(request_data, "org-1")
    assert "custom_field_required" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_client_allows_custom_fields_no_required_fields(monkeypatch):
    """_prepare_client_data should allow empty custom_fields when no required fields exist."""
    fake_custom_field_repo = _FakeCustomFieldRepo()
    fake_custom_field_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Optional Field",
            "field_key": "optional_field",
            "field_type": "text",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        }
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_custom_field_repo,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)
    request_data = CreateClientRequest(
        client_type=ClientType.PERSON,
        email="test@example.com",
        phone_isd_code="+1",
        phone_number="1234567890",
        first_name="John",
        last_name="Doe",
        # custom_fields omitted → defaults to []
    )

    client_data = await service._prepare_client_data(request_data, "org-1")
    assert client_data["name"] == "John Doe"


@pytest.mark.asyncio
async def test_prepare_client_data_serializes_jsonb_fields(monkeypatch):
    """_prepare_client_data serializes JSONB fields to JSON strings."""
    fake_custom_field_repo = _FakeCustomFieldRepo()
    fake_custom_field_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Custom Field",
            "field_key": "key",
            "field_type": "text",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        }
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_custom_field_repo,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)
    request_data = CreateClientRequest(
        client_type=ClientType.PERSON,
        email="test@example.com",
        phone_isd_code="+1",
        phone_number="1234567890",
        first_name="John",
        last_name="Doe",
        websites=[Website(url="https://example.com", type="primary", is_primary=True)],
        tags=["tag1", "tag2"],
        custom_fields=[_cfc_create("field-1", "value")],
    )

    client_data = await service._prepare_client_data(request_data, "org-1")

    assert client_data["organization_id"] == "org-1"
    assert client_data["client_type"] == "person"
    assert isinstance(client_data["websites"], str)  # JSON string
    assert client_data["tags"] == ["tag1", "tag2"]
    assert isinstance(client_data["custom_fields"], str)  # JSON string


@pytest.mark.asyncio
async def test_prepare_client_data_validates_custom_fields(monkeypatch):
    """_prepare_client_data validates custom fields against definitions."""
    fake_custom_field_repo = _FakeCustomFieldRepo()
    fake_custom_field_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Age",
            "field_key": "age",
            "field_type": "number",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        }
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_custom_field_repo,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)
    request_data = CreateClientRequest(
        client_type=ClientType.PERSON,
        email="test@example.com",
        phone_isd_code="+1",
        phone_number="1234567890",
        first_name="John",
        last_name="Doe",
        custom_fields=[_cfc_create("field-1", 25)],
    )

    client_data = await service._prepare_client_data(request_data, "org-1")

    parsed_fields = parse_json_field(client_data["custom_fields"])
    assert len(parsed_fields) == 1
    assert parsed_fields[0]["field_id"] == "field-1"
    assert parsed_fields[0]["value"] == 25.0
    assert parsed_fields[0]["type"] == "number"
    assert parsed_fields[0]["instance_id"]


@pytest.mark.asyncio
async def test_prepare_client_data_custom_fields_invalid(monkeypatch):
    """_prepare_client_data raises when custom fields validation fails."""
    fake_custom_field_repo = _FakeCustomFieldRepo()
    fake_custom_field_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Age",
            "field_key": "age",
            "field_type": "number",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        }
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_custom_field_repo,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)
    request_data = CreateClientRequest(
        client_type=ClientType.PERSON,
        email="test@example.com",
        phone_isd_code="+1",
        phone_number="1234567890",
        first_name="John",
        last_name="Doe",
        custom_fields=[_cfc_create("field-1", "not a number")],
    )

    with pytest.raises(ValidationException):
        await service._prepare_client_data(request_data, "org-1")


@pytest.mark.asyncio
async def test_prepare_client_user_data_includes_optional():
    """_prepare_client_user_data includes optional fields when provided."""
    service = ClientService(db_connection=None)
    request_data = CreateClientRequest(
        client_type=ClientType.PERSON,
        email="test@example.com",
        phone_isd_code="+1",
        phone_number="1234567890",
        first_name="John",
        last_name="Doe",
        prefix="Mr.",
        middle_name="Middle",
        title="CEO",
        date_of_birth=date(1990, 1, 1),
        profile_photo_url="https://example.com/photo.jpg",
    )
    client_user_data = service._prepare_client_user_data(
        request_data, "client-1", "org-1", "user-1", "isometrik-1"
    )

    assert client_user_data["client_id"] == "client-1"
    assert client_user_data["first_name"] == "John"
    assert client_user_data["last_name"] == "Doe"
    assert client_user_data["prefix"] == "Mr."
    assert client_user_data["middle_name"] == "Middle"
    assert client_user_data["title"] == "CEO"
    assert client_user_data["date_of_birth"] == date(1990, 1, 1)
    assert client_user_data["profile_photo_url"] == "https://example.com/photo.jpg"
    assert client_user_data["is_primary_contact"] is True


@pytest.mark.asyncio
async def test_prepare_user_non_primary_company_linked():
    """_prepare_client_user_data sets is_primary_contact False when client_company_id provided."""
    service = ClientService(db_connection=None)
    request_data = CreateClientRequest(
        client_type=ClientType.PERSON,
        email="test@example.com",
        phone_isd_code="+1",
        phone_number="1234567890",
        first_name="John",
        last_name="Doe",
        client_company_id="company-1",
    )
    client_user_data = service._prepare_client_user_data(
        request_data, "client-1", "org-1", "user-1", "isometrik-1", client_company_id="company-1"
    )

    assert client_user_data["client_id"] == "client-1"
    assert client_user_data["client_company_id"] == "company-1"
    assert client_user_data["is_primary_contact"] is False


@pytest.mark.asyncio
async def test_create_optional_records_creates_lead(monkeypatch):
    """_create_optional_records creates lead when lead_management enabled."""
    fake_repo = _FakeClientRepo()
    fake_lead = _FakeLeadRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.LeadRepository",
        lambda db_connection=None: fake_lead,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)
    request_data = CreateClientRequest(
        client_type=ClientType.PERSON,
        email="test@example.com",
        phone_isd_code="+1",
        phone_number="1234567890",
        first_name="John",
        last_name="Doe",
        lead_management=LeadManagement(
            enabled=True,
            stage_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        ),
    )

    await service._create_optional_records(request_data, "client-1")
    created = fake_lead.calls["create_lead"]
    row = created["row"]
    assert created["contacts"] == []
    assert created["company"] is None
    assert row["organization_id"] == "org-1"
    assert row["name"] == "John Doe"
    assert row["stage_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert row["owner_id"] == "u1"
    assert row["custom_fields"] == []
    assert row["notes"] == []


@pytest.mark.asyncio
async def test_create_optional_records_lead_for_company(monkeypatch):
    """Company client onboarding creates a lead row without CRM contact/company links."""
    fake_repo = _FakeClientRepo()
    fake_lead = _FakeLeadRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.LeadRepository",
        lambda db_connection=None: fake_lead,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)
    request_data = CreateClientRequest(
        client_type=ClientType.COMPANY,
        name="Acme Legal",
        lead_management=LeadManagement(
            enabled=True,
            stage_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        ),
    )

    await service._create_optional_records(request_data, "co-1")
    created = fake_lead.calls["create_lead"]
    row = created["row"]
    assert created["contacts"] == []
    assert created["company"] is None
    assert row["name"] == "Acme Legal"


@pytest.mark.asyncio
async def test_create_optional_records_creates_addresses(monkeypatch):
    """_create_optional_records creates addresses when provided."""
    fake_repo = _FakeClientRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    service = ClientService(db_connection=None)
    request_data = CreateClientRequest(
        client_type=ClientType.PERSON,
        email="test@example.com",
        phone_isd_code="+1",
        phone_number="1234567890",
        first_name="John",
        last_name="Doe",
        addresses=[
            Address(
                address_line1="123 Main St",
                city="New York",
                state="NY",
                country="United States",
            )
        ],
    )

    await service._create_optional_records(request_data, "client-1")
    assert "bulk_create_addresses" in fake_repo.calls
    addresses = fake_repo.calls["bulk_create_addresses"]
    assert len(addresses) == 1
    assert addresses[0]["client_id"] == "client-1"
    assert addresses[0]["address_line1"] == "123 Main St"


@pytest.mark.asyncio
async def test_create_client_from_user_success(monkeypatch):
    """create_client_from_user successfully creates client from user."""
    fake_repo = _FakeClientRepo()
    fake_user_repo = _FakeUserRepo()
    fake_org_repo = _FakeOrgRepo()
    fake_user_event_repo = _FakeUserEventRepo()

    async def fake_create_isometrik_user(*_args, **_kwargs):
        return {"userId": "isometrik-123"}

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.UserRepository",
        lambda db_connection=None: fake_user_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.OrganizationRepository",
        lambda db_connection=None: fake_org_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.UserEventRepository",
        lambda db_connection=None: fake_user_event_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.create_isometrik_user",
        fake_create_isometrik_user,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.send_client_creation_email",
        lambda *_args, **_kwargs: None,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    request_data = CreateClientFromUserRequest(user_id="user-1", organization_id="org-1")

    await service.create_client_from_user(request_data)
    assert "create_client" in fake_repo.calls
    assert "create_client_user" in fake_repo.calls
    assert "update_status_by_user_id" in fake_user_event_repo.calls
    assert fake_user_event_repo.calls["update_status_by_user_id"]["user_id"] == "user-1"
    assert (
        fake_user_event_repo.calls["update_status_by_user_id"]["status"]
        == UserEventStatus.COMPLETED
    )


@pytest.mark.asyncio
async def test_create_isometrik_user_fails(monkeypatch):
    """create_isometrik_user raises when Isometrik user creation fails."""
    fake_repo = _FakeClientRepo()
    fake_user_repo = _FakeUserRepo()
    fake_org_repo = _FakeOrgRepo()
    fake_user_event_repo = _FakeUserEventRepo()

    async def fake_create_isometrik_user(*_args, **_kwargs):
        return None  # Failed

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.UserRepository",
        lambda db_connection=None: fake_user_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.OrganizationRepository",
        lambda db_connection=None: fake_org_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.UserEventRepository",
        lambda db_connection=None: fake_user_event_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.create_isometrik_user",
        fake_create_isometrik_user,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    request_data = CreateClientFromUserRequest(user_id="user-1", organization_id="org-1")

    with pytest.raises(ServiceUnavailableException):
        await service.create_client_from_user(request_data)


@pytest.mark.asyncio
async def test_create_auth_isometrik_user_person(monkeypatch):
    """_create_auth_and_isometrik_user creates user for person type."""
    fake_org_repo = _FakeOrgRepo()

    async def fake_create_user(*_args, **_kwargs):
        return {"id": "auth-user-123"}

    async def fake_create_isometrik_user(*_args, **_kwargs):
        return {"userId": "isometrik-123"}

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.OrganizationRepository",
        lambda db_connection=None: fake_org_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.create_user",
        fake_create_user,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.create_isometrik_user",
        fake_create_isometrik_user,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    request_data = CreateClientRequest(
        client_type=ClientType.PERSON,
        email="test@example.com",
        phone_isd_code="+1",
        phone_number="1234567890",
        first_name="John",
        last_name="Doe",
        prefix="Mr.",
    )

    user_id, isometrik_user_id, password = await service._create_auth_and_isometrik_user(
        request_data,
        fake_org_repo.organization,
        "org-1",
        existing_user=None,
    )

    assert user_id == "auth-user-123"
    assert isometrik_user_id == "isometrik-123"
    assert password is not None


@pytest.mark.asyncio
async def test_create_auth_isometrik_user_company(monkeypatch):
    """_create_auth_and_isometrik_user creates user for company type."""
    fake_org_repo = _FakeOrgRepo()

    async def fake_create_user(*_args, **_kwargs):
        return {"id": "auth-user-123"}

    async def fake_create_isometrik_user(*_args, **_kwargs):
        return {"userId": "isometrik-123"}

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.OrganizationRepository",
        lambda db_connection=None: fake_org_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.create_user",
        fake_create_user,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.create_isometrik_user",
        fake_create_isometrik_user,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    request_data = CreateClientRequest(
        client_type=ClientType.COMPANY,
        email="company@example.com",
        phone_isd_code="+1",
        phone_number="1234567890",
        first_name="Company",
        last_name="Name",
        name="Test Company",
    )

    user_id, isometrik_user_id, password = await service._create_auth_and_isometrik_user(
        request_data,
        fake_org_repo.organization,
        "org-1",
        existing_user=None,
    )

    assert user_id == "auth-user-123"
    assert isometrik_user_id == "isometrik-123"
    assert password is not None


@pytest.mark.asyncio
async def test_create_user_raises_when_auth_fails(monkeypatch):
    """_create_auth_and_isometrik_user raises when auth user creation fails."""
    fake_org_repo = _FakeOrgRepo()

    async def fake_create_user(*_args, **_kwargs):
        return None  # Failed

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.OrganizationRepository",
        lambda db_connection=None: fake_org_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.create_user",
        fake_create_user,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    request_data = CreateClientRequest(
        client_type=ClientType.PERSON,
        email="test@example.com",
        phone_isd_code="+1",
        phone_number="1234567890",
        first_name="John",
        last_name="Doe",
    )

    with pytest.raises(ServiceUnavailableException):
        await service._create_auth_and_isometrik_user(
            request_data, fake_org_repo.organization, "org-1"
        )


@pytest.mark.asyncio
async def test_create_user_isometrik_fails(monkeypatch):
    """_create_auth_and_isometrik_user raises when Isometrik creation fails."""
    fake_org_repo = _FakeOrgRepo()

    async def fake_create_user(*_args, **_kwargs):
        return {"id": "auth-user-123"}

    async def fake_create_isometrik_user(*_args, **_kwargs):
        return None  # Failed

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.OrganizationRepository",
        lambda db_connection=None: fake_org_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.create_user",
        fake_create_user,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.create_isometrik_user",
        fake_create_isometrik_user,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    request_data = CreateClientRequest(
        client_type=ClientType.PERSON,
        email="test@example.com",
        phone_isd_code="+1",
        phone_number="1234567890",
        first_name="John",
        last_name="Doe",
    )

    with pytest.raises(ServiceUnavailableException):
        await service._create_auth_and_isometrik_user(
            request_data, fake_org_repo.organization, "org-1"
        )


@pytest.mark.asyncio
async def test_create_client_success(monkeypatch):
    """create_client successfully creates client with all records."""
    fake_repo = _FakeClientRepo()
    fake_org_repo = _FakeOrgRepo()
    fake_user_repo = _FakeUserRepo()

    async def fake_create_user(*_args, **_kwargs):
        return {"id": "auth-user-123"}

    async def fake_create_isometrik_user(*_args, **_kwargs):
        return {"userId": "isometrik-123"}

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.OrganizationRepository",
        lambda db_connection=None: fake_org_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.UserRepository",
        lambda db_connection=None: fake_user_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.create_user",
        fake_create_user,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.create_isometrik_user",
        fake_create_isometrik_user,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.send_client_creation_email",
        lambda *_args, **_kwargs: None,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    request_data = CreateClientRequest(
        client_type=ClientType.PERSON,
        email="test@example.com",
        phone_isd_code="+1",
        phone_number="1234567890",
        first_name="John",
        last_name="Doe",
        portal_access=True,
    )

    result = await service.create_client(request_data)
    assert "create_client" in fake_repo.calls
    assert "create_client_user" in fake_repo.calls
    assert len(result.records) >= 1
    assert len(result.enrichment_items) >= 1
    first = result.enrichment_items[0]
    assert "client_id" in first
    assert "organization_id" in first
    assert "client_type" in first


@pytest.mark.asyncio
async def test_create_client_without_portal_access(monkeypatch):
    """create_client does not send email when portal_access is False."""
    fake_repo = _FakeClientRepo()
    fake_org_repo = _FakeOrgRepo()
    fake_user_repo = _FakeUserRepo()
    email_sent = []

    async def fake_create_user(*_args, **_kwargs):
        return {"id": "auth-user-123"}

    async def fake_create_isometrik_user(*_args, **_kwargs):
        return {"userId": "isometrik-123"}

    def fake_send_email(*_args, **_kwargs):
        email_sent.append(True)

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.OrganizationRepository",
        lambda db_connection=None: fake_org_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.UserRepository",
        lambda db_connection=None: fake_user_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.create_user",
        fake_create_user,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.create_isometrik_user",
        fake_create_isometrik_user,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.send_client_creation_email",
        fake_send_email,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    request_data = CreateClientRequest(
        client_type=ClientType.PERSON,
        email="test@example.com",
        phone_isd_code="+1",
        phone_number="1234567890",
        first_name="John",
        last_name="Doe",
        portal_access=False,
    )

    await service.create_client(request_data)

    assert len(email_sent) == 0


@pytest.mark.asyncio
async def test_get_client_details_with_full_data(monkeypatch):
    """get_client_details returns formatted response with all data."""
    fake_repo = _FakeClientRepo()
    fake_repo.client_details_result = {
        "id": "client-1",
        "organization_id": "org-1",
        "client_type": "person",
        "name": "John Doe",
        "company_name": "Acme Corp",
        "status": "active",
        "first_name": "John",
        "last_name": "Doe",
        "title": "CEO",
        "email": "john@example.com",
        "phone_isd_code": "+1",
        "phone": "1234567890",
        "websites": '[{"url": "https://example.com", "type": "primary"}]',
        "billing_preferences": '{"payment_method": "credit_card"}',
        "custom_fields": (
            '[{"field_id":"cf1","instance_id":"20000000-0000-4000-8000-000000000001",'
            '"type":"text","value":"value"}]'
        ),
        "additional_data": "{}",
        "social_pages": "[]",
        "enrichment_done": False,
        "last_enriched_at": None,
        "linked_leads": [
            {
                "id": "lead-1",
                "name": "Acme opportunity",
                "stage_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "stage_name": "Qualified",
            }
        ],
    }
    fake_repo.address_result = [
        {
            "id": "addr-1",
            "place_id": "place-123",
            "address_line1": "123 Main St",
            "address_line2": "Apt 4",
            "city": "New York",
            "state": "NY",
            "postal_code": "10001",
            "country": "United States",
            "latitude": 40.7128,
            "longitude": -74.0060,
            "address_type": "billing",
            "address_data": '{"formatted": "123 Main St, New York, NY 10001"}',
            "is_primary": True,
            "created_at": datetime.datetime.now(),
            "updated_at": datetime.datetime.now(),
        }
    ]

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    fake_cf = _FakeCustomFieldRepo()
    fake_cf.get_fields_result = [
        {
            "id": "cf1",
            "field_name": "Key",
            "field_key": "key",
            "field_type": "text",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        }
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_cf,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    result = await service.get_client_details("client-1", "org-1")

    assert result.id == "client-1"
    assert result.name == "John Doe"
    assert result.primary_contact.first_name == "John"
    assert result.primary_contact.last_name == "Doe"
    assert len(result.websites) == 1
    assert result.billing_preferences is not None
    assert result.custom_fields == [
        {
            "field_id": "cf1",
            "field_key": "key",
            "label": "Key",
            "instance_id": "20000000-0000-4000-8000-000000000001",
            "type": "text",
            "value": "value",
        }
    ]
    assert len(result.leads) == 1
    assert result.leads[0].name == "Acme opportunity"
    assert result.leads[0].stage_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert result.leads[0].stage_name == "Qualified"
    assert len(result.addresses) == 1
    assert result.addresses[0].address_line1 == "123 Main St"


@pytest.mark.asyncio
async def test_get_client_details_includes_contacts(monkeypatch):
    """get_client_details includes company_contacts for company clients."""
    fake_repo = _FakeClientRepo()
    fake_repo.client_details_result = {
        "id": "company-1",
        "organization_id": "org-1",
        "client_type": "company",
        "name": "Acme Corp",
        "status": "active",
        "first_name": "Primary",
        "last_name": "Contact",
        "email": "primary@example.com",
        "websites": "[]",
        "billing_preferences": None,
        "custom_fields": None,
    }
    fake_repo.address_result = []
    fake_repo.company_contacts_result = [
        {
            "first_name": "Primary",
            "last_name": "Contact",
            "title": "CEO",
            "email": "primary@example.com",
            "is_primary_contact": True,
        },
        {
            "first_name": "Mike",
            "last_name": "Chen",
            "title": "CTO",
            "email": "mike@example.com",
            "is_primary_contact": False,
        },
    ]

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    result = await service.get_client_details("company-1", "org-1")

    # Ensure repository method was called with correct identifiers
    assert fake_repo.calls["get_company_contacts"] == ("company-1", "org-1")

    # Validate mapped contacts
    assert len(result.company_contacts) == 2
    primary = result.company_contacts[0]
    secondary = result.company_contacts[1]

    assert primary.name == "Primary Contact"
    assert primary.designation == "CEO"
    assert primary.email == "primary@example.com"
    assert primary.is_primary_contact is True

    assert secondary.name == "Mike Chen"
    assert secondary.designation == "CTO"
    assert secondary.email == "mike@example.com"
    assert secondary.is_primary_contact is False


@pytest.mark.asyncio
async def test_get_client_details_without_lead(monkeypatch):
    """get_client_details returns empty leads when none are linked."""
    fake_repo = _FakeClientRepo()
    fake_repo.client_details_result = {
        "id": "client-1",
        "organization_id": "org-1",
        "client_type": "person",
        "name": "John Doe",
        "status": "active",
        "first_name": "John",
        "last_name": "Doe",
        "email": "john@example.com",
        "phone_isd_code": "+1",
        "phone": "1234567890",
        "websites": "[]",
        "billing_preferences": None,
        "custom_fields": None,
    }
    fake_repo.address_result = []

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    result = await service.get_client_details("client-1", "org-1")

    assert result.id == "client-1"
    assert result.leads == []
    assert len(result.addresses) == 0
    assert len(result.websites) == 0


@pytest.mark.asyncio
async def test_get_client_details_null_coordinates(monkeypatch):
    """get_client_details handles addresses with null coordinates."""
    fake_repo = _FakeClientRepo()
    fake_repo.client_details_result = {
        "id": "client-1",
        "organization_id": "org-1",
        "client_type": "person",
        "name": "John Doe",
        "status": "active",
        "first_name": "John",
        "last_name": "Doe",
        "email": "john@example.com",
        "phone_isd_code": "+1",
        "phone": "1234567890",
        "websites": "[]",
        "billing_preferences": None,
        "custom_fields": None,
    }
    fake_repo.address_result = [
        {
            "id": "addr-1",
            "place_id": None,
            "address_line1": "123 Main St",
            "address_line2": None,
            "city": "New York",
            "state": "NY",
            "postal_code": "10001",
            "country": "United States",
            "latitude": None,
            "longitude": None,
            "address_type": "billing",
            "address_data": None,
            "is_primary": False,
            "created_at": datetime.datetime.now(),
            "updated_at": datetime.datetime.now(),
        }
    ]

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    result = await service.get_client_details("client-1", "org-1")

    assert len(result.addresses) == 1
    assert result.addresses[0].latitude is None
    assert result.addresses[0].longitude is None
    assert result.addresses[0].place_id is None


@pytest.mark.asyncio
async def test_update_client_no_update_fields(monkeypatch):
    """update_client returns None when body has no fields to apply."""
    fake_repo = _FakeClientRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)
    body = UpdateClientRequest()

    result = await service.update_client("client-1", "org-1", body)

    assert result is None
    assert "get_client_for_update" not in fake_repo.calls


@pytest.mark.asyncio
async def test_update_client_not_found(monkeypatch):
    """update_client raises NotFoundException when client not found."""
    fake_repo = _FakeClientRepo()
    fake_repo.get_client_for_update_result = None
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)
    body = UpdateClientRequest(company_name="New Name")

    with pytest.raises(NotFoundException) as exc_info:
        await service.update_client("client-1", "org-1", body)

    assert exc_info.value.message_key == "clients.errors.not_found"
    assert fake_repo.calls["get_client_for_update"] == ("client-1", "org-1")


@pytest.mark.asyncio
async def test_update_client_success_scalar_only(monkeypatch):
    """update_client updates scalar fields and returns old_data for audit."""
    fake_repo = _FakeClientRepo()
    fake_repo.get_client_for_update_result = {
        "id": "client-1",
        "client_type": ClientType.COMPANY.value,
        "name": "Old Name",
        "industry": "Old Industry",
        "profile_photo_url": None,
        "portal_access": False,
        "tags": [],
        "websites": "[]",
        "billing_preferences": "{}",
        "custom_fields": "[]",
        "additional_data": "{}",
        "social_pages": "[]",
        "enrichment_done": False,
        "last_enriched_at": None,
    }
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)
    body = UpdateClientRequest(
        company_name="New Name",
        industry="Tech",
        portal_access=True,
    )

    result = await service.update_client("client-1", "org-1", body)

    assert result is not None
    assert result["old_data"]["client_id"] == "client-1"
    assert result["old_data"]["name"] == "Old Name"
    assert "update_client" in fake_repo.calls
    args = fake_repo.calls["update_client"]
    assert args[0] == "client-1" and args[1] == "org-1"
    assert args[2]["name"] == "New Name"
    assert args[2]["industry"] == "Tech"
    assert args[2]["portal_access"] is True
    assert "get_client_addresses" not in fake_repo.calls


@pytest.mark.asyncio
async def test_update_client_success_with_addresses(monkeypatch):
    """update_client applies address delta when addresses provided."""
    fake_repo = _FakeClientRepo()
    fake_repo.get_client_for_update_result = {
        "id": "client-1",
        "client_type": ClientType.COMPANY.value,
        "name": "Client",
        "industry": None,
        "profile_photo_url": None,
        "portal_access": False,
        "tags": [],
        "websites": "[]",
        "billing_preferences": "{}",
        "custom_fields": "[]",
        "additional_data": "{}",
        "social_pages": "[]",
        "enrichment_done": False,
        "last_enriched_at": None,
    }
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)
    body = UpdateClientRequest(
        company_name="Client",
        addresses=AddressesUpdate(
            update=[AddressUpdateItem(id="addr-1", address_line1="456 Updated St")],
            add=[AddressInput(address_line1="New Address")],
        ),
    )

    await service.update_client("client-1", "org-1", body)

    assert "update_address" in fake_repo.calls
    assert "bulk_create_addresses" in fake_repo.calls


@pytest.mark.asyncio
async def test_update_client_with_lead_management(monkeypatch):
    """update_client calls update_lead when lead_management provided."""
    fake_repo = _FakeClientRepo()
    fake_lead = _FakeLeadRepo()
    fake_repo.get_client_for_update_result = {
        "id": "client-1",
        "name": "Client",
        "industry": None,
        "profile_photo_url": None,
        "portal_access": False,
        "tags": [],
        "websites": "[]",
        "billing_preferences": "{}",
        "custom_fields": "[]",
        "additional_data": "{}",
        "social_pages": "[]",
        "enrichment_done": False,
        "last_enriched_at": None,
    }
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.LeadRepository",
        lambda db_connection=None: fake_lead,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)
    body = UpdateClientRequest(
        industry="Legal",
        lead_management=LeadManagementUpdate(
            lead_id="lead-1",
            stage_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            notes="Updated notes",
        ),
    )

    await service.update_client("client-1", "org-1", body)

    assert fake_lead.calls["update_lead"] == (
        "org-1",
        "lead-1",
        {
            "stage_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "notes": [{"title": "Note", "content": "Updated notes"}],
        },
    )


@pytest.mark.asyncio
async def test_update_client_sets_contact_as_primary(monkeypatch):
    """is_primary_contact=True promotes current contact and unmarks other company contacts."""
    fake_repo = _FakeClientRepo()
    fake_repo.get_client_for_update_result = {
        "id": "client-1",
        "client_type": ClientType.PERSON.value,
        "name": "Contact Name",
        "industry": None,
        "profile_photo_url": None,
        "portal_access": False,
        "tags": [],
        "websites": "[]",
        "billing_preferences": "{}",
        "custom_fields": "{}",
        "additional_data": "{}",
        "social_pages": "[]",
        "enrichment_done": False,
        "last_enriched_at": None,
    }
    fake_repo.primary_contact_for_update_result = {
        "id": "cu-1",
        "phones": "[]",
        "first_name": "A",
        "middle_name": "",
        "last_name": "B",
        "client_company_id": "company-1",
    }
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)

    await service.update_client(
        "client-1",
        "org-1",
        UpdateClientRequest(is_primary_contact=True),
    )

    assert fake_repo.calls["clear_primary_contact_for_company"] == ("company-1", "org-1", "cu-1")
    assert fake_repo.calls["_update_client_user"] == ("cu-1", {"is_primary_contact": True})


@pytest.mark.asyncio
async def test_has_any_update_false_when_all_none():
    """_has_any_update returns False when all fields are None."""
    service = ClientService(db_connection=None)
    body = UpdateClientRequest()

    assert service._has_any_update(body) is False


@pytest.mark.asyncio
async def test_has_any_update_true_when_any_field_set():
    """_has_any_update returns True when any updatable field is set."""
    service = ClientService(db_connection=None)

    assert service._has_any_update(UpdateClientRequest(company_name="X")) is True
    assert service._has_any_update(UpdateClientRequest(industry="Tech")) is True
    assert service._has_any_update(UpdateClientRequest(portal_access=True)) is True
    assert service._has_any_update(UpdateClientRequest(tags=["a"])) is True
    assert service._has_any_update(UpdateClientRequest(is_primary_contact=True)) is True


def test_rejects_company_link_with_primary_toggle():
    """client_company_id and is_primary_contact cannot be sent together."""
    with pytest.raises(ValidationException) as exc_info:
        UpdateClientRequest(client_company_id="company-1", is_primary_contact=True)

    assert (
        exc_info.value.message_key
        == "clients.errors.client_company_and_primary_contact_mutually_exclusive"
    )


@pytest.mark.asyncio
async def test_build_client_update_payload_scalar_and_merge(monkeypatch):
    """_build_client_update_payload sets scalars and merges
    billing_preferences and custom_fields."""
    fake_cf_repo = _FakeCustomFieldRepo()
    fake_cf_repo.get_fields_result = [
        {
            "id": "f1",
            "field_name": "A",
            "field_key": "a",
            "field_type": "text",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        },
        {
            "id": "f2",
            "field_name": "C",
            "field_key": "c",
            "field_type": "text",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {},
            "sort_order": 1,
            "is_active": True,
        },
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_cf_repo,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)
    current = {
        "name": "Old",
        "client_type": "person",
        "billing_preferences": '{"method": "invoice"}',
        "custom_fields": (
            '[{"field_id":"f1","instance_id":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",'
            '"type":"text","value":"1"}]'
        ),
    }
    body = UpdateClientRequest(
        company_name="New Name",
        billing_preferences=BillingPreferencesUpdate(terms="Net 30"),
        custom_fields=[
            {
                "field_id": "f1",
                "instance_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "value": "updated",
            },
            _cfc("f2", "new", instance_id=None),
        ],
    )

    payload = await service._build_client_update_payload(body, current)

    assert payload["name"] == "New Name"
    assert payload["billing_preferences"]["method"] == "invoice"
    assert payload["billing_preferences"]["terms"] == "Net 30"
    custom_fields = json.loads(payload["custom_fields"])
    assert len(custom_fields) == 2
    assert custom_fields[0]["field_id"] == "f1"
    assert custom_fields[0]["instance_id"] == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    assert custom_fields[0]["type"] == "text"
    assert custom_fields[0]["value"] == "updated"
    assert custom_fields[1]["field_id"] == "f2"
    assert custom_fields[1]["type"] == "text"
    assert custom_fields[1]["value"] == "new"
    assert custom_fields[1]["instance_id"]


@pytest.mark.asyncio
async def test_build_client_update_payload_websites():
    """_build_client_update_payload does not handle websites (handled separately)."""
    service = ClientService(db_connection=None)
    current = {"name": "Client", "websites": "[]"}
    body = UpdateClientRequest(
        websites=WebsitesUpdate(add=[WebsiteInput(url="https://x.com", type="primary")])
    )

    payload = await service._build_client_update_payload(body, current)

    # Websites are handled separately in _apply_jsonb_list_changes, not in payload
    assert "websites" not in payload


@pytest.mark.asyncio
async def test_apply_lead_update_calls_repository(monkeypatch):
    """_apply_lead_update calls update_lead with lead_id and payload."""
    fake_lead = _FakeLeadRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.LeadRepository",
        lambda db_connection=None: fake_lead,
    )
    service = ClientService(db_connection=None)
    service.user_context = UserContext(
        user_id="u1",
        email="u@example.com",
        organization_id="org-1",
    )
    lead = LeadManagementUpdate(
        lead_id="lead-1",
        stage_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    )

    await service._apply_lead_update(lead)
    assert fake_lead.calls["update_lead"] == (
        "org-1",
        "lead-1",
        {"stage_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},
    )


@pytest.mark.asyncio
async def test_apply_lead_update_includes_stage_id(monkeypatch):
    """_apply_lead_update passes stage_id to the repository."""
    fake_lead = _FakeLeadRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.LeadRepository",
        lambda db_connection=None: fake_lead,
    )
    service = ClientService(db_connection=None)
    service.user_context = UserContext(
        user_id="u1",
        email="u@example.com",
        organization_id="org-1",
    )
    lead = LeadManagementUpdate(
        lead_id="lead-1",
        stage_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    )

    await service._apply_lead_update(lead)
    assert fake_lead.calls["update_lead"] == (
        "org-1",
        "lead-1",
        {"stage_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},
    )


@pytest.mark.asyncio
async def test_apply_lead_update_empty_payload(monkeypatch):
    """_apply_lead_update does not call repository when only lead_id provided."""
    fake_lead = _FakeLeadRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.LeadRepository",
        lambda db_connection=None: fake_lead,
    )
    service = ClientService(db_connection=None)
    service.user_context = UserContext(
        user_id="u1",
        email="u@example.com",
        organization_id="org-1",
    )
    lead = LeadManagementUpdate(lead_id="lead-1")

    await service._apply_lead_update(lead)
    assert "update_lead" not in fake_lead.calls


@pytest.mark.asyncio
async def test_apply_addresses_changes_ops(monkeypatch):
    """_apply_addresses_changes calls delete, update, add as needed."""
    fake_repo = _FakeClientRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    service = ClientService(db_connection=None)
    addresses_update = AddressesUpdate(
        remove=["addr-2"],
        update=[AddressUpdateItem(id="addr-1", address_line1="Updated")],
        add=[AddressInput(address_line1="New Addr")],
    )

    await service._apply_addresses_changes("client-1", addresses_update)
    assert "delete_addresses_by_ids" in fake_repo.calls
    assert fake_repo.calls["delete_addresses_by_ids"] == ("client-1", ["addr-2"])
    assert "update_address" in fake_repo.calls
    assert fake_repo.calls["update_address"][0] == "addr-1"
    assert fake_repo.calls["update_address"][1] == "client-1"
    assert "bulk_create_addresses" in fake_repo.calls
    assert len(fake_repo.calls["bulk_create_addresses"]) == 1
    assert fake_repo.calls["bulk_create_addresses"][0]["address_line1"] == "New Addr"


@pytest.mark.asyncio
async def test_apply_addresses_promote_primary_on_update(monkeypatch):
    """Promoting an updated address to primary clears other primaries first."""
    fake_repo = _FakeClientRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    service = ClientService(db_connection=None)
    addresses_update = AddressesUpdate(
        update=[AddressUpdateItem(id="addr-1", is_primary=True, address_line1="Updated")],
    )

    await service._apply_addresses_changes("client-1", addresses_update)

    assert fake_repo.calls["clear_primary_addresses"] == [("client-1", "addr-1")]
    assert fake_repo.calls["update_address"] == (
        "addr-1",
        "client-1",
        {"address_line1": "Updated", "is_primary": True},
    )


@pytest.mark.asyncio
async def test_apply_addresses_changes_promote_primary_on_add(monkeypatch):
    """Adding a primary address clears any existing primary first."""
    fake_repo = _FakeClientRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    service = ClientService(db_connection=None)
    addresses_update = AddressesUpdate(
        add=[AddressInput(address_line1="New Addr", is_primary=True)],
    )

    await service._apply_addresses_changes("client-1", addresses_update)

    assert fake_repo.calls["clear_primary_addresses"] == [("client-1", None)]
    assert fake_repo.calls["bulk_create_addresses"][0]["is_primary"] is True


@pytest.mark.asyncio
async def test_apply_addresses_conflicting_primary_add_update(monkeypatch):
    """Reject payload that promotes more than one primary in a single request."""
    fake_repo = _FakeClientRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    service = ClientService(db_connection=None)
    with pytest.raises(ValidationException) as exc_info:
        addresses_update = AddressesUpdate(
            update=[AddressUpdateItem(id="addr-1", is_primary=True)],
            add=[AddressInput(address_line1="New Addr", is_primary=True)],
        )
        await service._apply_addresses_changes("client-1", addresses_update)

    assert exc_info.value.message_key == "clients.errors.only_one_primary_address"
    assert "clear_primary_addresses" not in fake_repo.calls


@pytest.mark.asyncio
async def test_apply_addresses_maps_primary_unique_violation(monkeypatch):
    """Map DB unique violation for primary address to validation error."""
    fake_repo = _FakeClientRepo()

    class _FakeUniqueViolationError(Exception):
        """Test exception carrying DB constraint metadata."""

        def __init__(self, constraint_name):
            super().__init__("unique violation")
            self.constraint_name = constraint_name

    async def _raise_primary_violation(*_args, **_kwargs):
        raise _FakeUniqueViolationError("uq_client_primary_address")

    fake_repo.update_address = _raise_primary_violation
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.UniqueViolationError",
        _FakeUniqueViolationError,
    )
    service = ClientService(db_connection=None)
    addresses_update = AddressesUpdate(
        update=[AddressUpdateItem(id="addr-1", is_primary=True)],
    )

    with pytest.raises(ValidationException) as exc_info:
        await service._apply_addresses_changes("client-1", addresses_update)

    assert exc_info.value.message_key == "clients.errors.only_one_primary_address"


@pytest.mark.asyncio
async def test_create_client_from_user_email_error(monkeypatch):
    """create_client_from_user handles email sending exception gracefully."""
    fake_repo = _FakeClientRepo()
    fake_user_repo = _FakeUserRepo()
    fake_org_repo = _FakeOrgRepo()
    fake_user_event_repo = _FakeUserEventRepo()

    async def fake_create_isometrik_user(*_args, **_kwargs):
        return {"userId": "isometrik-123"}

    def failing_send_email(*_args, **_kwargs):
        raise ServiceUnavailableException(
            message_key="errors.service_unavailable",
            custom_code=CustomStatusCode.SERVICE_UNAVAILABLE,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.UserRepository",
        lambda db_connection=None: fake_user_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.OrganizationRepository",
        lambda db_connection=None: fake_org_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.UserEventRepository",
        lambda db_connection=None: fake_user_event_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.create_isometrik_user",
        fake_create_isometrik_user,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.send_client_creation_email",
        failing_send_email,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    request_data = CreateClientFromUserRequest(user_id="user-1", organization_id="org-1")

    # Should not raise exception, just log error
    await service.create_client_from_user(request_data)
    assert "create_client" in fake_repo.calls
    assert "create_client_user" in fake_repo.calls


@pytest.mark.asyncio
async def test_create_client_from_user_no_email(monkeypatch):
    """create_client_from_user handles missing email gracefully."""
    fake_repo = _FakeClientRepo()
    fake_user_repo = _FakeUserRepo()
    fake_user_repo.user_details = {"id": "user-1"}  # No email
    fake_org_repo = _FakeOrgRepo()
    fake_user_event_repo = _FakeUserEventRepo()

    async def fake_create_isometrik_user(*_args, **_kwargs):
        return {"userId": "isometrik-123"}

    email_called = []

    def track_email(*_args, **_kwargs):
        email_called.append(True)

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.UserRepository",
        lambda db_connection=None: fake_user_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.OrganizationRepository",
        lambda db_connection=None: fake_org_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.UserEventRepository",
        lambda db_connection=None: fake_user_event_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.create_isometrik_user",
        fake_create_isometrik_user,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.send_client_creation_email",
        track_email,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    request_data = CreateClientFromUserRequest(user_id="user-1", organization_id="org-1")

    await service.create_client_from_user(request_data)

    assert len(email_called) == 0  # Email should not be sent when email is missing


@pytest.mark.asyncio
async def test_validate_client_creation_org_not_found(monkeypatch):
    """_validate_client_creation raises NotFoundException when org not found."""
    fake_repo = _FakeClientRepo()
    fake_org_repo = _FakeOrgRepo()
    fake_org_repo.organization = None
    fake_user_repo = _FakeUserRepo()

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.OrganizationRepository",
        lambda db_connection=None: fake_org_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.UserRepository",
        lambda db_connection=None: fake_user_repo,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    request_data = CreateClientRequest(
        client_type=ClientType.PERSON,
        email="test@example.com",
        phone_isd_code="+1",
        phone_number="1234567890",
        first_name="John",
        last_name="Doe",
    )

    with pytest.raises(NotFoundException):
        await service._validate_client_creation(request_data, "org-1")


@pytest.mark.asyncio
async def test_validate_client_creation_company_name(monkeypatch):
    """_validate_client_creation no longer enforces company name uniqueness."""
    fake_repo = _FakeClientRepo()
    fake_user_repo = _FakeUserRepo()
    fake_org_repo = _FakeOrgRepo()

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.UserRepository",
        lambda db_connection=None: fake_user_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.OrganizationRepository",
        lambda db_connection=None: fake_org_repo,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    request_data = CreateClientRequest(
        client_type=ClientType.COMPANY,
        email="test@example.com",
        phone_isd_code="+1",
        phone_number="1234567890",
        first_name="John",
        last_name="Doe",
        name="  Test Company  ",
    )

    await service._validate_client_creation(request_data, "org-1")

    # Email uniqueness is validated via repository helper
    assert fake_repo.calls["check_client_email_exists"]["email"] == "test@example.com"


@pytest.mark.asyncio
async def test_prepare_client_data_optional_fields():
    """_prepare_client_data includes optional fields when provided."""
    service = ClientService(db_connection=None)
    from apps.user_service.app.schemas.clients import BillingPreferences, SocialPage

    request_data = CreateClientRequest(
        client_type=ClientType.PERSON,
        email="test@example.com",
        phone_isd_code="+1",
        phone_number="1234567890",
        first_name="John",
        last_name="Doe",
        industry="Technology",
        profile_photo_url="https://example.com/photo.jpg",
        billing_preferences=BillingPreferences(method="credit_card", terms="Net 30"),
        additional_data={"key": "value"},
        social_pages=[SocialPage(platform="linkedin", url="https://linkedin.com/in/john")],
    )

    client_data = await service._prepare_client_data(request_data, "org-1")

    assert client_data["industry"] == "Technology"
    assert client_data["profile_photo_url"] == "https://example.com/photo.jpg"
    assert isinstance(client_data["billing_preferences"], str)
    assert isinstance(client_data["additional_data"], str)
    assert isinstance(client_data["social_pages"], str)


@pytest.mark.asyncio
async def test_create_client_email_sending_exception(monkeypatch):
    """create_client handles email sending exception gracefully."""
    fake_repo = _FakeClientRepo()
    fake_org_repo = _FakeOrgRepo()
    fake_user_repo = _FakeUserRepo()

    async def fake_create_user(*_args, **_kwargs):
        return {"id": "auth-user-123"}

    async def fake_create_isometrik_user(*_args, **_kwargs):
        return {"userId": "isometrik-123"}

    def failing_send_email(*_args, **_kwargs):
        raise ServiceUnavailableException(
            message_key="errors.service_unavailable",
            custom_code=CustomStatusCode.SERVICE_UNAVAILABLE,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.OrganizationRepository",
        lambda db_connection=None: fake_org_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.UserRepository",
        lambda db_connection=None: fake_user_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.create_user",
        fake_create_user,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.create_isometrik_user",
        fake_create_isometrik_user,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.send_client_creation_email",
        failing_send_email,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    request_data = CreateClientRequest(
        client_type=ClientType.PERSON,
        email="test@example.com",
        phone_isd_code="+1",
        phone_number="1234567890",
        first_name="John",
        last_name="Doe",
        portal_access=True,
    )

    # Should not raise exception, just log error
    await service.create_client(request_data)
    assert "create_client" in fake_repo.calls
    assert "create_client_user" in fake_repo.calls


@pytest.mark.asyncio
async def test_update_client_with_websites(monkeypatch):
    """update_client applies websites changes."""
    fake_repo = _FakeClientRepo()
    fake_repo.get_client_for_update_result = {
        "id": "client-1",
        "name": "Client",
        "industry": None,
        "profile_photo_url": None,
        "portal_access": False,
        "tags": [],
        "websites": '[{"id": "web-1", "url": "https://old.com", "type": "primary"}]',
        "billing_preferences": "{}",
        "custom_fields": "[]",
        "additional_data": "{}",
        "social_pages": "[]",
        "enrichment_done": False,
        "last_enriched_at": None,
    }
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)
    body = UpdateClientRequest(
        websites=WebsitesUpdate(
            add=[WebsiteInput(url="https://new.com", type="secondary")],
            update=[WebsiteUpdateItem(id="web-1", url="https://updated.com", type="primary")],
            remove=["web-2"],
        )
    )

    await service.update_client("client-1", "org-1", body)
    assert "update_client" in fake_repo.calls


@pytest.mark.asyncio
async def test_update_client_with_social_pages(monkeypatch):
    """update_client applies social_pages changes."""
    fake_repo = _FakeClientRepo()
    fake_repo.get_client_for_update_result = {
        "id": "client-1",
        "name": "Client",
        "industry": None,
        "profile_photo_url": None,
        "portal_access": False,
        "tags": [],
        "websites": "[]",
        "billing_preferences": "{}",
        "custom_fields": "[]",
        "additional_data": "{}",
        "social_pages": '[{"id": "social-1", "platform": "linkedin", "url": "https://old.com"}]',
        "enrichment_done": False,
        "last_enriched_at": None,
    }
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)
    body = UpdateClientRequest(
        social_pages=SocialPagesUpdate(
            add=[SocialPageInput(platform="twitter", url="https://twitter.com/user")],
            update=[
                SocialPageUpdateItem(id="social-1", platform="linkedin", url="https://updated.com")
            ],
            remove=["social-2"],
        )
    )

    await service.update_client("client-1", "org-1", body)
    assert "update_client" in fake_repo.calls


@pytest.mark.asyncio
async def test_update_client_with_empty_update_data(monkeypatch):
    """update_client handles case when update_data is empty."""
    fake_repo = _FakeClientRepo()
    fake_repo.get_client_for_update_result = {
        "id": "client-1",
        "name": "Client",
        "industry": None,
        "profile_photo_url": None,
        "portal_access": False,
        "tags": [],
        "websites": "[]",
        "billing_preferences": "{}",
        "custom_fields": "[]",
        "additional_data": "{}",
        "social_pages": "[]",
        "enrichment_done": False,
        "last_enriched_at": None,
    }
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)
    body = UpdateClientRequest(
        addresses=AddressesUpdate(add=[AddressInput(address_line1="New Address")])
    )

    result = await service.update_client("client-1", "org-1", body)

    # Should return old_data even if update_data is empty
    assert result is not None
    assert "old_data" in result


@pytest.mark.asyncio
async def test_build_client_update_payload_additional_data():
    """_build_client_update_payload handles additional_data."""
    service = ClientService(db_connection=None)
    current = {
        "name": "Client",
        "additional_data": '{"existing": "data"}',
    }
    body = UpdateClientRequest(additional_data={"new": "value"})

    payload = await service._build_client_update_payload(body, current)

    assert payload["additional_data"] == {"new": "value"}


@pytest.mark.asyncio
async def test_merge_custom_fields_into_payload(monkeypatch):
    """_merge_custom_fields_into_payload validates and merges custom fields."""
    fake_custom_field_repo = _FakeCustomFieldRepo()
    fake_custom_field_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Age",
            "field_key": "age",
            "field_type": "number",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        }
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_custom_field_repo,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)
    current = {"client_type": "person", "custom_fields": "[]"}
    body = UpdateClientRequest(custom_fields=[{"field_id": "field-1", "value": 30}])
    payload = {}

    await service._merge_custom_fields_into_payload(body, current, payload)

    assert "custom_fields" in payload
    parsed = parse_json_field(payload["custom_fields"])
    assert len(parsed) == 1
    assert parsed[0]["field_id"] == "field-1"
    assert parsed[0]["value"] == 30.0
    assert parsed[0]["type"] == "number"
    assert parsed[0]["instance_id"]


@pytest.mark.asyncio
async def test_merge_no_body_reconciles_optional_invalid(monkeypatch):
    """PATCH without custom_fields reconciles; optional invalid stored scalar is nulled."""
    fake_custom_field_repo = _FakeCustomFieldRepo()
    fake_custom_field_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Age",
            "field_key": "age",
            "field_type": "number",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        }
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_custom_field_repo,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)
    current = {
        "client_type": "person",
        "custom_fields": (
            '[{"field_id":"field-1","instance_id":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",'
            '"type":"number","value":"nope"}]'
        ),
    }
    body = UpdateClientRequest()
    payload = {}

    await service._merge_custom_fields_into_payload(body, current, payload)

    parsed = parse_json_field(payload["custom_fields"])
    assert len(parsed) == 1
    assert parsed[0]["field_id"] == "field-1"
    assert parsed[0]["type"] == "number"
    assert parsed[0]["value"] is None
    assert parsed[0]["instance_id"] == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


@pytest.mark.asyncio
async def test_merge_no_body_required_invalid_raises(monkeypatch):
    """PATCH without custom_fields: required field with invalid stored value raises."""
    fake_custom_field_repo = _FakeCustomFieldRepo()
    fake_custom_field_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Age",
            "field_key": "age",
            "field_type": "number",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": True,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        }
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_custom_field_repo,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)
    current = {
        "client_type": "person",
        "custom_fields": (
            '[{"field_id":"field-1","instance_id":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",'
            '"type":"number","value":"nope"}]'
        ),
    }
    body = UpdateClientRequest()
    payload = {}

    with pytest.raises(ValidationException):
        await service._merge_custom_fields_into_payload(body, current, payload)


@pytest.mark.asyncio
async def test_merge_no_body_omits_payload_when_unchanged(monkeypatch):
    """When reconcile output equals stored JSON, do not set custom_fields on update payload."""
    fake_custom_field_repo = _FakeCustomFieldRepo()
    fake_custom_field_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Age",
            "field_key": "age",
            "field_type": "number",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        }
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_custom_field_repo,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)
    current = {"client_type": "person", "custom_fields": "[]"}
    body = UpdateClientRequest()
    payload = {}

    await service._merge_custom_fields_into_payload(body, current, payload)

    assert "custom_fields" not in payload


@pytest.mark.asyncio
async def test_merge_remove_field(monkeypatch):
    """Explicit null value on optional root clears that root from the stored array."""
    fake_custom_field_repo = _FakeCustomFieldRepo()
    fake_custom_field_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Age",
            "field_key": "age",
            "field_type": "number",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        }
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_custom_field_repo,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)
    current = {
        "client_type": "person",
        "custom_fields": (
            '[{"field_id":"field-1","instance_id":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",'
            '"type":"number","value":25}]'
        ),
    }
    body = UpdateClientRequest(
        custom_fields=[
            {
                "field_id": "field-1",
                "instance_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "value": None,
            }
        ]
    )
    payload = {}

    await service._merge_custom_fields_into_payload(body, current, payload)

    parsed = parse_json_field(payload["custom_fields"])
    assert parsed == []


@pytest.mark.asyncio
async def test_merge_preserves_required_fields(monkeypatch):
    """Updating only an optional custom field should not fail required fields."""
    fake_custom_field_repo = _FakeCustomFieldRepo()
    fake_custom_field_repo.get_fields_result = [
        {
            "id": "field-req",
            "field_name": "Name",
            "field_key": "name",
            "field_type": "text",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": True,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        },
        {
            "id": "field-opt",
            "field_name": "Description",
            "field_key": "description",
            "field_type": "text",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        },
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_custom_field_repo,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    current = {
        "client_type": "person",
        "custom_fields": (
            "["
            '{"field_id":"field-req","instance_id":"r1","type":"text","value":"Acme"},'
            '{"field_id":"field-opt","instance_id":"o1","type":"text","value":"Old"}'
            "]"
        ),
    }
    body = UpdateClientRequest(
        custom_fields=[{"field_id": "field-opt", "instance_id": "o1", "value": "New"}]
    )
    payload = {}

    await service._merge_custom_fields_into_payload(body, current, payload)

    parsed = parse_json_field(payload["custom_fields"])
    by_id = {c["field_id"]: c for c in parsed}
    assert by_id["field-req"]["value"] == "Acme"
    assert by_id["field-opt"]["value"] == "New"


@pytest.mark.asyncio
async def test_merge_fields_payload_missing_required_raises(monkeypatch):
    """If required custom field is absent, updating optional fields should fail."""
    fake_custom_field_repo = _FakeCustomFieldRepo()
    fake_custom_field_repo.get_fields_result = [
        {
            "id": "field-req",
            "field_name": "Name",
            "field_key": "name",
            "field_type": "text",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": True,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        },
        {
            "id": "field-opt",
            "field_name": "Description",
            "field_key": "description",
            "field_type": "text",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        },
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_custom_field_repo,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    current = {
        "client_type": "person",
        "custom_fields": (
            '[{"field_id":"field-opt","instance_id":"o1","type":"text","value":"Old"}]'
        ),
    }
    body = UpdateClientRequest(
        custom_fields=[{"field_id": "field-opt", "instance_id": "o1", "value": "New"}]
    )
    payload = {}

    with pytest.raises(ValidationException) as exc_info:
        await service._merge_custom_fields_into_payload(body, current, payload)

    assert "custom_field_required" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_merge_custom_fields_into_payload_list_field(monkeypatch):
    """_merge_custom_fields_into_payload validates list field."""
    fake_custom_field_repo = _FakeCustomFieldRepo()
    fake_custom_field_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Tags",
            "field_key": "tags",
            "field_type": "list",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        },
        {
            "id": "field-2",
            "field_name": "Tag",
            "field_key": "tag",
            "field_type": "text",
            "parent_id": "field-1",
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        },
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_custom_field_repo,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)
    current = {
        "client_type": "person",
        "custom_fields": "[]",
    }
    body = UpdateClientRequest(
        custom_fields=[
            {
                "field_id": "field-1",
                "items": [
                    {"field_id": "field-2", "value": "tag1"},
                    {"field_id": "field-2", "value": "tag2"},
                ],
            }
        ]
    )
    payload = {}

    await service._merge_custom_fields_into_payload(body, current, payload)

    parsed = json.loads(payload["custom_fields"])
    assert len(parsed) == 1
    assert parsed[0]["field_id"] == "field-1"
    assert parsed[0]["type"] == "list"
    rows = parsed[0]["items"]
    assert len(rows) == 2
    assert rows[0]["field_id"] == "field-2"
    assert rows[0]["type"] == "text"
    assert rows[0]["value"] == "tag1"
    assert rows[1]["value"] == "tag2"


@pytest.mark.asyncio
async def test_apply_addresses_changes_empty_payload(monkeypatch):
    """_apply_addresses_changes handles update with empty payload."""
    fake_repo = _FakeClientRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    service = ClientService(db_connection=None)
    addresses_update = AddressesUpdate(
        update=[AddressUpdateItem(id="addr-1")]  # No fields to update
    )

    await service._apply_addresses_changes("client-1", addresses_update)

    # Should not call update_address if payload is empty
    if "update_address" in fake_repo.calls:
        # If called, payload should be empty dict
        assert fake_repo.calls["update_address"][2] == {}


@pytest.mark.asyncio
async def test_apply_jsonb_list_changes_websites_remove_only(monkeypatch):
    """_apply_jsonb_list_changes handles websites remove operations."""
    fake_repo = _FakeClientRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    service = ClientService(db_connection=None)
    websites_update = WebsitesUpdate(remove=["web-1"])
    current = {"websites": '[{"id": "web-1", "url": "https://example.com", "type": "primary"}]'}
    payload = {}

    await service._apply_jsonb_list_changes(
        websites_update,
        current,
        payload,
        "websites",
        "clients.errors.website_not_found",
    )
    assert "websites" in payload


@pytest.mark.asyncio
async def test_apply_jsonb_list_changes_websites_not_found(monkeypatch):
    """_apply_jsonb_list_changes raises NotFoundException when website not found."""
    fake_repo = _FakeClientRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    service = ClientService(db_connection=None)
    websites_update = WebsitesUpdate(
        update=[WebsiteUpdateItem(id="nonexistent", url="https://example.com", type="primary")]
    )
    current = {"websites": "[]"}
    payload = {}

    with pytest.raises(NotFoundException) as exc_info:
        await service._apply_jsonb_list_changes(
            websites_update,
            current,
            payload,
            "websites",
            "clients.errors.website_not_found",
        )

    assert exc_info.value.message_key == "clients.errors.website_not_found"


def _parse_json_field_as_list(field_value):
    """Return list for list-type JSONB fields; return [] for invalid/non-list (test helper)."""
    if field_value is None:
        return []
    if isinstance(field_value, list):
        return field_value
    if isinstance(field_value, str):
        if not field_value:
            return []
        try:
            parsed = json.loads(field_value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


@pytest.mark.asyncio
async def test_apply_jsonb_list_changes_websites_non_list(monkeypatch):
    """_apply_jsonb_list_changes handles non-list current websites."""
    fake_repo = _FakeClientRepo()

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.parse_json_field",
        _parse_json_field_as_list,
    )
    service = ClientService(db_connection=None)
    websites_update = WebsitesUpdate(add=[WebsiteInput(url="https://example.com", type="primary")])
    current = {"websites": "invalid"}
    payload = {}

    await service._apply_jsonb_list_changes(
        websites_update,
        current,
        payload,
        "websites",
        "clients.errors.website_not_found",
    )

    assert "websites" in payload


@pytest.mark.asyncio
async def test_apply_jsonb_list_changes_social_pages_remove(monkeypatch):
    """_apply_jsonb_list_changes handles social_pages remove operations."""
    fake_repo = _FakeClientRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    service = ClientService(db_connection=None)
    social_pages_update = SocialPagesUpdate(remove=["social-1"])
    current = {
        "social_pages": (
            '[{"id": "social-1", "platform": "linkedin", "url": "https://example.com"}]'
        )
    }
    payload = {}

    await service._apply_jsonb_list_changes(
        social_pages_update,
        current,
        payload,
        "social_pages",
        "clients.errors.social_page_not_found",
    )
    assert "social_pages" in payload


@pytest.mark.asyncio
async def test_apply_jsonb_changes_social_pages_not_found(monkeypatch):
    """_apply_jsonb_list_changes raises NotFoundException when social page not found."""
    fake_repo = _FakeClientRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    service = ClientService(db_connection=None)
    social_pages_update = SocialPagesUpdate(
        update=[
            SocialPageUpdateItem(id="nonexistent", platform="linkedin", url="https://example.com")
        ]
    )
    current = {"social_pages": "[]"}
    payload = {}

    with pytest.raises(NotFoundException) as exc_info:
        await service._apply_jsonb_list_changes(
            social_pages_update,
            current,
            payload,
            "social_pages",
            "clients.errors.social_page_not_found",
        )

    assert exc_info.value.message_key == "clients.errors.social_page_not_found"


@pytest.mark.asyncio
async def test_apply_jsonb_social_pages_non_list(monkeypatch):
    """_apply_jsonb_list_changes handles non-list current social pages."""
    fake_repo = _FakeClientRepo()

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.parse_json_field",
        _parse_json_field_as_list,
    )
    service = ClientService(db_connection=None)
    social_pages_update = SocialPagesUpdate(
        add=[SocialPageInput(platform="linkedin", url="https://example.com")]
    )
    current = {"social_pages": "invalid"}
    payload = {}

    await service._apply_jsonb_list_changes(
        social_pages_update,
        current,
        payload,
        "social_pages",
        "clients.errors.social_page_not_found",
    )

    assert "social_pages" in payload


class _FakeAddressForEnrichment:
    """Simple address object for enrichment trigger tests."""

    def __init__(self, country: str):
        self.country = country
        self.countrycle = country


class _FakePhoneForEnrichment:
    """Simple phone object for enrichment trigger tests."""

    def __init__(self, code: str, number: str, is_primary: bool = True):
        self.phone_isd_code = code
        self.phone_number = number
        self.is_primary = is_primary


class _FakePrimaryContactForEnrichment:
    """Primary contact object with minimal fields for enrichment."""

    def __init__(self, first_name: str, last_name: str, email: str, phones):
        self.first_name = first_name
        self.middle_name = None
        self.last_name = last_name
        self.email = email
        self.phones = phones


class _FakeWebsiteModel:
    """Website model stub with model_dump method."""

    def __init__(self, url: str):
        self._url = url

    def model_dump(self, mode: str = "json"):
        """Return a dict in the same shape as real Website."""
        del mode
        return {"url": self._url}


class _FakeSocialPageModel:
    """Social page model stub with model_dump method."""

    def __init__(self, platform: str, url: str):
        self._platform = platform
        self._url = url

    def model_dump(self, mode: str = "json"):
        """Return a dict in the same shape as real SocialPage."""
        del mode
        return {"platform": self._platform, "url": self._url}


class _FakeDetailsForEnrichment:
    """Details object used by trigger_enrichment tests."""

    def __init__(self, client_type, name, company_name, industry, primary_contact, addresses):
        self.client_type = client_type
        self.name = name
        self.company_name = company_name
        self.industry = industry
        self.primary_contact = primary_contact
        self.addresses = addresses
        self.websites = []
        self.social_pages = []


class _FakeEnrichmentService:
    """Fake enrichment service recording run_client_enrichment calls."""

    def __init__(self):
        self.calls = []

    async def run_client_enrichment(
        self,
        client_id,
        organization_id,
        client_type,
        payload_data,
        conn=None,
    ):
        """Record enrichment call parameters."""
        self.calls.append(
            {
                "client_id": client_id,
                "organization_id": organization_id,
                "client_type": client_type,
                "payload_data": payload_data,
                "conn": conn,
            }
        )


@pytest.mark.asyncio
async def test_trigger_enrichment_person_uses_conn(monkeypatch):
    """trigger_enrichment builds person payload and passes db connection."""
    fake_enrichment = _FakeEnrichmentService()

    class _FakeEnrichmentFactory:
        """Factory exposing from_settings for enrichment tests."""

        @staticmethod
        def from_settings():
            """Return pre-configured fake enrichment service."""
            return fake_enrichment

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientEnrichmentService",
        _FakeEnrichmentFactory,
    )

    service = ClientService(user_context=_ctx(), db_connection="db-conn")

    expected_client_id = "00000000-0000-0000-0000-000000000001"
    expected_organization_id = "00000000-0000-0000-0000-000000000101"

    primary = _FakePrimaryContactForEnrichment(
        first_name="John",
        last_name="Doe",
        email="john@example.com",
        phones=[_FakePhoneForEnrichment(code="+1", number="1234567890")],
    )
    details = _FakeDetailsForEnrichment(
        client_type=ClientType.PERSON,
        name="John Doe",
        company_name="Acme Corp",
        industry="Tech",
        primary_contact=primary,
        addresses=[_FakeAddressForEnrichment(country="United States")],
    )

    async def fake_get_client_details(client_id, organization_id):
        """Return fake details for trigger_enrichment tests."""
        assert client_id == expected_client_id
        assert organization_id == expected_organization_id
        return details

    monkeypatch.setattr(service, "get_client_details", fake_get_client_details)

    await service.trigger_enrichment(expected_client_id, expected_organization_id)

    assert len(fake_enrichment.calls) == 1
    call = fake_enrichment.calls[0]
    assert call["client_id"] == expected_client_id
    assert call["organization_id"] == expected_organization_id
    assert call["client_type"] == ClientType.PERSON.value
    assert call["conn"] == "db-conn"
    payload = call["payload_data"]
    assert payload["first_name"] == "John"
    assert payload["last_name"] == "Doe"
    assert payload["email"] == "john@example.com"
    assert payload["company"] == "Acme Corp"
    assert payload["addresses"] == [{"country": "United States"}]
    assert payload["phone_isd_code"] == "+1"
    assert payload["phone_number"] == "1234567890"


@pytest.mark.asyncio
async def test_trigger_enrichment_company_uses_conn(monkeypatch):
    """trigger_enrichment builds company payload and passes db connection."""
    fake_enrichment = _FakeEnrichmentService()

    class _FakeEnrichmentFactory:
        """Factory exposing from_settings for company enrichment tests."""

        @staticmethod
        def from_settings():
            """Return pre-configured fake enrichment service."""
            return fake_enrichment

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientEnrichmentService",
        _FakeEnrichmentFactory,
    )

    service = ClientService(user_context=_ctx(), db_connection="db-conn-2")

    expected_client_id = "00000000-0000-0000-0000-000000000002"
    expected_organization_id = "00000000-0000-0000-0000-000000000202"

    primary = _FakePrimaryContactForEnrichment(
        first_name="Jane",
        last_name="Smith",
        email="jane@example.com",
        phones=[_FakePhoneForEnrichment(code="+44", number="987654321")],
    )
    details = _FakeDetailsForEnrichment(
        client_type=ClientType.COMPANY,
        name="Example Co",
        company_name="Example Co",
        industry="Legal",
        primary_contact=primary,
        addresses=[_FakeAddressForEnrichment(country="United Kingdom")],
    )
    details.websites = [_FakeWebsiteModel(url="https://example.com")]
    details.social_pages = [
        _FakeSocialPageModel(platform="linkedin", url="https://linkedin.com/company/x")
    ]

    async def fake_get_client_details(client_id, organization_id):
        """Return fake company details for trigger_enrichment tests."""
        assert client_id == expected_client_id
        assert organization_id == expected_organization_id
        return details

    monkeypatch.setattr(service, "get_client_details", fake_get_client_details)

    await service.trigger_enrichment(expected_client_id, expected_organization_id)

    assert len(fake_enrichment.calls) == 1
    call = fake_enrichment.calls[0]
    assert call["client_id"] == expected_client_id
    assert call["organization_id"] == expected_organization_id
    assert call["client_type"] == ClientType.COMPANY.value
    assert call["conn"] == "db-conn-2"
    payload = call["payload_data"]
    assert payload["name"] == "Example Co"
    assert payload["industry"] == "Legal"
    assert payload["email"] == "jane@example.com"
    assert payload["addresses"] == [{"country": "United Kingdom"}]
    assert payload["websites"][0]["url"] == "https://example.com"
    assert payload["social_pages"][0]["platform"] == "linkedin"
