"""Activity service for UI-friendly audit history.

This module provides a thin, reusable layer that reads existing audit logs and turns them into
"activity feed" items suitable for UI timelines.

Key behaviors:
- Filter by `(table_name, new_values.meta.requested_id)` to get activity for one record.
- Flatten a single audit entry containing multiple field changes into multiple activity rows.
- Enrich certain fields (e.g. lead `stage_id`) into human-friendly labels.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.audit_log_repository import (
    AuditLogRepository,
)
from apps.user_service.app.schemas.activity import ActivityActor, ActivityItem
from apps.user_service.app.utils.common_utils import (
    UserContext,
    extract_audit_data_value,
    parse_json_any,
    safe_str,
)


@dataclass(frozen=True)
class _OldNewPair:
    """Resolved old/new display strings for a single semantic dimension (stage, company, owner)."""

    old: str | None
    new: str | None


@dataclass(frozen=True)
class _AuditRow:
    """Internal normalized audit row for activity processing."""

    id: str
    user_id: str | None
    user_email: str | None
    actor_first_name: str | None
    actor_last_name: str | None
    action_type: str
    table_name: str
    timestamp: str
    old_values: dict | None
    new_values: dict | None
    changed_fields: list[str] | None
    stage_names: _OldNewPair
    company_names: _OldNewPair
    contact_names: _OldNewPair
    owner_names: _OldNewPair


def _coerce_audit_values_blob(values: Any) -> dict[str, Any] | None:
    """Normalize audit payloads that may be dicts or JSON strings from JSONB columns."""
    if values is None:
        return None
    if isinstance(values, dict):
        return values
    parsed = parse_json_any(values, None)
    return parsed if isinstance(parsed, dict) else None


class ActivityService:
    """Build activity feed items from audit logs.

    The activity feed is a derived view, not a separate table. The source-of-truth remains
    `audit_logs`.
    """

    def __init__(self, *, user_context: UserContext, db_connection: asyncpg.Connection) -> None:
        self.user_context = user_context
        self.db_connection = db_connection
        self.audit_log_repository = AuditLogRepository(db_connection=db_connection)

    async def get_lead_activity(
        self,
        *,
        lead_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Get flattened activity items for a lead id.

        Args:
            lead_id: Lead id to build activity for.
            limit: Page size.
            offset: Pagination offset.

        Returns:
            (items, total) where ``items`` are flattened activity lines (one per field change
            where applicable). ``total`` is the number of **audit log rows** for this lead;
            ``limit``/``offset`` paginate those audit rows in SQL—only that page is loaded and
            flattened (fast). ``len(items)`` can exceed ``limit`` when one audit UPDATE touches
            many fields.
        """
        rows, total = await self.audit_log_repository.get_activity_logs_for_record_with_actor_names(
            organization_id=self.user_context.organization_id,
            table_name="leads",
            record_id=lead_id,
            limit=limit,
            offset=offset,
        )

        audit_rows = [self._to_audit_row(r) for r in rows]

        flattened: list[ActivityItem] = []
        for audit_row in audit_rows:
            flattened.extend(
                self._flatten_lead_audit_row(
                    audit_row=audit_row,
                    record_id=lead_id,
                )
            )

        return [i.model_dump(mode="json", exclude_none=True) for i in flattened], total

    @staticmethod
    def _format_association_names(
        values_blob: dict[str, Any] | None,
        *,
        list_key: str,
        name_key: str,
        label_key: str | None = None,
        max_names: int = 3,
    ) -> str | None:
        """Format association names from audit JSON snapshots (no DB lookups)."""
        if not isinstance(values_blob, dict):
            return None
        data = values_blob.get("data")
        if not isinstance(data, dict):
            return None
        items = data.get(list_key)
        if not isinstance(items, list) or not items:
            return None

        names: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = (item.get(name_key) or "").strip()
            if name:
                if label_key:
                    label = (item.get(label_key) or "").strip()
                    names.append(f"{name} ({label})" if label else name)
                else:
                    names.append(name)

        if not names:
            return None
        head = names[:max_names]
        suffix = f" +{len(names) - max_names} more" if len(names) > max_names else ""
        return ", ".join(head) + suffix

    def _to_audit_row(self, row: dict[str, Any]) -> _AuditRow:
        """Normalize repository rows into a strongly-typed internal structure."""
        old_values = parse_json_any(row.get("old_values"), None)
        new_values = parse_json_any(row.get("new_values"), None)

        changed_fields_raw = parse_json_any(row.get("changed_fields"), None)
        changed_fields: list[str] | None = None
        if isinstance(changed_fields_raw, list):
            changed_fields = [str(x) for x in changed_fields_raw if x is not None]

        old_values_blob = old_values if isinstance(old_values, dict) else None
        new_values_blob = new_values if isinstance(new_values, dict) else None

        return _AuditRow(
            id=safe_str(row.get("id")),
            user_id=safe_str(row.get("user_id")) if row.get("user_id") is not None else None,
            user_email=row.get("user_email"),
            actor_first_name=row.get("actor_first_name"),
            actor_last_name=row.get("actor_last_name"),
            action_type=safe_str(row.get("action_type")),
            table_name=safe_str(row.get("table_name")),
            timestamp=safe_str(row.get("timestamp")),
            old_values=old_values if isinstance(old_values, dict) else None,
            new_values=new_values if isinstance(new_values, dict) else None,
            changed_fields=changed_fields,
            stage_names=_OldNewPair(
                old=row.get("old_stage_name"),
                new=row.get("new_stage_name"),
            ),
            company_names=_OldNewPair(
                old=self._format_association_names(
                    old_values_blob, list_key="companies", name_key="company_name"
                ),
                new=self._format_association_names(
                    new_values_blob, list_key="companies", name_key="company_name"
                ),
            ),
            contact_names=_OldNewPair(
                old=self._format_association_names(
                    old_values_blob,
                    list_key="contacts",
                    name_key="contact_name",
                    label_key="label",
                ),
                new=self._format_association_names(
                    new_values_blob,
                    list_key="contacts",
                    name_key="contact_name",
                    label_key="label",
                ),
            ),
            owner_names=_OldNewPair(
                old=row.get("old_owner_name"),
                new=row.get("new_owner_name"),
            ),
        )

    def _flatten_lead_audit_row(
        self,
        audit_row: _AuditRow,
        *,
        record_id: str,
    ) -> list[ActivityItem]:
        """Flatten one audit record into multiple `ActivityItem`s (one per changed field)."""
        first = safe_str(audit_row.actor_first_name).strip()
        last = safe_str(audit_row.actor_last_name).strip()
        name = (f"{first} {last}").strip() or (audit_row.user_email or "Unknown user")
        actor = ActivityActor(user_id=audit_row.user_id, name=name, email=audit_row.user_email)

        old_values_blob = _coerce_audit_values_blob(audit_row.old_values)
        new_values_blob = _coerce_audit_values_blob(audit_row.new_values)

        changed_fields = list(audit_row.changed_fields or [])
        # Fallback: if audit didn't compute changed_fields, infer from top-level keys.
        if not changed_fields:
            old_data = (
                (old_values_blob or {}).get("data") if isinstance(old_values_blob, dict) else None
            )
            new_data = (
                (new_values_blob or {}).get("data") if isinstance(new_values_blob, dict) else None
            )
            if isinstance(old_data, dict) and isinstance(new_data, dict):
                changed_fields = sorted(set(old_data.keys()) | set(new_data.keys()))
            elif isinstance(new_data, dict):
                changed_fields = sorted(new_data.keys())

        deny = {"updated_at", "created_at", "stage_name"}
        changed_fields = [f for f in changed_fields if f.split(".")[-1] not in deny]

        # CREATE/DELETE may not have changed_fields meaningful; emit a single item.
        if audit_row.action_type in {"CREATE", "DELETE"} or not changed_fields:
            return [
                ActivityItem(
                    id=f"{audit_row.id}:summary",
                    audit_log_id=audit_row.id,
                    timestamp=audit_row.timestamp,
                    table_name=audit_row.table_name,
                    record_id=record_id,
                    action_type=audit_row.action_type,
                    actor=actor,
                )
            ]

        items: list[ActivityItem] = []
        for field_path in changed_fields:
            # Only support simple fields for leads today; dotted paths are still handled.
            old_val = extract_audit_data_value(old_values_blob, field_path)
            new_val = extract_audit_data_value(new_values_blob, field_path)

            old_display, new_display = self._get_display_values_for_lead_field(
                field_path=field_path,
                audit_row=audit_row,
            )
            items.append(
                ActivityItem(
                    id=f"{audit_row.id}:{field_path}",
                    audit_log_id=audit_row.id,
                    timestamp=audit_row.timestamp,
                    table_name=audit_row.table_name,
                    record_id=record_id,
                    action_type=audit_row.action_type,
                    actor=actor,
                    field=field_path,
                    old_value=old_val,
                    new_value=new_val,
                    old_display_value=old_display,
                    new_display_value=new_display,
                )
            )
        return items

    def _get_display_values_for_lead_field(
        self,
        *,
        field_path: str,
        audit_row: _AuditRow,
    ) -> tuple[str | None, str | None]:
        """Return optional human-friendly display values for special fields.

        The API keeps `old_value/new_value` as raw values; frontend can prefer display values.
        """
        field_key = field_path.split(".")[-1]

        if field_key == "stage_id":
            old_name = (audit_row.stage_names.old or "").strip() or None
            new_name = (audit_row.stage_names.new or "").strip() or None
            return old_name, new_name

        if field_key == "companies":
            old_name = (audit_row.company_names.old or "").strip() or None
            new_name = (audit_row.company_names.new or "").strip() or None
            if old_name is None or new_name is None:
                old_values_blob = _coerce_audit_values_blob(audit_row.old_values)
                new_values_blob = _coerce_audit_values_blob(audit_row.new_values)
                old_name = old_name or self._format_association_names(
                    old_values_blob, list_key="companies", name_key="company_name"
                )
                new_name = new_name or self._format_association_names(
                    new_values_blob, list_key="companies", name_key="company_name"
                )
            return old_name, new_name

        if field_key == "contacts":
            old_name = (audit_row.contact_names.old or "").strip() or None
            new_name = (audit_row.contact_names.new or "").strip() or None
            # Unit tests may construct `_AuditRow` without computed contact_names;
            # fall back to formatting from the embedded audit snapshot.
            if old_name is None or new_name is None:
                old_values_blob = _coerce_audit_values_blob(audit_row.old_values)
                new_values_blob = _coerce_audit_values_blob(audit_row.new_values)
                old_name = old_name or self._format_association_names(
                    old_values_blob,
                    list_key="contacts",
                    name_key="contact_name",
                    label_key="label",
                )
                new_name = new_name or self._format_association_names(
                    new_values_blob,
                    list_key="contacts",
                    name_key="contact_name",
                    label_key="label",
                )
            return old_name, new_name

        if field_key == "owner_id":
            old_name = (audit_row.owner_names.old or "").strip() or None
            new_name = (audit_row.owner_names.new or "").strip() or None
            return old_name, new_name

        return None, None
