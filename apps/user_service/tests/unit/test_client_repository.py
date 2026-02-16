"""Unit tests for ClientRepository with fake asyncpg connection."""

import datetime

import pytest

from apps.user_service.app.db.repositories.client_repository import ClientRepository
from apps.user_service.app.schemas.enums import ClientStatus, ClientUserStatus
from libs.shared_utils.http_exceptions import NotFoundException


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self):
        """Initialize fake call stores."""
        self.fetchrow_calls = []
        self.fetch_calls = []
        self.fetchval_calls = []
        self.execute_calls = []
        self.fetchrow_result = None
        self.fetch_result = []
        self.fetchval_result = None

    async def fetchrow(self, query, *args):
        """Record fetchrow calls."""
        self.fetchrow_calls.append((query.strip(), args))
        return self.fetchrow_result

    async def fetch(self, query, *args):
        """Record fetch calls."""
        self.fetch_calls.append((query.strip(), args))
        return self.fetch_result

    async def fetchval(self, query, *args):
        """Record fetchval calls."""
        self.fetchval_calls.append((query.strip(), args))
        return self.fetchval_result

    async def execute(self, query, *args):
        """Record execute calls."""
        self.execute_calls.append((query.strip(), args))
        return None


@pytest.mark.asyncio
async def test_create_client_raises_required_fields_missing():
    """create_client raises ValueError when required fields missing."""
    conn = _FakeConn()
    repo = ClientRepository(db_connection=conn)

    with pytest.raises(ValueError, match="organization_id and client_type are required"):
        await repo.create_client({})

    with pytest.raises(ValueError, match="organization_id and client_type are required"):
        await repo.create_client({"organization_id": "org-1"})

    with pytest.raises(ValueError, match="organization_id and client_type are required"):
        await repo.create_client({"client_type": "person"})


@pytest.mark.asyncio
async def test_create_client_includes_only_provided_fields():
    """create_client only includes fields that are explicitly provided."""
    conn = _FakeConn()
    conn.fetchrow_result = {
        "id": "client-1",
        "organization_id": "org-1",
        "client_type": "person",
        "name": "John Doe",
    }
    repo = ClientRepository(db_connection=conn)

    result = await repo.create_client(
        {
            "organization_id": "org-1",
            "client_type": "person",
            "name": "John Doe",
        }
    )

    assert result["id"] == "client-1"
    assert len(conn.fetchrow_calls) == 1
    query = conn.fetchrow_calls[0][0]
    assert "INSERT INTO clients" in query
    assert "organization_id" in query
    assert "client_type" in query
    assert "name" in query


@pytest.mark.asyncio
async def test_check_client_user_exists():
    """check_client_user_exists returns boolean."""
    conn = _FakeConn()
    conn.fetchval_result = True
    repo = ClientRepository(db_connection=conn)

    result = await repo.check_client_user_exists("user-1", "org-1")

    assert result is True
    assert len(conn.fetchval_calls) == 1
    query = conn.fetchval_calls[0][0]
    assert "EXISTS" in query
    assert "client_users" in query


@pytest.mark.asyncio
async def test_get_clients_list_excludes_deleted():
    """get_clients_list filters out deleted clients."""
    conn = _FakeConn()
    conn.fetch_result = [
        {
            "id": "client-1",
            "name": "Client 1",
            "client_type": "person",
            "status": "active",
            "created_at": datetime.datetime.now(),
            "updated_at": datetime.datetime.now(),
        }
    ]
    repo = ClientRepository(db_connection=conn)

    result = await repo.get_clients_list("org-1", {"limit": 20, "offset": 0})

    assert len(result) == 1
    assert len(conn.fetch_calls) == 1
    query = conn.fetch_calls[0][0]
    assert "c.status != $" in query
    assert ClientStatus.DELETED.value in conn.fetch_calls[0][1]


