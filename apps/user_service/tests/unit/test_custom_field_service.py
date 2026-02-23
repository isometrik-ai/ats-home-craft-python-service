"""Unit tests for CustomFieldService business logic."""

# pylint: disable=too-many-lines
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
# VALIDATE AND FORMAT CUSTOM FIELDS TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_validate_and_format_custom_fields_empty(monkeypatch):
    """Test validate_and_format_custom_fields with empty dict."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = []
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)

    result = await service.validate_and_format_custom_fields({}, EntityType.CONTACT)

    assert result == {}


@pytest.mark.asyncio
async def test_validate_format_cf_no_definitions(monkeypatch):
    """validate_and_format raises when no definitions found."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = []
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)

    with pytest.raises(ValidationException) as exc_info:
        await service.validate_and_format_custom_fields({"field1": "value"}, EntityType.CONTACT)
    assert "custom_field_definitions_not_found" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_validate_format_cf_undefined_field(monkeypatch):
    """validate_and_format raises when field not defined."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Defined Field",
            "field_key": "defined_field",
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

    with pytest.raises(ValidationException) as exc_info:
        await service.validate_and_format_custom_fields(
            {"undefined_field": "value"}, EntityType.CONTACT
        )
    assert "custom_field_not_defined" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_validate_and_format_custom_fields_text(monkeypatch):
    """Test validate_and_format_custom_fields validates text field."""
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

    result = await service.validate_and_format_custom_fields(
        {"name": "John Doe"}, EntityType.CONTACT
    )

    assert result == {"name": "John Doe"}


@pytest.mark.asyncio
async def test_validate_and_format_custom_fields_number(monkeypatch):
    """Test validate_and_format_custom_fields validates number field."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
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
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)

    result = await service.validate_and_format_custom_fields({"age": 25}, EntityType.CONTACT)

    assert result == {"age": 25.0}


@pytest.mark.asyncio
async def test_validate_format_cf_required_missing(monkeypatch):
    """validate_and_format raises when required field missing."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
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
        },
        {
            "id": "field-2",
            "field_name": "Optional Field",
            "field_key": "optional_field",
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
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    # Pass only optional field so required_field is missing; service raises.
    with pytest.raises(ValidationException) as exc_info:
        await service.validate_and_format_custom_fields(
            {"optional_field": "value"}, EntityType.CONTACT
        )
    assert "custom_field_required" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_validate_and_format_custom_fields_list(monkeypatch):
    """Test validate_and_format_custom_fields validates list field."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
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
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)

    result = await service.validate_and_format_custom_fields(
        {"tags": ["tag1", "tag2", "tag3"]}, EntityType.CONTACT
    )

    assert result == {"tags": ["tag1", "tag2", "tag3"]}


@pytest.mark.asyncio
async def test_validate_format_cf_list_invalid_type(monkeypatch):
    """validate_and_format raises when list field has invalid type."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
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
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)

    with pytest.raises(ValidationException) as exc_info:
        await service.validate_and_format_custom_fields({"tags": "not a list"}, EntityType.CONTACT)
    assert "custom_field_invalid_type" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_validate_and_format_custom_fields_object(monkeypatch):
    """Test validate_and_format_custom_fields validates object field."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Address",
            "field_key": "address",
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
            "id": "field-2",
            "field_name": "Street",
            "field_key": "street",
            "field_type": "text",
            "parent_id": "field-1",
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": True,
            "type_config": {},
            "sort_order": 0,
            "is_active": True,
        },
        {
            "id": "field-3",
            "field_name": "City",
            "field_key": "city",
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

    result = await service.validate_and_format_custom_fields(
        {"address": {"street": "123 Main St", "city": "New York"}}, EntityType.CONTACT
    )

    assert result == {"address": {"street": "123 Main St", "city": "New York"}}


@pytest.mark.asyncio
async def test_validate_format_cf_object_required_subfield(monkeypatch):
    """Test validate_and_format_custom_fields raises when required sub-field missing."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Address",
            "field_key": "address",
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
            "id": "field-2",
            "field_name": "Street",
            "field_key": "street",
            "field_type": "text",
            "parent_id": "field-1",
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
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

    with pytest.raises(ValidationException) as exc_info:
        await service.validate_and_format_custom_fields(
            {"address": {"city": "New York"}}, EntityType.CONTACT
        )
    assert "custom_field_required" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_validate_and_format_custom_fields_dropdown(monkeypatch):
    """Test validate_and_format_custom_fields validates dropdown field."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Status",
            "field_key": "status",
            "field_type": "dropdown",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {"options": ["active", "inactive", "pending"]},
            "sort_order": 0,
            "is_active": True,
        }
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)

    result = await service.validate_and_format_custom_fields(
        {"status": "active"}, EntityType.CONTACT
    )

    assert result == {"status": "active"}


