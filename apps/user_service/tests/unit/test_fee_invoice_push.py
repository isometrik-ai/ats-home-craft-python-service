"""Push notifications for a new fee invoice and a pre-due reminder."""

from __future__ import annotations

from typing import Any

import pytest

from apps.user_service.app.services.fee_invoice_push import (
    notify_invoice_push,
    push_recipients,
)


class _Dispatcher:
    """Records each push send."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_user: str | None = None

    async def send_to_user(self, **kwargs: Any) -> None:
        """Store the call, or fail for one user."""
        if kwargs["recipient_user_id"] == self.fail_user:
            raise RuntimeError("push down")
        self.calls.append(kwargs)


def _notice(**overrides: Any) -> dict[str, Any]:
    """One invoice the push copy is built from."""
    payload: dict[str, Any] = {
        "invoice_id": "inv-oct",
        "organization_id": "org-1",
        "project_id": "project-1",
        "unit_id": "unit-1",
        "invoice_number": "INV-A-101-20261001",
        "total_amount": "1770.00",
        "due_date": "2026-10-11",
    }
    payload.update(overrides)
    return payload


def test_push_recipients_skip_blank_and_repeat_users():
    """A resident without an app login is skipped, and one login is notified once."""
    people = push_recipients(
        [
            {"user_id": "user-1", "additional_data": {"preferred_language": "en"}},
            {"user_id": "user-1", "additional_data": {}},
            {"user_id": "  ", "additional_data": {}},
            {"user_id": None, "additional_data": {}},
        ]
    )
    assert people == [("user-1", "en")]


@pytest.mark.asyncio
async def test_issued_invoice_push_uses_the_invoice_copy():
    """A new invoice pushes the amount, due date, and invoice id."""
    dispatcher = _Dispatcher()
    await notify_invoice_push(
        dispatcher,  # type: ignore[arg-type]
        [{"user_id": "user-1", "additional_data": {}}],
        _notice(),
        kind="issued",
    )
    call = dispatcher.calls[0]
    assert call["message_key"] == "notifications.push.fee_invoice.issued"
    assert call["notification_type"] == "NOTIFICATION_TYPE_SYSTEM"
    assert call["feed_type"] == "fee_invoice"
    assert call["params"]["invoice_number"] == "INV-A-101-20261001"
    assert call["params"]["amount"] == "Rs 1,770.00"
    assert call["params"]["due_date"] == "2026-10-11"
    assert call["data"]["invoice_id"] == "inv-oct"
    assert call["data"]["screen"] == "fee_invoice_detail"
    assert call["entity"] == {"kind": "fee_invoice", "id": "inv-oct"}
    assert call["options"]["idempotency_key"] == "fee_invoice:inv-oct:issued"


@pytest.mark.asyncio
async def test_reminder_push_uses_the_reminder_day():
    """Each reminder day has its own idempotency key."""
    dispatcher = _Dispatcher()
    await notify_invoice_push(
        dispatcher,  # type: ignore[arg-type]
        [{"user_id": "user-1", "additional_data": {}}],
        _notice(remind_on="2026-10-08"),
        kind="reminder",
    )
    call = dispatcher.calls[0]
    assert call["message_key"] == "notifications.push.fee_invoice.reminder"
    assert call["options"]["idempotency_key"] == "fee_invoice:inv-oct:2026-10-08"


@pytest.mark.asyncio
async def test_push_failure_continues_to_next_user():
    """A failed send for one login does not stop the others."""
    dispatcher = _Dispatcher()
    dispatcher.fail_user = "user-1"
    await notify_invoice_push(
        dispatcher,  # type: ignore[arg-type]
        [
            {"user_id": "user-1", "additional_data": {}},
            {"user_id": "user-2", "additional_data": {}},
        ],
        _notice(),
        kind="issued",
    )
    assert [call["recipient_user_id"] for call in dispatcher.calls] == ["user-2"]
