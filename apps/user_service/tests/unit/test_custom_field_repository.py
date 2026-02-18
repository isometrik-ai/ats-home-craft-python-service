"""Unit tests for CustomFieldRepository with fake asyncpg connection."""

import json

import pytest

from apps.user_service.app.db.repositories.custom_field_repository import (
    CustomFieldRepository,
)
from apps.user_service.app.schemas.enums import EntityType


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self):
        """Initialize fake call stores."""
        self.fetchrow_calls = []
        self.fetch_calls = []
        self.fetchval_calls = []
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


# ============================================================================
# CREATE CUSTOM FIELD TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_create_field_required_fields():
    """Test create_custom_field with required fields only."""
    conn = _FakeConn()
    conn.fetchrow_result = {"id": "field-1"}
    repo = CustomFieldRepository(db_connection=conn)

    result = await repo.create_custom_field(
        {
            "organization_id": "org-1",
            "field_name": "Test Field",
            "field_key": "test_field",
            "field_type": "text",
        }
    )

    assert result["id"] == "field-1"
    assert len(conn.fetchrow_calls) == 1
    query = conn.fetchrow_calls[0][0]
    assert "INSERT INTO custom_fields" in query
    assert "organization_id" in query
    assert "field_name" in query
    assert "field_key" in query
    assert "field_type" in query


@pytest.mark.asyncio
async def test_create_field_with_optional_fields():
    """Test create_custom_field with optional fields."""
    conn = _FakeConn()
    conn.fetchrow_result = {"id": "field-1"}
    repo = CustomFieldRepository(db_connection=conn)

    result = await repo.create_custom_field(
        {
            "organization_id": "org-1",
            "field_name": "Test",
            "field_key": "test",
            "field_type": "text",
            "entity_type": "contact",
            "description": "Test description",
            "show_on_create": False,
            "show_on_detail": True,
            "is_required": True,
            "sort_order": 5,
            "is_active": True,
            "created_by": "user-1",
        }
    )

    assert result["id"] == "field-1"
    query = conn.fetchrow_calls[0][0]
    assert "entity_type" in query
    assert "description" in query
    assert "show_on_create" in query
    assert "show_on_detail" in query
    assert "is_required" in query
    assert "sort_order" in query


@pytest.mark.asyncio
async def test_create_field_serializes_type_config():
    """Test create_custom_field serializes type_config to JSON."""
    conn = _FakeConn()
    conn.fetchrow_result = {"id": "field-1"}
    repo = CustomFieldRepository(db_connection=conn)

    await repo.create_custom_field(
        {
            "organization_id": "org-1",
            "field_name": "Test",
            "field_key": "test",
            "field_type": "dropdown",
            "type_config": {"options": ["a", "b"]},
        }
    )

    query = conn.fetchrow_calls[0][0]
    args = conn.fetchrow_calls[0][1]
    # type_config should be serialized to JSON string
    type_config_arg = None
    for i, field in enumerate(
        query.split("INSERT INTO custom_fields")[1]
        .split("VALUES")[0]
        .split("(")[1]
        .split(")")[0]
        .split(",")
    ):
        if "type_config" in field.strip():
            type_config_arg = args[i]
            break
    assert isinstance(type_config_arg, str)
    assert json.loads(type_config_arg) == {"options": ["a", "b"]}


@pytest.mark.asyncio
async def test_create_field_with_parent_id():
    """Test create_custom_field with parent_id."""
    conn = _FakeConn()
    conn.fetchrow_result = {"id": "field-1"}
    repo = CustomFieldRepository(db_connection=conn)

    await repo.create_custom_field(
        {
            "organization_id": "org-1",
            "field_name": "Sub Field",
            "field_key": "sub_field",
            "field_type": "text",
            "parent_id": "parent-1",
        }
    )

    query = conn.fetchrow_calls[0][0]
    assert "parent_id" in query
    args = conn.fetchrow_calls[0][1]
    assert "parent-1" in args


# ============================================================================
# BULK CREATE CUSTOM FIELDS TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_bulk_create_empty_list():
    """Test bulk_create_custom_fields returns empty for empty input."""
    conn = _FakeConn()
    repo = CustomFieldRepository(db_connection=conn)

    result = await repo.bulk_create_custom_fields([])

    assert result == []
    assert len(conn.fetch_calls) == 0


@pytest.mark.asyncio
async def test_bulk_create_single_field():
    """Test bulk_create_custom_fields creates single field."""
    conn = _FakeConn()
    conn.fetch_result = [{"id": "field-1"}]
    repo = CustomFieldRepository(db_connection=conn)

    result = await repo.bulk_create_custom_fields(
        [
            {
                "organization_id": "org-1",
                "field_name": "Test",
                "field_key": "test",
                "field_type": "text",
            }
        ]
    )

    assert len(result) == 1
    assert result[0] == "field-1"
    assert len(conn.fetch_calls) == 1
    query = conn.fetch_calls[0][0]
    assert "INSERT INTO custom_fields" in query