@pytest.mark.asyncio
async def test_get_clients_list_applies_search_filter():
    """get_clients_list applies search filter when provided."""
    conn = _FakeConn()
    conn.fetch_result = []
    repo = ClientRepository(db_connection=conn)

    await repo.get_clients_list("org-1", {"search": "test", "limit": 20, "offset": 0})

    query = conn.fetch_calls[0][0]
    assert "ILIKE" in query
    assert "%test%" in conn.fetch_calls[0][1]


@pytest.mark.asyncio
async def test_get_clients_count_excludes_deleted():
    """get_clients_count excludes deleted clients."""
    conn = _FakeConn()
    conn.fetchval_result = 5
    repo = ClientRepository(db_connection=conn)

    result = await repo.get_clients_count("org-1", {})

    assert result == 5
    query = conn.fetchval_calls[0][0]
    assert "c.status != $" in query


@pytest.mark.asyncio
async def test_delete_client_raises_when_not_found():
    """delete_client raises NotFoundException when client not found."""
    conn = _FakeConn()
    conn.fetchrow_result = None  # Client not found
    repo = ClientRepository(db_connection=conn)

    with pytest.raises(NotFoundException):
        await repo.delete_client("client-1", "org-1")

    assert len(conn.fetchrow_calls) == 1
    query = conn.fetchrow_calls[0][0]
    assert "UPDATE clients" in query
    assert "status = $" in query


@pytest.mark.asyncio
async def test_delete_client_success():
    """delete_client succeeds when client exists."""
    conn = _FakeConn()
    conn.fetchrow_result = {"id": "client-1"}  # Client found
    repo = ClientRepository(db_connection=conn)

    result = await repo.delete_client("client-1", "org-1")

    assert result is True
    assert len(conn.fetchrow_calls) == 1


@pytest.mark.asyncio
async def test_delete_client_users():
    """delete_client_users soft deletes all client users."""
    conn = _FakeConn()
    repo = ClientRepository(db_connection=conn)

    result = await repo.delete_client_users("client-1")

    assert result is True
    assert len(conn.execute_calls) == 1
    query = conn.execute_calls[0][0]
    assert "UPDATE client_users" in query
    assert ClientUserStatus.DELETED.value in conn.execute_calls[0][1]


@pytest.mark.asyncio
async def test_check_client_name_exists():
    """check_client_name_exists checks name uniqueness."""
    conn = _FakeConn()
    conn.fetchval_result = True
    repo = ClientRepository(db_connection=conn)

    result = await repo.check_client_name_exists("Test Client", "org-1")

    assert result is True
    query = conn.fetchval_calls[0][0]
    assert "clients" in query
    assert "LOWER(name) = $" in query


@pytest.mark.asyncio
async def test_create_lead_requires_client_id_and_lead_status():
    """create_lead raises ValueError when required fields missing."""
    conn = _FakeConn()
    repo = ClientRepository(db_connection=conn)

    with pytest.raises(ValueError, match="client_id is required"):
        await repo.create_lead({})

    with pytest.raises(ValueError, match="lead_status is required"):
        await repo.create_lead({"client_id": "client-1"})


@pytest.mark.asyncio
async def test_bulk_create_addresses_empty_list():
    """bulk_create_addresses returns empty list for empty input."""
    conn = _FakeConn()
    repo = ClientRepository(db_connection=conn)

    result = await repo.bulk_create_addresses([])

    assert result == []
    assert len(conn.fetch_calls) == 0


@pytest.mark.asyncio
async def test_bulk_create_addresses_single_address():
    """bulk_create_addresses creates single address."""
    conn = _FakeConn()
    conn.fetch_result = [
        {
            "id": "addr-1",
            "client_id": "client-1",
            "address_line1": "123 Main St",
        }
    ]
    repo = ClientRepository(db_connection=conn)

    result = await repo.bulk_create_addresses(
        [{"client_id": "client-1", "address_line1": "123 Main St"}]
    )

    assert len(result) == 1
    assert len(conn.fetch_calls) == 1
    query = conn.fetch_calls[0][0]
    assert "INSERT INTO client_addresses" in query


