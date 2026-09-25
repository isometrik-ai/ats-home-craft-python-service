"""Lifecycle transition rules for facility reservations."""

from apps.user_service.app.schemas.enums import FacilityReservationStatus
from apps.user_service.app.services.facility_booking.lifecycle import (
    ReservationAction,
    can_transition,
    exclusion_key,
    initial_status,
    target_status,
)


def test_initial_status_depends_on_approval():
    assert initial_status(True) == FacilityReservationStatus.PENDING_APPROVAL.value
    assert initial_status(False) == FacilityReservationStatus.CONFIRMED.value


def test_resident_cannot_cancel_completed():
    assert not can_transition(
        ReservationAction.RESIDENT_CANCEL, FacilityReservationStatus.COMPLETED.value
    )
    assert can_transition(
        ReservationAction.RESIDENT_CANCEL, FacilityReservationStatus.CONFIRMED.value
    )


def test_approve_only_from_pending():
    assert can_transition(
        ReservationAction.APPROVE, FacilityReservationStatus.PENDING_APPROVAL.value
    )
    assert not can_transition(ReservationAction.APPROVE, FacilityReservationStatus.CONFIRMED.value)
    assert target_status(ReservationAction.APPROVE) == FacilityReservationStatus.CONFIRMED.value


def test_exclusion_key_by_archetype():
    assert exclusion_key("slot", "fac-1", "unit-1") == "unit-1"
    assert exclusion_key("duration", "fac-1", None) == "fac-1"
    assert exclusion_key("tee_time", "fac-1", None) is None
    assert exclusion_key("day_range", "fac-1", None) is None
