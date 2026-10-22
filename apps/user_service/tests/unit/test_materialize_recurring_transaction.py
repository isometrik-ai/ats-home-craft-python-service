"""Transaction boundaries for recurring materialization job."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.jobs.publish_scheduled_notices import (
    materialize_due_recurring_notices,
)

ORG = "11111111-1111-1111-1111-111111111111"


@pytest.mark.asyncio
async def test_materialize_uses_single_batch_when_in_transaction():
    conn = MagicMock()
    conn.is_in_transaction = MagicMock(return_value=True)
    template = {"organization_id": ORG, "id": "tpl-1"}

    with patch(
        "apps.user_service.app.jobs.publish_scheduled_notices.NoticesRepository"
    ) as repo_cls:
        repo = repo_cls.return_value
        repo.lock_due_recurring_templates = AsyncMock(return_value=[template])
        with patch(
            "apps.user_service.app.jobs.publish_scheduled_notices._materialize_locked_templates",
            AsyncMock(return_value=["inst-1"]),
        ) as materialize:
            result = await materialize_due_recurring_notices(conn)

    assert result == ["inst-1"]
    repo.lock_due_recurring_templates.assert_awaited_once()
    materialize.assert_awaited_once_with(conn, [template])
    conn.transaction.assert_not_called()


@pytest.mark.asyncio
async def test_materialize_claims_one_template_per_transaction_when_autocommit():
    conn = MagicMock()
    conn.is_in_transaction = MagicMock(return_value=False)
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=tx)
    template = {"organization_id": ORG, "id": "tpl-1"}

    with patch(
        "apps.user_service.app.jobs.publish_scheduled_notices.NoticesRepository"
    ) as repo_cls:
        repo = repo_cls.return_value
        repo.lock_due_recurring_templates = AsyncMock(
            side_effect=[[template], []],
        )
        with patch(
            "apps.user_service.app.jobs.publish_scheduled_notices._materialize_locked_templates",
            AsyncMock(return_value=["inst-1"]),
        ):
            result = await materialize_due_recurring_notices(conn)

    assert result == ["inst-1"]
    assert conn.transaction.call_count == 2
    assert repo.lock_due_recurring_templates.await_count == 2
