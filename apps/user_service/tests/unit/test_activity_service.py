"""Unit tests for ActivityService formatting helpers."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums import EntityType
from apps.user_service.app.services.activity_service import (
    ActivityService,
    _AuditRow,
    _OldNewPair,
)
from apps.user_service.app.utils.common_utils import UserContext


def _ctx() -> UserContext:
    """Build a reusable UserContext for tests."""
    return UserContext(
        user_id="u1",
        email="u1@example.com",
        organization_id="org-1",
        user_type="admin",
    )


def _service() -> ActivityService:
    """Create ActivityService for pure helper tests (no DB calls)."""
    return ActivityService(user_context=_ctx(), db_connection=None)  # type: ignore[arg-type]


def _audit_row(**overrides) -> _AuditRow:
    """Build a minimal AuditLog row used by ActivityService flatteners."""
    base = {
        "id": "audit-1",
        "user_id": "actor-1",
        "user_email": "actor@example.com",
        "actor_first_name": "Tejas",
        "actor_last_name": "Marthak",
        "action_type": "UPDATE",
        "table_name": "leads",
        "timestamp": "2026-04-06T13:53:55Z",
        "old_values": {"data": {}},
        "new_values": {"data": {}},
        "changed_fields": [],
        "stage_names": _OldNewPair(None, None),
        "company_names": _OldNewPair(None, None),
        "contact_names": _OldNewPair(None, None),
        "owner_names": _OldNewPair(None, None),
    }
    return _AuditRow(**{**base, **overrides})


def test_stage_change_includes_display_values():
    """Stage change should include display values for frontend rendering."""
    service = _service()
    row = _audit_row(
        id="audit-1",
        actor_first_name="Rohit",
        old_values={"data": {"stage_id": str(uuid.uuid4())}},
        new_values={"data": {"stage_id": str(uuid.uuid4())}},
        changed_fields=["stage_id"],
        stage_names=_OldNewPair("Prospect", "Meeting"),
    )

    items = service._flatten_lead_audit_row(  # pylint: disable=protected-access
        audit_row=row, record_id="lead-1"
    )
    assert len(items) == 1
    assert items[0].old_display_value == "Prospect"
    assert items[0].new_display_value == "Meeting"


def test_company_change_includes_display_values():
    """Company change should include display values for frontend rendering."""
    service = _service()
    row = _audit_row(
        id="audit-2",
        old_values={"data": {"companies": []}},
        new_values={"data": {"companies": []}},
        changed_fields=["companies"],
        company_names=_OldNewPair("Acme LLC", "Umbrella Corp"),
    )

    items = service._flatten_lead_audit_row(  # pylint: disable=protected-access
        audit_row=row, record_id="lead-1"
    )
    assert items[0].old_display_value == "Acme LLC"
    assert items[0].new_display_value == "Umbrella Corp"


def test_contacts_change_includes_display_values():
    """contacts changes should produce display values (names derived from snapshot)."""
    service = _service()
    row = _audit_row(
        id="audit-2c",
        old_values={
            "data": {
                "contacts": [
                    {"contact_id": "c1", "label": "decision_maker", "contact_name": "Alice"},
                ]
            }
        },
        new_values={
            "data": {
                "contacts": [
                    {"contact_id": "c1", "label": "decision_maker", "contact_name": "Alice"},
                    {"contact_id": "c2", "label": None, "contact_name": "Bob"},
                ]
            }
        },
        changed_fields=["contacts"],
    )

    items = service._flatten_lead_audit_row(  # pylint: disable=protected-access
        audit_row=row, record_id="lead-1"
    )
    assert len(items) == 1
    assert items[0].field == "contacts"
    assert items[0].old_display_value == "Alice (decision_maker)"
    assert items[0].new_display_value == "Alice (decision_maker), Bob"


def test_owner_change_includes_display_values():
    """Owner change should include display values for frontend rendering."""
    service = _service()
    row = _audit_row(
        id="audit-3",
        old_values={"data": {"owner_id": str(uuid.uuid4())}},
        new_values={"data": {"owner_id": str(uuid.uuid4())}},
        changed_fields=["owner_id"],
        owner_names=_OldNewPair("Alice Smith", "Bob Jones"),
    )

    items = service._flatten_lead_audit_row(  # pylint: disable=protected-access
        audit_row=row, record_id="lead-1"
    )
    assert items[0].old_display_value == "Alice Smith"
    assert items[0].new_display_value == "Bob Jones"


def test_denylist_drops_updated_at_rows():
    """Denylist should remove updated_at changes from activity list."""
    service = _service()
    row = _audit_row(
        id="audit-4",
        old_values={"data": {"updated_at": "x", "name": "Old"}},
        new_values={"data": {"updated_at": "y", "name": "New"}},
        changed_fields=["updated_at", "name"],
    )

    items = service._flatten_lead_audit_row(  # pylint: disable=protected-access
        audit_row=row, record_id="lead-1"
    )
    assert [i.field for i in items] == ["name"]


def test_non_special_fields_do_not_set_display_values():
    """Non-special fields should not set display values."""
    service = _service()
    old_uuid = str(uuid.uuid4())
    new_uuid = str(uuid.uuid4())
    row = _audit_row(
        id="audit-5",
        old_values={"data": {"some_other_id": old_uuid}},
        new_values={"data": {"some_other_id": new_uuid}},
        changed_fields=["some_other_id"],
    )

    items = service._flatten_lead_audit_row(  # pylint: disable=protected-access
        audit_row=row, record_id="lead-1"
    )
    assert items[0].old_value == old_uuid
    assert items[0].new_value == new_uuid
    assert items[0].old_display_value is None
    assert items[0].new_display_value is None


def test_field_paths_with_data_prefix_resolve_values():
    """Changed fields may be emitted as `data.<field>`; values should still resolve."""
    service = _service()
    row = _audit_row(
        id="audit-6",
        old_values={"data": {"amount": 25000}},
        new_values={"data": {"amount": 50000}},
        changed_fields=["data.amount"],
    )

    items = service._flatten_lead_audit_row(  # pylint: disable=protected-access
        audit_row=row, record_id="lead-1"
    )
    assert len(items) == 1
    assert items[0].old_value == 25000
    assert items[0].new_value == 50000
    assert items[0].old_display_value is None
    assert items[0].new_display_value is None


def test_old_new_values_as_json_strings():
    """Repository may return JSONB columns as strings; service should still extract values."""
    service = _service()
    row = _audit_row(
        id="audit-7",
        old_values='{"data":{"description":"Old desc"}}',
        new_values='{"data":{"description":"New desc"}}',
        changed_fields=["description"],
    )

    items = service._flatten_lead_audit_row(  # pylint: disable=protected-access
        audit_row=row, record_id="lead-1"
    )
    assert len(items) == 1
    assert items[0].old_value == "Old desc"
    assert items[0].new_value == "New desc"
    assert items[0].old_display_value is None
    assert items[0].new_display_value is None


def test_custom_fields_parsed_and_enriched_with_names():
    """custom_fields should be returned as JSON and include field_name keys when possible."""
    service = _service()

    # We pass a map directly to the flattener so the test doesn't require DB.
    custom_field_name_map = {
        "root-1": "Vehicle details",
        "sub-1": "Wheel type",
        "sub-2": "Wheel count",
    }
    row = _audit_row(
        id="audit-cf-1",
        old_values={"data": {"custom_fields": "[]"}},
        new_values={
            "data": {
                "custom_fields": (
                    '[{"type":"object","field_id":"root-1","sub_fields":'
                    '[{"type":"dropdown","value":"Steel wheels","field_id":"sub-1",'
                    '"instance_id":"i1"},'
                    '{"type":"dropdown","value":"3","field_id":"sub-2","instance_id":"i2"}],'
                    '"instance_id":"root-inst"}]'
                )
            }
        },
        changed_fields=["custom_fields"],
    )

    items = service._flatten_lead_audit_row(  # pylint: disable=protected-access
        audit_row=row,
        record_id="lead-1",
        custom_field_name_map=custom_field_name_map,
    )
    assert len(items) == 1
    assert items[0].field == "custom_fields"
    assert items[0].old_value == []
    assert isinstance(items[0].new_value, list)
    assert items[0].new_value[0]["field_name"] == "Vehicle details"
    assert items[0].new_value[0]["sub_fields"][0]["field_name"] == "Wheel type"
    assert items[0].new_value[0]["sub_fields"][1]["field_name"] == "Wheel count"


def test_format_association_names_with_labels():
    """Association formatter includes labels and truncates long lists."""
    blob = {
        "data": {
            "contacts": [
                {"contact_name": "Alice", "label": "primary"},
                {"contact_name": "Bob", "label": ""},
                {"contact_name": "Carol", "label": "exec"},
                {"contact_name": "Dan", "label": "exec"},
            ]
        }
    }
    formatted = ActivityService._format_association_names(
        blob,
        list_key="contacts",
        name_key="contact_name",
        label_key="label",
        max_names=2,
    )
    assert formatted == "Alice (primary), Bob +2 more"


def test_to_audit_row_parses_json_strings():
    """Repository JSON strings normalize into audit row structure."""
    service = _service()
    row = service._to_audit_row(  # pylint: disable=protected-access
        {
            "id": "a1",
            "user_id": "u1",
            "user_email": "u@example.com",
            "action_type": "UPDATE",
            "table_name": "leads",
            "timestamp": "2026-01-01T00:00:00Z",
            "old_values": '{"data":{"name":"Old"}}',
            "new_values": '{"data":{"name":"New"}}',
            "changed_fields": '["name"]',
        }
    )
    assert row.changed_fields == ["name"]
    assert row.old_values["data"]["name"] == "Old"


def test_flatten_lead_create_summary_item():
    """CREATE actions emit a single summary activity item."""
    service = _service()
    row = _audit_row(action_type="CREATE", changed_fields=[])

    items = service._flatten_lead_audit_row(  # pylint: disable=protected-access
        audit_row=row, record_id="lead-1"
    )

    assert len(items) == 1
    assert items[0].id.endswith(":summary")
    assert items[0].field is None


def test_flatten_contact_company_display():
    """Contact company association changes get display values."""
    service = _service()
    row = _audit_row(
        table_name="contacts",
        old_values={"data": {"companies": [{"company_name": "Acme"}]}},
        new_values={"data": {"companies": [{"company_name": "Beta"}]}},
        changed_fields=["companies"],
    )

    items = service._flatten_contact_audit_row(  # pylint: disable=protected-access
        audit_row=row, record_id="c1"
    )

    assert items[0].old_display_value == "Acme"
    assert items[0].new_display_value == "Beta"


def test_flatten_company_contact_display():
    """Company contact association changes get display values."""
    service = _service()
    row = _audit_row(
        table_name="companies",
        old_values={"data": {"contacts": [{"contact_name": "Alice", "label": "primary"}]}},
        new_values={"data": {"contacts": [{"contact_name": "Bob", "label": "exec"}]}},
        changed_fields=["contacts"],
    )

    items = service._flatten_company_audit_row(  # pylint: disable=protected-access
        audit_row=row, record_id="co1"
    )

    assert items[0].old_display_value == "Alice (primary)"
    assert items[0].new_display_value == "Bob (exec)"


def test_normalize_custom_fields_enriches_names():
    """Custom field values receive field_name from map."""
    enriched = ActivityService._normalize_and_enrich_custom_fields_value(
        [{"field_id": "f1", "sub_fields": [{"field_id": "f2", "value": "x"}]}],
        field_name_map={"f1": "Root", "f2": "Child"},
    )
    assert enriched[0]["field_name"] == "Root"
    assert enriched[0]["sub_fields"][0]["field_name"] == "Child"


def test_coerce_audit_values_blob():
    """_coerce_audit_values_blob accepts dicts and JSON strings."""
    from apps.user_service.app.services.activity_service import (
        _coerce_audit_values_blob,
    )

    assert _coerce_audit_values_blob({"data": {"name": "A"}})["data"]["name"] == "A"
    assert _coerce_audit_values_blob('{"data":{"name":"B"}}')["data"]["name"] == "B"
    assert _coerce_audit_values_blob(None) is None


def test_format_association_names_edge_cases():
    """Association formatter handles invalid blobs and empty names."""
    assert (
        ActivityService._format_association_names(
            None, list_key="contacts", name_key="contact_name"
        )
        is None
    )
    assert (
        ActivityService._format_association_names(
            {"data": {"contacts": [{"contact_name": ""}]}},
            list_key="contacts",
            name_key="contact_name",
        )
        is None
    )


def test_flatten_lead_delete_summary():
    """DELETE actions emit a single summary activity item."""
    service = _service()
    row = _audit_row(action_type="DELETE", changed_fields=["name"])
    items = service._flatten_lead_audit_row(audit_row=row, record_id="lead-1")  # pylint: disable=protected-access
    assert len(items) == 1
    assert items[0].action_type == "DELETE"


def test_flatten_infers_changed_fields_when_missing():
    """Flatteners infer changed fields from old/new data when audit metadata is absent."""
    service = _service()
    row = _audit_row(
        changed_fields=[],
        old_values={"data": {"title": "Old"}},
        new_values={"data": {"title": "New"}},
    )
    items = service._flatten_lead_audit_row(audit_row=row, record_id="lead-1")  # pylint: disable=protected-access
    assert len(items) == 1
    assert items[0].field == "title"


@pytest.mark.asyncio
async def test_get_lead_activity_with_mock_repo():
    """get_lead_activity flattens repository rows into activity items."""
    service = ActivityService(user_context=_ctx(), db_connection=MagicMock())
    service.audit_log_repository = MagicMock()
    service.audit_log_repository.get_activity_logs_for_record_with_actor_names = AsyncMock(
        return_value=(
            [
                {
                    "id": "audit-1",
                    "user_id": "actor-1",
                    "user_email": "actor@example.com",
                    "action_type": "UPDATE",
                    "table_name": "leads",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "old_values": {"data": {"name": "Old"}},
                    "new_values": {"data": {"name": "New"}},
                    "changed_fields": ["name"],
                }
            ],
            1,
        )
    )
    service._get_custom_field_name_map = AsyncMock(return_value={})  # pylint: disable=protected-access

    items, total = await service.get_lead_activity(lead_id="lead-1", limit=10, offset=0)

    assert total == 1
    assert items[0]["field"] == "name"


@pytest.mark.asyncio
async def test_get_contact_activity_with_mock_repo():
    """get_contact_activity returns flattened contact audit items."""
    service = ActivityService(user_context=_ctx(), db_connection=MagicMock())
    service.audit_log_repository = MagicMock()
    service.audit_log_repository.get_activity_logs_for_record_with_actor_names = AsyncMock(
        return_value=(
            [
                {
                    "id": "audit-c1",
                    "user_id": "actor-1",
                    "user_email": "actor@example.com",
                    "action_type": "UPDATE",
                    "table_name": "contacts",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "old_values": {"data": {"first_name": "Old"}},
                    "new_values": {"data": {"first_name": "New"}},
                    "changed_fields": ["first_name"],
                }
            ],
            1,
        )
    )
    service._get_custom_field_name_map = AsyncMock(return_value={})  # pylint: disable=protected-access

    items, total = await service.get_contact_activity(contact_id="c1")

    assert total == 1
    assert items[0]["field"] == "first_name"


@pytest.mark.asyncio
async def test_get_company_activity_with_mock_repo():
    """get_company_activity returns flattened company audit items."""
    service = ActivityService(user_context=_ctx(), db_connection=MagicMock())
    service.audit_log_repository = MagicMock()
    service.audit_log_repository.get_activity_logs_for_record_with_actor_names = AsyncMock(
        return_value=(
            [
                {
                    "id": "audit-co1",
                    "user_id": "actor-1",
                    "user_email": "actor@example.com",
                    "action_type": "UPDATE",
                    "table_name": "companies",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "old_values": {"data": {"name": "Old Co"}},
                    "new_values": {"data": {"name": "New Co"}},
                    "changed_fields": ["name"],
                }
            ],
            1,
        )
    )
    service._get_custom_field_name_map = AsyncMock(return_value={})  # pylint: disable=protected-access

    items, total = await service.get_company_activity(company_id="co1")

    assert total == 1
    assert items[0]["field"] == "name"


@pytest.mark.asyncio
async def test_get_custom_field_name_map_caches_definitions(monkeypatch):
    """_get_custom_field_name_map builds and caches field id/name mappings."""
    service = ActivityService(user_context=_ctx(), db_connection=MagicMock())
    root = MagicMock()
    root.model_dump.return_value = {
        "id": "root-1",
        "field_name": "Vehicle details",
        "sub_fields": [{"id": "sub-1", "field_name": "Wheel type"}],
    }

    mock_service_cls = MagicMock()
    mock_service_cls.return_value.get_custom_fields_list = AsyncMock(return_value=([root], 1))
    monkeypatch.setattr(
        "apps.user_service.app.services.activity_service.CustomFieldService",
        mock_service_cls,
    )
    mapping = await service._get_custom_field_name_map(entity_type=EntityType.LEAD)  # pylint: disable=protected-access

    assert mapping["root-1"] == "Vehicle details"
    assert mapping["sub-1"] == "Wheel type"
