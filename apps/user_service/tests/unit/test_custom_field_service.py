"""Unit tests for CustomFieldService business logic."""

# pylint: disable=too-many-lines
import types
from typing import Any

import pytest

from apps.user_service.app.schemas.custom_fields import (
    CreateCustomFieldRequest,
    CustomFieldResponse,
    FlatFieldUpdateRequest,
    SubFieldResponse,
    UpdateCustomFieldRequest,
)
from apps.user_service.app.schemas.enums import EntityType, FieldType
from apps.user_service.app.services.custom_field_service import CustomFieldService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)


def _root_cell(
    field_id: str,
    value: Any,
    instance_id: str = "10000000-0000-4000-8000-000000000001",
    *,
    field_type: str = "text",
) -> dict[str, Any]:
    """Stored/read root FieldCell (includes server snapshot ``type``)."""
    return {
        "field_id": field_id,
        "instance_id": instance_id,
        "type": field_type,
        "value": value,
    }


class _FakeCustomFieldRepo:
    """Lightweight fake custom field repository."""

    def __init__(self):
        """Initialize fake repository."""
        self.calls = {}
        self.field_key_exists = False
        self.create_result = {"id": "field-1"}
        self.bulk_create_result = ["field-2", "field-3"]
        self.get_fields_result = []
        self.get_field_result = None

    async def check_field_key_exists(self, organization_id, entity_type, field_key):
        """Return existence flag."""
        self.calls["check_field_key_exists"] = (
            organization_id,
            entity_type,
            field_key,
        )
        return self.field_key_exists

    async def create_custom_field(self, field_data):
        """Create field and return result."""
        self.calls["create_custom_field"] = field_data
        return self.create_result

    async def bulk_create_custom_fields(self, fields_data):
        """Bulk create fields."""
        self.calls["bulk_create_custom_fields"] = fields_data
        # Return IDs matching the number of fields being created
        num_fields = len(fields_data)
        return [f"field-{i + 2}" for i in range(num_fields)]

    async def get_custom_fields_by_entity_type(self, organization_id, entity_type):
        """Get fields by entity type."""
        self.calls["get_custom_fields_by_entity_type"] = (
            organization_id,
            entity_type,
        )
        return self.get_fields_result

    async def get_custom_field_with_descendants(self, field_id, organization_id):
        """Get field with descendants."""
        self.calls["get_custom_field_with_descendants"] = (
            field_id,
            organization_id,
        )
        return self.get_field_result

    async def update_custom_field(self, field_id, organization_id, update_data):
        """Record update call."""
        self.calls["update_custom_field"] = (field_id, organization_id, update_data)
        return {"id": field_id}

    async def bulk_update_custom_fields(self, organization_id, updates):
        """Record bulk update call."""
        self.calls["bulk_update_custom_fields"] = (organization_id, updates)

    async def bulk_delete_custom_fields_with_descendants(self, organization_id, field_ids):
        """Record bulk delete call."""
        self.calls["bulk_delete_custom_fields_with_descendants"] = (
            organization_id,
            field_ids,
        )


def _ctx(org_id="org-1"):
    """Build reusable UserContext for tests."""
    return UserContext(
        user_id="u1",
        email="u1@example.com",
        organization_id=org_id,
        user_type="admin",
    )


# ============================================================================
# FIELD KEY GENERATION TESTS
# ============================================================================


def test_generate_field_key_simple():
    """Test generate_field_key with simple name."""
    service = CustomFieldService(db_connection=None)
    key = service.generate_field_key("Test Field")
    assert key == "test_field"


def test_generate_field_key_with_spaces():
    """Test generate_field_key with multiple spaces."""
    service = CustomFieldService(db_connection=None)
    key = service.generate_field_key("Test  Multiple   Spaces")
    assert key == "test_multiple_spaces"


def test_generate_field_key_with_hyphens():
    """Test generate_field_key with hyphens."""
    service = CustomFieldService(db_connection=None)
    key = service.generate_field_key("Test-Field-Name")
    assert key == "test_field_name"


def test_generate_field_key_special_chars():
    """Test generate_field_key removes special characters."""
    service = CustomFieldService(db_connection=None)
    key = service.generate_field_key("Test@Field#Name$123")
    assert key == "testfieldname123"


def test_generate_key_leading_trailing_underscores():
    """Test generate_field_key removes leading/trailing underscores."""
    service = CustomFieldService(db_connection=None)
    key = service.generate_field_key("  Test Field  ")
    assert key == "test_field"


def test_generate_field_key_consecutive_underscores():
    """Test generate_field_key collapses consecutive underscores."""
    service = CustomFieldService(db_connection=None)
    key = service.generate_field_key("Test___Field")
    assert key == "test_field"


# ============================================================================
# CREATE CUSTOM FIELD TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_create_field_missing_entity_type(monkeypatch):
    """Test create_field raises when entity_type missing."""
    fake_repo = _FakeCustomFieldRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    request_data = CreateCustomFieldRequest(field_name="Test", field_type=FieldType.TEXT)

    with pytest.raises(ValidationException) as exc_info:
        await service.create_custom_field(request_data)
    assert "entity_type_required" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_create_field_key_exists(monkeypatch):
    """Test create_field raises when field_key exists."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.field_key_exists = True
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    request_data = CreateCustomFieldRequest(
        field_name="Test",
        field_type=FieldType.TEXT,
        entity_type=EntityType.CONTACT,
    )

    with pytest.raises(ConflictException) as exc_info:
        await service.create_custom_field(request_data)
    assert "field_key_exists" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_create_field_success(monkeypatch):
    """Test create_field successfully creates field."""
    fake_repo = _FakeCustomFieldRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    request_data = CreateCustomFieldRequest(
        field_name="Test Field",
        field_type=FieldType.TEXT,
        entity_type=EntityType.CONTACT,
        description="Test description",
    )

    await service.create_custom_field(request_data)

    assert "check_field_key_exists" in fake_repo.calls
    assert "create_custom_field" in fake_repo.calls
    call_data = fake_repo.calls["create_custom_field"]
    assert call_data["field_name"] == "Test Field"
    assert call_data["field_key"] == "test_field"
    assert call_data["entity_type"] == "contact"


@pytest.mark.asyncio
async def test_create_field_with_object_sub_fields(monkeypatch):
    """Test create_field with object type and sub_fields."""
    fake_repo = _FakeCustomFieldRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    request_data = CreateCustomFieldRequest(
        field_name="Parent Field",
        field_type=FieldType.OBJECT,
        entity_type=EntityType.CONTACT,
        sub_fields=[
            CreateCustomFieldRequest(field_name="Child 1", field_type=FieldType.TEXT),
            CreateCustomFieldRequest(field_name="Child 2", field_type=FieldType.NUMBER),
        ],
    )

    await service.create_custom_field(request_data)

    assert "create_custom_field" in fake_repo.calls
    assert "bulk_create_custom_fields" in fake_repo.calls
    bulk_data = fake_repo.calls["bulk_create_custom_fields"]
    assert len(bulk_data) == 2
    assert bulk_data[0]["field_name"] == "Child 1"
    assert bulk_data[1]["field_name"] == "Child 2"


@pytest.mark.asyncio
async def test_create_object_sub_fields_with_description(monkeypatch):
    """Test create_field with object type and sub_fields that have description (covers line 207)."""
    fake_repo = _FakeCustomFieldRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    request_data = CreateCustomFieldRequest(
        field_name="Parent Field",
        field_type=FieldType.OBJECT,
        entity_type=EntityType.CONTACT,
        sub_fields=[
            CreateCustomFieldRequest(
                field_name="Child With Desc",
                field_type=FieldType.TEXT,
                description="Sub-field description",
            ),
        ],
    )

    await service.create_custom_field(request_data)

    bulk_data = fake_repo.calls["bulk_create_custom_fields"]
    assert len(bulk_data) == 1
    assert bulk_data[0]["description"] == "Sub-field description"


@pytest.mark.asyncio
async def test_create_field_nested_sub_fields(monkeypatch):
    """Test create_field with nested sub_fields."""
    fake_repo = _FakeCustomFieldRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    request_data = CreateCustomFieldRequest(
        field_name="Root",
        field_type=FieldType.OBJECT,
        entity_type=EntityType.CONTACT,
        sub_fields=[
            CreateCustomFieldRequest(
                field_name="Child",
                field_type=FieldType.OBJECT,
                sub_fields=[
                    CreateCustomFieldRequest(
                        field_name="Grandchild",
                        field_type=FieldType.TEXT,
                    )
                ],
            )
        ],
    )

    await service.create_custom_field(request_data)

    # Should create root, then child, then grandchild
    assert "create_custom_field" in fake_repo.calls
    assert "bulk_create_custom_fields" in fake_repo.calls


@pytest.mark.asyncio
async def test_create_field_with_list_type(monkeypatch):
    """Test create_field with list type and single child field."""
    fake_repo = _FakeCustomFieldRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    request_data = CreateCustomFieldRequest(
        field_name="Tags List",
        field_type=FieldType.LIST,
        entity_type=EntityType.CONTACT,
        sub_fields=[
            CreateCustomFieldRequest(field_name="Tag", field_type=FieldType.TEXT),
        ],
    )

    await service.create_custom_field(request_data)

    assert "create_custom_field" in fake_repo.calls
    assert "bulk_create_custom_fields" in fake_repo.calls
    bulk_data = fake_repo.calls["bulk_create_custom_fields"]
    assert len(bulk_data) == 1
    assert bulk_data[0]["field_name"] == "Tag"


@pytest.mark.asyncio
async def test_create_field_list_with_nested_object(monkeypatch):
    """Test create_field with list type containing object child."""
    fake_repo = _FakeCustomFieldRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    request_data = CreateCustomFieldRequest(
        field_name="Addresses List",
        field_type=FieldType.LIST,
        entity_type=EntityType.CONTACT,
        sub_fields=[
            CreateCustomFieldRequest(
                field_name="Address",
                field_type=FieldType.OBJECT,
                sub_fields=[
                    CreateCustomFieldRequest(field_name="Street", field_type=FieldType.TEXT),
                    CreateCustomFieldRequest(field_name="City", field_type=FieldType.TEXT),
                ],
            ),
        ],
    )

    await service.create_custom_field(request_data)

    assert "create_custom_field" in fake_repo.calls
    assert "bulk_create_custom_fields" in fake_repo.calls


@pytest.mark.asyncio
async def test_create_field_duplicate_sub_field_keys(monkeypatch):
    """Test create_field raises when sub_fields have duplicate keys."""
    fake_repo = _FakeCustomFieldRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    request_data = CreateCustomFieldRequest(
        field_name="Parent",
        field_type=FieldType.OBJECT,
        entity_type=EntityType.CONTACT,
        sub_fields=[
            CreateCustomFieldRequest(field_name="Same Name", field_type=FieldType.TEXT),
            CreateCustomFieldRequest(field_name="Same Name", field_type=FieldType.NUMBER),
        ],
    )

    with pytest.raises(ConflictException) as exc_info:
        await service.create_custom_field(request_data)
    assert "field_key_exists" in str(exc_info.value.message_key)


# ============================================================================
# GET CUSTOM FIELDS LIST TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_get_fields_list_empty(monkeypatch):
    """Test get_custom_fields_list returns empty list."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = []
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)

    fields, total = await service.get_custom_fields_list(EntityType.CONTACT)

    assert len(fields) == 0
    assert total == 0


@pytest.mark.asyncio
async def test_get_fields_list_with_results(monkeypatch):
    """Test get_custom_fields_list returns fields."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Field 1",
            "field_key": "field_1",
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
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)

    fields, total = await service.get_custom_fields_list(EntityType.CONTACT)

    assert len(fields) == 1
    assert total == 1
    assert fields[0].id == "field-1"


@pytest.mark.asyncio
async def test_get_fields_list_with_sub_fields(monkeypatch):
    """Test get_custom_fields_list builds nested structure."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "parent-1",
            "field_name": "Parent",
            "field_key": "parent",
            "field_type": "object",
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
            "id": "child-1",
            "field_name": "Child",
            "field_key": "child",
            "field_type": "text",
            "parent_id": "parent-1",
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
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)

    fields, total = await service.get_custom_fields_list(EntityType.CONTACT)

    assert len(fields) == 1
    assert total == 1
    assert len(fields[0].sub_fields) == 1
    assert fields[0].sub_fields[0].field_name == "Child"


# ============================================================================
# GET CUSTOM FIELD BY ID TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_get_field_by_id_not_found(monkeypatch):
    """Test get_custom_field_by_id raises when field not found."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_field_result = []
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)

    with pytest.raises(NotFoundException) as exc_info:
        await service.get_custom_field_by_id("field-1")
    assert "field_not_found" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_get_field_by_id_success(monkeypatch):
    """Test get_custom_field_by_id returns field."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_field_result = [
        {
            "id": "field-1",
            "field_name": "Test Field",
            "field_key": "test_field",
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
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)

    field = await service.get_custom_field_by_id("field-1")

    assert field.id == "field-1"
    assert field.field_name == "Test Field"