@pytest.mark.asyncio
async def test_validate_format_cf_dropdown_invalid_option(monkeypatch):
    """Test validate_and_format_custom_fields raises when dropdown option invalid."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Status",
            "field_key": "status",
            "field_type": "dropdown",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {"options": ["active", "inactive"]},
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
        await service.validate_and_format_custom_fields({"status": "invalid"}, EntityType.CONTACT)
    assert "custom_field_invalid_option" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_validate_and_format_custom_fields_currency(monkeypatch):
    """Test validate_and_format_custom_fields validates currency field."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Price",
            "field_key": "price",
            "field_type": "currency",
            "parent_id": None,
            "entity_type": "company",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {"allowed_currencies": ["USD", "EUR"]},
            "sort_order": 0,
            "is_active": True,
        }
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)

    result = await service.validate_and_format_custom_fields(
        {"price": {"amount": 100.50, "currency_code": "USD"}}, EntityType.COMPANY
    )

    assert result == {"price": {"amount": 100.5, "currency_code": "USD"}}


@pytest.mark.asyncio
async def test_validate_format_cf_currency_invalid_format(monkeypatch):
    """Test validate_and_format_custom_fields raises when currency format invalid."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Price",
            "field_key": "price",
            "field_type": "currency",
            "parent_id": None,
            "entity_type": "company",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {"allowed_currencies": ["USD", "EUR"]},
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
        await service.validate_and_format_custom_fields({"price": "invalid"}, EntityType.COMPANY)
    assert "custom_field_invalid_currency_format" in str(exc_info.value.message_key)


# --- validate_and_format: string invalid type ---
@pytest.mark.asyncio
async def test_validate_format_cf_text_invalid_type(monkeypatch):
    """validate_and_format raises when text field value is not string."""
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
    with pytest.raises(ValidationException) as exc_info:
        await service.validate_and_format_custom_fields({"name": 123}, EntityType.CONTACT)
    assert "custom_field_invalid_type" in str(exc_info.value.message_key)


# --- validate_and_format: number invalid type / coerce ---
@pytest.mark.asyncio
async def test_validate_format_cf_number_invalid_type(monkeypatch):
    """validate_and_format raises when number value cannot coerce to float."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
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
        },
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    with pytest.raises(ValidationException) as exc_info:
        await service.validate_and_format_custom_fields({"age": "not_a_number"}, EntityType.CONTACT)
    assert "custom_field_invalid_type" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_validate_format_cf_yes_no_string_and_int(monkeypatch):
    """validate_and_format yes_no accepts string true/yes/1 and int."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Flag",
            "field_key": "flag",
            "field_type": "yes_no",
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
    result = await service.validate_and_format_custom_fields({"flag": "yes"}, EntityType.CONTACT)
    assert result["flag"] is True
    result2 = await service.validate_and_format_custom_fields({"flag": 1}, EntityType.CONTACT)
    assert result2["flag"] is True


@pytest.mark.asyncio
async def test_validate_format_cf_yes_no_invalid_type(monkeypatch):
    """validate_and_format raises when yes_no value is invalid type."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Flag",
            "field_key": "flag",
            "field_type": "yes_no",
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
    with pytest.raises(ValidationException) as exc_info:
        await service.validate_and_format_custom_fields({"flag": ["invalid"]}, EntityType.CONTACT)
    assert "custom_field_invalid_type" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_validate_format_cf_url_valid(monkeypatch):
    """validate_and_format url accepts https URL."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Link",
            "field_key": "link",
            "field_type": "url",
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
    result = await service.validate_and_format_custom_fields(
        {"link": "https://example.com"}, EntityType.CONTACT
    )
    assert result["link"] == "https://example.com"


@pytest.mark.asyncio
async def test_validate_format_cf_url_invalid(monkeypatch):
    """validate_and_format raises when url value is not http(s)."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Link",
            "field_key": "link",
            "field_type": "url",
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
    with pytest.raises(ValidationException) as exc_info:
        await service.validate_and_format_custom_fields(
            {"link": "ftp://bad.com"}, EntityType.CONTACT
        )
    assert "custom_field_invalid_url" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_validate_format_cf_range_slider_valid(monkeypatch):
    """validate_and_format range_slider accepts value in range."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Score",
            "field_key": "score",
            "field_type": "range_slider",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {"min": 0, "max": 100},
            "sort_order": 0,
            "is_active": True,
        },
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    result = await service.validate_and_format_custom_fields({"score": 50}, EntityType.CONTACT)
    assert result["score"] == 50.0


@pytest.mark.asyncio
async def test_validate_format_cf_range_slider_out_of_range(monkeypatch):
    """validate_and_format raises when range_slider value out of range."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Score",
            "field_key": "score",
            "field_type": "range_slider",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {"min": 0, "max": 100},
            "sort_order": 0,
            "is_active": True,
        },
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    with pytest.raises(ValidationException) as exc_info:
        await service.validate_and_format_custom_fields({"score": 150}, EntityType.CONTACT)
    assert "custom_field_out_of_range" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_validate_format_cf_currency_invalid_currency(monkeypatch):
    """validate_and_format raises when currency not in allowed_currencies."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Price",
            "field_key": "price",
            "field_type": "currency",
            "parent_id": None,
            "entity_type": "company",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {"allowed_currencies": ["USD", "EUR"]},
            "sort_order": 0,
            "is_active": True,
        },
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    with pytest.raises(ValidationException) as exc_info:
        await service.validate_and_format_custom_fields(
            {"price": {"amount": 10, "currency_code": "GBP"}}, EntityType.COMPANY
        )
    assert "custom_field_invalid_currency" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_validate_format_cf_currency_missing_keys(monkeypatch):
    """validate_and_format raises when currency missing amount or currency_code."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Price",
            "field_key": "price",
            "field_type": "currency",
            "parent_id": None,
            "entity_type": "company",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {"allowed_currencies": ["USD"]},
            "sort_order": 0,
            "is_active": True,
        },
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    with pytest.raises(ValidationException) as exc_info:
        await service.validate_and_format_custom_fields(
            {"price": {"amount": 10}}, EntityType.COMPANY
        )
    assert "custom_field_invalid_currency_format" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_validate_cf_file_upload_too_many(monkeypatch):
    """validate_and_format raises when file_upload allow_multiple and too many."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Files",
            "field_key": "files",
            "field_type": "file_upload",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {"allow_multiple": True, "max_files": 2},
            "sort_order": 0,
            "is_active": True,
        },
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    with pytest.raises(ValidationException) as exc_info:
        await service.validate_and_format_custom_fields(
            {"files": ["a", "b", "c"]}, EntityType.CONTACT
        )
    assert "custom_field_too_many_files" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_validate_cf_file_upload_single_two(monkeypatch):
    """validate_and_format raises when file_upload single but list has >1."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "File",
            "field_key": "file",
            "field_type": "file_upload",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {"allow_multiple": False, "max_files": 1},
            "sort_order": 0,
            "is_active": True,
        },
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    with pytest.raises(ValidationException) as exc_info:
        await service.validate_and_format_custom_fields({"file": ["a", "b"]}, EntityType.CONTACT)
    assert "custom_field_too_many_files" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_validate_format_cf_image_multiple_and_single(monkeypatch):
    """validate_and_format image allow_multiple and single paths."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Pics",
            "field_key": "pics",
            "field_type": "image",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {"allow_multiple": True, "max_files": 2},
            "sort_order": 0,
            "is_active": True,
        },
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    result = await service.validate_and_format_custom_fields(
        {"pics": ["url1", "url2"]}, EntityType.CONTACT
    )
    assert result["pics"] == ["url1", "url2"]


@pytest.mark.asyncio
async def test_validate_format_cf_image_single_empty_list(monkeypatch):
    """validate_and_format image single returns first item or None from list."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Pic",
            "field_key": "pic",
            "field_type": "image",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {"allow_multiple": False, "max_files": 1},
            "sort_order": 0,
            "is_active": True,
        },
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    result = await service.validate_and_format_custom_fields({"pic": []}, EntityType.CONTACT)
    assert result["pic"] is None


