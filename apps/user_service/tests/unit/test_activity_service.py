"""Unit tests for ActivityService formatting helpers."""

import uuid

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