@pytest.mark.asyncio
async def test_get_field_by_id_with_descendants(monkeypatch):
    """Test get_custom_field_by_id includes descendants."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_field_result = [
        {
            "id": "parent-1",
            "field_name": "Parent",
            "field_key": "parent",
            "field_type": "object",
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
            "id": "child-1",
            "field_name": "Child",
            "field_key": "child",
            "field_type": "text",
            "parent_id": "parent-1",
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
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)

    field = await service.get_custom_field_by_id("parent-1")

    assert field.id == "parent-1"
    assert len(field.sub_fields) == 1
    assert field.sub_fields[0].id == "child-1"


# ============================================================================
# PREPARE FIELD DATA TESTS
# ============================================================================


def test_prepare_field_data_top_level():
    """Test _prepare_field_data for top-level field."""
    service = CustomFieldService(db_connection=None)
    request = CreateCustomFieldRequest(
        field_name="Test",
        field_type=FieldType.TEXT,
        entity_type=EntityType.CONTACT,
        description="Desc",
    )

    data = service._prepare_field_data(request, "org-1", "contact", "test", "user-1", None)

    assert data["organization_id"] == "org-1"
    assert data["entity_type"] == "contact"
    assert data["field_name"] == "Test"
    assert data["field_key"] == "test"
    assert data["description"] == "Desc"
    assert "parent_id" not in data


def test_prepare_field_data_sub_field():
    """Test _prepare_field_data for sub-field."""
    service = CustomFieldService(db_connection=None)
    request = CreateCustomFieldRequest(field_name="Sub", field_type=FieldType.TEXT)

    data = service._prepare_field_data(request, "org-1", "contact", "sub", "user-1", "parent-1")

    assert data["parent_id"] == "parent-1"
    assert data["organization_id"] == "org-1"


def test_prepare_field_data_optional_fields():
    """Test _prepare_field_data includes optional fields."""
    service = CustomFieldService(db_connection=None)
    request = CreateCustomFieldRequest(
        field_name="Test",
        field_type=FieldType.TEXT,
        entity_type=EntityType.CONTACT,
        show_on_create=False,
        show_on_detail=True,
        is_required=True,
        sort_order=5,
    )

    data = service._prepare_field_data(request, "org-1", "contact", "test", "user-1", None)

    assert data["show_on_create"] is False
    assert data["show_on_detail"] is True
    assert data["is_required"] is True
    assert data["sort_order"] == 5


def test_prepare_field_data_no_description():
    """Test _prepare_field_data omits None description."""
    service = CustomFieldService(db_connection=None)
    request = CreateCustomFieldRequest(
        field_name="Test",
        field_type=FieldType.TEXT,
        entity_type=EntityType.CONTACT,
    )

    data = service._prepare_field_data(request, "org-1", "contact", "test", "user-1", None)

    assert "description" not in data


# ============================================================================
# UPDATE CUSTOM FIELD TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_update_field_not_found(monkeypatch):
    """Test update_custom_field raises when field not found."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_field_result = []
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    body = UpdateCustomFieldRequest()

    with pytest.raises(NotFoundException) as exc_info:
        await service.update_custom_field("field-1", body)
    assert "field_not_found" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_update_field_root_only(monkeypatch):
    """Test update_custom_field updates root field only."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_field_result = [
        {
            "id": "field-1",
            "field_name": "Old",
            "field_key": "old",
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
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    body = UpdateCustomFieldRequest(field_name="New Name")

    await service.update_custom_field("field-1", body)

    assert "update_custom_field" in fake_repo.calls
    _, org_id, update_data = fake_repo.calls["update_custom_field"]
    assert org_id == "org-1"
    assert update_data["field_name"] == "New Name"
    assert "updated_by" in update_data


@pytest.mark.asyncio
async def test_update_field_with_remove(monkeypatch):
    """Test update_custom_field calls bulk_delete for remove."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_field_result = [
        {
            "id": "field-1",
            "field_name": "Root",
            "field_key": "root",
            "field_type": "object",
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
            "id": "child-1",
            "field_name": "Child",
            "field_key": "child",
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
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    body = UpdateCustomFieldRequest(remove=["child-1"])

    await service.update_custom_field("field-1", body)

    assert "bulk_delete_custom_fields_with_descendants" in fake_repo.calls
    _, field_ids = fake_repo.calls["bulk_delete_custom_fields_with_descendants"]
    assert field_ids == ["child-1"]


@pytest.mark.asyncio
async def test_update_field_with_flat_updates(monkeypatch):
    """Test update_custom_field calls bulk_update for update list."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_field_result = [
        {
            "id": "field-1",
            "field_name": "Root",
            "field_key": "root",
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
            "id": "child-1",
            "field_name": "Child",
            "field_key": "child",
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
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    body = UpdateCustomFieldRequest(
        update=[
            FlatFieldUpdateRequest(id="child-1", field_name="Updated Child"),
        ],
    )

    await service.update_custom_field("field-1", body)

    assert "bulk_update_custom_fields" in fake_repo.calls
    _, updates = fake_repo.calls["bulk_update_custom_fields"]
    assert len(updates) == 1
    assert updates[0]["id"] == "child-1"
    assert updates[0]["field_name"] == "Updated Child"


@pytest.mark.asyncio
async def test_update_field_remove_id_not_in_subtree_raises(monkeypatch):
    """Test update_custom_field raises when remove id not in subtree."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_field_result = [
        {
            "id": "field-1",
            "field_name": "Root",
            "field_key": "root",
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
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    body = UpdateCustomFieldRequest(remove=["other-id"])

    with pytest.raises(NotFoundException) as exc_info:
        await service.update_custom_field("field-1", body)
    assert "field_not_found" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_update_field_update_id_not_in_subtree_raises(monkeypatch):
    """Test update_custom_field raises when update id not in subtree."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_field_result = [
        {
            "id": "field-1",
            "field_name": "Root",
            "field_key": "root",
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
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    body = UpdateCustomFieldRequest(
        update=[FlatFieldUpdateRequest(id="other-id", field_name="X")],
    )

    with pytest.raises(NotFoundException) as exc_info:
        await service.update_custom_field("field-1", body)
    assert "field_not_found" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_update_field_with_add(monkeypatch):
    """Test update_custom_field calls create for add list."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_field_result = [
        {
            "id": "field-1",
            "field_name": "Root",
            "field_key": "root",
            "field_type": "object",
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
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    body = UpdateCustomFieldRequest(
        add=[
            CreateCustomFieldRequest(
                field_name="New Sub",
                field_type=FieldType.TEXT,
                parent_id="field-1",
            ),
        ],
    )

    await service.update_custom_field("field-1", body)

    assert "bulk_create_custom_fields" in fake_repo.calls or "create_custom_field" in (
        fake_repo.calls
    )


@pytest.mark.asyncio
async def test_update_field_add_without_parent_id_raises(monkeypatch):
    """Test update_custom_field raises when add item has no parent_id (schema validation)."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_field_result = [
        {
            "id": "field-1",
            "field_name": "Root",
            "field_key": "root",
            "field_type": "object",
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
        lambda db_connection=None: fake_repo,
    )
    _ = CustomFieldService(user_context=_ctx(), db_connection=None)
    with pytest.raises(ValidationException) as exc_info:
        UpdateCustomFieldRequest(
            add=[
                CreateCustomFieldRequest(
                    field_name="Orphan",
                    field_type=FieldType.TEXT,
                    parent_id=None,
                ),
            ],
        )
    assert "parent_id_required_for_add" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_add_missing_parent_id_raises(monkeypatch):
    """Test _create_fields_with_nested_children raises when parent_id is None (covers line 551)."""
    fake_repo = _FakeCustomFieldRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    fields_to_add = [
        CreateCustomFieldRequest(
            field_name="Orphan",
            field_type=FieldType.TEXT,
            parent_id=None,
        ),
    ]

    with pytest.raises(ValidationException) as exc_info:
        await service._create_fields_with_nested_children(
            fields_to_add, "contact", service.user_context.user_id
        )
    assert "parent_id_required_for_add" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_add_parent_id_not_in_subtree_raises(monkeypatch):
    """Test update_custom_field raises when add item parent_id not in subtree (covers line 736)."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_field_result = [
        {
            "id": "field-1",
            "field_name": "Root",
            "field_key": "root",
            "field_type": "object",
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
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    body = UpdateCustomFieldRequest(
        add=[
            CreateCustomFieldRequest(
                field_name="New Sub",
                field_type=FieldType.TEXT,
                parent_id="nonexistent-parent-id",
            ),
        ],
    )

    with pytest.raises(NotFoundException) as exc_info:
        await service.update_custom_field("field-1", body)
    assert "field_not_found" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_update_field_with_add_object_and_sub_fields(monkeypatch):
    """Test update_custom_field add with OBJECT type and sub_fields (covers 571-574)."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_field_result = [
        {
            "id": "field-1",
            "field_name": "Root",
            "field_key": "root",
            "field_type": "object",
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
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    body = UpdateCustomFieldRequest(
        add=[
            CreateCustomFieldRequest(
                field_name="New Object",
                field_type=FieldType.OBJECT,
                parent_id="field-1",
                sub_fields=[
                    CreateCustomFieldRequest(
                        field_name="Nested Child",
                        field_type=FieldType.TEXT,
                    ),
                ],
            ),
        ],
    )

    await service.update_custom_field("field-1", body)

    assert "bulk_create_custom_fields" in fake_repo.calls
    bulk_data = fake_repo.calls["bulk_create_custom_fields"]
    assert len(bulk_data) >= 1
    assert bulk_data[0]["field_name"] == "New Object"
    # Nested child is created via _create_field_iterative (second bulk_create or create)
    assert fake_repo.calls.get("bulk_create_custom_fields") or fake_repo.calls.get(
        "create_custom_field"
    )


@pytest.mark.asyncio
async def test_root_object_to_non_obj_deletes_desc(monkeypatch):
    """Update_custom_field: root OBJECT->non-OBJECT deletes descendants."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_field_result = [
        {
            "id": "field-1",
            "field_name": "Root",
            "field_key": "root",
            "field_type": "object",
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
            "id": "child-1",
            "field_name": "Child",
            "field_key": "child",
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
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    body = UpdateCustomFieldRequest(field_type=FieldType.TEXT)

    await service.update_custom_field("field-1", body)

    assert "bulk_delete_custom_fields_with_descendants" in fake_repo.calls
    _, deleted_ids = fake_repo.calls["bulk_delete_custom_fields_with_descendants"]
    assert "child-1" in deleted_ids


@pytest.mark.asyncio
async def test_child_object_to_non_obj_deletes_desc(monkeypatch):
    """Update_custom_field: child OBJECT->non-OBJECT deletes descendants."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_field_result = [
        {
            "id": "field-1",
            "field_name": "Root",
            "field_key": "root",
            "field_type": "object",
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
            "id": "child-obj",
            "field_name": "Child Object",
            "field_key": "child_object",
            "field_type": "object",
            "parent_id": "field-1",
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        },
        {
            "id": "grandchild-1",
            "field_name": "Grandchild",
            "field_key": "grandchild",
            "field_type": "text",
            "parent_id": "child-obj",
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
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    body = UpdateCustomFieldRequest(
        update=[
            FlatFieldUpdateRequest(id="child-obj", field_type=FieldType.TEXT),
        ],
    )

    await service.update_custom_field("field-1", body)

    assert "bulk_delete_custom_fields_with_descendants" in fake_repo.calls
    _, deleted_ids = fake_repo.calls["bulk_delete_custom_fields_with_descendants"]
    assert "grandchild-1" in deleted_ids


@pytest.mark.asyncio
async def test_update_field_root_and_flat_updates_together(monkeypatch):
    """Test update_custom_field with both root update and flat updates (covers 701-706)."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_field_result = [
        {
            "id": "field-1",
            "field_name": "Root",
            "field_key": "root",
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
            "id": "child-1",
            "field_name": "Child",
            "field_key": "child",
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
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    body = UpdateCustomFieldRequest(
        field_name="New Root Name",
        update=[
            FlatFieldUpdateRequest(id="child-1", field_name="New Child Name"),
        ],
    )

    await service.update_custom_field("field-1", body)

    assert "update_custom_field" in fake_repo.calls
    _, _, root_data = fake_repo.calls["update_custom_field"]
    assert root_data["field_name"] == "New Root Name"
    assert "bulk_update_custom_fields" in fake_repo.calls
    _, updates = fake_repo.calls["bulk_update_custom_fields"]
    assert len(updates) == 1
    assert updates[0]["field_name"] == "New Child Name"


# ============================================================================
# PREPARE ROOT / FLAT UPDATE DATA TESTS
# ============================================================================


def test_prepare_root_field_update_data():
    """Test _prepare_root_field_update_data includes only set fields."""
    service = CustomFieldService(db_connection=None)
    req = UpdateCustomFieldRequest(
        field_name="New Name",
        description="New desc",
        sort_order=1,
    )

    data = service._prepare_root_field_update_data(req, "user-1")

    assert data["field_name"] == "New Name"
    assert data["description"] == "New desc"
    assert data["sort_order"] == 1
    assert data["updated_by"] == "user-1"
    assert "field_type" not in data


def test_prepare_root_field_update_data_empty():
    """Test _prepare_root_field_update_data returns empty when nothing set."""
    service = CustomFieldService(db_connection=None)
    req = UpdateCustomFieldRequest()

    data = service._prepare_root_field_update_data(req, "user-1")

    assert not data


def test_prepare_root_field_update_data_all_optionals():
    """Test _prepare_root_field_update_data includes
    field_type, type_config, show_on_*, is_required."""
    service = CustomFieldService(db_connection=None)
    req = UpdateCustomFieldRequest(
        field_name="Name",
        description="Desc",
        field_type=FieldType.TEXT,
        type_config={"max_length": 100},
        show_on_create=False,
        show_on_detail=True,
        is_required=True,
        sort_order=3,
    )

    data = service._prepare_root_field_update_data(req, "user-1")

    assert data["field_name"] == "Name"
    assert data["description"] == "Desc"
    assert data["field_type"] == "text"
    assert "type_config" in data
    assert data["show_on_create"] is False
    assert data["show_on_detail"] is True
    assert data["is_required"] is True
    assert data["sort_order"] == 3
    assert data["updated_by"] == "user-1"


def test_prepare_flat_field_update_data():
    """Test _prepare_flat_field_update_data includes id and updated_by."""
    service = CustomFieldService(db_connection=None)
    req = FlatFieldUpdateRequest(
        id="child-1",
        field_name="Child Name",
        sort_order=2,
    )

    data = service._prepare_flat_field_update_data(req, "user-1")

    assert data["id"] == "child-1"
    assert data["field_name"] == "Child Name"
    assert data["sort_order"] == 2
    assert data["updated_by"] == "user-1"


def test_prepare_flat_field_update_data_all_optionals():
    """Test _prepare_flat_field_update_data includes all optional fields when set."""
    service = CustomFieldService(db_connection=None)
    req = FlatFieldUpdateRequest(
        id="child-1",
        field_name="Child Name",
        description="Child desc",
        field_type=FieldType.NUMBER,
        type_config={"min": 0, "max": 100},
        show_on_create=False,
        show_on_detail=True,
        is_required=True,
        sort_order=2,
    )

    data = service._prepare_flat_field_update_data(req, "user-1")

    assert data["id"] == "child-1"
    assert data["field_name"] == "Child Name"
    assert data["description"] == "Child desc"
    assert data["field_type"] == "number"
    assert "type_config" in data
    assert data["show_on_create"] is False
    assert data["show_on_detail"] is True
    assert data["is_required"] is True
    assert data["sort_order"] == 2
    assert data["updated_by"] == "user-1"


# ============================================================================
# DELETE CUSTOM FIELD TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_delete_custom_field_not_found(monkeypatch):
    """Test delete_custom_field raises NotFoundException when field not found."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_field_result = []
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)

    with pytest.raises(NotFoundException) as exc_info:
        await service.delete_custom_field("field-1")
    assert "field_not_found" in str(exc_info.value.message_key)
    assert "get_custom_field_with_descendants" in fake_repo.calls
    assert "bulk_delete_custom_fields_with_descendants" not in fake_repo.calls


@pytest.mark.asyncio
async def test_delete_custom_field_success(monkeypatch):
    """Test delete_custom_field successfully deletes field and descendants."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_field_result = [
        {
            "id": "field-1",
            "field_name": "Root Field",
            "field_key": "root_field",
            "field_type": "object",
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
            "id": "child-1",
            "field_name": "Child Field",
            "field_key": "child_field",
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
        {
            "id": "grandchild-1",
            "field_name": "Grandchild Field",
            "field_key": "grandchild_field",
            "field_type": "text",
            "parent_id": "child-1",
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
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)

    await service.delete_custom_field("field-1")

    # Verify field existence check was called
    assert "get_custom_field_with_descendants" in fake_repo.calls
    field_id, org_id = fake_repo.calls["get_custom_field_with_descendants"]
    assert field_id == "field-1"
    assert org_id == "org-1"

    # Verify delete was called with correct parameters
    assert "bulk_delete_custom_fields_with_descendants" in fake_repo.calls
    delete_org_id, delete_field_ids = fake_repo.calls["bulk_delete_custom_fields_with_descendants"]
    assert delete_org_id == "org-1"
    assert delete_field_ids == ["field-1"]


@pytest.mark.asyncio
async def test_delete_custom_field_with_descendants(monkeypatch):
    """Test delete_custom_field cascades to all descendants."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_field_result = [
        {
            "id": "field-1",
            "field_name": "Parent",
            "field_key": "parent",
            "field_type": "object",
            "parent_id": None,
            "entity_type": "company",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        },
        {
            "id": "child-1",
            "field_name": "Child 1",
            "field_key": "child_1",
            "field_type": "text",
            "parent_id": "field-1",
            "entity_type": "company",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        },
        {
            "id": "child-2",
            "field_name": "Child 2",
            "field_key": "child_2",
            "field_type": "number",
            "parent_id": "field-1",
            "entity_type": "company",
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
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)

    await service.delete_custom_field("field-1")

    # Verify delete was called - repository handles cascading
    assert "bulk_delete_custom_fields_with_descendants" in fake_repo.calls
    _, delete_field_ids = fake_repo.calls["bulk_delete_custom_fields_with_descendants"]
    # Only the root field ID is passed; repository handles finding descendants
    assert delete_field_ids == ["field-1"]


@pytest.mark.asyncio
async def test_delete_custom_field_different_organization(monkeypatch):
    """Test delete_custom_field raises NotFoundException for different organization."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_field_result = []  # Field not found in this organization
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(org_id="org-1"), db_connection=None)

    with pytest.raises(NotFoundException) as exc_info:
        await service.delete_custom_field("field-other-org")
    assert "field_not_found" in str(exc_info.value.message_key)
    # Verify organization check happened
    field_id, org_id = fake_repo.calls["get_custom_field_with_descendants"]
    assert field_id == "field-other-org"
    assert org_id == "org-1"
    # Verify delete was not called
    assert "bulk_delete_custom_fields_with_descendants" not in fake_repo.calls


# ============================================================================
# FieldCell integration (validate / merge / read)
# ============================================================================


@pytest.mark.asyncio
async def test_validate_and_format_empty_list(monkeypatch):
    """Empty list returns []."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = []
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    result = await service.validate_and_format_custom_fields([], EntityType.CONTACT)
    assert result == []


@pytest.mark.asyncio
async def test_validate_for_create_required_missing(monkeypatch):
    """Required root missing from list raises."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
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
        }
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    with pytest.raises(ValidationException) as exc_info:
        await service.validate_for_create([], EntityType.CONTACT)
    assert "custom_field_required" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_validate_for_create_unknown_root_field_id(monkeypatch):
    """validate_for_create rejects payloads whose field_id is not in definitions."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
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
        }
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    with pytest.raises(ValidationException):
        await service.validate_for_create(
            [{"field_id": "nope", "value": "x"}],
            EntityType.CONTACT,
        )


@pytest.mark.asyncio
async def test_validate_for_create_assigns_instance_id(monkeypatch):
    """validate_for_create adds instance_id and normalizes type for valid cells."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
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
        }
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    out = await service.validate_for_create(
        [{"field_id": "field-1", "value": "hello"}],
        EntityType.CONTACT,
    )
    assert len(out) == 1
    assert out[0]["field_id"] == "field-1"
    assert out[0]["value"] == "hello"
    assert out[0]["instance_id"]
    assert out[0]["type"] == "text"


@pytest.mark.asyncio
async def test_merge_list_patch_deletes_omitted_rows(monkeypatch):
    """PATCH list: ``items`` is authoritative; omitted stored rows are removed."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "list-1",
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
            "id": "item-1",
            "field_name": "Tag",
            "field_key": "tag",
            "field_type": "text",
            "parent_id": "list-1",
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
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    stored = [
        {
            "field_id": "list-1",
            "instance_id": "root-1",
            "type": "list",
            "items": [
                {
                    "field_id": "item-1",
                    "instance_id": "row-a",
                    "type": "text",
                    "value": "A",
                },
                {
                    "field_id": "item-1",
                    "instance_id": "row-b",
                    "type": "text",
                    "value": "B",
                },
            ],
        }
    ]
    patch = [
        {
            "field_id": "list-1",
            "instance_id": "root-1",
            "items": [{"field_id": "item-1", "instance_id": "row-a", "value": "A2"}],
        }
    ]
    merged = await service.merge_for_update(patch, stored, EntityType.CONTACT)
    assert len(merged) == 1
    rows = merged[0]["items"]
    assert len(rows) == 1
    assert rows[0]["instance_id"] == "row-a"
    assert rows[0]["value"] == "A2"
    assert rows[0]["type"] == "text"


@pytest.mark.asyncio
async def test_merge_update_iid_shortcut_nested_scalars(monkeypatch):
    """PATCH nested scalars by instance_id only (no root path)."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "list-1",
            "field_name": "Open Jobs",
            "field_key": "open_jobs",
            "field_type": "list",
            "parent_id": None,
            "entity_type": "company",
            "show_on_create": True,
            "show_on_detail": True,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        },
        {
            "id": "item-obj",
            "field_name": "Item",
            "field_key": "item",
            "field_type": "object",
            "parent_id": "list-1",
            "entity_type": "company",
            "show_on_create": True,
            "show_on_detail": True,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        },
        {
            "id": "exp-req",
            "field_name": "Experience Required",
            "field_key": "experience_required",
            "field_type": "text",
            "parent_id": "item-obj",
            "entity_type": "company",
            "show_on_create": True,
            "show_on_detail": True,
            "is_required": False,
            "type_config": {},
            "sort_order": 1,
            "is_active": True,
        },
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    stored = [
        {
            "field_id": "list-1",
            "instance_id": "root-1",
            "type": "list",
            "items": [
                {
                    "field_id": "item-obj",
                    "instance_id": "row-1",
                    "type": "object",
                    "sub_fields": [
                        {
                            "field_id": "exp-req",
                            "instance_id": "c1",
                            "type": "text",
                            "value": None,
                        },
                    ],
                },
                {
                    "field_id": "item-obj",
                    "instance_id": "row-2",
                    "type": "object",
                    "sub_fields": [
                        {
                            "field_id": "exp-req",
                            "instance_id": "c2",
                            "type": "text",
                            "value": None,
                        },
                    ],
                },
            ],
        }
    ]
    patch = [
        {"instance_id": "c1", "value": "5+ years"},
        {"instance_id": "c2", "value": "2+ years"},
    ]
    merged = await service.merge_for_update(patch, stored, EntityType.COMPANY)
    assert len(merged) == 1
    rows = merged[0]["items"]
    assert len(rows) == 2
    by_iid = {r["instance_id"]: r for r in rows}
    subs1 = {str(s["field_id"]): s for s in by_iid["row-1"]["sub_fields"]}
    subs2 = {str(s["field_id"]): s for s in by_iid["row-2"]["sub_fields"]}
    assert subs1["exp-req"]["value"] == "5+ years"
    assert subs2["exp-req"]["value"] == "2+ years"


@pytest.mark.asyncio
async def test_merge_update_iid_shortcut_bad_type_raises(monkeypatch):
    """Shortcut-only PATCH must not reconcile-null invalid explicit values.

    Validation must fail instead.
    """
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "list-1",
            "field_name": "Open Jobs",
            "field_key": "open_jobs",
            "field_type": "list",
            "parent_id": None,
            "entity_type": "company",
            "show_on_create": True,
            "show_on_detail": True,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        },
        {
            "id": "item-obj",
            "field_name": "Item",
            "field_key": "item",
            "field_type": "object",
            "parent_id": "list-1",
            "entity_type": "company",
            "show_on_create": True,
            "show_on_detail": True,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        },
        {
            "id": "year-f",
            "field_name": "Year",
            "field_key": "year",
            "field_type": "number",
            "parent_id": "item-obj",
            "entity_type": "company",
            "show_on_create": True,
            "show_on_detail": True,
            "is_required": False,
            "type_config": {},
            "sort_order": 2,
            "is_active": True,
        },
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    stored = [
        {
            "field_id": "list-1",
            "instance_id": "root-1",
            "type": "list",
            "items": [
                {
                    "field_id": "item-obj",
                    "instance_id": "row-1",
                    "type": "object",
                    "sub_fields": [
                        {
                            "field_id": "year-f",
                            "instance_id": "y1",
                            "type": "number",
                            "value": 2020,
                        },
                    ],
                },
            ],
        }
    ]
    patch = [{"instance_id": "y1", "value": "not-a-number"}]
    with pytest.raises(ValidationException):
        await service.merge_for_update(patch, stored, EntityType.COMPANY)


@pytest.mark.asyncio
async def test_merge_update_num_shortcut_rejects_str(monkeypatch):
    """Number fields require JSON numbers; digit-only strings fail.

    Field definition is the source of truth.
    """
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "phone-f",
            "field_name": "Phone",
            "field_key": "phone",
            "field_type": "number",
            "parent_id": None,
            "entity_type": "company",
            "show_on_create": True,
            "show_on_detail": True,
            "is_required": True,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        },
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    iid = "540660c3-d125-47ec-8c97-7b4b57690447"
    stored = [
        {
            "field_id": "phone-f",
            "instance_id": iid,
            "type": "text",
            "value": "9823238287",
        }
    ]
    patch = [{"instance_id": iid, "value": "9823238287"}]
    with pytest.raises(ValidationException):
        await service.merge_for_update(patch, stored, EntityType.COMPANY)


@pytest.mark.asyncio
async def test_merge_no_patch_nulls_stale_opt_nested_scalar(monkeypatch):
    """Optional sub-field with wrong stored type is nulled when custom_fields omitted on update."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "list-1",
            "field_name": "Open Jobs",
            "field_key": "open_jobs",
            "field_type": "list",
            "parent_id": None,
            "entity_type": "company",
            "show_on_create": True,
            "show_on_detail": True,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        },
        {
            "id": "item-obj",
            "field_name": "Item",
            "field_key": "item",
            "field_type": "object",
            "parent_id": "list-1",
            "entity_type": "company",
            "show_on_create": True,
            "show_on_detail": True,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        },
        {
            "id": "exp-req",
            "field_name": "Experience Required",
            "field_key": "experience_required",
            "field_type": "text",
            "parent_id": "item-obj",
            "entity_type": "company",
            "show_on_create": True,
            "show_on_detail": True,
            "is_required": False,
            "type_config": {},
            "sort_order": 1,
            "is_active": True,
        },
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    stored = [
        {
            "field_id": "list-1",
            "instance_id": "root-1",
            "type": "list",
            "items": [
                {
                    "field_id": "item-obj",
                    "instance_id": "row-1",
                    "type": "object",
                    "sub_fields": [
                        {
                            "field_id": "exp-req",
                            "instance_id": "c1",
                            "type": "number",
                            "value": 7,
                        },
                    ],
                },
            ],
        }
    ]
    merged = await service.merge_for_update(None, stored, EntityType.COMPANY)
    assert len(merged) == 1
    row = merged[0]["items"][0]
    subs = {str(c["field_id"]): c for c in row["sub_fields"]}
    assert subs["exp-req"]["value"] is None
    assert subs["exp-req"]["type"] == "text"


@pytest.mark.asyncio
async def test_reconcile_list_obj_missing_req_nested_raises(monkeypatch):
    """Reconcile must not replace list object rows with empty sub_fields.

    A required nested field must be present or validation raises.
    """
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "list-1",
            "field_name": "Open Jobs",
            "field_key": "open_jobs",
            "field_type": "list",
            "parent_id": None,
            "entity_type": "company",
            "show_on_create": True,
            "show_on_detail": True,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        },
        {
            "id": "item-obj",
            "field_name": "Item",
            "field_key": "item",
            "field_type": "object",
            "parent_id": "list-1",
            "entity_type": "company",
            "show_on_create": True,
            "show_on_detail": True,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        },
        {
            "id": "exp-req",
            "field_name": "Experience Required",
            "field_key": "experience_required",
            "field_type": "text",
            "parent_id": "item-obj",
            "entity_type": "company",
            "show_on_create": True,
            "show_on_detail": True,
            "is_required": True,
            "type_config": {},
            "sort_order": 1,
            "is_active": True,
        },
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    stored = [
        {
            "field_id": "list-1",
            "instance_id": "root-1",
            "type": "list",
            "items": [
                {
                    "field_id": "item-obj",
                    "instance_id": "row-1",
                    "type": "object",
                    "sub_fields": [],
                },
            ],
        }
    ]
    with pytest.raises(ValidationException):
        await service.merge_for_update(None, stored, EntityType.COMPANY)


@pytest.mark.asyncio
async def test_merge_no_patch_nulls_stale_opt_siblings(monkeypatch):
    """Every optional stale sibling is nulled in one reconcile pass, not only the first."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "list-1",
            "field_name": "Open Jobs",
            "field_key": "open_jobs",
            "field_type": "list",
            "parent_id": None,
            "entity_type": "company",
            "show_on_create": True,
            "show_on_detail": True,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        },
        {
            "id": "item-obj",
            "field_name": "Item",
            "field_key": "item",
            "field_type": "object",
            "parent_id": "list-1",
            "entity_type": "company",
            "show_on_create": True,
            "show_on_detail": True,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        },
        {
            "id": "f-a",
            "field_name": "A",
            "field_key": "field_a",
            "field_type": "text",
            "parent_id": "item-obj",
            "entity_type": "company",
            "show_on_create": True,
            "show_on_detail": True,
            "is_required": False,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        },
        {
            "id": "f-b",
            "field_name": "B",
            "field_key": "field_b",
            "field_type": "text",
            "parent_id": "item-obj",
            "entity_type": "company",
            "show_on_create": True,
            "show_on_detail": True,
            "is_required": False,
            "type_config": {},
            "sort_order": 1,
            "is_active": True,
        },
        {
            "id": "f-c",
            "field_name": "C",
            "field_key": "field_c",
            "field_type": "number",
            "parent_id": "item-obj",
            "entity_type": "company",
            "show_on_create": True,
            "show_on_detail": True,
            "is_required": False,
            "type_config": {},
            "sort_order": 2,
            "is_active": True,
        },
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    stored = [
        {
            "field_id": "list-1",
            "instance_id": "root-1",
            "type": "list",
            "items": [
                {
                    "field_id": "item-obj",
                    "instance_id": "row-1",
                    "type": "object",
                    "sub_fields": [
                        {
                            "field_id": "f-a",
                            "instance_id": "ia",
                            "type": "number",
                            "value": 1,
                        },
                        {
                            "field_id": "f-b",
                            "instance_id": "ib",
                            "type": "bool",
                            "value": True,
                        },
                        {
                            "field_id": "f-c",
                            "instance_id": "ic",
                            "type": "text",
                            "value": "not-a-number",
                        },
                    ],
                },
            ],
        }
    ]
    merged = await service.merge_for_update(None, stored, EntityType.COMPANY)
    row = merged[0]["items"][0]
    subs = {str(c["field_id"]): c for c in row["sub_fields"]}
    assert subs["f-a"]["value"] is None
    assert subs["f-b"]["value"] is None
    assert subs["f-c"]["value"] is None


