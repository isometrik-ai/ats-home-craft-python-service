"""Unit tests for pass calendar-day validity helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from apps.user_service.app.utils.pass_validity import (
    is_pass_expired_by_day,
    is_pass_too_early_by_day,
    pass_validity_local_date,
)


def test_same_day_before_start_not_too_early():
    """Minutes before valid_from on the same day are admissible."""
    now = datetime(2026, 7, 22, 9, 57, tzinfo=timezone.utc)
    valid_from = datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc)
    assert is_pass_too_early_by_day(valid_from, now=now) is False


def test_same_day_after_end_not_expired():
    """Minutes after valid_until on the same day are still valid."""
    now = datetime(2026, 7, 22, 18, 3, tzinfo=timezone.utc)
    valid_until = datetime(2026, 7, 22, 18, 0, tzinfo=timezone.utc)
    assert is_pass_expired_by_day(valid_until, now=now) is False


def test_next_day_after_end_is_expired():
    """Pass expires after the valid_until calendar day (Asia/Kolkata)."""
    now = datetime(2026, 7, 22, 20, 0, tzinfo=timezone.utc)
    valid_until = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    assert is_pass_expired_by_day(valid_until, now=now) is True


def test_previous_day_before_start_is_too_early():
    """Pass is too early before the valid_from calendar day (Asia/Kolkata)."""
    now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    valid_from = datetime(2026, 7, 22, 6, 0, tzinfo=timezone.utc)
    assert is_pass_too_early_by_day(valid_from, now=now) is True


def test_pass_validity_local_date_uses_asia_kolkata():
    """Local date conversion uses Asia/Kolkata for gate decisions."""
    dt = datetime(2026, 7, 21, 20, 0, tzinfo=timezone.utc)
    assert pass_validity_local_date(dt).isoformat() == "2026-07-22"
