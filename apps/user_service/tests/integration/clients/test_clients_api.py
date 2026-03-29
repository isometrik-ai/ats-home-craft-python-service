"""Integration tests for clients API endpoints."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.clients import ClientDetailsResponse
from apps.user_service.app.services.client_service import CreateClientResult
from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.tests.utils.assertions import assert_success


def _ctx():
    """Return a reusable user context."""
    return UserContext(
        user_id="u1",
        email="u1@example.com",
        organization_id="org-1",
        user_type="admin",
    )


@pytest.mark.asyncio
async def test_list_clients(monkeypatch, client):
    """List clients with filtering and pagination."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes, organization_id
        return _ctx()

    async def fake_get_clients_list(self, organization_id, filter_params):
        """Fake get clients list."""
        del self, organization_id, filter_params
        return {
            "clients": [
                {
                    "id": "client-1",
                    "name": "Client 1",
                    "primary_contact": {
                        "first_name": "John",
                        "last_name": "Doe",
                        "email": "john@example.com",
                    },
                    "client_type": "person",
                    "status": "active",
                    "matters": [],
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                    "outstanding": None,
                    "tags": [],
                }
            ],
            "total": 1,
        }

    monkeypatch.setattr(
        "apps.user_service.app.api.clients.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientService.get_clients_list",
        fake_get_clients_list,
    )

    res = await client.get("/v1/clients?page=1&page_size=20")
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == "client-1"
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_get_client_details(monkeypatch, client):
    """Get client details by ID."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes, organization_id
        return _ctx()

    async def fake_get_client_details(self, client_id, organization_id):
        """Fake get client details."""
        del self
        assert client_id == "client-123"
        assert organization_id == "org-1"
        return ClientDetailsResponse(
            id="client-123",
            organization_id="org-1",
            client_type="person",
            name="John Doe",
            status="active",
            primary_contact={
                "first_name": "John",
                "last_name": "Doe",
                "email": "john@example.com",
            },
            tags=[],
            websites=[],
            addresses=[],
            additional_data={},
            social_pages=[],
            enrichment_done=False,
            last_enriched_at=None,
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )

    monkeypatch.setattr(
        "apps.user_service.app.api.clients.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientService.get_client_details",
        fake_get_client_details,
    )

    res = await client.get("/v1/clients/client-123")
    body = assert_success(res, 200)
    assert body["data"]["id"] == "client-123"
    assert body["data"]["name"] == "John Doe"


@pytest.mark.asyncio
async def test_create_client(monkeypatch, client):
    """Create a new client."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes, organization_id
        return _ctx()

    async def fake_create_client(self, request_data):
        """Fake create client; returns result so route can schedule enrichment."""
        del self
        assert request_data.client_type == "person"
        assert request_data.email == "newclient@example.com"
        assert request_data.first_name == "Jane"
        assert request_data.last_name == "Smith"
        return CreateClientResult(
            records=[],
            enrichment_items=[
                {
                    "client_id": "client-new-1",
                    "organization_id": "org-1",
                    "client_type": "person",
                }
            ],
        )

    monkeypatch.setattr(
        "apps.user_service.app.api.clients.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientService.create_client",
        fake_create_client,
    )
    mock_enrichment = MagicMock()
    mock_enrichment.run_bulk_client_enrichment = AsyncMock()
    monkeypatch.setattr(
        "apps.user_service.app.api.clients.ClientEnrichmentService.from_settings",
        lambda: mock_enrichment,
    )

    res = await client.post(
        "/v1/clients",
        json={
            "client_type": "person",
            "email": "newclient@example.com",
            "phone_isd_code": "+1",
            "phone_number": "1234567890",
            "first_name": "Jane",
            "last_name": "Smith",
        },
    )
    assert_success(res, 201)