def test_resolve_fields_for_read_scalar_root():
    """resolve_fields_for_read maps a single stored text cell to read shape."""
    service = CustomFieldService(db_connection=None)
    root = CustomFieldResponse(
        id="field-1",
        field_name="N",
        field_key="n",
        description=None,
        field_type="text",
        show_on_create=True,
        show_on_detail=False,
        is_required=False,
        type_config={},
        sort_order=0,
        is_active=True,
        entity_type="contact",
        parent_id=None,
        sub_fields=[],
    )
    id_to_def = {"field-1": root}
    stored = [_root_cell("field-1", "hello")]
    out = service.resolve_fields_for_read(stored, id_to_def)
    assert len(out) == 1
    assert out[0]["field_id"] == "field-1"
    assert out[0]["field_key"] == "n"
    assert out[0]["label"] == "N"
    assert out[0]["type"] == "text"
    assert out[0]["instance_id"]
    assert out[0]["value"] == "hello"


# ============================================================================
# DROPDOWN FILTER VALIDATION TESTS
# ============================================================================


def test_collect_dropdown_ids_nested():
    """collect_dropdown_custom_field_ids finds nested dropdown fields."""
    child = types.SimpleNamespace(id="dd-1", field_type="dropdown", sub_fields=[])
    root = types.SimpleNamespace(id="obj-1", field_type="object", sub_fields=[child])
    ids = CustomFieldService.collect_dropdown_custom_field_ids([root])
    assert ids == {"dd-1"}


