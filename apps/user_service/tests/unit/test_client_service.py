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
    SocialPageInput,
    SocialPagesUpdate,
    SocialPageUpdateItem,
    UpdateClientRequest,
    Website,
    WebsiteInput,
    WebsitesUpdate,
    WebsiteUpdateItem,
)
from apps.user_service.app.schemas.enums import ClientType, LeadStatus, UserEventStatus
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
        self.client_details_result = None
        self.clients_list_result = []
        self.clients_count_result = 0
        self.address_result = []

    async def check_client_user_exists(self, user_id, organization_id):
        """Return existence flag."""
        self.calls["check_client_user_exists"] = (user_id, organization_id)
        return self.client_user_exists

    async def check_client_name_exists(self, name, organization_id, exclude_client_id=None):
        """Return name existence flag."""
        self.calls["check_client_name_exists"] = {
            "name": name,
            "organization_id": organization_id,
            "exclude_client_id": exclude_client_id,
        }
        return self.name_exists

    async def create_client(self, client_data):
        """Create client and return result."""
        self.calls["create_client"] = client_data
        return self.client_result or {"id": "client-1", **client_data}

    async def create_client_user(self, client_user_data):
        """Create client user."""
        self.calls["create_client_user"] = client_user_data
        return {"id": "client-user-1", **client_user_data}

    async def create_lead(self, lead_data):
        """Create lead."""
        self.calls["create_lead"] = lead_data
        return {"id": "lead-1", **lead_data}

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

    async def delete_leads(self, client_id):
        """Delete leads."""
        self.calls["delete_leads"] = client_id
        return True

    async def delete_addresses(self, client_id):
        """Delete addresses."""
        self.calls["delete_addresses"] = client_id
        return True

    async def get_client_addresses(self, client_id):
        """Get client addresses."""
        self.calls["get_client_addresses"] = client_id
        return self.address_result

    get_client_for_update_result = None

    async def get_client_for_update(self, client_id, organization_id):
        """Get client for update."""
        self.calls["get_client_for_update"] = (client_id, organization_id)
        return self.get_client_for_update_result

    async def update_client(self, client_id, organization_id, update_data):
        """Update client."""
        self.calls["update_client"] = (client_id, organization_id, update_data)
        return {"id": client_id, **update_data}

    async def update_lead(self, lead_id, client_id, update_data):
        """Update lead."""
        self.calls["update_lead"] = (lead_id, client_id, update_data)
        return True

    async def update_address(self, address_id, client_id, update_data):
        """Update address."""
        self.calls["update_address"] = (address_id, client_id, update_data)
        return True

    async def delete_addresses_by_ids(self, client_id, address_ids):
        """Delete addresses by ids."""
        self.calls["delete_addresses_by_ids"] = (client_id, address_ids)


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


def _ctx(org_id="org-1"):
    """Build reusable UserContext for tests."""
    return UserContext(
        user_id="u1",
        email="u1@example.com",
        organization_id=org_id,
        user_type="admin",
    )


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
async def test_client_from_user_raises_user_already_client(monkeypatch):
    """Raises ConflictException when user is already a client."""
    fake_repo = _FakeClientRepo()
    fake_repo.client_user_exists = True
    fake_user_event_repo = _FakeUserEventRepo()
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

    with pytest.raises(ConflictException):
        await service.create_client_from_user(request_data)


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
    """Raises ConflictException when email already exists."""
    fake_repo = _FakeClientRepo()
    fake_user_repo = _FakeUserRepo()
    fake_user_repo.email_exists = True
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
        phone_isd_code="+1",
        phone_number="1234567890",
        first_name="John",
        last_name="Doe",
    )

    with pytest.raises(ConflictException):
        await service.create_client(request_data)


@pytest.mark.asyncio
async def test_create_client_phone_exists(monkeypatch):
    """Raises ConflictException when phone already exists."""
    fake_repo = _FakeClientRepo()
    fake_user_repo = _FakeUserRepo()
    fake_user_repo.phone_exists = True
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
        phone_isd_code="+1",
        phone_number="1234567890",
        first_name="John",
        last_name="Doe",
    )

    with pytest.raises(ConflictException):
        await service.create_client(request_data)


