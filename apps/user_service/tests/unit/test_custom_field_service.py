"""Unit tests for CustomFieldService business logic."""

import pytest

from apps.user_service.app.schemas.custom_fields import (
    CreateCustomFieldRequest,
    FlatFieldUpdateRequest,
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
async def test_root_object_to_non_object_deletes_descendants(monkeypatch):
    """Test update_custom_field when root changes OBJECT->non-OBJECT
    deletes descendants (606-613)."""
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
async def test_child_object_to_non_object_deletes_descendants(monkeypatch):
    """Test update_custom_field when a child changes
    OBJECT->non-OBJECT deletes its descendants (630-636)."""
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
