"""Unit tests for AuditLogFilter schema validation."""

from datetime import date

import pytest

from apps.user_service.app.schemas.audit_logs import AuditLogFilter
from apps.user_service.app.schemas.enums import AuditLogActionType, AuditLogRiskLevel
from libs.shared_utils.http_exceptions import ValidationException


def _filter(**overrides):
    base = {
        "organization_id": "org-1",
        "search": None,
        "action_type": None,
        "table_name": None,
        "user_id": None,
        "category": None,
        "risk_level": None,
        "start_date": None,
        "end_date": None,
        "limit": 20,
        "offset": 0,
    }
    base.update(overrides)
    return AuditLogFilter(**base)


def test_filter_accepts_all_action_types():
    """Each supported action type is accepted."""
    for action in AuditLogActionType:
        filt = _filter(action_type=action)
        assert filt.action_type == action


def test_filter_accepts_all_risk_levels():
    """Each supported risk level is accepted."""
    for risk in AuditLogRiskLevel:
        filt = _filter(risk_level=risk)
        assert filt.risk_level == risk


def test_filter_accepts_equal_start_and_end_date():
    """Same-day range is valid."""
    same_day = date(2026, 8, 20)
    filt = _filter(start_date=same_day, end_date=same_day)

    assert filt.start_date == same_day
    assert filt.end_date == same_day


def test_filter_rejects_inverted_date_range():
    """end_date before start_date raises ValidationException."""
    with pytest.raises(ValidationException):
        _filter(start_date=date(2026, 2, 1), end_date=date(2026, 1, 1))


def test_filter_allows_partial_date_range():
    """Only start_date or only end_date is valid."""
    start_only = _filter(start_date=date(2026, 1, 1))
    end_only = _filter(end_date=date(2026, 12, 31))

    assert start_only.start_date == date(2026, 1, 1)
    assert start_only.end_date is None
    assert end_only.end_date == date(2026, 12, 31)
    assert end_only.start_date is None