@pytest.mark.asyncio
async def test_create_client_raises_when_name_exists(monkeypatch):
    """Raises ConflictException when person name already exists."""
    fake_repo = _FakeClientRepo()
    fake_repo.name_exists = True
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
        phone_isd_code="+1",
        phone_number="1234567890",
        first_name="John",
        last_name="Doe",
    )

    with pytest.raises(ConflictException):
        await service.create_client(request_data)


@pytest.mark.asyncio
async def test_create_client_company_name_exists(monkeypatch):
    """Raises ConflictException when company name already exists."""
    fake_repo = _FakeClientRepo()
    fake_repo.name_exists = True
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
        name="Test Company",
    )

    with pytest.raises(ConflictException):
        await service.create_client(request_data)


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
            "client_type": "person",
            "status": "active",
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


@pytest.mark.asyncio
async def test_delete_client_calls_delete_methods(monkeypatch):
    """delete_client calls all related delete methods."""
    fake_repo = _FakeClientRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    await service.delete_client("client-1", "org-1")

    assert "delete_client" in fake_repo.calls
    assert "delete_client_users" in fake_repo.calls
    assert "delete_leads" in fake_repo.calls
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
        custom_fields={"key": "value"},
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
        custom_fields={"age": 25},
    )

    client_data = await service._prepare_client_data(request_data, "org-1")

    parsed_fields = parse_json_field(client_data["custom_fields"])
    assert parsed_fields == {"age": 25.0}


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
        custom_fields={"age": "not a number"},
    )

    with pytest.raises(ValidationException):
        await service._prepare_client_data(request_data, "org-1")


@pytest.mark.asyncio
async def test_prepare_client_user_data_includes_fields():
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
    assert client_user_data["is_primary_contact"] is True


@pytest.mark.asyncio
async def test_create_records_creates_lead(monkeypatch):
    """_create_optional_records creates lead when lead_management enabled."""
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
        lead_management=LeadManagement(enabled=True, lead_status=LeadStatus.PROSPECT),
    )

    await service._create_optional_records(request_data, "client-1")
    assert "create_lead" in fake_repo.calls
    assert fake_repo.calls["create_lead"]["client_id"] == "client-1"


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
        request_data, fake_org_repo.organization, "org-1"
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
        request_data, fake_org_repo.organization, "org-1"
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

    await service.create_client(request_data)
    assert "create_client" in fake_repo.calls
    assert "create_client_user" in fake_repo.calls


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
        "status": "active",
        "first_name": "John",
        "last_name": "Doe",
        "title": "CEO",
        "email": "john@example.com",
        "phone_isd_code": "+1",
        "phone": "1234567890",
        "websites": '[{"url": "https://example.com", "type": "primary"}]',
        "billing_preferences": '{"payment_method": "credit_card"}',
        "custom_fields": '{"key": "value"}',
        "additional_data": "{}",
        "social_pages": "[]",
        "enrichment_done": False,
        "last_enriched_at": None,
        "lead_id": "lead-1",
        "lead_status": "prospect",
        "intake_stage": "Initial Contact",
        "lead_source": "website",
        "referral_source": "google",
        "lead_score": "85",
        "converted_at": datetime.datetime.now(),
        "lead_notes": "Interested",
        "lead_created_at": datetime.datetime.now(),
        "lead_updated_at": datetime.datetime.now(),
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

    service = ClientService(user_context=_ctx(), db_connection=None)
    result = await service.get_client_details("client-1", "org-1")

    assert result.id == "client-1"
    assert result.name == "John Doe"
    assert result.primary_contact.first_name == "John"
    assert result.primary_contact.last_name == "Doe"
    assert len(result.websites) == 1
    assert result.billing_preferences is not None
    assert result.custom_fields == {"key": "value"}
    assert result.lead is not None
    assert result.lead.lead_status == "prospect"
    assert len(result.addresses) == 1
    assert result.addresses[0].address_line1 == "123 Main St"