@pytest.mark.asyncio
async def test_validate_dropdown_filters_empty(monkeypatch):
    """Empty filter dict is a no-op."""
    fake_repo = _FakeCustomFieldRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    await service.validate_dropdown_filters_for_entity(EntityType.CONTACT, {})


@pytest.mark.asyncio
async def test_validate_dropdown_filters_valid(monkeypatch):
    """Known dropdown field ids pass validation."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "dd-1",
            "field_name": "Status",
            "field_key": "status",
            "field_type": "dropdown",
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
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    await service.validate_dropdown_filters_for_entity(
        EntityType.CONTACT,
        {"dd-1": ["open"]},
    )


@pytest.mark.asyncio
async def test_validate_dropdown_unknown_raises(monkeypatch):
    """Unknown custom field ids in filters raise ValidationException."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = []
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    with pytest.raises(ValidationException) as exc_info:
        await service.validate_dropdown_filters_for_entity(
            EntityType.CONTACT,
            {"missing-id": ["x"]},
        )
    assert "invalid_filter_payload" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_validate_dropdown_non_dropdown_raises(monkeypatch):
    """Text field ids cannot be used as dropdown filters."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "text-1",
            "field_name": "Name",
            "field_key": "name",
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
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    with pytest.raises(ValidationException):
        await service.validate_dropdown_filters_for_entity(
            EntityType.CONTACT,
            {"text-1": ["val"]},
        )


# ============================================================================
# TEST HELPERS (extended coverage)
# ============================================================================


def _patch_repo(monkeypatch, fake_repo):
    """Monkeypatch CustomFieldRepository to return fake_repo."""
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )


def _field_row(
    field_id: str,
    field_name: str,
    field_key: str,
    field_type: str,
    *,
    parent_id: str | None = None,
    entity_type: str = "contact",
    is_required: bool = False,
    type_config: dict[str, Any] | None = None,
    sort_order: int = 0,
) -> dict[str, Any]:
    """Build a flat DB row dict for custom field definitions."""
    return {
        "id": field_id,
        "field_name": field_name,
        "field_key": field_key,
        "field_type": field_type,
        "parent_id": parent_id,
        "entity_type": entity_type,
        "show_on_create": True,
        "show_on_detail": False,
        "is_required": is_required,
        "type_config": type_config or {},
        "sort_order": sort_order,
        "is_active": True,
    }


def _defn(
    field_id: str,
    field_key: str,
    field_type: str,
    *,
    field_name: str = "Field",
    is_required: bool = False,
    type_config: dict[str, Any] | None = None,
    sub_fields: list[Any] | None = None,
    sort_order: int = 0,
) -> Any:
    """Build a lightweight field-definition namespace for unit tests."""
    return types.SimpleNamespace(
        id=field_id,
        field_key=field_key,
        field_name=field_name,
        field_type=field_type,
        is_required=is_required,
        type_config=type_config or {},
        sub_fields=sub_fields or [],
        sort_order=sort_order,
    )


def _sub_field_response(
    field_id: str,
    field_key: str,
    field_type: str,
    parent_id: str,
    *,
    field_name: str = "Field",
    is_required: bool = False,
    type_config: dict[str, Any] | None = None,
    sub_fields: list[Any] | None = None,
    sort_order: int = 0,
    entity_type: str = "contact",
) -> SubFieldResponse:
    """Build SubFieldResponse for nested resolve/read tests."""
    return SubFieldResponse(
        id=field_id,
        field_name=field_name,
        field_key=field_key,
        description=None,
        field_type=field_type,
        show_on_create=True,
        show_on_detail=False,
        is_required=is_required,
        type_config=type_config or {},
        sort_order=sort_order,
        is_active=True,
        entity_type=entity_type,
        parent_id=parent_id,
        sub_fields=sub_fields or [],
    )


def _custom_field_response(
    field_id: str,
    field_key: str,
    field_type: str,
    *,
    field_name: str = "Field",
    is_required: bool = False,
    type_config: dict[str, Any] | None = None,
    sub_fields: list[Any] | None = None,
    sort_order: int = 0,
    entity_type: str = "contact",
) -> CustomFieldResponse:
    """Build CustomFieldResponse for resolve/read tests."""
    return CustomFieldResponse(
        id=field_id,
        field_name=field_name,
        field_key=field_key,
        description=None,
        field_type=field_type,
        show_on_create=True,
        show_on_detail=False,
        is_required=is_required,
        type_config=type_config or {},
        sort_order=sort_order,
        is_active=True,
        entity_type=entity_type,
        parent_id=None,
        sub_fields=sub_fields or [],
    )


# ============================================================================
# SCALAR FIELD COERCION / VALIDATION TESTS
# ============================================================================


def test_coerce_text_field():
    """_coerce_field_value accepts string for text fields."""
    service = CustomFieldService(db_connection=None)
    field_def = _defn("f1", "name", "text")
    assert service._coerce_field_value("name", "hello", field_def) == "hello"


def test_coerce_text_rejects_non_string():
    """_coerce_field_value rejects non-string for text fields."""
    service = CustomFieldService(db_connection=None)
    field_def = _defn("f1", "name", "text")
    with pytest.raises(ValidationException):
        service._coerce_field_value("name", 123, field_def)


def test_coerce_number_accepts_int_and_float():
    """_coerce_field_value coerces JSON numbers."""
    service = CustomFieldService(db_connection=None)
    field_def = _defn("f1", "count", "number")
    assert service._coerce_field_value("count", 42, field_def) == 42.0
    assert service._coerce_field_value("count", 3.5, field_def) == 3.5


def test_coerce_number_rejects_bool_and_string():
    """_coerce_field_value rejects bool and digit strings for numbers."""
    service = CustomFieldService(db_connection=None)
    field_def = _defn("f1", "count", "number")
    with pytest.raises(ValidationException):
        service._coerce_field_value("count", True, field_def)
    with pytest.raises(ValidationException):
        service._coerce_field_value("count", "42", field_def)


def test_coerce_yes_no_from_string_and_int():
    """_coerce_field_value coerces yes/no from strings and ints."""
    service = CustomFieldService(db_connection=None)
    field_def = _defn("f1", "active", "yes_no")
    assert service._coerce_field_value("active", True, field_def) is True
    assert service._coerce_field_value("active", "yes", field_def) is True
    assert service._coerce_field_value("active", 0, field_def) is False


def test_coerce_yes_no_rejects_invalid():
    """_coerce_field_value rejects invalid yes/no values."""
    service = CustomFieldService(db_connection=None)
    field_def = _defn("f1", "active", "yes_no")
    with pytest.raises(ValidationException):
        service._coerce_field_value("active", {"yes": True}, field_def)


def test_coerce_url_valid_and_invalid():
    """_coerce_field_value validates URL prefix."""
    service = CustomFieldService(db_connection=None)
    field_def = _defn("f1", "site", "url")
    assert service._coerce_field_value("site", "https://example.com", field_def) == (
        "https://example.com"
    )
    with pytest.raises(ValidationException):
        service._coerce_field_value("site", "ftp://bad", field_def)


def test_coerce_dropdown_valid_and_invalid_option():
    """_coerce_field_value validates dropdown options."""
    service = CustomFieldService(db_connection=None)
    field_def = _defn(
        "f1",
        "status",
        "dropdown",
        type_config={"options": ["open", "closed"]},
    )
    assert service._coerce_field_value("status", "open", field_def) == "open"
    with pytest.raises(ValidationException):
        service._coerce_field_value("status", "pending", field_def)


def test_coerce_range_slider_in_and_out_of_range():
    """_coerce_field_value enforces range_slider bounds."""
    service = CustomFieldService(db_connection=None)
    field_def = _defn(
        "f1",
        "score",
        "range_slider",
        type_config={"min": 0, "max": 10},
    )
    assert service._coerce_field_value("score", 5, field_def) == 5.0
    with pytest.raises(ValidationException):
        service._coerce_field_value("score", 11, field_def)


def test_coerce_currency_valid_and_invalid():
    """_coerce_field_value validates currency shape and allowed codes."""
    service = CustomFieldService(db_connection=None)
    field_def = _defn(
        "f1",
        "price",
        "currency",
        type_config={"allowed_currencies": ["USD", "EUR"]},
    )
    result = service._coerce_field_value(
        "price",
        {"amount": 100, "currency_code": "USD"},
        field_def,
    )
    assert result == {"amount": 100.0, "currency_code": "USD"}
    with pytest.raises(ValidationException):
        service._coerce_field_value("price", {"amount": 1}, field_def)
    with pytest.raises(ValidationException):
        service._coerce_field_value(
            "price",
            {"amount": 1, "currency_code": "GBP"},
            field_def,
        )


def test_coerce_file_upload_single_and_multiple():
    """_coerce_field_value validates file_upload cardinality."""
    service = CustomFieldService(db_connection=None)
    single_def = _defn(
        "f1",
        "doc",
        "file_upload",
        type_config={"allow_multiple": False, "max_files": 1},
    )
    assert service._coerce_field_value("doc", "file.pdf", single_def) == "file.pdf"
    assert service._coerce_field_value("doc", ["only.pdf"], single_def) == "only.pdf"

    multi_def = _defn(
        "f2",
        "docs",
        "file_upload",
        type_config={"allow_multiple": True, "max_files": 2},
    )
    assert service._coerce_field_value("docs", ["a.pdf", "b.pdf"], multi_def) == [
        "a.pdf",
        "b.pdf",
    ]
    with pytest.raises(ValidationException):
        service._coerce_field_value("docs", ["a", "b", "c"], multi_def)


def test_coerce_address_with_lat_long():
    """_coerce_field_value builds address dict with optional lat/long."""
    service = CustomFieldService(db_connection=None)
    field_def = _defn(
        "f1",
        "addr",
        "address",
        type_config={"include_lat_long": True},
    )
    result = service._coerce_field_value(
        "addr",
        {
            "address_line1": "123 Main",
            "city": "NYC",
            "latitude": 40.7,
            "longitude": -74.0,
        },
        field_def,
    )
    assert result["address_line1"] == "123 Main"
    assert result["latitude"] == 40.7


def test_coerce_required_null_raises():
    """_coerce_field_value raises when required field value is null."""
    service = CustomFieldService(db_connection=None)
    field_def = _defn("f1", "name", "text", is_required=True)
    with pytest.raises(ValidationException):
        service._coerce_field_value("name", None, field_def)


def test_coerce_optional_null_returns_none():
    """_coerce_field_value returns None for optional null scalars."""
    service = CustomFieldService(db_connection=None)
    field_def = _defn("f1", "name", "text")
    assert service._coerce_field_value("name", None, field_def) is None


def test_coerce_rejects_object_scalar():
    """_coerce_field_value rejects scalar coercion on object/list types."""
    service = CustomFieldService(db_connection=None)
    field_def = _defn("f1", "obj", "object")
    with pytest.raises(ValidationException):
        service._coerce_field_value("obj", "x", field_def)


def test_validate_required_fields_presence():
    """_validate_required_fields raises when required id missing from presence."""
    service = CustomFieldService(db_connection=None)
    field_def = _defn("f1", "req", "text", is_required=True)
    with pytest.raises(ValidationException):
        service._validate_required_fields({"f1": field_def}, {})


# ============================================================================
# PAYLOAD PARSE / ENFORCE TESTS
# ============================================================================


def test_parse_roots_create_rejects_server_keys():
    """_parse_roots_create_payload rejects type and instance_id on create."""
    service = CustomFieldService(db_connection=None)
    with pytest.raises(ValidationException) as exc_info:
        service._parse_roots_create_payload([{"field_id": "f1", "type": "text", "value": "x"}])
    assert "forbidden_payload_key" in str(exc_info.value.message_key)

    with pytest.raises(ValidationException):
        service._parse_roots_create_payload([{"field_id": "f1", "instance_id": "i1", "value": "x"}])


def test_parse_roots_create_rejects_bad_discriminator():
    """_parse_roots_create_payload requires exactly one discriminator."""
    service = CustomFieldService(db_connection=None)
    with pytest.raises(ValidationException):
        service._parse_roots_create_payload([{"field_id": "f1"}])
    with pytest.raises(ValidationException):
        service._parse_roots_create_payload([{"field_id": "f1", "value": "x", "sub_fields": []}])


def test_parse_roots_create_nested_sub_fields():
    """_parse_roots_create_payload validates nested sub_fields on create."""
    service = CustomFieldService(db_connection=None)
    roots = service._parse_roots_create_payload(
        [
            {
                "field_id": "obj-1",
                "sub_fields": [{"field_id": "child-1", "value": "nested"}],
            }
        ]
    )
    assert len(roots) == 1
    assert roots[0]["sub_fields"][0]["field_id"] == "child-1"


def test_parse_roots_create_rejects_non_list():
    """_parse_roots_create_payload rejects non-array custom_fields."""
    service = CustomFieldService(db_connection=None)
    with pytest.raises(ValidationException):
        service._parse_roots_create_payload({"field_id": "f1"})


def test_parse_patch_rejects_type_key():
    """_parse_patch_roots_payload rejects type key on patch."""
    service = CustomFieldService(db_connection=None)
    with pytest.raises(ValidationException):
        service._parse_patch_roots_payload([{"field_id": "f1", "type": "text", "value": "x"}])


def test_parse_patch_rejects_non_list():
    """_parse_patch_roots_payload rejects non-array payload."""
    service = CustomFieldService(db_connection=None)
    with pytest.raises(ValidationException):
        service._parse_patch_roots_payload("not-a-list")


def test_parse_roots_storage_variants():
    """_parse_roots_storage handles None, dict, JSON string, and filters bad rows."""
    assert CustomFieldService._parse_roots_storage(None) == []
    assert CustomFieldService._parse_roots_storage({}) == []
    assert CustomFieldService._parse_roots_storage(123) == []
    json_stored = '[{"field_id": "f1", "value": "x"}]'
    parsed = CustomFieldService._parse_roots_storage(json_stored)
    assert len(parsed) == 1
    assert parsed[0]["field_id"] == "f1"


def test_index_patch_roots_errors():
    """_index_patch_roots raises on duplicate or missing field_id."""
    service = CustomFieldService(db_connection=None)
    with pytest.raises(ValidationException):
        service._index_patch_roots([{"value": "x"}])
    with pytest.raises(ValidationException):
        service._index_patch_roots(
            [
                {"field_id": "f1", "value": "a"},
                {"field_id": "f1", "value": "b"},
            ]
        )
    with pytest.raises(ValidationException):
        service._index_patch_roots(["not-a-dict"])


def test_index_sub_field_cells_errors():
    """_index_sub_field_cells raises on invalid or duplicate sub cells."""
    service = CustomFieldService(db_connection=None)
    with pytest.raises(ValidationException):
        service._index_sub_field_cells(["bad"], "path")
    with pytest.raises(ValidationException):
        service._index_sub_field_cells([{"value": "x"}], "path")
    with pytest.raises(ValidationException):
        service._index_sub_field_cells(
            [
                {"field_id": "c1", "value": "a"},
                {"field_id": "c1", "value": "b"},
            ],
            "path",
        )


def test_partition_patch_entries_shortcut_vs_normal():
    """_partition_custom_field_patch_entries splits root vs instance_id patches."""
    service = CustomFieldService(db_connection=None)
    id_to_def = {"root-1": _defn("root-1", "root", "text")}
    normal, shortcuts = service._partition_custom_field_patch_entries(
        [
            {"field_id": "root-1", "value": "a"},
            {"instance_id": "i1", "value": "b"},
        ],
        id_to_def,
    )
    assert len(normal) == 1
    assert len(shortcuts) == 1

    with pytest.raises(ValidationException):
        service._partition_custom_field_patch_entries([{"value": "orphan"}], id_to_def)

    with pytest.raises(ValidationException):
        service._partition_custom_field_patch_entries(["not-dict"], id_to_def)


def test_find_field_cells_by_instance_id():
    """_find_field_cells_by_instance_id DFS finds nested cells."""
    roots = [
        {
            "field_id": "list-1",
            "instance_id": "root",
            "items": [
                {
                    "field_id": "item-1",
                    "instance_id": "target",
                    "value": "x",
                }
            ],
        }
    ]
    matches = CustomFieldService._find_field_cells_by_instance_id(roots, "target")
    assert len(matches) == 1
    assert matches[0]["value"] == "x"
    assert CustomFieldService._find_field_cells_by_instance_id(roots, "missing") == []


# ============================================================================
# MERGE / UPDATE PATH TESTS (extended)
# ============================================================================


@pytest.mark.asyncio
async def test_merge_root_scalar_patch(monkeypatch):
    """merge_for_update patches scalar root value."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        _field_row("f1", "Name", "name", "text"),
    ]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    stored = [_root_cell("f1", "Old")]
    patch = [{"field_id": "f1", "instance_id": stored[0]["instance_id"], "value": "New"}]
    merged = await service.merge_for_update(patch, stored, EntityType.CONTACT)
    assert merged[0]["value"] == "New"