@pytest.mark.asyncio
async def test_create_person_client_with_company_id(monkeypatch, client):
    """Create a person client with linked company id."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes, organization_id
        return _ctx()

    async def fake_create_client(self, request_data):
        """Fake create client; ensure company id is passed through for person."""
        del self
        assert request_data.client_type == "person"
        assert request_data.client_company_id == "company-client-1"
        return CreateClientResult(
            records=[],
            enrichment_items=[
                {
                    "client_id": "client-new-2",
                    "organization_id": "org-1",
                    "client_type": "person",
                }
            ],
        )

    monkeypatch.setattr(
        "apps.user_service.app.api.clients.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientService.create_client",
        fake_create_client,
    )
    mock_enrichment = MagicMock()
    mock_enrichment.run_bulk_client_enrichment = AsyncMock()
    monkeypatch.setattr(
        "apps.user_service.app.api.clients.ClientEnrichmentService.from_settings",
        lambda: mock_enrichment,
    )

    res = await client.post(
        "/v1/clients",
        json={
            "client_type": "person",
            "email": "linkedclient@example.com",
            "phone_isd_code": "+1",
            "phone_number": "9876543210",
            "first_name": "Alice",
            "last_name": "Doe",
            "client_company_id": "company-client-1",
        },
    )
    assert_success(res, 201)


@pytest.mark.asyncio
async def test_create_client_company_returns_201(monkeypatch, client):
    """Create a company client returns 201; route schedules company enrichment."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes, organization_id
        return _ctx()

    async def fake_create_client(self, request_data):
        """Fake create client for company; return result for enrichment."""
        del self
        assert request_data.client_type == "company"
        assert request_data.name == "Acme Corp"
        # Company flow creates company + primary contact; both get enrichment
        return CreateClientResult(
            records=[],
            enrichment_items=[
                {"client_id": "client-co-1", "organization_id": "org-1", "client_type": "company"},
                {
                    "client_id": "client-co-person-1",
                    "organization_id": "org-1",
                    "client_type": "person",
                },
            ],
        )

    monkeypatch.setattr(
        "apps.user_service.app.api.clients.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientService.create_client",
        fake_create_client,
    )
    mock_enrichment = MagicMock()
    mock_enrichment.run_bulk_client_enrichment = AsyncMock()
    monkeypatch.setattr(
        "apps.user_service.app.api.clients.ClientEnrichmentService.from_settings",
        lambda: mock_enrichment,
    )

    res = await client.post(
        "/v1/clients",
        json={
            "client_type": "company",
            "name": "Acme Corp",
            "email": "contact@acme.com",
            "phone_isd_code": "+1",
            "phone_number": "5551234567",
            "first_name": "Contact",
            "last_name": "Person",
        },
    )
    assert_success(res, 201)
    # One bulk enrichment task for company + primary contact
    bulk = mock_enrichment.run_bulk_client_enrichment
    assert bulk.call_count == 1
    assert len(bulk.call_args[0][0]) == 2


@pytest.mark.asyncio
async def test_create_client_from_user(monkeypatch, client):
    """Create client from user ID."""

    async def fake_create_client_from_user(self, request_data):
        """Fake create client from user."""
        del self
        assert request_data.user_id == "user-123"
        assert request_data.organization_id == "org-1"
        return None

    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientService.create_client_from_user",
        fake_create_client_from_user,
    )

    res = await client.post(
        "/v1/clients/from-auth",
        json={
            "user_id": "user-123",
            "organization_id": "org-1",
        },
    )
    assert_success(res, 201)