@pytest.mark.asyncio
async def test_validate_format_cf_address_with_lat_long(monkeypatch):
    """validate_and_format address with include_lat_long."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Location",
            "field_key": "location",
            "field_type": "address",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {"include_lat_long": True},
            "sort_order": 0,
            "is_active": True,
        },
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    val = {
        "address_line1": "123 Main",
        "city": "NYC",
        "latitude": 40.7,
        "longitude": -74.0,
    }
    result = await service.validate_and_format_custom_fields({"location": val}, EntityType.CONTACT)
    assert result["location"]["latitude"] == 40.7
    assert result["location"]["longitude"] == -74.0


@pytest.mark.asyncio
async def test_validate_format_cf_address_invalid_type(monkeypatch):
    """validate_and_format raises when address value is not dict."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Location",
            "field_key": "location",
            "field_type": "address",
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
    with pytest.raises(ValidationException) as exc_info:
        await service.validate_and_format_custom_fields(
            {"location": "not a dict"}, EntityType.CONTACT
        )
    assert "custom_field_invalid_type" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_validate_format_cf_object_invalid_type(monkeypatch):
    """validate_and_format raises when object value is not dict."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Data",
            "field_key": "data",
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
    with pytest.raises(ValidationException) as exc_info:
        await service.validate_and_format_custom_fields(
            {"data": "not an object"}, EntityType.CONTACT
        )
    assert "custom_field_invalid_type" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_validate_format_cf_required_null_value(monkeypatch):
    """validate_and_format raises when required field has None value."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Required",
            "field_key": "required",
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
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    with pytest.raises(ValidationException) as exc_info:
        await service.validate_and_format_custom_fields({"required": None}, EntityType.CONTACT)
    assert "custom_field_required" in str(exc_info.value.message_key)