@pytest.mark.asyncio
async def test_get_client_addresses():
    """get_client_addresses returns addresses ordered by primary and created_at."""
    conn = _FakeConn()
    conn.fetch_result = [
        {
            "id": "addr-1",
            "client_id": "client-1",
            "address_line1": "123 Main St",
            "is_primary": True,
        }
    ]
    repo = ClientRepository(db_connection=conn)

    result = await repo.get_client_addresses("client-1")

    assert len(result) == 1
    query = conn.fetch_calls[0][0]
    assert "ORDER BY is_primary DESC, created_at ASC" in query


@pytest.mark.asyncio
async def test_get_client_details_with_primary_contact():
    """get_client_details_with_primary_contact returns None when client not found."""
    conn = _FakeConn()
    conn.fetchrow_result = None
    repo = ClientRepository(db_connection=conn)

    result = await repo.get_client_details_with_primary_contact("client-1", "org-1")

    assert result is None
    query = conn.fetchrow_calls[0][0]
    assert "LEFT JOIN client_users" in query
    assert "LEFT JOIN leads" in query


@pytest.mark.asyncio
async def test_create_client_user_with_required_fields_only():
    """create_client_user creates record with only required fields."""
    conn = _FakeConn()
    conn.fetchrow_result = {
        "id": "cu-1",
        "client_id": "client-1",
        "organization_id": "org-1",
        "isometrik_user_id": "iso-1",
    }
    repo = ClientRepository(db_connection=conn)

    result = await repo.create_client_user(
        {
            "client_id": "client-1",
            "organization_id": "org-1",
            "isometrik_user_id": "iso-1",
        }
    )

    assert result["id"] == "cu-1"
    assert len(conn.fetchrow_calls) == 1
    query = conn.fetchrow_calls[0][0]
    assert "INSERT INTO client_users" in query
    assert "client_id" in query
    assert "organization_id" in query
    assert "isometrik_user_id" in query


@pytest.mark.asyncio
async def test_create_client_user_with_optional_fields():
    """create_client_user includes optional fields when provided."""
    conn = _FakeConn()
    conn.fetchrow_result = {
        "id": "cu-1",
        "client_id": "client-1",
        "organization_id": "org-1",
        "isometrik_user_id": "iso-1",
        "user_id": "user-1",
        "prefix": "Mr.",
        "first_name": "John",
        "middle_name": "Middle",
        "last_name": "Doe",
        "title": "CEO",
        "date_of_birth": "1990-01-01",
        "profile_photo_url": "https://example.com/photo.jpg",
        "status": "active",
        "is_primary_contact": True,
    }
    repo = ClientRepository(db_connection=conn)

    result = await repo.create_client_user(
        {
            "client_id": "client-1",
            "organization_id": "org-1",
            "isometrik_user_id": "iso-1",
            "user_id": "user-1",
            "prefix": "Mr.",
            "first_name": "John",
            "middle_name": "Middle",
            "last_name": "Doe",
            "title": "CEO",
            "date_of_birth": "1990-01-01",
            "profile_photo_url": "https://example.com/photo.jpg",
            "status": "active",
            "is_primary_contact": True,
        }
    )

    assert result["id"] == "cu-1"
    query = conn.fetchrow_calls[0][0]
    assert "first_name" in query
    assert "last_name" in query
    assert "prefix" in query
    assert "middle_name" in query
    assert "title" in query


@pytest.mark.asyncio
async def test_get_clients_list_with_client_type_filter():
    """get_clients_list applies client_type filter when provided."""
    conn = _FakeConn()
    conn.fetch_result = []
    repo = ClientRepository(db_connection=conn)

    await repo.get_clients_list("org-1", {"client_type": "person", "limit": 20, "offset": 0})

    query = conn.fetch_calls[0][0]
    assert "client_type = $" in query
    assert "person" in conn.fetch_calls[0][1]


@pytest.mark.asyncio
async def test_get_clients_list_with_status_filter():
    """get_clients_list applies status filter when provided."""
    conn = _FakeConn()
    conn.fetch_result = []
    repo = ClientRepository(db_connection=conn)

    await repo.get_clients_list("org-1", {"status": "active", "limit": 20, "offset": 0})

    query = conn.fetch_calls[0][0]
    assert "c.status = $" in query
    assert "active" in conn.fetch_calls[0][1]


