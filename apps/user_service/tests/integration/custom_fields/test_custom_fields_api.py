"""Integration tests for custom fields API endpoints."""

import pytest

from apps.user_service.app.schemas.custom_fields import CustomFieldResponse
from apps.user_service.app.schemas.enums import EntityType, FieldType
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
async def test_create_custom_field(monkeypatch, client):
    """Create a new custom field without sub_fields."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes
        return _ctx()

    async def fake_create_custom_field(self, body):
        """Fake create custom field."""
        del self
        assert body.field_name == "Test Field"
        assert body.entity_type == EntityType.COMPANY
        assert body.field_type == FieldType.TEXT
        assert not body.sub_fields

    monkeypatch.setattr(
        "apps.user_service.app.api.custom_fields.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        (
            "apps.user_service.app.services.custom_field_service"
            ".CustomFieldService.create_custom_field"
        ),
        fake_create_custom_field,
    )

    res = await client.post(
        "/v1/custom-fields",
        json={
            "field_name": "Test Field",
            "field_type": "text",
            "entity_type": "company",
        },
    )
    assert_success(res, 201)


@pytest.mark.asyncio
async def test_create_custom_field_with_sub_fields(monkeypatch, client):
    """Create a custom field with sub_fields."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes
        return _ctx()

    async def fake_create_custom_field(self, body):
        """Fake create custom field with sub_fields."""
        del self
        assert body.field_name == "Address Field"
        assert body.field_type == FieldType.OBJECT
        assert len(body.sub_fields) == 2

    monkeypatch.setattr(
        "apps.user_service.app.api.custom_fields.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        (
            "apps.user_service.app.services.custom_field_service"
            ".CustomFieldService.create_custom_field"
        ),
        fake_create_custom_field,
    )

    res = await client.post(
        "/v1/custom-fields",
        json={
            "field_name": "Address Field",
            "field_type": "object",
            "entity_type": "company",
            "sub_fields": [
                {
                    "field_name": "Street",
                    "field_type": "text",
                },
                {
                    "field_name": "City",
                    "field_type": "text",
                },
            ],
        },
    )
    assert_success(res, 201)


@pytest.mark.asyncio
async def test_list_custom_fields(monkeypatch, client):
    """List custom fields with results."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes
        return _ctx()

    async def fake_get_custom_fields_list(self, entity_type):
        """Fake get custom fields list."""
        del self
        assert entity_type == EntityType.COMPANY
        field = CustomFieldResponse(
            id="field-1",
            field_name="Test Field",
            field_key="test_field",
            field_type="text",
            show_on_create=True,
            show_on_detail=False,
            is_required=False,
            type_config={},
            sort_order=0,
            is_active=True,
            entity_type="company",
            parent_id=None,
            sub_fields=[],
        )
        return [field], 1

    monkeypatch.setattr(
        "apps.user_service.app.api.custom_fields.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        (
            "apps.user_service.app.services.custom_field_service"
            ".CustomFieldService.get_custom_fields_list"
        ),
        fake_get_custom_fields_list,
    )

    res = await client.get("/v1/custom-fields?entity_type=company")
    body = assert_success(res, 200)
    assert body["data"] is not None
    assert len(body["data"]) == 1
    assert body["data"][0]["field_name"] == "Test Field"
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_list_custom_fields_empty(monkeypatch, client):
    """List custom fields returns empty result."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes
        return _ctx()

    async def fake_get_custom_fields_list(self, entity_type):
        """Fake get custom fields list returning empty."""
        del self, entity_type
        return [], 0

    monkeypatch.setattr(
        "apps.user_service.app.api.custom_fields.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        (
            "apps.user_service.app.services.custom_field_service"
            ".CustomFieldService.get_custom_fields_list"
        ),
        fake_get_custom_fields_list,
    )

    res = await client.get("/v1/custom-fields?entity_type=company")
    body = assert_success(res, 200)
    assert body["data"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_get_custom_field_by_id(monkeypatch, client):
    """Get a custom field by ID."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes
        return _ctx()

    async def fake_get_custom_field_by_id(self, field_id):
        """Fake get custom field by id."""
        del self
        assert field_id == "field-123"
        return CustomFieldResponse(
            id="field-123",
            field_name="Test Field",
            field_key="test_field",
            field_type="text",
            show_on_create=True,
            show_on_detail=False,
            is_required=False,
            type_config={},
            sort_order=0,
            is_active=True,
            entity_type="company",
            parent_id=None,
            sub_fields=[],
        )

    monkeypatch.setattr(
        "apps.user_service.app.api.custom_fields.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        (
            "apps.user_service.app.services.custom_field_service"
            ".CustomFieldService.get_custom_field_by_id"
        ),
        fake_get_custom_field_by_id,
    )

    res = await client.get("/v1/custom-fields/field-123")
    body = assert_success(res, 200)
    assert body["data"]["id"] == "field-123"
    assert body["data"]["field_name"] == "Test Field"


@pytest.mark.asyncio
async def test_get_custom_field_with_sub_fields(monkeypatch, client):
    """Get a custom field with nested sub_fields."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes
        return _ctx()

    async def fake_get_custom_field_by_id(self, _field_id):
        """Fake get custom field with sub_fields."""
        del self
        from apps.user_service.app.schemas.custom_fields import (
            SubFieldResponse,
        )

        sub_field = SubFieldResponse(
            id="sub-field-1",
            field_name="Street",
            field_key="street",
            field_type="text",
            show_on_create=True,
            show_on_detail=False,
            is_required=False,
            type_config={},
            sort_order=0,
            is_active=True,
            entity_type="company",
            parent_id="field-123",
            sub_fields=[],
        )

        return CustomFieldResponse(
            id="field-123",
            field_name="Address",
            field_key="address",
            field_type="object",
            show_on_create=True,
            show_on_detail=False,
            is_required=False,
            type_config={},
            sort_order=0,
            is_active=True,
            entity_type="company",
            parent_id=None,
            sub_fields=[sub_field],
        )

    monkeypatch.setattr(
        "apps.user_service.app.api.custom_fields.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        (
            "apps.user_service.app.services.custom_field_service"
            ".CustomFieldService.get_custom_field_by_id"
        ),
        fake_get_custom_field_by_id,
    )

    res = await client.get("/v1/custom-fields/field-123")
    body = assert_success(res, 200)
    assert body["data"]["id"] == "field-123"
    assert body["data"]["field_type"] == "object"
    assert len(body["data"]["sub_fields"]) == 1
    assert body["data"]["sub_fields"][0]["field_name"] == "Street"


@pytest.mark.asyncio
async def test_update_custom_field(monkeypatch, client):
    """Update a custom field via PATCH."""

    async def fake_check_permissions(current_user, db_connection, permission_codes):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes
        return _ctx()

    async def fake_update_custom_field(self, field_id, body):
        """Fake update custom field."""
        del self
        assert field_id == "field-456"
        assert body.field_name == "Updated Field Name"
        assert body.description == "Updated description"

    monkeypatch.setattr(
        "apps.user_service.app.api.custom_fields.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        (
            "apps.user_service.app.services.custom_field_service"
            ".CustomFieldService.update_custom_field"
        ),
        fake_update_custom_field,
    )

    res = await client.patch(
        "/v1/custom-fields/field-456",
        json={
            "field_name": "Updated Field Name",
            "description": "Updated description",
        },
    )
    assert_success(res, 200)