@pytest.mark.asyncio
async def test_merge_root_object_sub_fields(monkeypatch):
    """merge_for_update merges object root sub_fields."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        _field_row("obj-1", "Profile", "profile", "object"),
        _field_row("name-1", "Name", "name", "text", parent_id="obj-1"),
    ]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    stored = [
        {
            "field_id": "obj-1",
            "instance_id": "root-iid",
            "type": "object",
            "sub_fields": [
                {
                    "field_id": "name-1",
                    "instance_id": "name-iid",
                    "type": "text",
                    "value": "Old",
                }
            ],
        }
    ]
    patch = [
        {
            "field_id": "obj-1",
            "instance_id": "root-iid",
            "sub_fields": [
                {"field_id": "name-1", "instance_id": "name-iid", "value": "New"},
            ],
        }
    ]
    merged = await service.merge_for_update(patch, stored, EntityType.CONTACT)
    subs = merged[0]["sub_fields"]
    assert subs[0]["value"] == "New"


@pytest.mark.asyncio
async def test_merge_list_add_new_row(monkeypatch):
    """merge_for_update appends new list rows without instance_id."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        _field_row("list-1", "Tags", "tags", "list"),
        _field_row("tag-1", "Tag", "tag", "text", parent_id="list-1"),
    ]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    stored = [
        {
            "field_id": "list-1",
            "instance_id": "list-iid",
            "type": "list",
            "items": [],
        }
    ]
    patch = [
        {
            "field_id": "list-1",
            "instance_id": "list-iid",
            "items": [{"field_id": "tag-1", "value": "alpha"}],
        }
    ]
    merged = await service.merge_for_update(patch, stored, EntityType.CONTACT)
    assert len(merged[0]["items"]) == 1
    assert merged[0]["items"][0]["value"] == "alpha"