@pytest.mark.asyncio
async def test_get_clients_list_with_all_filters():
    """get_clients_list applies all filters together."""
    conn = _FakeConn()
    conn.fetch_result = []
    repo = ClientRepository(db_connection=conn)

    await repo.get_clients_list(
        "org-1",
        {"search": "test", "client_type": "person", "status": "active", "limit": 20, "offset": 0},
    )

    query = conn.fetch_calls[0][0]
    assert "ILIKE" in query
    assert "client_type = $" in query
    assert "c.status = $" in query
    params = conn.fetch_calls[0][1]
    assert "test" in str(params)
    assert "person" in params
    assert "active" in params


@pytest.mark.asyncio
async def test_get_clients_count_with_search_filter():
    """get_clients_count applies search filter when provided."""
    conn = _FakeConn()
    conn.fetchval_result = 3
    repo = ClientRepository(db_connection=conn)

    result = await repo.get_clients_count("org-1", {"search": "test"})

    assert result == 3
    query = conn.fetchval_calls[0][0]
    assert "ILIKE" in query
    assert "%test%" in conn.fetchval_calls[0][1]


@pytest.mark.asyncio
async def test_get_clients_count_with_client_type_filter():
    """get_clients_count applies client_type filter when provided."""
    conn = _FakeConn()
    conn.fetchval_result = 2
    repo = ClientRepository(db_connection=conn)

    result = await repo.get_clients_count("org-1", {"client_type": "company"})

    assert result == 2
    query = conn.fetchval_calls[0][0]
    assert "client_type = $" in query
    assert "company" in conn.fetchval_calls[0][1]


@pytest.mark.asyncio
async def test_get_clients_count_with_status_filter():
    """get_clients_count applies status filter when provided."""
    conn = _FakeConn()
    conn.fetchval_result = 1
    repo = ClientRepository(db_connection=conn)

    result = await repo.get_clients_count("org-1", {"status": "inactive"})

    assert result == 1
    query = conn.fetchval_calls[0][0]
    assert "c.status = $" in query
    assert "inactive" in conn.fetchval_calls[0][1]


@pytest.mark.asyncio
async def test_delete_leads():
    """delete_leads deletes all leads for a client."""
    conn = _FakeConn()
    repo = ClientRepository(db_connection=conn)

    result = await repo.delete_leads("client-1")

    assert result is True
    assert len(conn.execute_calls) == 1
    query = conn.execute_calls[0][0]
    assert "DELETE FROM leads" in query
    assert "client_id = $1" in query
    assert "client-1" in conn.execute_calls[0][1]


@pytest.mark.asyncio
async def test_delete_addresses():
    """delete_addresses deletes all addresses for a client."""
    conn = _FakeConn()
    repo = ClientRepository(db_connection=conn)

    result = await repo.delete_addresses("client-1")

    assert result is True
    assert len(conn.execute_calls) == 1
    query = conn.execute_calls[0][0]
    assert "DELETE FROM client_addresses" in query
    assert "client_id = $1" in query
    assert "client-1" in conn.execute_calls[0][1]


@pytest.mark.asyncio
async def test_client_name_exists_excludes_client_id():
    """check_client_name_exists excludes client_id when provided."""
    conn = _FakeConn()
    conn.fetchval_result = False
    repo = ClientRepository(db_connection=conn)

    await repo.check_client_name_exists("Test Client", "org-1", exclude_client_id="client-1")

    query = conn.fetchval_calls[0][0]
    assert "id != $" in query
    assert "client-1" in conn.fetchval_calls[0][1]