@pytest.mark.asyncio
async def test_get_client_details_without_lead(monkeypatch):
    """get_client_details handles client without lead information."""
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
        "lead_id": None,
    }
    fake_repo.address_result = []

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )

    service = ClientService(user_context=_ctx(), db_connection=None)
    result = await service.get_client_details("client-1", "org-1")

    assert result.id == "client-1"
    assert result.lead is None
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
        "lead_id": None,
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
    body = UpdateClientRequest(client_name="New Name")

    with pytest.raises(NotFoundException) as exc_info:
        await service.update_client("client-1", "org-1", body)

    assert exc_info.value.message_key == "clients.errors.not_found"
    assert fake_repo.calls["get_client_for_update"] == ("client-1", "org-1")


@pytest.mark.asyncio
async def test_update_client_name_exists(monkeypatch):
    """update_client raises ConflictException when client_name already exists."""
    fake_repo = _FakeClientRepo()
    fake_repo.get_client_for_update_result = {
        "id": "client-1",
        "name": "Old Name",
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
    fake_repo.name_exists = True
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)
    body = UpdateClientRequest(client_name="Existing Client")

    with pytest.raises(ConflictException) as exc_info:
        await service.update_client("client-1", "org-1", body)

    assert exc_info.value.message_key == "clients.errors.client_name_already_exists"
    assert fake_repo.calls["check_client_name_exists"]["name"] == "existing client"
    assert fake_repo.calls["check_client_name_exists"]["exclude_client_id"] == "client-1"
    assert "client_type" not in fake_repo.calls["check_client_name_exists"]


@pytest.mark.asyncio
async def test_update_client_success_scalar_only(monkeypatch):
    """update_client updates scalar fields and returns old_data for audit."""
    fake_repo = _FakeClientRepo()
    fake_repo.get_client_for_update_result = {
        "id": "client-1",
        "name": "Old Name",
        "industry": "Old Industry",
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
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)
    body = UpdateClientRequest(
        client_name="New Name",
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
    assert "update_lead" not in fake_repo.calls


@pytest.mark.asyncio
async def test_update_client_success_with_addresses(monkeypatch):
    """update_client applies address delta when addresses provided."""
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
        "custom_fields": "{}",
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
        client_name="Client",
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
    fake_repo.get_client_for_update_result = {
        "id": "client-1",
        "name": "Client",
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
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    service = ClientService(user_context=_ctx(), db_connection=None)
    body = UpdateClientRequest(
        industry="Legal",
        lead_management=LeadManagementUpdate(
            lead_id="lead-1",
            lead_status=LeadStatus.QUALIFIED,
            notes="Updated notes",
        ),
    )

    await service.update_client("client-1", "org-1", body)

    assert "update_lead" in fake_repo.calls
    assert fake_repo.calls["update_lead"] == (
        "lead-1",
        "client-1",
        {"lead_status": LeadStatus.QUALIFIED.value, "notes": "Updated notes"},
    )


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

    assert service._has_any_update(UpdateClientRequest(client_name="X")) is True
    assert service._has_any_update(UpdateClientRequest(industry="Tech")) is True
    assert service._has_any_update(UpdateClientRequest(portal_access=True)) is True
    assert service._has_any_update(UpdateClientRequest(tags=["a"])) is True


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
        "custom_fields": '{"a": "1", "b": "2"}',
    }
    body = UpdateClientRequest(
        client_name="New Name",
        billing_preferences=BillingPreferencesUpdate(terms="Net 30"),
        custom_fields={"a": "updated", "b": None, "c": "new"},
    )

    payload = await service._build_client_update_payload(body, current)

    assert payload["name"] == "New Name"
    assert payload["billing_preferences"]["method"] == "invoice"
    assert payload["billing_preferences"]["terms"] == "Net 30"
    custom_fields = json.loads(payload["custom_fields"])
    assert custom_fields["a"] == "updated"
    assert "b" not in custom_fields
    assert custom_fields["c"] == "new"


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
    fake_repo = _FakeClientRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    service = ClientService(db_connection=None)
    lead = LeadManagementUpdate(lead_id="lead-1", lead_status=LeadStatus.CONVERTED)

    await service._apply_lead_update("client-1", lead)
    assert fake_repo.calls["update_lead"] == (
        "lead-1",
        "client-1",
        {"lead_status": LeadStatus.CONVERTED.value},
    )


@pytest.mark.asyncio
async def test_apply_lead_update_empty_payload(monkeypatch):
    """_apply_lead_update does not call repository when only lead_id provided."""
    fake_repo = _FakeClientRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
    )
    service = ClientService(db_connection=None)
    lead = LeadManagementUpdate(lead_id="lead-1")

    await service._apply_lead_update("client-1", lead)
    assert "update_lead" not in fake_repo.calls


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
    """_validate_client_creation validates company name correctly."""
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

    # Name is stripped but not lowercased in validation
    assert fake_repo.calls["check_client_name_exists"]["name"] == "Test Company"


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
async def test_prepare_client_user_data_with_optional_fields():
    """_prepare_client_user_data includes optional fields when provided."""
    service = ClientService(db_connection=None)
    request_data = CreateClientRequest(
        client_type=ClientType.PERSON,
        email="test@example.com",
        phone_isd_code="+1",
        phone_number="1234567890",
        first_name="John",
        last_name="Doe",
        date_of_birth=datetime.date(1990, 1, 1),
        profile_photo_url="https://example.com/photo.jpg",
    )
    client_user_data = service._prepare_client_user_data(
        request_data, "client-1", "org-1", "user-1", "isometrik-1"
    )

    assert client_user_data["date_of_birth"] == date(1990, 1, 1)
    assert client_user_data["profile_photo_url"] == "https://example.com/photo.jpg"


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
        "custom_fields": "{}",
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
        "custom_fields": "{}",
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
        "custom_fields": "{}",
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
    current = {
        "client_type": "person",
        "custom_fields": '{"existing": "field"}',
    }
    body = UpdateClientRequest(custom_fields={"age": 30})
    payload = {}

    await service._merge_custom_fields_into_payload(body, current, payload)

    assert "custom_fields" in payload
    parsed = parse_json_field(payload["custom_fields"])
    assert parsed["age"] == 30.0
    assert parsed["existing"] == "field"