@pytest.mark.asyncio
async def test_merge_required_null_raises(monkeypatch):
    """merge_for_update rejects explicit null on required root."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        _field_row("f1", "Name", "name", "text", is_required=True),
    ]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    stored = [_root_cell("f1", "Old")]
    patch = [
        {
            "field_id": "f1",
            "instance_id": stored[0]["instance_id"],
            "value": None,
        }
    ]
    with pytest.raises(ValidationException) as exc_info:
        await service.merge_for_update(patch, stored, EntityType.CONTACT)
    assert "custom_field_cannot_be_null" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_merge_patch_unknown_root_raises(monkeypatch):
    """merge_for_update rejects unknown root field_id in patch."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [_field_row("f1", "A", "a", "text")]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    with pytest.raises(ValidationException):
        await service.merge_for_update(
            [{"field_id": "unknown", "value": "x"}],
            [],
            EntityType.CONTACT,
        )


@pytest.mark.asyncio
async def test_merge_missing_instance_id_on_stored_raises(monkeypatch):
    """merge_for_update requires instance_id when patching stored root."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [_field_row("f1", "Name", "name", "text")]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    stored = [_root_cell("f1", "Old")]
    with pytest.raises(ValidationException) as exc_info:
        await service.merge_for_update(
            [{"field_id": "f1", "value": "New"}],
            stored,
            EntityType.CONTACT,
        )
    assert "patch_instance_id_required" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_merge_shortcut_instance_not_found_raises(monkeypatch):
    """merge_for_update shortcut patch raises when instance_id not in storage."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [_field_row("f1", "Name", "name", "text")]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    with pytest.raises(ValidationException) as exc_info:
        await service.merge_for_update(
            [{"instance_id": "missing-iid", "value": "x"}],
            [_root_cell("f1", "Old")],
            EntityType.CONTACT,
        )
    assert "custom_field_instance_not_found" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_merge_object_sub_null_optional_skipped(monkeypatch):
    """merge_for_update skips optional sub-field explicitly nulled."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        _field_row("obj-1", "Profile", "profile", "object"),
        _field_row("opt-1", "Nickname", "nickname", "text", parent_id="obj-1"),
    ]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    stored = [
        {
            "field_id": "obj-1",
            "instance_id": "root-iid",
            "type": "object",
            "sub_fields": [
                {
                    "field_id": "opt-1",
                    "instance_id": "opt-iid",
                    "type": "text",
                    "value": "Nick",
                }
            ],
        }
    ]
    patch = [
        {
            "field_id": "obj-1",
            "instance_id": "root-iid",
            "sub_fields": [
                {"field_id": "opt-1", "instance_id": "opt-iid", "value": None},
            ],
        }
    ]
    merged = await service.merge_for_update(patch, stored, EntityType.CONTACT)
    assert merged[0]["sub_fields"] == []


@pytest.mark.asyncio
async def test_merge_no_definitions_returns_stored(monkeypatch):
    """merge_for_update returns stored as-is when no field definitions exist."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = []
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    stored = [_root_cell("f1", "x")]
    result = await service.merge_for_update(None, stored, EntityType.CONTACT)
    assert result == stored


@pytest.mark.asyncio
async def test_reconcile_stored_custom_fields_for_write(monkeypatch):
    """reconcile_stored_custom_fields_for_write delegates to merge_for_update(None)."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [_field_row("f1", "Name", "name", "text")]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    stored = [_root_cell("f1", "hello")]
    result = await service.reconcile_stored_custom_fields_for_write(stored, EntityType.CONTACT)
    assert len(result) == 1
    assert result[0]["value"] == "hello"


# ============================================================================
# VALIDATE FOR CREATE (extended)
# ============================================================================


@pytest.mark.asyncio
async def test_validate_for_create_no_definitions_with_payload(monkeypatch):
    """validate_for_create raises when payload sent but no definitions exist."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = []
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    with pytest.raises(ValidationException) as exc_info:
        await service.validate_for_create(
            [{"field_id": "f1", "value": "x"}],
            EntityType.LEAD,
        )
    assert "custom_field_definitions_not_found" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_validate_for_create_required_explicit_null(monkeypatch):
    """validate_for_create rejects explicit null on required field."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        _field_row("f1", "Name", "name", "text", is_required=True),
    ]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    with pytest.raises(ValidationException) as exc_info:
        await service.validate_for_create(
            [{"field_id": "f1", "value": None}],
            EntityType.CONTACT,
        )
    assert "custom_field_cannot_be_null" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_validate_for_create_optional_explicit_null_skipped(monkeypatch):
    """validate_for_create skips optional fields explicitly set to null."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        _field_row("f1", "Nickname", "nickname", "text", is_required=False),
    ]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    result = await service.validate_for_create(
        [{"field_id": "f1", "value": None}],
        EntityType.CONTACT,
    )
    assert result == []


@pytest.mark.asyncio
async def test_validate_for_create_object_with_sub_fields(monkeypatch):
    """validate_for_create validates nested object sub_fields on create."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        _field_row("obj-1", "Profile", "profile", "object", is_required=True),
        _field_row("name-1", "Name", "name", "text", parent_id="obj-1", is_required=True),
    ]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    result = await service.validate_for_create(
        [
            {
                "field_id": "obj-1",
                "sub_fields": [{"field_id": "name-1", "value": "Alice"}],
            }
        ],
        EntityType.CONTACT,
    )
    assert len(result) == 1
    assert result[0]["type"] == "object"
    assert result[0]["sub_fields"][0]["value"] == "Alice"


@pytest.mark.asyncio
async def test_validate_and_format_required_presence(monkeypatch):
    """validate_and_format_custom_fields checks required presence when list empty."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        _field_row("f1", "Name", "name", "text", is_required=True),
    ]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    with pytest.raises(ValidationException):
        await service.validate_and_format_custom_fields([], EntityType.CONTACT, {})


@pytest.mark.asyncio
async def test_validate_and_format_delegates_to_create(monkeypatch):
    """validate_and_format_custom_fields delegates non-empty list to validate_for_create."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [_field_row("f1", "Name", "name", "text")]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    result = await service.validate_and_format_custom_fields(
        [{"field_id": "f1", "value": "Bob"}],
        EntityType.CONTACT,
    )
    assert result[0]["value"] == "Bob"


# ============================================================================
# RESOLVE FIELDS FOR READ (extended)
# ============================================================================


def test_resolve_object_with_children():
    """resolve_fields_for_read resolves nested object sub_fields."""
    service = CustomFieldService(db_connection=None)
    child_def = _sub_field_response(
        "child-1",
        "name",
        "text",
        "obj-1",
        field_name="Name",
    )
    root_def = _custom_field_response(
        "obj-1",
        "profile",
        "object",
        field_name="Profile",
        sub_fields=[child_def],
    )
    id_to_def = {"obj-1": root_def}
    stored = [
        {
            "field_id": "obj-1",
            "instance_id": "root-iid",
            "type": "object",
            "sub_fields": [
                {
                    "field_id": "child-1",
                    "instance_id": "child-iid",
                    "type": "text",
                    "value": "Alice",
                }
            ],
        }
    ]
    out = service.resolve_fields_for_read(stored, id_to_def)
    assert out[0]["sub_fields"][0]["value"] == "Alice"
    assert out[0]["sub_fields"][0]["label"] == "Name"


def test_resolve_list_items():
    """resolve_fields_for_read resolves list item rows."""
    service = CustomFieldService(db_connection=None)
    item_def = _sub_field_response(
        "item-1",
        "tag",
        "text",
        "list-1",
        field_name="Tag",
    )
    root_def = _custom_field_response(
        "list-1",
        "tags",
        "list",
        field_name="Tags",
        sub_fields=[item_def],
    )
    id_to_def = {"list-1": root_def}
    stored = [
        {
            "field_id": "list-1",
            "instance_id": "list-iid",
            "type": "list",
            "items": [
                {
                    "field_id": "item-1",
                    "instance_id": "row-iid",
                    "type": "text",
                    "value": "vip",
                }
            ],
        }
    ]
    out = service.resolve_fields_for_read(stored, id_to_def)
    assert out[0]["items"][0]["value"] == "vip"


def test_resolve_type_mismatch_scalar():
    """resolve_fields_for_read marks stale when stored type differs from definition."""
    service = CustomFieldService(db_connection=None)
    root_def = _custom_field_response("f1", "score", "number")
    stored = [_root_cell("f1", "not-a-number", field_type="text")]
    out = service.resolve_fields_for_read(stored, {"f1": root_def})
    assert out[0]["_stale"] is True
    assert out[0]["value"] is None


def test_resolve_type_mismatch_object_and_list():
    """resolve_fields_for_read handles object/list type mismatches."""
    service = CustomFieldService(db_connection=None)
    obj_def = _custom_field_response("obj-1", "profile", "object")
    list_def = _custom_field_response("list-1", "tags", "list", sub_fields=[])
    obj_out = service.resolve_fields_for_read(
        [{"field_id": "obj-1", "instance_id": "i1", "type": "text", "value": "x"}],
        {"obj-1": obj_def},
    )
    assert obj_out[0]["_stale"] is True
    assert obj_out[0]["sub_fields"] == []

    list_out = service.resolve_fields_for_read(
        [{"field_id": "list-1", "instance_id": "i1", "type": "text", "value": "x"}],
        {"list-1": list_def},
    )
    assert list_out[0]["_stale"] is True
    assert list_out[0]["items"] == []


def test_resolve_dropdown_stale_option():
    """resolve_fields_for_read marks dropdown value stale when option removed."""
    service = CustomFieldService(db_connection=None)
    root_def = _custom_field_response(
        "f1",
        "status",
        "dropdown",
        type_config={"options": ["open", "closed"]},
    )
    stored = [_root_cell("f1", "archived")]
    out = service.resolve_fields_for_read(stored, {"f1": root_def})
    assert out[0]["_stale"] is True
    assert out[0]["value"] is None


def test_resolve_coercion_failure_uses_default():
    """resolve_fields_for_read falls back to default_value on coercion failure."""
    service = CustomFieldService(db_connection=None)
    root_def = _custom_field_response(
        "f1",
        "score",
        "number",
        type_config={"default_value": 0},
    )
    stored = [_root_cell("f1", "bad", field_type="number")]
    out = service.resolve_fields_for_read(stored, {"f1": root_def})
    assert out[0]["_stale"] is True
    assert out[0]["value"] == 0


def test_resolve_skips_unknown_field_ids():
    """resolve_fields_for_read ignores stored cells without matching definitions."""
    service = CustomFieldService(db_connection=None)
    root_def = _custom_field_response("f1", "name", "text")
    stored = [
        _root_cell("f1", "ok"),
        _root_cell("unknown", "skip"),
    ]
    out = service.resolve_fields_for_read(stored, {"f1": root_def})
    assert len(out) == 1
    assert out[0]["field_id"] == "f1"


# ============================================================================
# TYPESENSE FACET TESTS
# ============================================================================


def test_typesense_facets_scalar_and_nested():
    """field_cells_typesense_facets collects keys/values from nested structure."""
    text_def = _custom_field_response("text-1", "label", "text", field_name="Label")
    obj_child = _sub_field_response(
        "child-1",
        "city",
        "text",
        "obj-1",
        field_name="City",
    )
    obj_def = _custom_field_response(
        "obj-1",
        "address",
        "object",
        field_name="Address",
        sub_fields=[obj_child],
    )
    list_item = _sub_field_response(
        "item-1",
        "tag",
        "text",
        "list-1",
        field_name="Tag",
    )
    list_def = _custom_field_response(
        "list-1",
        "tags",
        "list",
        field_name="Tags",
        sub_fields=[list_item],
    )
    id_to_def = {
        "text-1": text_def,
        "obj-1": obj_def,
        "list-1": list_def,
    }
    roots = [
        _root_cell("text-1", "hello"),
        {
            "field_id": "obj-1",
            "instance_id": "o1",
            "type": "object",
            "sub_fields": [
                {
                    "field_id": "child-1",
                    "instance_id": "c1",
                    "type": "text",
                    "value": "NYC",
                }
            ],
        },
        {
            "field_id": "list-1",
            "instance_id": "l1",
            "type": "list",
            "items": [
                {
                    "field_id": "item-1",
                    "instance_id": "r1",
                    "type": "text",
                    "value": "vip",
                }
            ],
        },
    ]
    keys, vals = CustomFieldService.field_cells_typesense_facets(roots, id_to_def)
    assert "label" in keys
    assert "city" in keys
    assert "tag" in keys
    assert "hello" in vals
    assert "NYC" in vals
    assert "vip" in vals


def test_collect_dropdown_ids_skips_none():
    """collect_dropdown_custom_field_ids skips None definitions in list."""
    child = types.SimpleNamespace(id="dd-1", field_type="dropdown", sub_fields=[])
    assert CustomFieldService.collect_dropdown_custom_field_ids([None, child]) == {"dd-1"}


# ============================================================================
# ENTITY-SPECIFIC / LIST API TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_get_fields_list_explicit_org_id(monkeypatch):
    """get_custom_fields_list accepts explicit organization_id override."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [_field_row("f1", "A", "a", "text")]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(org_id="ctx-org"), db_connection=None)
    fields, total = await service.get_custom_fields_list(
        EntityType.COMPANY,
        organization_id="override-org",
    )
    assert total == 1
    org_id, entity_type = fake_repo.calls["get_custom_fields_by_entity_type"]
    assert org_id == "override-org"
    assert entity_type == EntityType.COMPANY
    assert fields[0].entity_type == "contact"