@pytest.mark.asyncio
async def test_create_lead_with_optional_fields():
    """create_lead includes optional fields when provided."""
    conn = _FakeConn()
    conn.fetchrow_result = {
        "id": "lead-1",
        "client_id": "client-1",
        "lead_status": "prospect",
        "intake_stage": "initial",
        "lead_source": "website",
        "referral_source": "google",
        "lead_score": 85,
        "notes": "Interested in services",
    }
    repo = ClientRepository(db_connection=conn)

    result = await repo.create_lead(
        {
            "client_id": "client-1",
            "lead_status": "prospect",
            "intake_stage": "initial",
            "lead_source": "website",
            "referral_source": "google",
            "lead_score": 85,
            "notes": "Interested in services",
        }
    )

    assert result["id"] == "lead-1"
    query = conn.fetchrow_calls[0][0]
    assert "intake_stage" in query
    assert "lead_source" in query
    assert "referral_source" in query
    assert "lead_score" in query
    assert "notes" in query


@pytest.mark.asyncio
async def test_create_lead_with_required_fields_only():
    """create_lead creates record with only required fields."""
    conn = _FakeConn()
    conn.fetchrow_result = {
        "id": "lead-1",
        "client_id": "client-1",
        "lead_status": "prospect",
    }
    repo = ClientRepository(db_connection=conn)

    result = await repo.create_lead(
        {
            "client_id": "client-1",
            "lead_status": "prospect",
        }
    )

    assert result["id"] == "lead-1"
    query = conn.fetchrow_calls[0][0]
    assert "client_id" in query
    assert "lead_status" in query


# --- Update operations ---
@pytest.mark.asyncio
async def test_get_client_for_update_returns_none_not_found():
    """get_client_for_update returns None when client not found."""
    conn = _FakeConn()
    conn.fetchrow_result = None
    repo = ClientRepository(db_connection=conn)

    result = await repo.get_client_for_update("client-1", "org-1")

    assert result is None
    assert len(conn.fetchrow_calls) == 1
    query = conn.fetchrow_calls[0][0]
    assert "SELECT id, client_type, name, industry" in query
    assert "additional_data" in query
    assert "social_pages" in query
    assert "status != $" in query
    assert ClientStatus.DELETED.value in conn.fetchrow_calls[0][1]


@pytest.mark.asyncio
async def test_get_client_for_update_returns_row_when_found():
    """get_client_for_update returns client row when found."""
    conn = _FakeConn()
    conn.fetchrow_result = {
        "id": "client-1",
        "name": "Old Name",
        "industry": "Tech",
        "profile_photo_url": None,
        "portal_access": True,
        "tags": [],
        "websites": "[]",
        "billing_preferences": "{}",
        "custom_fields": "{}",
        "additional_data": "{}",
        "social_pages": "[]",
        "enrichment_done": False,
        "last_enriched_at": None,
    }
    repo = ClientRepository(db_connection=conn)

    result = await repo.get_client_for_update("client-1", "org-1")

    assert result is not None
    assert result["id"] == "client-1"
    assert result["name"] == "Old Name"


@pytest.mark.asyncio
async def test_update_client_builds_set_clause():
    """update_client builds SET clause and returns updated row."""
    conn = _FakeConn()
    conn.fetchrow_result = {
        "id": "client-1",
        "name": "New Name",
        "industry": "Tech",
        "updated_at": datetime.datetime.now(),
    }
    repo = ClientRepository(db_connection=conn)

    result = await repo.update_client("client-1", "org-1", {"name": "New Name", "industry": "Tech"})

    assert result is not None
    assert len(conn.fetchrow_calls) == 1
    query = conn.fetchrow_calls[0][0]
    assert "UPDATE clients" in query
    assert "SET " in query
    assert "updated_at = NOW()" in query
    assert "RETURNING *" in query
    assert ClientStatus.DELETED.value in conn.fetchrow_calls[0][1]


@pytest.mark.asyncio
async def test_update_lead_calls_fetchrow():
    """update_lead builds UPDATE and returns True when row updated."""
    conn = _FakeConn()
    conn.fetchrow_result = {"id": "lead-1"}
    repo = ClientRepository(db_connection=conn)

    result = await repo.update_lead(
        "lead-1", "client-1", {"lead_status": "qualified", "notes": "Updated"}
    )

    assert result is True
    assert len(conn.fetchrow_calls) == 1
    query = conn.fetchrow_calls[0][0]
    assert "UPDATE leads" in query
    assert "WHERE id = $" in query
    assert "client_id = $" in query