@pytest.mark.asyncio
async def test_bulk_create_multiple_fields():
    """Test bulk_create_custom_fields creates multiple fields."""
    conn = _FakeConn()
    conn.fetch_result = [
        {"id": "field-1"},
        {"id": "field-2"},
        {"id": "field-3"},
    ]
    repo = CustomFieldRepository(db_connection=conn)

    result = await repo.bulk_create_custom_fields(
        [
            {
                "organization_id": "org-1",
                "field_name": "Field 1",
                "field_key": "field_1",
                "field_type": "text",
            },
            {
                "organization_id": "org-1",
                "field_name": "Field 2",
                "field_key": "field_2",
                "field_type": "number",
            },
            {
                "organization_id": "org-1",
                "field_name": "Field 3",
                "field_key": "field_3",
                "field_type": "date",
            },
        ]
    )

    assert len(result) == 3
    assert result == ["field-1", "field-2", "field-3"]
    query = conn.fetch_calls[0][0]
    assert "VALUES" in query
    # Should have 3 value tuples
    assert query.count("(") == 4  # 3 value tuples + opening


@pytest.mark.asyncio
async def test_bulk_create_with_different_fields():
    """Test bulk_create handles fields with different columns."""
    conn = _FakeConn()
    conn.fetch_result = [{"id": "field-1"}, {"id": "field-2"}]
    repo = CustomFieldRepository(db_connection=conn)

    await repo.bulk_create_custom_fields(
        [
            {
                "organization_id": "org-1",
                "field_name": "Field 1",
                "field_key": "field_1",
                "field_type": "text",
                "description": "Desc 1",
            },
            {
                "organization_id": "org-1",
                "field_name": "Field 2",
                "field_key": "field_2",
                "field_type": "text",
                "show_on_create": False,
            },
        ]
    )

    query = conn.fetch_calls[0][0]
    # Should include all fields from both records
    assert "description" in query
    assert "show_on_create" in query


@pytest.mark.asyncio
async def test_bulk_create_serializes_type_config():
    """Test bulk_create serializes type_config to JSON."""
    conn = _FakeConn()
    conn.fetch_result = [{"id": "field-1"}]
    repo = CustomFieldRepository(db_connection=conn)

    await repo.bulk_create_custom_fields(
        [
            {
                "organization_id": "org-1",
                "field_name": "Test",
                "field_key": "test",
                "field_type": "dropdown",
                "type_config": {"options": ["a", "b"]},
            }
        ]
    )

    args = conn.fetch_calls[0][1]
    # Find type_config value
    type_config_found = False
    for arg in args:
        if isinstance(arg, str):
            try:
                parsed = json.loads(arg)
                if parsed == {"options": ["a", "b"]}:
                    type_config_found = True
                    break
            except (json.JSONDecodeError, TypeError):
                pass
    assert type_config_found


# ============================================================================
# GET CUSTOM FIELD WITH DESCENDANTS TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_get_field_with_descendants_empty():
    """Test get_custom_field_with_descendants returns empty list."""
    conn = _FakeConn()
    conn.fetch_result = []
    repo = CustomFieldRepository(db_connection=conn)

    result = await repo.get_custom_field_with_descendants("field-1", "org-1")

    assert result == []
    assert len(conn.fetch_calls) == 1
    query = conn.fetch_calls[0][0]
    assert "WITH RECURSIVE subtree" in query
    assert "field-1" in conn.fetch_calls[0][1]
    assert "org-1" in conn.fetch_calls[0][1]


@pytest.mark.asyncio
async def test_get_field_with_descendants_single():
    """Test get_custom_field_with_descendants returns single field."""
    conn = _FakeConn()
    conn.fetch_result = [
        {
            "id": "field-1",
            "field_name": "Test",
            "field_key": "test",
            "field_type": "text",
            "parent_id": None,
        }
    ]
    repo = CustomFieldRepository(db_connection=conn)

    result = await repo.get_custom_field_with_descendants("field-1", "org-1")

    assert len(result) == 1
    assert result[0]["id"] == "field-1"