@pytest.mark.asyncio
async def test_get_fields_list_lead_entity(monkeypatch):
    """get_custom_fields_list works for LEAD entity type."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        _field_row("f1", "Source", "source", "text", entity_type="lead"),
    ]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    fields, total = await service.get_custom_fields_list(EntityType.LEAD)
    assert total == 1
    assert fields[0].field_key == "source"


@pytest.mark.asyncio
async def test_get_fields_list_project_entity(monkeypatch):
    """get_custom_fields_list works for PROJECT entity type."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        _field_row("f1", "Phase", "phase", "dropdown", entity_type="project"),
    ]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    fields, total = await service.get_custom_fields_list(EntityType.PROJECT)
    assert total == 1
    assert fields[0].field_type == "dropdown"


@pytest.mark.asyncio
async def test_validate_dropdown_nested_in_object(monkeypatch):
    """validate_dropdown_filters_for_entity accepts nested dropdown field ids."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        _field_row("obj-1", "Details", "details", "object", entity_type="company"),
        _field_row(
            "dd-1",
            "Status",
            "status",
            "dropdown",
            parent_id="obj-1",
            entity_type="company",
        ),
    ]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    await service.validate_dropdown_filters_for_entity(
        EntityType.COMPANY,
        {"dd-1": ["active"]},
    )


# ============================================================================
# UPDATE TYPE-CHANGE DELETE PATHS (extended)
# ============================================================================


@pytest.mark.asyncio
async def test_update_root_list_to_text_no_children(monkeypatch):
    """update_custom_field: LIST->TEXT skips delete when root has no child definitions."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_field_result = [
        _field_row("field-1", "Root", "root", "list"),
    ]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    body = UpdateCustomFieldRequest(field_type=FieldType.TEXT)
    await service.update_custom_field("field-1", body)
    assert "bulk_delete_custom_fields_with_descendants" not in fake_repo.calls


@pytest.mark.asyncio
async def test_update_root_list_to_text_deletes_children(monkeypatch):
    """update_custom_field: LIST->TEXT deletes child field definitions."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_field_result = [
        _field_row("field-1", "Root", "root", "list"),
        _field_row("child-1", "Item", "item", "text", parent_id="field-1"),
    ]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    body = UpdateCustomFieldRequest(field_type=FieldType.TEXT)
    await service.update_custom_field("field-1", body)
    assert "bulk_delete_custom_fields_with_descendants" in fake_repo.calls
    _, deleted_ids = fake_repo.calls["bulk_delete_custom_fields_with_descendants"]
    assert "child-1" in deleted_ids


@pytest.mark.asyncio
async def test_update_child_list_to_text_no_grandchildren(monkeypatch):
    """update_custom_field: flat update LIST->TEXT skips delete when no children."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_field_result = [
        _field_row("field-1", "Root", "root", "object"),
        _field_row("child-list", "Items", "items", "list", parent_id="field-1"),
    ]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    body = UpdateCustomFieldRequest(
        update=[FlatFieldUpdateRequest(id="child-list", field_type=FieldType.TEXT)],
    )
    await service.update_custom_field("field-1", body)
    assert fake_repo.calls.get("bulk_delete_custom_fields_with_descendants") is None


@pytest.mark.asyncio
async def test_merge_duplicate_list_instance_id_raises(monkeypatch):
    """merge_for_update rejects duplicate instance_id within list items."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        _field_row("list-1", "Tags", "tags", "list"),
        _field_row("tag-1", "Tag", "tag", "text", parent_id="list-1"),
    ]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    dup_iid = "10000000-0000-4000-8000-000000000099"
    patch = [
        {
            "field_id": "list-1",
            "items": [
                {"field_id": "tag-1", "instance_id": dup_iid, "value": "a"},
                {"field_id": "tag-1", "instance_id": dup_iid, "value": "b"},
            ],
        }
    ]
    with pytest.raises(ValidationException) as exc_info:
        await service.merge_for_update(patch, [], EntityType.CONTACT)
    assert "duplicate_instance_id" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_merge_shortcut_ambiguous_instance_raises(monkeypatch):
    """merge_for_update rejects shortcut patch when instance_id matches multiple cells."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        _field_row("list-1", "Tags", "tags", "list"),
        _field_row("tag-1", "Tag", "tag", "text", parent_id="list-1"),
    ]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    dup_iid = "dup-iid"
    stored = [
        {
            "field_id": "list-1",
            "instance_id": "root",
            "type": "list",
            "items": [
                {"field_id": "tag-1", "instance_id": dup_iid, "type": "text", "value": "a"},
                {"field_id": "tag-1", "instance_id": dup_iid, "type": "text", "value": "b"},
            ],
        }
    ]
    with pytest.raises(ValidationException) as exc_info:
        await service.merge_for_update(
            [{"instance_id": dup_iid, "value": "x"}],
            stored,
            EntityType.CONTACT,
        )
    assert "custom_field_instance_ambiguous" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_merge_shortcut_field_id_mismatch_raises(monkeypatch):
    """merge_for_update rejects shortcut when field_id does not match stored cell."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [_field_row("f1", "Name", "name", "text")]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    stored = [_root_cell("f1", "Old", instance_id="target-iid")]
    with pytest.raises(ValidationException) as exc_info:
        await service.merge_for_update(
            [{"instance_id": "target-iid", "field_id": "wrong-id", "value": "New"}],
            stored,
            EntityType.CONTACT,
        )
    assert "custom_field_instance_field_mismatch" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_merge_new_root_without_stored(monkeypatch):
    """merge_for_update creates new root cell when no stored value exists."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        _field_row("f1", "Name", "name", "text", is_required=False),
    ]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    merged = await service.merge_for_update(
        [{"field_id": "f1", "value": "Brand New"}],
        [],
        EntityType.CONTACT,
    )
    assert len(merged) == 1
    assert merged[0]["value"] == "Brand New"
    assert merged[0]["instance_id"]


@pytest.mark.asyncio
async def test_merge_object_unknown_sub_field_raises(monkeypatch):
    """merge_for_update rejects unknown sub_field ids in object patch."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        _field_row("obj-1", "Profile", "profile", "object"),
        _field_row("name-1", "Name", "name", "text", parent_id="obj-1"),
    ]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    patch = [
        {
            "field_id": "obj-1",
            "sub_fields": [{"field_id": "unknown-child", "value": "x"}],
        }
    ]
    with pytest.raises(ValidationException) as exc_info:
        await service.merge_for_update(patch, [], EntityType.CONTACT)
    assert "custom_field_unknown_keys" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_merge_optional_root_omitted_keeps_stored(monkeypatch):
    """merge_for_update carries forward optional stored root omitted from patch."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        _field_row("f1", "Nickname", "nickname", "text", is_required=False),
    ]
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    stored = [_root_cell("f1", "Nick")]
    merged = await service.merge_for_update([], stored, EntityType.CONTACT)
    assert merged[0]["value"] == "Nick"