@pytest.mark.asyncio
async def test_update_address_calls_fetchrow():
    """update_address builds UPDATE and returns True when row updated."""
    conn = _FakeConn()
    conn.fetchrow_result = {"id": "addr-1"}
    repo = ClientRepository(db_connection=conn)

    result = await repo.update_address(
        "addr-1", "client-1", {"address_line1": "456 New St", "city": "Boston"}
    )

    assert result is True
    assert len(conn.fetchrow_calls) == 1
    query = conn.fetchrow_calls[0][0]
    assert "UPDATE client_addresses" in query
    assert "client_id = $" in query


@pytest.mark.asyncio
async def test_delete_addresses_by_ids_executes_delete():
    """delete_addresses_by_ids executes DELETE with client_id and address_ids."""
    conn = _FakeConn()
    repo = ClientRepository(db_connection=conn)

    await repo.delete_addresses_by_ids("client-1", ["addr-1", "addr-2"])

    assert len(conn.execute_calls) == 1
    query = conn.execute_calls[0][0]
    assert "DELETE FROM client_addresses" in query
    assert "client_id = $1" in query
    assert "ANY($2::uuid[])" in query
    assert "client-1" in conn.execute_calls[0][1]
    assert ["addr-1", "addr-2"] in conn.execute_calls[0][1]


@pytest.mark.asyncio
async def test_delete_addresses_by_ids_no_op_when_empty():
    """delete_addresses_by_ids does nothing when address_ids is empty."""
    conn = _FakeConn()
    repo = ClientRepository(db_connection=conn)

    await repo.delete_addresses_by_ids("client-1", [])

    assert len(conn.execute_calls) == 0


@pytest.mark.asyncio
async def test_serialize_jsonb_param_serializes_jsonb_columns():
    """_serialize_jsonb_param serializes JSONB column values to JSON string."""
    # Test websites (list)
    result = ClientRepository._serialize_jsonb_param("websites", [{"url": "https://example.com"}])
    assert isinstance(result, str)
    assert "example.com" in result

    # Test billing_preferences (dict)
    result = ClientRepository._serialize_jsonb_param("billing_preferences", {"method": "credit"})
    assert isinstance(result, str)
    assert "credit" in result

    # Test custom_fields (dict)
    result = ClientRepository._serialize_jsonb_param("custom_fields", {"key": "value"})
    assert isinstance(result, str)

    # Test additional_data (dict)
    result = ClientRepository._serialize_jsonb_param("additional_data", {"data": "test"})
    assert isinstance(result, str)

    # Test social_pages (list)
    result = ClientRepository._serialize_jsonb_param("social_pages", [{"platform": "linkedin"}])
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_serialize_jsonb_param_passes_through_non_jsonb():
    """_serialize_jsonb_param passes through non-JSONB column values."""
    # Test non-JSONB column
    result = ClientRepository._serialize_jsonb_param("name", "Test Client")
    assert result == "Test Client"

    # Test non-JSONB column with dict value (should pass through)
    result = ClientRepository._serialize_jsonb_param("other_field", {"key": "value"})
    assert isinstance(result, dict)
    assert result == {"key": "value"}


@pytest.mark.asyncio
async def test_update_client_serializes_jsonb_fields():
    """update_client serializes JSONB fields correctly."""
    conn = _FakeConn()
    conn.fetchrow_result = {
        "id": "client-1",
        "websites": '[{"id": "web-1", "url": "https://example.com"}]',
        "billing_preferences": '{"method": "credit"}',
    }
    repo = ClientRepository(db_connection=conn)

    result = await repo.update_client(
        "client-1",
        "org-1",
        {
            "websites": [{"id": "web-1", "url": "https://updated.com"}],
            "billing_preferences": {"method": "invoice"},
        },
    )

    assert result is not None
    # Check that JSONB fields were serialized in the query
    query = conn.fetchrow_calls[0][0]
    assert "websites = $" in query or "websites = $1::jsonb" in query
    assert "billing_preferences = $" in query or "billing_preferences = $2::jsonb" in query