@pytest.mark.asyncio
async def test_update_client(monkeypatch, client):
    """Update client by ID; only provided fields are applied."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes, organization_id
        return _ctx()

    async def fake_update_client(self, client_id, organization_id, body):
        """Fake update client."""
        del self
        assert client_id == "00000000-0000-0000-0000-000000000123"
        assert organization_id == "org-1"
        assert body.company_name == "Updated Name"
        return {"old_data": {"client_id": "client-123", "name": "Old Name"}}

    monkeypatch.setattr(
        "apps.user_service.app.api.clients.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientService.update_client",
        fake_update_client,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.clients.ClientService.index_clients_in_typesense_background",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.clients.ClientService.trigger_enrichment_background",
        AsyncMock(),
    )

    res = await client.patch(
        "/v1/clients/00000000-0000-0000-0000-000000000123",
        json={"company_name": "Updated Name"},
    )
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_delete_client(monkeypatch, client):
    """Delete a client (soft delete)."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes, organization_id
        return _ctx()

    async def fake_delete_client(self, client_id, organization_id):
        """Fake delete client."""
        del self
        assert client_id == "client-123"
        assert organization_id == "org-1"
        return None

    monkeypatch.setattr(
        "apps.user_service.app.api.clients.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientService.delete_client",
        fake_delete_client,
    )

    res = await client.delete("/v1/clients/client-123")
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_list_clients_with_filters(monkeypatch, client):
    """List clients with search and filter parameters."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes, organization_id
        return _ctx()

    async def fake_get_clients_list(self, organization_id, filter_params):
        """Fake get clients list with filters."""
        del self, organization_id
        assert filter_params["search"] == "test"
        assert filter_params["client_type"] == "person"
        assert filter_params["status"] == "active"
        return {"clients": [], "total": 0}

    monkeypatch.setattr(
        "apps.user_service.app.api.clients.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientService.get_clients_list",
        fake_get_clients_list,
    )

    res = await client.get(
        "/v1/clients?search=test&client_type=person&status=active&page=1&page_size=20"
    )
    body = assert_success(res, 200)
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_list_clients_empty_result(monkeypatch, client):
    """List clients returns empty result with proper message."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes, organization_id
        return _ctx()

    async def fake_get_clients_list(self, organization_id, filter_params):
        """Fake get clients list returning empty."""
        del self, organization_id, filter_params
        return {"clients": [], "total": 0}

    monkeypatch.setattr(
        "apps.user_service.app.api.clients.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientService.get_clients_list",
        fake_get_clients_list,
    )

    res = await client.get("/v1/clients?page=1&page_size=20")
    body = assert_success(res, 200)
    assert body["data"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_create_client_with_custom_fields(monkeypatch, client):
    """Create a client with custom fields."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes, organization_id
        return _ctx()

    async def fake_create_client(self, request_data):
        """Fake create client with custom fields; return result for enrichment."""
        del self
        assert request_data.client_type == "person"
        assert request_data.custom_fields == [
            {"field_id": "00000000-0000-0000-0000-00000000a001", "value": 25},
            {
                "field_id": "00000000-0000-0000-0000-00000000a002",
                "value": ["tag1", "tag2"],
            },
        ]
        return CreateClientResult(
            records=[],
            enrichment_items=[
                {
                    "client_id": "client-cf-1",
                    "organization_id": "org-1",
                    "client_type": "person",
                }
            ],
        )

    monkeypatch.setattr(
        "apps.user_service.app.api.clients.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientService.create_client",
        fake_create_client,
    )
    mock_enrichment = MagicMock()
    mock_enrichment.run_bulk_client_enrichment = AsyncMock()
    monkeypatch.setattr(
        "apps.user_service.app.api.clients.ClientEnrichmentService.from_settings",
        lambda: mock_enrichment,
    )

    res = await client.post(
        "/v1/clients",
        json={
            "client_type": "person",
            "email": "newclient@example.com",
            "phones": [
                {"phone_isd_code": "+1", "phone_number": "1234567890", "is_primary": True},
            ],
            "first_name": "Jane",
            "last_name": "Smith",
            "custom_fields": [
                {"field_id": "00000000-0000-0000-0000-00000000a001", "value": 25},
                {
                    "field_id": "00000000-0000-0000-0000-00000000a002",
                    "value": ["tag1", "tag2"],
                },
            ],
        },
    )
    assert_success(res, 201)


@pytest.mark.asyncio
async def test_update_client_with_custom_fields(monkeypatch, client):
    """Update client with custom fields."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes, organization_id
        return _ctx()

    async def fake_update_client(self, client_id, organization_id, body):
        """Fake update client with custom fields."""
        del self
        assert client_id == "00000000-0000-0000-0000-000000000123"
        assert organization_id == "org-1"
        assert body.custom_fields == [
            {"field_id": "00000000-0000-0000-0000-00000000a001", "value": 30},
        ]
        return {"old_data": {"client_id": "client-123", "custom_fields": {"age": 25}}}

    monkeypatch.setattr(
        "apps.user_service.app.api.clients.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_service.ClientService.update_client",
        fake_update_client,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.clients.ClientService.index_clients_in_typesense_background",
        AsyncMock(),
    )

    res = await client.patch(
        "/v1/clients/00000000-0000-0000-0000-000000000123",
        json={
            "custom_fields": [
                {"field_id": "00000000-0000-0000-0000-00000000a001", "value": 30},
            ],
        },
    )
    assert_success(res, 200)
