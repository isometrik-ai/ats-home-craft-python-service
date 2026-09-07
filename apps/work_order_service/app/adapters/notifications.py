"""Push notification hooks for work order lifecycle events."""

from __future__ import annotations

from typing import Any

from libs.shared_config.app_settings import shared_settings
from libs.shared_utils.logger import get_logger
from libs.shared_utils.notification_grpc_client import notification_grpc_client

logger = get_logger("work_order_notifications")


async def notify_work_order_event(
    *,
    event: str,
    organization_id: str,
    project_id: str,
    work_order_id: str,
    title: str,
    user_id: str | None = None,
) -> None:
    """Send a push notification for a work-order lifecycle event (best-effort)."""
    if not shared_settings.notification.enabled:
        logger.debug(
            "work_order notification skipped (disabled) event=%s wo=%s",
            event,
            work_order_id,
        )
        return

    payload: dict[str, Any] = {
        "tenant_id": shared_settings.isometrik.client_name,
        "project_id": organization_id,
        "user_id": user_id or organization_id,
        "title": f"Work order {event}",
        "body": title or work_order_id,
        "type": f"work_order.{event}",
        "feed_type": "work_order",
        "data": {
            "work_order_id": work_order_id,
            "project_id": project_id,
            "event": event,
        },
        "options": {"save_to_db": True, "push_enabled": True},
        "entity": {"type": "work_order", "id": work_order_id},
    }
    try:
        await notification_grpc_client.send_notification(payload)
    except Exception:
        logger.exception(
            "work_order notification failed event=%s org=%s wo=%s",
            event,
            organization_id,
            work_order_id,
        )


async def notify_invoice_event(
    *,
    event: str,
    organization_id: str,
    project_id: str,
    invoice_id: str,
    work_order_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """Send a push notification for invoice lifecycle events (best-effort)."""
    if not shared_settings.notification.enabled:
        return

    payload: dict[str, Any] = {
        "tenant_id": shared_settings.isometrik.client_name,
        "project_id": organization_id,
        "user_id": user_id or organization_id,
        "title": f"Invoice {event}",
        "body": invoice_id,
        "type": f"invoice.{event}",
        "feed_type": "invoice",
        "data": {
            "invoice_id": invoice_id,
            "work_order_id": work_order_id,
            "project_id": project_id,
            "event": event,
        },
        "options": {"save_to_db": True, "push_enabled": True},
        "entity": {"type": "invoice", "id": invoice_id},
    }
    try:
        await notification_grpc_client.send_notification(payload)
    except Exception:
        logger.exception(
            "invoice notification failed event=%s org=%s invoice=%s",
            event,
            organization_id,
            invoice_id,
        )
