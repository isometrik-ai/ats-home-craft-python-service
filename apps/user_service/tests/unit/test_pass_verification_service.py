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


@pytest.fixture(autouse=True)
def patch_pass_event_actor_enrichment(monkeypatch):
    """Avoid DB lookups when resolving pass event actor labels in unit tests."""

    async def fake_enrich(**kwargs):
        return [{**event, "actor_label": "Test Guard"} for event in kwargs["events"]]

    monkeypatch.setattr(
        "apps.user_service.app.services.pass_verification_service.enrich_pass_event_actor_labels",
        fake_enrich,
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


class _FakeContactsRepo:
    """Map contact ids to push recipients for notification tests."""

    def __init__(self, contacts: dict[str, dict[str, Any]] | None = None) -> None:
        self.contacts = contacts or {
            "contact-1": {"user_id": "user-host", "additional_data": {}},
            "owner-1": {"user_id": "user-owner", "additional_data": {}},
            "tenant-1": {"user_id": "user-tenant", "additional_data": {}},
        }

    async def get_contact_for_update(
        self,
        *,
        contact_id: str,
        organization_id: str,
    ) -> dict[str, Any] | None:
        del organization_id
        return self.contacts.get(contact_id)


class _FakeUnitsRepo:
    """Return Owner/Tenant occupants for pass notification tests."""

    def __init__(
        self, occupants: dict[str, dict[str, dict[str, Any] | None]] | None = None
    ) -> None:
        self.occupants = occupants or {
            "unit-1": {
                "owner": {"contact_id": "owner-1"},
                "tenant": {"contact_id": "tenant-1"},
            }
        }

    async def get_unit_role_occupants_batch(
        self,
        *,
        organization_id: str,
        unit_ids: list[str],
    ) -> dict[str, dict[str, dict[str, Any] | None]]:
        del organization_id
        return {
            unit_id: self.occupants.get(unit_id, {"owner": None, "tenant": None})
            for unit_id in unit_ids
        }


class _FakePushDispatcher:
    def __init__(self, contacts_repo: _FakeContactsRepo | None = None) -> None:
        self.send_to_user_calls: list[dict[str, object]] = []
        self.contacts_repo = contacts_repo or _FakeContactsRepo()

    async def send_to_user(self, **kwargs):
        self.send_to_user_calls.append(kwargs)
        return None

    async def send_to_contact(self, **kwargs):
        del kwargs
        return None

    async def send_to_unit_residents(self, **kwargs):
        del kwargs
        return 1


def _service(
    *,
    passes_repo: _FakePassesRepo | None = None,
    events_repo: _FakeEventsRepo | None = None,
    towers_repo: _FakeTowersRepo | None = None,
    units_repo: _FakeUnitsRepo | None = None,
    push_dispatcher: _FakePushDispatcher | None = None,
) -> PassVerificationService:
    """Build PassVerificationService with fake repositories."""
    svc = PassVerificationService(
        db_connection=MagicMock(),
        user_context=_user_context(),
    )
    svc.passes_repo = passes_repo or _FakePassesRepo()
    svc.events_repo = events_repo or _FakeEventsRepo()
    svc.towers_repo = towers_repo or _FakeTowersRepo()
    svc.units_repo = units_repo or _FakeUnitsRepo()
    svc._push_dispatcher = push_dispatcher or _FakePushDispatcher()
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
    assert result["validity_type"] == PassValidityType.ONE_TIME.value


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
    """Verify flags a pass whose validity has not started yet (future calendar day)."""
    now = datetime.now(timezone.utc)
    passes_repo = _FakePassesRepo(
        row=_pass_row(
            valid_from=now + timedelta(days=1),
            valid_until=now + timedelta(days=2),
        )
    )
    svc = _service(passes_repo=passes_repo)
    result = await svc.verify(code="4821")
    assert result["access_status"] == PassAccessStatus.APPROVED.value
    assert result["can_check_in"] is False
    assert result["too_early"] is True


@pytest.mark.asyncio
async def test_verify_same_day_before_start_time():
    """Verify allows check-in on the valid day even before valid_from time."""
    now = datetime.now(timezone.utc)
    passes_repo = _FakePassesRepo(
        row=_pass_row(
            valid_from=now + timedelta(minutes=3),
            valid_until=now + timedelta(hours=8),
        )
    )
    svc = _service(passes_repo=passes_repo)
    result = await svc.verify(code="4821")
    assert result["access_status"] == PassAccessStatus.APPROVED.value
    assert result["can_check_in"] is True
    assert result["too_early"] is False


@pytest.mark.asyncio
async def test_check_in_same_day_before_start_time():
    """Check-in succeeds on the valid day even before valid_from time."""
    now = datetime.now(timezone.utc)
    passes_repo = _FakePassesRepo(
        row=_pass_row(
            valid_from=now + timedelta(minutes=3),
            valid_until=now + timedelta(hours=8),
        )
    )
    events_repo = _FakeEventsRepo()
    svc = _service(passes_repo=passes_repo, events_repo=events_repo)
    body = CheckInRequest(
        gate_id="gate-1",
        entry_method=PassEntryMethod.QR,
        access_status=PassAccessStatus.APPROVED,
    )
    result = await svc.check_in(pass_id="pass-1", body=body)
    assert result["entry_count"] == 1


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
    assert events_repo.insert_calls[-1]["actor_user_id"] == "staff-1"
    assert "actor_label" not in events_repo.insert_calls[-1]
    assert passes_repo.increment_calls


@pytest.mark.asyncio
async def test_check_in_without_gate_id():
    """Check-in succeeds when gate_id is omitted."""
    events_repo = _FakeEventsRepo()
    passes_repo = _FakePassesRepo()
    svc = _service(passes_repo=passes_repo, events_repo=events_repo)
    body = CheckInRequest(
        entry_method=PassEntryMethod.QR,
        access_status=PassAccessStatus.APPROVED,
    )
    result = await svc.check_in(pass_id="pass-1", body=body)
    assert result["entry_count"] == 1
    assert events_repo.insert_calls[-1]["gate_id"] is None


@pytest.mark.asyncio
async def test_check_out_without_gate_id():
    """Check-out succeeds when gate_id is omitted."""
    events_repo = _FakeEventsRepo(has_open_check_in=True)
    passes_repo = _FakePassesRepo()
    svc = _service(passes_repo=passes_repo, events_repo=events_repo)
    body = CheckOutRequest()
    result = await svc.check_out(pass_id="pass-1", body=body)
    assert result["pass_status"]
    assert events_repo.insert_calls[-1]["gate_id"] is None


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


@pytest.mark.asyncio
async def test_check_in_notifies_pass_host_and_unit_occupants():
    """Check-in push goes to pass host plus Owner/Tenant on the unit."""
    push = _FakePushDispatcher()
    svc = _service(events_repo=_FakeEventsRepo(), push_dispatcher=push)
    body = CheckInRequest(
        entry_method=PassEntryMethod.QR,
        access_status=PassAccessStatus.APPROVED,
    )
    await svc.check_in(pass_id="pass-1", body=body)
    assert len(push.send_to_user_calls) == 3
    recipient_user_ids = {call["recipient_user_id"] for call in push.send_to_user_calls}
    assert recipient_user_ids == {"user-host", "user-owner", "user-tenant"}
    assert push.send_to_user_calls[0]["message_key"] == "notifications.push.pass.checked_in"


@pytest.mark.asyncio
async def test_check_in_dedupes_host_when_also_owner():
    """Host is not notified twice when they are also the unit Owner."""
    contacts = {
        "contact-1": {"user_id": "user-owner", "additional_data": {}},
        "owner-1": {"user_id": "user-owner", "additional_data": {}},
        "tenant-1": {"user_id": "user-tenant", "additional_data": {}},
    }
    push = _FakePushDispatcher(contacts_repo=_FakeContactsRepo(contacts))
    units = _FakeUnitsRepo(
        occupants={
            "unit-1": {
                "owner": {"contact_id": "owner-1"},
                "tenant": {"contact_id": "tenant-1"},
            }
        }
    )
    passes_repo = _FakePassesRepo(row=_pass_row(host_contact_id="contact-1"))
    svc = _service(
        passes_repo=passes_repo,
        events_repo=_FakeEventsRepo(),
        units_repo=units,
        push_dispatcher=push,
    )
    body = CheckInRequest(
        entry_method=PassEntryMethod.QR,
        access_status=PassAccessStatus.APPROVED,
    )
    await svc.check_in(pass_id="pass-1", body=body)
    recipient_user_ids = {call["recipient_user_id"] for call in push.send_to_user_calls}
    assert recipient_user_ids == {"user-owner", "user-tenant"}
    assert len(push.send_to_user_calls) == 2


@pytest.mark.asyncio
async def test_check_in_skips_push_when_private():
    """Private passes do not notify anyone on check-in."""
    push = _FakePushDispatcher()
    passes_repo = _FakePassesRepo(row=_pass_row(is_private=True))
    svc = _service(passes_repo=passes_repo, events_repo=_FakeEventsRepo(), push_dispatcher=push)
    body = CheckInRequest(
        entry_method=PassEntryMethod.QR,
        access_status=PassAccessStatus.APPROVED,
    )
    await svc.check_in(pass_id="pass-1", body=body)
    assert push.send_to_user_calls == []