@pytest.mark.asyncio
async def test_merge_custom_fields_into_payload_remove_field(monkeypatch):
    """_merge_custom_fields_into_payload removes fields set to None."""
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
        "custom_fields": '{"age": 25, "other": "value"}',
    }
    body = UpdateClientRequest(custom_fields={"age": None})
    payload = {}

    await service._merge_custom_fields_into_payload(body, current, payload)

    parsed = parse_json_field(payload["custom_fields"])
    assert "age" not in parsed
    assert parsed["other"] == "value"


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
        "custom_fields": "{}",
    }
    body = UpdateClientRequest(custom_fields={"tags": ["tag1", "tag2"]})
    payload = {}

    await service._merge_custom_fields_into_payload(body, current, payload)

    parsed = json.loads(payload["custom_fields"])
    assert parsed["tags"] == ["tag1", "tag2"]


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

    await service._apply_jsonb_list_changes(
        "client-1",
        "org-1",
        websites_update,
        current,
        "websites",
        "clients.errors.website_not_found",
    )
    assert "update_client" in fake_repo.calls


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

    with pytest.raises(NotFoundException) as exc_info:
        await service._apply_jsonb_list_changes(
            "client-1",
            "org-1",
            websites_update,
            current,
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

    await service._apply_jsonb_list_changes(
        "client-1",
        "org-1",
        websites_update,
        current,
        "websites",
        "clients.errors.website_not_found",
    )

    assert "update_client" in fake_repo.calls


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

    await service._apply_jsonb_list_changes(
        "client-1",
        "org-1",
        social_pages_update,
        current,
        "social_pages",
        "clients.errors.social_page_not_found",
    )
    assert "update_client" in fake_repo.calls


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

    with pytest.raises(NotFoundException) as exc_info:
        await service._apply_jsonb_list_changes(
            "client-1",
            "org-1",
            social_pages_update,
            current,
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

    await service._apply_jsonb_list_changes(
        "client-1",
        "org-1",
        social_pages_update,
        current,
        "social_pages",
        "clients.errors.social_page_not_found",
    )

    assert "update_client" in fake_repo.calls