@pytest.mark.asyncio
async def test_validate_format_cf_optional_null_value(monkeypatch):
    """validate_and_format allows None for optional field."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Opt",
            "field_key": "opt",
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
    result = await service.validate_and_format_custom_fields({"opt": None}, EntityType.CONTACT)
    assert result["opt"] is None


@pytest.mark.asyncio
async def test_validate_format_cf_file_upload_invalid_type(monkeypatch):
    """validate_and_format raises when file_upload allow_multiple but value not list."""
    fake_repo = _FakeCustomFieldRepo()
    fake_repo.get_fields_result = [
        {
            "id": "field-1",
            "field_name": "Files",
            "field_key": "files",
            "field_type": "file_upload",
            "parent_id": None,
            "entity_type": "contact",
            "show_on_create": True,
            "show_on_detail": False,
            "is_required": False,
            "type_config": {"allow_multiple": True, "max_files": 2},
            "sort_order": 0,
            "is_active": True,
        },
    ]
    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: fake_repo,
    )
    service = CustomFieldService(user_context=_ctx(), db_connection=None)
    with pytest.raises(ValidationException) as exc_info:
        await service.validate_and_format_custom_fields({"files": "not a list"}, EntityType.CONTACT)
    assert "custom_field_invalid_type" in str(exc_info.value.message_key)
