"""Unit tests for PassVerificationService."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from apps.user_service.app.schemas.enums import (
    PassAccessStatus,
    PassEntryMethod,
    PassEventType,
    PassStatus,
    PassValidityType,
)
from apps.user_service.app.schemas.gate_passes import CheckInRequest, CheckOutRequest
from apps.user_service.app.services.pass_verification_service import (
    PassVerificationService,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    NotFoundException,
    ValidationException,
)


def _user_context() -> UserContext:
    """Build a staff user context for gate tests."""
    return UserContext(
        user_id="staff-1",
        email="guard@example.com",
        organization_id="org-1",
    )


def _pass_row(**overrides: Any) -> dict[str, Any]:
    """Build a gate pass row with sensible defaults."""
    now = datetime.now(timezone.utc)
    row = {
        "id": "pass-1",
        "organization_id": "org-1",
        "project_id": "project-1",
        "unit_id": "unit-1",
        "tower_id": "tower-1",
        "host_contact_id": "contact-1",
        "pass_type": "guest",
        "guest_name": "Ravi Kumar",
        "guest_phone_isd_code": "+91",
        "guest_phone_number": "9876543210",
        "visitor_count": 1,
        "vehicle_number": None,
        "purpose": "Visit",
        "valid_from": now - timedelta(hours=1),
        "valid_until": now + timedelta(hours=5),
        "validity_type": PassValidityType.ONE_TIME.value,
        "allow_multiple_entries": False,
        "is_private": False,
        "max_entries": None,
        "entry_count": 0,
        "status": PassStatus.ACTIVE.value,
        "code": "4821",
        "unit_label": "A-803",
        "tower_name": "Tower A",
        "host_first_name": "N.",
        "host_last_name": "Reddy",
    }
    row.update(overrides)
    return row


class _FakePassesRepo:
    """In-memory fake for PassesRepository gate methods."""

    def __init__(self, row: dict[str, Any] | None = None):
        self.row = row or _pass_row()
        self.increment_calls: list[dict[str, Any]] = []
        self.complete_calls: list[dict[str, Any]] = []

    async def get_by_code(self, **_kwargs):
        """Return configured pass row."""
        return self.row

    async def get_by_id(self, **_kwargs):
        """Return configured pass row."""
        return self.row

    async def increment_entry_count(self, **kwargs):
        """Increment entry_count on the configured row."""
        self.increment_calls.append(kwargs)
        self.row = {**self.row, "entry_count": int(self.row.get("entry_count") or 0) + 1}
        return {"id": self.row["id"], "entry_count": self.row["entry_count"]}

    async def complete(self, **kwargs):
        """Mark configured row completed."""
        self.complete_calls.append(kwargs)
        self.row = {**self.row, "status": PassStatus.COMPLETED.value}
        return {"id": self.row["id"], "status": PassStatus.COMPLETED.value}


_DEFAULT_GATE: dict[str, Any] = {"id": "gate-1", "organization_id": "org-1"}
_UNSET_GATE = object()


class _FakeTowersRepo:
    """In-memory fake for TowersRepository gate lookup."""

    def __init__(self, *, gate: dict[str, Any] | None | object = _UNSET_GATE):
        if gate is _UNSET_GATE:
            self.gate = _DEFAULT_GATE
        else:
            self.gate = gate

    async def get_gate_by_id(self, **_kwargs):
        """Return configured gate row."""
        return self.gate


class _FakeEventsRepo:
    """In-memory fake for PassEventsRepository gate methods."""

    def __init__(self, *, has_open_check_in: bool = False):
        self.insert_calls: list[dict[str, Any]] = []
        self._has_open_check_in = has_open_check_in

    async def insert_event(self, data):
        """Record inserted event and return normalized payload."""
        self.insert_calls.append(data)
        return {
            "id": f"event-{len(self.insert_calls)}",
            **data,
            "occurred_at": datetime.now(timezone.utc),
        }

    async def has_open_check_in(self, **_kwargs):
        """Return configured open check-in flag."""
        return self._has_open_check_in


def _service(
    *,
    passes_repo: _FakePassesRepo | None = None,
    events_repo: _FakeEventsRepo | None = None,
    towers_repo: _FakeTowersRepo | None = None,
) -> PassVerificationService:
    """Build PassVerificationService with fake repositories."""
    svc = PassVerificationService(
        db_connection=MagicMock(),
        user_context=_user_context(),
    )
    svc.passes_repo = passes_repo or _FakePassesRepo()
    svc.events_repo = events_repo or _FakeEventsRepo()
    svc.towers_repo = towers_repo or _FakeTowersRepo()
    return svc


@pytest.mark.asyncio
async def test_verify_approved():
    """Verify returns approved snapshot for an active pass."""
    svc = _service()
    result = await svc.verify(code="4821")
    assert result["access_status"] == PassAccessStatus.APPROVED.value
    assert result["can_check_in"] is True
    assert result["guest_name"] == "Ravi Kumar"
    assert result["host_name"] == "N. Reddy"


@pytest.mark.asyncio
async def test_verify_expired():
    """Verify marks an expired pass as expired."""
    now = datetime.now(timezone.utc)
    passes_repo = _FakePassesRepo(
        row=_pass_row(
            valid_from=now - timedelta(days=2),
            valid_until=now - timedelta(days=1),
        )
    )
    svc = _service(passes_repo=passes_repo)
    result = await svc.verify(code="4821")
    assert result["access_status"] == PassAccessStatus.EXPIRED.value
    assert result["can_check_in"] is False


@pytest.mark.asyncio
async def test_verify_denied_when_used():
    """Verify denies a one-time pass that already has an entry."""
    passes_repo = _FakePassesRepo(row=_pass_row(entry_count=1))
    svc = _service(passes_repo=passes_repo)
    result = await svc.verify(code="4821")
    assert result["access_status"] == PassAccessStatus.DENIED.value
    assert result["can_check_in"] is False


@pytest.mark.asyncio
async def test_verify_too_early():
    """Verify flags a pass that has not started yet."""
    now = datetime.now(timezone.utc)
    passes_repo = _FakePassesRepo(
        row=_pass_row(
            valid_from=now + timedelta(hours=2),
            valid_until=now + timedelta(hours=8),
        )
    )
    svc = _service(passes_repo=passes_repo)
    result = await svc.verify(code="4821")
    assert result["access_status"] == PassAccessStatus.APPROVED.value
    assert result["can_check_in"] is False
    assert result["too_early"] is True


@pytest.mark.asyncio
async def test_verify_not_found():
    """Verify raises 404 when code is missing."""
    passes_repo = _FakePassesRepo()
    passes_repo.row = None

    async def _missing(**_kwargs):
        return None

    passes_repo.get_by_code = _missing  # type: ignore[method-assign]
    svc = _service(passes_repo=passes_repo)
    with pytest.raises(NotFoundException):
        await svc.verify(code="9999")


@pytest.mark.asyncio
async def test_check_in_gate_not_found():
    """Check-in raises 404 when gate_id is not a configured tower gate."""
    svc = _service(towers_repo=_FakeTowersRepo(gate=None))
    body = CheckInRequest(
        gate_id="missing-gate",
        entry_method=PassEntryMethod.QR,
        access_status=PassAccessStatus.APPROVED,
    )
    with pytest.raises(NotFoundException):
        await svc.check_in(pass_id="pass-1", body=body)


@pytest.mark.asyncio
async def test_check_in_success():
    """Successful check-in records event and increments entry_count."""
    events_repo = _FakeEventsRepo()
    passes_repo = _FakePassesRepo()
    svc = _service(passes_repo=passes_repo, events_repo=events_repo)
    body = CheckInRequest(
        gate_id="gate-1",
        entry_method=PassEntryMethod.QR,
        access_status=PassAccessStatus.APPROVED,
    )
    result = await svc.check_in(pass_id="pass-1", body=body)
    assert result["entry_count"] == 1
    assert events_repo.insert_calls[-1]["event_type"] == PassEventType.CHECKED_IN.value
    assert events_repo.insert_calls[-1]["entry_method"] == PassEntryMethod.QR.value
    assert passes_repo.increment_calls


@pytest.mark.asyncio
async def test_check_in_refusal_audit():
    """Refused check-in records audit event without incrementing entry_count."""
    now = datetime.now(timezone.utc)
    passes_repo = _FakePassesRepo(
        row=_pass_row(
            valid_from=now - timedelta(days=2),
            valid_until=now - timedelta(days=1),
        )
    )
    events_repo = _FakeEventsRepo()
    svc = _service(passes_repo=passes_repo, events_repo=events_repo)
    body = CheckInRequest(
        gate_id="gate-1",
        entry_method=PassEntryMethod.CODE,
        access_status=PassAccessStatus.APPROVED,
    )
    with pytest.raises(ValidationException):
        await svc.check_in(pass_id="pass-1", body=body)
    assert events_repo.insert_calls
    assert events_repo.insert_calls[-1]["access_status"] == PassAccessStatus.EXPIRED.value
    assert not passes_repo.increment_calls


@pytest.mark.asyncio
async def test_check_in_granted_override():
    """Granted override allows check-in even when pass is expired."""
    now = datetime.now(timezone.utc)
    passes_repo = _FakePassesRepo(
        row=_pass_row(
            valid_from=now - timedelta(days=2),
            valid_until=now - timedelta(days=1),
        )
    )
    events_repo = _FakeEventsRepo()
    svc = _service(passes_repo=passes_repo, events_repo=events_repo)
    body = CheckInRequest(
        gate_id="gate-1",
        entry_method=PassEntryMethod.MANUAL,
        access_status=PassAccessStatus.GRANTED,
    )
    result = await svc.check_in(pass_id="pass-1", body=body)
    assert result["entry_count"] == 1
    assert events_repo.insert_calls[-1]["access_status"] == PassAccessStatus.GRANTED.value


@pytest.mark.asyncio
async def test_check_in_max_entries():
    """Max entries guard records refusal and raises validation error."""
    passes_repo = _FakePassesRepo(row=_pass_row(max_entries=1, entry_count=1))
    events_repo = _FakeEventsRepo()
    svc = _service(passes_repo=passes_repo, events_repo=events_repo)
    body = CheckInRequest(
        gate_id="gate-1",
        entry_method=PassEntryMethod.QR,
        access_status=PassAccessStatus.APPROVED,
    )
    with pytest.raises(ValidationException):
        await svc.check_in(pass_id="pass-1", body=body)
    assert not passes_repo.increment_calls


@pytest.mark.asyncio
async def test_check_out_requires_open_check_in():
    """Check-out fails when there is no open check-in."""
    svc = _service(events_repo=_FakeEventsRepo(has_open_check_in=False))
    body = CheckOutRequest(gate_id="gate-1")
    with pytest.raises(ValidationException):
        await svc.check_out(pass_id="pass-1", body=body)


@pytest.mark.asyncio
async def test_check_out_completes_one_time_pass():
    """Check-out completes a one-time pass."""
    passes_repo = _FakePassesRepo()
    events_repo = _FakeEventsRepo(has_open_check_in=True)
    svc = _service(passes_repo=passes_repo, events_repo=events_repo)
    body = CheckOutRequest(gate_id="gate-1")
    result = await svc.check_out(pass_id="pass-1", body=body)
    assert result["pass_status"] == PassStatus.COMPLETED.value
    assert passes_repo.complete_calls
    assert events_repo.insert_calls[-1]["event_type"] == PassEventType.CHECKED_OUT.value
