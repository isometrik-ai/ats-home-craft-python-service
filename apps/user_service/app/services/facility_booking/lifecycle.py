"""Reservation status transitions and derived lifecycle rules."""

from __future__ import annotations

from enum import Enum

from apps.user_service.app.schemas.enums import FacilityReservationStatus as Status


class ReservationAction(str, Enum):
    """Staff or resident actions that move a reservation through its lifecycle."""

    APPROVE = "approve"
    REJECT = "reject"
    CHECK_IN = "check_in"
    COMPLETE = "complete"
    NO_SHOW = "no_show"
    RESIDENT_CANCEL = "resident_cancel"
    STAFF_CANCEL = "staff_cancel"
    RESIDENT_RESCHEDULE = "resident_reschedule"
    STAFF_RESCHEDULE = "staff_reschedule"


_ALLOWED_FROM: dict[ReservationAction, frozenset[str]] = {
    ReservationAction.APPROVE: frozenset({Status.PENDING_APPROVAL.value}),
    ReservationAction.REJECT: frozenset({Status.PENDING_APPROVAL.value}),
    ReservationAction.CHECK_IN: frozenset({Status.CONFIRMED.value}),
    ReservationAction.COMPLETE: frozenset({Status.CHECKED_IN.value}),
    ReservationAction.NO_SHOW: frozenset({Status.CONFIRMED.value, Status.CHECKED_IN.value}),
    ReservationAction.RESIDENT_CANCEL: frozenset(
        {Status.PENDING_APPROVAL.value, Status.CONFIRMED.value}
    ),
    ReservationAction.STAFF_CANCEL: frozenset(
        {Status.PENDING_APPROVAL.value, Status.CONFIRMED.value, Status.CHECKED_IN.value}
    ),
    ReservationAction.RESIDENT_RESCHEDULE: frozenset(
        {Status.PENDING_APPROVAL.value, Status.CONFIRMED.value}
    ),
    ReservationAction.STAFF_RESCHEDULE: frozenset(
        {Status.PENDING_APPROVAL.value, Status.CONFIRMED.value}
    ),
}

_TARGET: dict[ReservationAction, str] = {
    ReservationAction.APPROVE: Status.CONFIRMED.value,
    ReservationAction.REJECT: Status.REJECTED.value,
    ReservationAction.CHECK_IN: Status.CHECKED_IN.value,
    ReservationAction.COMPLETE: Status.COMPLETED.value,
    ReservationAction.NO_SHOW: Status.NO_SHOW.value,
    ReservationAction.RESIDENT_CANCEL: Status.CANCELLED.value,
    ReservationAction.STAFF_CANCEL: Status.CANCELLED.value,
    ReservationAction.RESIDENT_RESCHEDULE: Status.RESCHEDULED.value,
    ReservationAction.STAFF_RESCHEDULE: Status.RESCHEDULED.value,
}


def can_transition(action: ReservationAction, current_status: str) -> bool:
    """Return whether ``action`` is allowed from ``current_status``."""
    return current_status in _ALLOWED_FROM[action]


def target_status(action: ReservationAction) -> str:
    """Return the status a reservation reaches after ``action``."""
    return _TARGET[action]


def initial_status(requires_approval: bool) -> str:
    """Return the status assigned when a new reservation is created."""
    return Status.PENDING_APPROVAL.value if requires_approval else Status.CONFIRMED.value


def exclusion_key(archetype: str, facility_id: str, unit_id: str | None) -> str | None:
    """Key for the DB overlap guard (see facility_reservations_no_overlap)."""
    if archetype in ("slot", "room"):
        return unit_id
    if archetype == "duration":
        return facility_id
    return None