@pytest.mark.asyncio
async def test_get_field_with_descendants_multiple():
    """Test get_custom_field_with_descendants returns field tree."""
    conn = _FakeConn()
    conn.fetch_result = [
        {
            "id": "parent-1",
            "field_name": "Parent",
            "field_key": "parent",
            "field_type": "object",
            "parent_id": None,
        },
        {
            "id": "child-1",
            "field_name": "Child",
            "field_key": "child",
            "field_type": "text",
            "parent_id": "parent-1",
        },
    ]
    repo = CustomFieldRepository(db_connection=conn)

    result = await repo.get_custom_field_with_descendants("parent-1", "org-1")

    assert len(result) == 2
    assert result[0]["id"] == "parent-1"
    assert result[1]["id"] == "child-1"


@pytest.mark.asyncio
async def test_get_field_with_descendants_orders_by_sort():
    """Test get_custom_field_with_descendants orders by sort_order."""
    conn = _FakeConn()
    conn.fetch_result = []
    repo = CustomFieldRepository(db_connection=conn)

    await repo.get_custom_field_with_descendants("field-1", "org-1")

    query = conn.fetch_calls[0][0]
    assert "ORDER BY parent_id NULLS FIRST, sort_order ASC" in query


# ============================================================================
# GET CUSTOM FIELDS BY ENTITY TYPE TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_get_fields_by_entity_type_empty():
    """Test get_custom_fields_by_entity_type returns empty list."""
    conn = _FakeConn()
    conn.fetch_result = []
    repo = CustomFieldRepository(db_connection=conn)

    result = await repo.get_custom_fields_by_entity_type("org-1", EntityType.CONTACT)

    assert result == []
    assert len(conn.fetch_calls) == 1
    query = conn.fetch_calls[0][0]
    assert "SELECT *" in query
    assert "FROM custom_fields" in query
    assert "organization_id = $1" in query
    assert "entity_type = $2" in query
    assert EntityType.CONTACT.value in conn.fetch_calls[0][1]


@pytest.mark.asyncio
async def test_get_fields_by_entity_type_with_results():
    """Test get_custom_fields_by_entity_type returns fields."""
    conn = _FakeConn()
    conn.fetch_result = [
        {
            "id": "field-1",
            "field_name": "Field 1",
            "field_key": "field_1",
            "field_type": "text",
            "entity_type": "contact",
        }
    ]
    repo = CustomFieldRepository(db_connection=conn)

    result = await repo.get_custom_fields_by_entity_type("org-1", EntityType.CONTACT)

    assert len(result) == 1
    assert result[0]["id"] == "field-1"


@pytest.mark.asyncio
async def test_get_fields_by_entity_type_filters_active():
    """Test get_custom_fields_by_entity_type filters active only."""
    conn = _FakeConn()
    conn.fetch_result = []
    repo = CustomFieldRepository(db_connection=conn)

    await repo.get_custom_fields_by_entity_type("org-1", EntityType.CONTACT)

    query = conn.fetch_calls[0][0]
    assert "is_active = TRUE" in query


@pytest.mark.asyncio
async def test_get_fields_by_entity_type_orders_correctly():
    """Test get_custom_fields_by_entity_type orders correctly."""
    conn = _FakeConn()
    conn.fetch_result = []
    repo = CustomFieldRepository(db_connection=conn)

    await repo.get_custom_fields_by_entity_type("org-1", EntityType.CONTACT)

    query = conn.fetch_calls[0][0]
    assert "ORDER BY parent_id NULLS FIRST, sort_order ASC" in query


# ============================================================================
# CHECK FIELD KEY EXISTS TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_check_field_key_exists_true():
    """Test check_field_key_exists returns True when exists."""
    conn = _FakeConn()
    conn.fetchrow_result = {"exists": True}
    repo = CustomFieldRepository(db_connection=conn)

    result = await repo.check_field_key_exists("org-1", "contact", "test_field")

    assert result is True
    assert len(conn.fetchrow_calls) == 1
    query = conn.fetchrow_calls[0][0]
    assert "EXISTS" in query
    assert "custom_fields" in query
    assert "organization_id = $1" in query
    assert "entity_type = $2" in query
    assert "field_key = $3" in query
    assert "parent_id IS NULL" in query
    assert "is_active = TRUE" in query


@pytest.mark.asyncio
async def test_check_field_key_exists_false():
    """Test check_field_key_exists returns False when not exists."""
    conn = _FakeConn()
    conn.fetchrow_result = {"exists": False}
    repo = CustomFieldRepository(db_connection=conn)

    result = await repo.check_field_key_exists("org-1", "contact", "test_field")

    assert result is False


@pytest.mark.asyncio
async def test_check_field_key_exists_only_root_fields():
    """Test check_field_key_exists only checks root fields."""
    conn = _FakeConn()
    conn.fetchrow_result = {"exists": False}
    repo = CustomFieldRepository(db_connection=conn)

    await repo.check_field_key_exists("org-1", "contact", "test_field")

    query = conn.fetchrow_calls[0][0]
    assert "parent_id IS NULL" in query
