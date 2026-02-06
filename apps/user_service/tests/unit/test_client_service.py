"""Unit tests for ClientService business logic."""

import datetime

import pytest

from apps.user_service.app.schemas.clients import (
    Address,
    CreateClientFromUserRequest,
    CreateClientRequest,
    LeadManagement,
    Website,
)
from apps.user_service.app.schemas.enums import ClientType, LeadStatus, UserEventStatus
from apps.user_service.app.services.client_service import ClientService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ServiceUnavailableException,
)


class _FakeClientRepo:
    """Lightweight fake client repository."""

    def __init__(self, db_connection=None):
        self.db_connection = db_connection
        self.calls = {}
        self.client_user_exists = False
        self.email_exists = False
        self.phone_exists = False
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

    async def check_email_exists(self, email, organization_id, exclude_client_id=None):
        """Return email existence flag."""
        self.calls["check_email_exists"] = (email, organization_id, exclude_client_id)
        return self.email_exists

    async def check_client_name_exists(
        self, name, organization_id, client_type=None, exclude_client_id=None
    ):
        """Return name existence flag."""
        self.calls["check_client_name_exists"] = {
            "name": name,
            "organization_id": organization_id,
            "client_type": client_type,
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


class _FakeUserRepo:
    """Fake user repository."""

    def __init__(self):
        self.user_details = {"id": "user-1", "email": "test@example.com"}
        self.phone_exists = False

    async def get_user_details_by_id(self, _user_id, _fields):
        """Get user details."""
        return self.user_details

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

    def __init__(self, db_connection=None, user_event_details=None):
        self.db_connection = db_connection
        self.calls = {}
        self.user_event_details = user_event_details or {"status": "pending"}

    async def get_user_event_by_user_id(self, _user_id: str, _select_columns=None):
        """Return configured user_event details."""
        return self.user_event_details

    async def update_status_by_user_id(self, user_id: str, status: UserEventStatus) -> None:
        """Record call and no-op."""
        self.calls["update_status_by_user_id"] = {"user_id": user_id, "status": status}


def _ctx(org_id="org-1"):
    """Build reusable UserContext for tests."""
    return UserContext(
        user_id="u1",
        email="u1@example.com",
        organization_id=org_id,
        user_type="admin",
    )


@pytest.mark.asyncio
async def test_create_client_from_user_when_user_event_none(monkeypatch):
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
async def test_create_client_from_user_when_event_not_pending(monkeypatch):
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
async def test_create_client_from_user_raises_user_not_found(monkeypatch):
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
    fake_repo.email_exists = True
    fake_org_repo = _FakeOrgRepo()

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientRepository",
        lambda db_connection=None: fake_repo,
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
async def test_create_client_raises_when_phone_exists(monkeypatch):
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
async def test_create_client_raises_when_company_name_exists(monkeypatch):
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
async def test_get_client_details_raises_when_not_found(monkeypatch):
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

    client_data = service._prepare_client_data(request_data, "org-1")

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

    client_data = service._prepare_client_data(request_data, "org-1")

    assert client_data["name"] == "Test Company"


@pytest.mark.asyncio
async def test_prepare_client_data_serializes_jsonb_fields():
    """_prepare_client_data serializes JSONB fields to JSON strings."""
    service = ClientService(db_connection=None)
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

    client_data = service._prepare_client_data(request_data, "org-1")

    assert client_data["organization_id"] == "org-1"
    assert client_data["client_type"] == "person"
    assert isinstance(client_data["websites"], str)  # JSON string
    assert client_data["tags"] == ["tag1", "tag2"]
    assert isinstance(client_data["custom_fields"], str)  # JSON string


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
async def test_create_records_creates_lead_when_enabled(monkeypatch):
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
async def test_create_isometrik_user_raises_when_fails(monkeypatch):
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
async def test_create_auth_and_isometrik_user_for_person(monkeypatch):
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

    user_id, isometrik_user_id = await service._create_auth_and_isometrik_user(
        request_data, fake_org_repo.organization, "org-1"
    )

    assert user_id == "auth-user-123"
    assert isometrik_user_id == "isometrik-123"


@pytest.mark.asyncio
async def test_create_auth_and_isometrik_user_for_company(monkeypatch):
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

    user_id, isometrik_user_id = await service._create_auth_and_isometrik_user(
        request_data, fake_org_repo.organization, "org-1"
    )

    assert user_id == "auth-user-123"
    assert isometrik_user_id == "isometrik-123"


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
async def test_create_user_raises_when_isometrik_fails(monkeypatch):
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
async def test_get_client_details_with_null_coordinates(monkeypatch):
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