@pytest.mark.asyncio
async def test_validate_for_create_empty_no_definitions(monkeypatch):
    """validate_for_create returns [] when no definitions and empty payload."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = []
    _patch_repo(monkeypatch, fake_repo)
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    assert await service.validate_for_create([], EntityType.PROJECT) == []


def test_coerce_image_field_single_file():
    """_coerce_field_value validates image field like file_upload."""
    service = CustomFieldService(db_connection=None)
    field_def = _defn(
        "f1",
        "photo",
        "image",
        type_config={"allow_multiple": False, "max_files": 1},
    )
    assert service._coerce_field_value("photo", "pic.png", field_def) == "pic.png"


def test_coerce_currency_rejects_non_dict():
    """_coerce_field_value rejects non-dict currency values."""
    service = CustomFieldService(db_connection=None)
    field_def = _defn(
        "f1",
        "price",
        "currency",
        type_config={"allowed_currencies": ["USD"]},
    )
    with pytest.raises(ValidationException):
        service._coerce_field_value("price", "100 USD", field_def)


def test_coerce_file_upload_rejects_non_list_when_multiple():
    """_coerce_field_value rejects non-list when allow_multiple is true."""
    service = CustomFieldService(db_connection=None)
    field_def = _defn(
        "f1",
        "docs",
        "file_upload",
        type_config={"allow_multiple": True, "max_files": 3},
    )
    with pytest.raises(ValidationException):
        service._coerce_field_value("docs", "single.pdf", field_def)


def test_coerce_address_rejects_non_object():
    """_coerce_field_value rejects non-dict address values."""
    service = CustomFieldService(db_connection=None)
    field_def = _defn("f1", "addr", "address")
    with pytest.raises(ValidationException):
        service._coerce_field_value("addr", "123 Main", field_def)


def test_coerce_url_rejects_non_string():
    """_coerce_field_value rejects non-string URL values."""
    service = CustomFieldService(db_connection=None)
    field_def = _defn("f1", "site", "url")
    with pytest.raises(ValidationException):
        service._coerce_field_value("site", 123, field_def)


def test_coerce_dropdown_rejects_non_string():
    """_coerce_field_value rejects non-string dropdown values."""
    service = CustomFieldService(db_connection=None)
    field_def = _defn(
        "f1",
        "status",
        "dropdown",
        type_config={"options": ["a"]},
    )
    with pytest.raises(ValidationException):
        service._coerce_field_value("status", 1, field_def)


def test_parse_roots_create_rejects_invalid_sub_fields_type():
    """_parse_roots_create_payload rejects non-list sub_fields on create."""
    service = CustomFieldService(db_connection=None)
    with pytest.raises(ValidationException):
        service._parse_roots_create_payload([{"field_id": "obj-1", "sub_fields": "not-a-list"}])


def test_parse_roots_create_rejects_invalid_items_type():
    """_parse_roots_create_payload rejects non-list items on create."""
    service = CustomFieldService(db_connection=None)
    with pytest.raises(ValidationException):
        service._parse_roots_create_payload([{"field_id": "list-1", "items": "not-a-list"}])


def test_enforce_patch_no_type_key_nested():
    """_enforce_patch_no_type_key recurses into nested sub_fields/items."""
    service = CustomFieldService(db_connection=None)
    with pytest.raises(ValidationException):
        service._enforce_patch_no_type_key(
            {
                "field_id": "obj-1",
                "sub_fields": [{"field_id": "c1", "type": "text", "value": "x"}],
            },
            "root",
        )


@pytest.mark.asyncio
async def test_delete_descendants_if_root_type_change(monkeypatch):
    """Root type change from object to text deletes descendants."""
    deleted: list[tuple] = []

    class _Repo:
        async def bulk_delete_custom_fields_with_descendants(self, organization_id, field_ids):
            deleted.append((organization_id, field_ids))

    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    service.custom_field_repository = _Repo()
    subtree = [
        {"id": "root-1", "field_type": "object"},
        {"id": "child-1", "field_type": "text", "parent_id": "root-1"},
    ]
    direct_children = {"root-1": ["child-1"]}
    await service._delete_descendants_if_root_type_change(
        subtree,
        "root-1",
        FieldType.TEXT.value,
        direct_children,
        "org-1",
    )
    assert deleted == [("org-1", ["child-1"])]


def test_merge_list_row_cell_value_branch():
    """_merge_list_row_cell accepts scalar value patches for list item child."""
    child_def = _custom_field_response("child-1", "name", "text")
    service = CustomFieldService(db_connection=None)
    row = service._merge_list_row_cell(
        child_def,
        stored_row=None,
        patch_row={"field_id": "child-1", "instance_id": "i1", "value": "Jane"},
        path_key="items[0]",
    )
    assert row["value"] == "Jane"


def test_merge_list_row_cell_items_branch():
    """_merge_list_row_cell merges nested list items for list child definitions."""
    child_def = _custom_field_response("child-1", "tags", "list")
    child_def.sub_fields = [_custom_field_response("leaf-1", "tag", "text")]
    service = CustomFieldService(db_connection=None)
    row = service._merge_list_row_cell(
        child_def,
        stored_row=None,
        patch_row={
            "field_id": "child-1",
            "instance_id": "i1",
            "items": [{"field_id": "leaf-1", "instance_id": "i2", "value": "vip"}],
        },
        path_key="items[0]",
    )
    assert row["items"][0]["value"] == "vip"


def test_merge_list_row_cell_object_branch():
    """_merge_list_row_cell merges object sub_fields for object child definitions."""
    child_def = _custom_field_response("child-1", "profile", "object")
    child_def.sub_fields = [_custom_field_response("leaf-1", "city", "text")]
    service = CustomFieldService(db_connection=None)
    row = service._merge_list_row_cell(
        child_def,
        stored_row=None,
        patch_row={
            "field_id": "child-1",
            "instance_id": "i1",
            "sub_fields": [{"field_id": "leaf-1", "instance_id": "i2", "value": "Austin"}],
        },
        path_key="items[0]",
    )
    assert row["sub_fields"][0]["value"] == "Austin"


@pytest.mark.asyncio
async def test_delete_descendants_for_object_to_non_object(monkeypatch):
    """Nested update items changing object to text delete descendants."""
    deleted: list[tuple] = []

    class _Repo:
        async def bulk_delete_custom_fields_with_descendants(self, organization_id, field_ids):
            deleted.append((organization_id, field_ids))

    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    service.custom_field_repository = _Repo()
    await service._delete_descendants_for_object_to_non_object(
        [FlatFieldUpdateRequest(id="obj-1", field_type=FieldType.TEXT)],
        {"obj-1": FieldType.OBJECT.value},
        {"obj-1": ["child-1"]},
        "org-1",
    )
    assert deleted == [("org-1", ["child-1"])]


def test_merge_child_cell_object_rejects_value_discriminator():
    """_merge_child_cell_object rejects scalar value patches."""
    sub_def = _custom_field_response("obj-1", "profile", "object")
    service = CustomFieldService(db_connection=None)
    with pytest.raises(ValidationException):
        service._merge_child_cell_object(
            sub_def,
            stored_d=None,
            patch={"value": "x"},
            path_key="profile",
            fid="obj-1",
            iid="i1",
            which="value",
        )


def test_merge_child_cell_object_keeps_stored_sub_fields():
    """_merge_child_cell_object preserves stored sub_fields when patch omits them."""
    sub_def = _custom_field_response("obj-1", "profile", "object")
    sub_def.sub_fields = [_custom_field_response("leaf-1", "city", "text")]
    service = CustomFieldService(db_connection=None)
    stored = {
        "field_id": "obj-1",
        "instance_id": "i1",
        "sub_fields": [{"field_id": "leaf-1", "instance_id": "i2", "value": "Austin"}],
    }
    row = service._merge_child_cell_object(
        sub_def,
        stored,
        {"field_id": "obj-1", "instance_id": "i1"},
        "profile",
        "obj-1",
        "i1",
        "sub_fields",
    )
    assert row["sub_fields"][0]["value"] == "Austin"


def test_merge_child_cell_list_keeps_stored_items():
    """_merge_child_cell_list preserves stored items when patch omits them."""
    sub_def = _custom_field_response("list-1", "tags", "list")
    sub_def.sub_fields = [_custom_field_response("leaf-1", "tag", "text")]
    service = CustomFieldService(db_connection=None)
    stored = {
        "field_id": "list-1",
        "instance_id": "i1",
        "items": [{"field_id": "leaf-1", "instance_id": "i2", "value": "vip"}],
    }
    row = service._merge_child_cell_list(
        sub_def,
        stored,
        {"field_id": "list-1", "instance_id": "i1"},
        "tags",
        "list-1",
        "i1",
        "items",
    )
    assert row["items"][0]["value"] == "vip"


def test_merge_child_cell_rejects_non_dict_patch():
    """_merge_child_cell rejects non-object patch payloads."""
    sub_def = _custom_field_response("f1", "name", "text")
    service = CustomFieldService(db_connection=None)
    with pytest.raises(ValidationException):
        service._merge_child_cell(sub_def, None, "bad", "name")


def test_merge_child_cell_scalar_keeps_stored_value():
    """_merge_child_cell_scalar preserves stored value when patch omits it."""
    _custom_field_response("f1", "name", "text")
    service = CustomFieldService(db_connection=None)
    row = service._merge_child_cell_scalar(
        {"value": "stored"},
        {"field_id": "f1", "instance_id": "i1"},
        "name",
        "f1",
        "i1",
        "value",
    )
    assert row["value"] == "stored"


def test_merge_child_cell_dispatches_scalar_object_and_list():
    """_merge_child_cell routes to scalar, object, and list merge helpers."""
    service = CustomFieldService(db_connection=None)
    text_def = _custom_field_response("t1", "name", "text")
    scalar = service._merge_child_cell(text_def, None, {"value": "Ann"}, "name")
    assert scalar["value"] == "Ann"

    obj_def = _custom_field_response("o1", "profile", "object")
    obj_def.sub_fields = [_custom_field_response("leaf", "city", "text")]
    obj = service._merge_child_cell(
        obj_def,
        None,
        {
            "sub_fields": [{"field_id": "leaf", "instance_id": "i2", "value": "Austin"}],
        },
        "profile",
    )
    assert obj["sub_fields"][0]["value"] == "Austin"

    list_def = _custom_field_response("l1", "tags", "list")
    list_def.sub_fields = [_custom_field_response("tag", "tag", "text")]
    lst = service._merge_child_cell(
        list_def,
        None,
        {
            "items": [{"field_id": "tag", "instance_id": "i3", "value": "vip"}],
        },
        "tags",
    )
    assert lst["items"][0]["value"] == "vip"


def test_merge_child_cell_generates_instance_id():
    """_merge_child_cell assigns instance_id when patch and stored omit it."""
    sub_def = _custom_field_response("f1", "name", "text")
    service = CustomFieldService(db_connection=None)
    row = service._merge_child_cell(sub_def, None, {"value": "x"}, "name")
    assert row["instance_id"]


def test_merge_list_row_cell_rejects_sub_fields_on_scalar():
    """_merge_list_row_cell rejects sub_fields discriminator on non-object child."""
    child_def = _custom_field_response("child-1", "name", "text")
    service = CustomFieldService(db_connection=None)
    with pytest.raises(ValidationException):
        service._merge_list_row_cell(
            child_def,
            stored_row=None,
            patch_row={
                "field_id": "child-1",
                "instance_id": "i1",
                "sub_fields": [],
            },
            path_key="items[0]",
        )


def test_merge_list_row_cell_rejects_items_on_non_list():
    """_merge_list_row_cell rejects items discriminator on non-list child."""
    child_def = _custom_field_response("child-1", "name", "text")
    service = CustomFieldService(db_connection=None)
    with pytest.raises(ValidationException):
        service._merge_list_row_cell(
            child_def,
            stored_row=None,
            patch_row={
                "field_id": "child-1",
                "instance_id": "i1",
                "items": [],
            },
            path_key="items[0]",
        )


def test_merge_root_cell_new_patch_assigns_ids():
    """_merge_root_cell on first write fills field_id and instance_id."""
    field_def = _custom_field_response("root-1", "notes", "text")
    service = CustomFieldService(db_connection=None)
    row = service._merge_root_cell(field_def, None, {"value": "hello"})
    assert row["field_id"] == "root-1"
    assert row["instance_id"]
    assert row["value"] == "hello"


def test_merge_root_cell_scalar_keeps_stored_value():
    """_merge_root_cell_scalar preserves stored value when patch omits it."""
    field_def = _custom_field_response("root-1", "notes", "text")
    service = CustomFieldService(db_connection=None)
    stored = _root_cell("root-1", "stored")
    out = {"field_id": "root-1", "instance_id": stored["instance_id"]}
    row = service._merge_root_cell_scalar(
        field_def,
        stored,
        {"field_id": "root-1", "instance_id": stored["instance_id"]},
        out,
        "value",
    )
    assert row["value"] == "stored"


def test_merge_root_cell_object_keeps_stored_sub_fields():
    """_merge_root_cell_object preserves stored sub_fields when patch omits them."""
    field_def = _custom_field_response("root-1", "profile", "object")
    field_def.sub_fields = [_custom_field_response("leaf", "city", "text")]
    service = CustomFieldService(db_connection=None)
    stored = {
        "field_id": "root-1",
        "instance_id": "i1",
        "sub_fields": [{"field_id": "leaf", "instance_id": "i2", "value": "Austin"}],
    }
    out = {"field_id": "root-1", "instance_id": "i1"}
    row = service._merge_root_cell_object(
        field_def, stored, {"field_id": "root-1", "instance_id": "i1"}, out, "sub_fields"
    )
    assert row["sub_fields"][0]["value"] == "Austin"


def test_merge_root_cell_list_keeps_stored_items():
    """_merge_root_cell_list preserves stored items when patch omits them."""
    field_def = _custom_field_response("root-1", "tags", "list")
    field_def.sub_fields = [_custom_field_response("tag", "tag", "text")]
    service = CustomFieldService(db_connection=None)
    stored = {
        "field_id": "root-1",
        "instance_id": "i1",
        "items": [{"field_id": "tag", "instance_id": "i2", "value": "vip"}],
    }
    out = {"field_id": "root-1", "instance_id": "i1"}
    row = service._merge_root_cell_list(
        field_def, stored, {"field_id": "root-1", "instance_id": "i1"}, out, "items"
    )
    assert row["items"][0]["value"] == "vip"


def test_merge_root_cell_scalar_rejects_wrong_discriminator():
    """_merge_root_cell_scalar rejects non-value discriminators."""
    field_def = _custom_field_response("root-1", "notes", "text")
    service = CustomFieldService(db_connection=None)
    stored = _root_cell("root-1", "x")
    with pytest.raises(ValidationException):
        service._merge_root_cell(
            field_def,
            stored,
            {"field_id": "root-1", "instance_id": stored["instance_id"], "items": []},
        )


@pytest.mark.asyncio
async def test_delete_descendants_if_root_type_change_stays_object():
    """Root type change object->object skips descendant deletion."""
    deleted: list[tuple] = []

    class _Repo:
        async def bulk_delete_custom_fields_with_descendants(self, organization_id, field_ids):
            deleted.append((organization_id, field_ids))

    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    service.custom_field_repository = _Repo()
    subtree = [
        {"id": "root-1", "field_type": "object"},
        {"id": "child-1", "field_type": "text", "parent_id": "root-1"},
    ]
    await service._delete_descendants_if_root_type_change(
        subtree,
        "root-1",
        FieldType.OBJECT.value,
        {"root-1": ["child-1"]},
        "org-1",
    )
    assert deleted == []


@pytest.mark.asyncio
async def test_delete_descendants_for_object_to_non_object_skips_list_target():
    """Nested update keeping LIST type skips descendant deletion."""
    deleted: list[tuple] = []

    class _Repo:
        async def bulk_delete_custom_fields_with_descendants(self, organization_id, field_ids):
            deleted.append((organization_id, field_ids))

    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    service.custom_field_repository = _Repo()
    await service._delete_descendants_for_object_to_non_object(
        [FlatFieldUpdateRequest(id="list-1", field_type=FieldType.LIST)],
        {"list-1": FieldType.LIST.value},
        {"list-1": ["child-1"]},
        "org-1",
    )
    assert deleted == []


def test_field_cell_resolve_identity_rejects_non_dict():
    """_field_cell_resolve_identity rejects non-object cells."""
    field_def = _custom_field_response("f1", "name", "text")
    service = CustomFieldService(db_connection=None)
    with pytest.raises(ValidationException):
        service._field_cell_resolve_identity(field_def, "bad", "name", False, "root")


def test_field_cell_resolve_identity_optional_reconcile_nulls():
    """Optional reconcile returns nulled cell for invalid non-dict input."""
    field_def = _custom_field_response("f1", "name", "text", is_required=False)
    service = CustomFieldService(db_connection=None)
    result = service._field_cell_resolve_identity(field_def, "bad", "name", True, "root")
    assert isinstance(result, dict)
