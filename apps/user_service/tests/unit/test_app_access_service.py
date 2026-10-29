"""Unit tests for app access evaluation (ADR 0019 §6.1)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.app_access import UpsertAppAccessRuleRequest
from apps.user_service.app.schemas.enums.app_access import (
    AppAccessBlockedBy,
    AppAccessMatchMode,
    AppAccessOverrideKind,
    AppAccessStatus,
)
from apps.user_service.app.services.app_access_evaluate import evaluate_unit_status
from apps.user_service.app.services.app_access_service import AppAccessService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import ConflictException, ValidationException

_AS_OF = "2026-10-28"
_THRESHOLD = Decimal("25000.00")
_DAYS = 15


def _eval(
    *,
    outstanding: str,
    days: int,
    override: str | None = None,
    enabled: bool = True,
    match_mode: str = "all",
    has_open: bool | None = None,
) -> dict:
    """Run the formula with the worked-example rule."""
    owed = Decimal(outstanding)
    return evaluate_unit_status(
        outstanding=owed,
        days_past_due=days,
        has_open_invoice=owed > 0 if has_open is None else has_open,
        override_kind=override,
        enabled=enabled,
        by_amount=True,
        amount_threshold=_THRESHOLD,
        by_days=True,
        days_threshold=_DAYS,
        match_mode=match_mode,
    )


def test_unit_a_blocked_amount_and_days():
    result = _eval(outstanding="48200", days=26)
    assert result["status"] == AppAccessStatus.BLOCKED.value
    assert result["blocked_by"] == AppAccessBlockedBy.RULE.value


def test_unit_b_watch_days_only():
    result = _eval(outstanding="12800", days=36)
    assert result["status"] == AppAccessStatus.WATCH.value


def test_unit_c_watch_amount_only():
    result = _eval(outstanding="92400", days=9)
    assert result["status"] == AppAccessStatus.WATCH.value


def test_unit_d_notdue():
    result = _eval(outstanding="27600", days=0)
    assert result["status"] == AppAccessStatus.NOTDUE.value


def test_unit_e_clear_zero():
    result = _eval(outstanding="0", days=0, has_open=False)
    assert result["status"] == AppAccessStatus.CLEAR.value


def test_unit_f_released_skips_rule():
    result = _eval(outstanding="7400", days=56, override="release")
    assert result["status"] == AppAccessStatus.RELEASED.value


def test_unit_g_hand_hold():
    result = _eval(outstanding="18900", days=8, override="hold")
    assert result["status"] == AppAccessStatus.BLOCKED.value
    assert result["blocked_by"] == AppAccessBlockedBy.HAND.value


def test_unit_h_watch_days_not_more_than_threshold():
    result = _eval(outstanding="1150000", days=12)
    assert result["status"] == AppAccessStatus.WATCH.value


def test_any_mode_blocks_unit_b_on_days():
    result = _eval(outstanding="12800", days=36, match_mode="any")
    assert result["status"] == AppAccessStatus.BLOCKED.value


def test_any_mode_blocks_unit_c_on_amount():
    result = _eval(outstanding="92400", days=9, match_mode="any")
    assert result["status"] == AppAccessStatus.BLOCKED.value


def test_rule_off_is_clear():
    result = _eval(outstanding="48200", days=26, enabled=False)
    assert result["status"] == AppAccessStatus.CLEAR.value


def test_stamp_keeps_blocked_on():
    previous_on = datetime(2026, 10, 27, 9, 12, tzinfo=timezone.utc)
    previous = {
        "status": AppAccessStatus.BLOCKED.value,
        "blocked_on": previous_on,
        "blocked_by": "rule",
    }
    now = datetime(2026, 10, 28, 0, 5, tzinfo=timezone.utc)
    blocked_on, blocked_by = AppAccessService._stamp(
        previous=previous,
        outcome={
            "status": AppAccessStatus.BLOCKED.value,
            "blocked_by": "rule",
        },
        now=now,
        override=None,
    )
    assert blocked_on == previous_on
    assert blocked_by == "rule"


def test_stamp_clears_when_leaving_blocked():
    blocked_on, blocked_by = AppAccessService._stamp(
        previous={
            "status": AppAccessStatus.BLOCKED.value,
            "blocked_on": datetime.now(timezone.utc),
            "blocked_by": "rule",
        },
        outcome={"status": AppAccessStatus.WATCH.value, "blocked_by": None},
        now=datetime.now(timezone.utc),
        override=None,
    )
    assert blocked_on is None
    assert blocked_by is None


def _service(repo) -> AppAccessService:
    """Service with a replaced repository."""
    svc = AppAccessService(
        MagicMock(),
        user_context=UserContext(
            user_id="user-1",
            email="admin@example.com",
            organization_id="org-1",
            user_type="admin",
        ),
    )
    svc.repo = repo
    return svc


def _rule_body(**overrides) -> UpsertAppAccessRuleRequest:
    payload = {
        "enabled": True,
        "by_amount": True,
        "amount_threshold": Decimal("25000.00"),
        "by_days": True,
        "days_threshold": 15,
        "match_mode": AppAccessMatchMode.ALL,
        "version": 0,
    }
    payload.update(overrides)
    return UpsertAppAccessRuleRequest(**payload)


@pytest.mark.asyncio
async def test_enabled_without_condition_is_422():
    svc = _service(MagicMock())
    with pytest.raises(ValidationException) as exc:
        await svc.upsert_rule(
            project_id="project-1",
            body=_rule_body(by_amount=False, by_days=False),
        )
    assert exc.value.message_key == "app_access.errors.condition_required"


@pytest.mark.asyncio
async def test_version_conflict_on_stale_save():
    repo = MagicMock()
    repo.get_rule = AsyncMock(return_value={"version": 2, "enabled": False})
    repo.update_rule = AsyncMock(return_value=None)
    svc = _service(repo)
    with pytest.raises(ConflictException) as exc:
        await svc.upsert_rule(project_id="project-1", body=_rule_body(version=1))
    assert exc.value.message_key == "app_access.errors.version_conflict"


@pytest.mark.asyncio
async def test_evaluate_unit_auto_clears_hold_at_zero():
    repo = MagicMock()
    repo.list_unit_dues = AsyncMock(
        return_value=[
            {
                "outstanding_amount": Decimal("0"),
                "days_past_due": 0,
                "oldest_invoice_date": None,
            }
        ]
    )
    repo.get_override = AsyncMock(return_value={"kind": AppAccessOverrideKind.HOLD.value})
    repo.delete_override = AsyncMock()
    repo.get_rule = AsyncMock(return_value=None)
    repo.get_state = AsyncMock(return_value=None)
    repo.upsert_state = AsyncMock()
    svc = _service(repo)
    state = await svc.evaluate_unit(
        organization_id="org-1",
        project_id="project-1",
        unit_id="unit-1",
    )
    assert state["status"] == AppAccessStatus.CLEAR.value
    repo.delete_override.assert_awaited_once()
    assert state["blocked_on"] is None
