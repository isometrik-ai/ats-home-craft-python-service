"""Integration smoke tests for work_order_service (mocked DB)."""

from __future__ import annotations

from apps.work_order_service.app.services.scheduler_service import _parse_d, _step


def test_parse_date_iso():
    """Verify parse date iso."""
    assert _parse_d("2026-09-15") == __import__("datetime").date(2026, 9, 15)
    assert _parse_d("") is None


def test_step_quarterly():
    """Verify step quarterly."""
    from datetime import date

    start = date(2026, 1, 1)
    nxt = _step(start, "quarterly")
    assert nxt.month == 4
