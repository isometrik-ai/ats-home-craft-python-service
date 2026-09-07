"""Audit events and webhook dispatch pipeline."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import asyncpg

from apps.work_order_service.app.db.repositories.integration_repository import (
    IntegrationRepository,
)
from apps.work_order_service.app.services.webhook_worker import enqueue_delivery
from apps.work_order_service.app.utils.webhook_url import validate_outbound_webhook_url

VALID_SOURCES = ("fm", "vendor_portal", "scheduler", "api")
ENTITY_EVENTS = ("created", "updated", "deleted", "status_changed")


def _now_iso() -> str:
    """Now iso."""
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    """Json safe."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _deep_safe(value: Any) -> Any:
    """Deep safe."""
    if isinstance(value, dict):
        return {k: _deep_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_deep_safe(v) for v in value]
    return _json_safe(value)


def snapshot_record(record: dict[str, Any]) -> dict[str, Any]:
    """Snapshot record."""
    return _deep_safe(record)


def diff_records(
    before: dict[str, Any], after: dict[str, Any], skip: tuple[str, ...] = ("updated_at",)
) -> list[dict[str, Any]]:
    """Diff records."""
    changes: list[dict[str, Any]] = []
    for field, new in after.items():
        if field in skip:
            continue
        old = before.get(field)
        if old != new:
            changes.append({"field": field, "old": _deep_safe(old), "new": _deep_safe(new)})
    return changes


def entity_label(record: dict[str, Any]) -> str | None:
    """Entity label."""
    for attr in ("title", "name", "invoice_number", "label"):
        value = record.get(attr)
        if value:
            return str(value)[:256]
    return None


def event_for(action: str, changes: list[dict[str, Any]]) -> str:
    """Event for."""
    if action == "updated" and any(c["field"] in ("state", "status") for c in changes):
        return "status_changed"
    return action


class EventsService:
    """Record audit rows and enqueue webhook deliveries."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        """init  ."""
        self.conn = conn
        self.repo = IntegrationRepository(conn)

    async def record_and_dispatch(
        self,
        *,
        entity: str,
        action: str,
        record: dict[str, Any],
        changes: list[dict[str, Any]] | None = None,
        actor_name: str | None = None,
        actor_user_id: str | None = None,
        source: str = "fm",
        organization_id: str | None = None,
        project_id: str | None = None,
    ) -> None:
        """Record and dispatch."""
        org_id = organization_id or record.get("organization_id")
        proj_id = project_id or record.get("project_id")
        if not org_id or not proj_id:
            return

        changes = changes or []
        event = event_for(action, changes)
        snapshot = snapshot_record(record)
        safe_source = source if source in VALID_SOURCES else "fm"

        await self.repo.insert_audit_event(
            {
                "organization_id": org_id,
                "project_id": proj_id,
                "entity": entity,
                "entity_id": record["id"],
                "entity_label": entity_label(record),
                "action": action,
                "actor_name": actor_name,
                "actor_user_id": actor_user_id,
                "source": safe_source,
                "changes": changes,
                "snapshot": snapshot,
            }
        )

        triggers = await self.repo.list_matching_triggers(
            organization_id=org_id,
            project_id=proj_id,
            entity=entity,
            event=event,
        )
        if not triggers:
            return

        payload = {
            "id": str(uuid.uuid4()),
            "at": _now_iso(),
            "entity": entity,
            "entity_id": record.get("id"),
            "entity_label": entity_label(record),
            "action": action,
            "actor": actor_name,
            "source": safe_source,
            "changes": _deep_safe(changes),
            "snapshot": snapshot,
        }
        for trigger in triggers:
            delivery = await self.repo.insert_webhook_delivery(
                {
                    "organization_id": org_id,
                    "project_id": proj_id,
                    "trigger_id": trigger["id"],
                    "entity": entity,
                    "entity_id": record.get("id"),
                    "event": f"{entity}.{event}",
                    "request_payload": payload,
                    "delivered": False,
                }
            )
            enqueue_delivery(
                organization_id=org_id,
                project_id=proj_id,
                trigger_id=trigger["id"],
                webhook_url=trigger["webhook_url"],
                secret=trigger.get("secret"),
                event=f"{entity}.{event}",
                payload=payload,
                delivery_id=delivery["id"],
            )

    async def test_trigger(
        self,
        *,
        trigger: dict[str, Any],
        actor_name: str | None = None,
    ) -> dict[str, Any]:
        """Fire a sample payload at a trigger and record the delivery."""
        from apps.work_order_service.app.services.webhook_worker import (
            _persist_delivery_result,
            _post_webhook,
        )

        started = datetime.now(timezone.utc)
        validate_outbound_webhook_url(trigger["webhook_url"])
        payload = {
            "id": str(uuid.uuid4()),
            "at": _now_iso(),
            "entity": trigger["entity"],
            "entity_id": "test",
            "entity_label": "Test event",
            "action": "test",
            "actor": actor_name,
            "source": "fm",
            "changes": [],
            "snapshot": {"note": "Test event from triggers page."},
        }
        body = __import__("json").dumps(payload, default=str).encode()
        event = f"{trigger['entity']}.test"
        status, error = await _post_webhook(
            trigger["webhook_url"], trigger.get("secret"), event, body
        )
        duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        delivered = status is not None and 200 <= status < 300
        await _persist_delivery_result(
            {
                "organization_id": trigger["organization_id"],
                "project_id": trigger["project_id"],
                "trigger_id": trigger["id"],
                "event": event,
                "payload": payload,
            },
            status=status,
            error=error or (None if delivered else f"endpoint returned {status}"),
            attempts=1,
            duration_ms=duration_ms,
            delivered=delivered,
        )
        return {
            "delivered": delivered,
            "status": status,
            "error": error,
            "duration_ms": duration_ms,
        }
