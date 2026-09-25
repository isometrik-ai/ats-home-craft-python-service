"""Enumeration values for the facility booking domain."""

from enum import Enum

from apps.user_service.app.schemas.enums.property import FacilityType


class FacilityBookingArchetype(str, Enum):
    """How a facility is booked (Postgres facility_booking_archetype enum)."""

    SLOT = "slot"
    TEE_TIME = "tee_time"
    DURATION = "duration"
    DAY_RANGE = "day_range"
    ROOM = "room"


class FacilityReservationStatus(str, Enum):
    """Reservation lifecycle (Postgres facility_reservation_status enum)."""

    PENDING_APPROVAL = "pending_approval"
    CONFIRMED = "confirmed"
    CHECKED_IN = "checked_in"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"
    RESCHEDULED = "rescheduled"


class FacilityReservationEventType(str, Enum):
    """Reservation audit trail entry (Postgres facility_reservation_event_type enum)."""

    CREATED = "created"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    RESCHEDULED = "rescheduled"
    CHECKED_IN = "checked_in"
    COMPLETED = "completed"
    NO_SHOW = "no_show"
    NOTE = "note"


class FacilityReservationActorType(str, Enum):
    """Who performed a reservation action (Postgres facility_reservation_actor_type enum)."""

    STAFF = "staff"
    RESIDENT = "resident"
    SYSTEM = "system"


class FacilityParticipantKind(str, Enum):
    """Participant kind (Postgres facility_reservation_participant_kind enum)."""

    RESIDENT = "resident"
    GUEST = "guest"


class FacilityRateUnit(str, Enum):
    """Unit the base resident rate is quoted in."""

    HOUR = "hour"
    PLAYER = "player"
    DAY = "day"
    MONTH = "month"


class FacilityPriceMode(str, Enum):
    """Effective pricing mode derived from the pricing toggles."""

    INCLUDED = "included"
    FIXED = "fixed"
    RULE_BASED = "rule_based"


class FacilityMembershipMode(str, Enum):
    """Whether membership covers the booking or bills it on the invoice."""

    INCLUDED = "included"
    EXCLUDED = "excluded"


class FacilityReservationListTab(str, Enum):
    """Resident reservation list filter."""

    UPCOMING = "upcoming"
    PAST = "past"
    ALL = "all"


class FacilityBookingLedgerType(str, Enum):
    """Booking ledger entry (Postgres facility_booking_ledger_type enum)."""

    CHARGE = "charge"
    DEPOSIT = "deposit"
    BALANCE = "balance"
    ADJUSTMENT = "adjustment"
    CANCELLATION_FEE = "cancellation_fee"
    NO_SHOW_FORFEIT = "no_show_forfeit"
    MANUAL_CHARGE = "manual_charge"
    REFUND = "refund"
    PAYMENT = "payment"


UNBILLED_LEDGER_TYPES: frozenset[str] = frozenset(
    {
        FacilityBookingLedgerType.CHARGE.value,
        FacilityBookingLedgerType.DEPOSIT.value,
        FacilityBookingLedgerType.BALANCE.value,
        FacilityBookingLedgerType.ADJUSTMENT.value,
        FacilityBookingLedgerType.CANCELLATION_FEE.value,
        FacilityBookingLedgerType.NO_SHOW_FORFEIT.value,
        FacilityBookingLedgerType.MANUAL_CHARGE.value,
        FacilityBookingLedgerType.REFUND.value,
    }
)


class FacilityBookingInvoiceStatus(str, Enum):
    """Facility booking invoice status."""

    ISSUED = "issued"
    PAID = "paid"


class FacilityBookingInvoiceFrequency(str, Enum):
    """Facility booking invoice frequency."""

    WEEKLY = "weekly"
    FORTNIGHTLY = "fortnightly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


class FacilityBookingPaymentMethod(str, Enum):
    """Facility booking payment method."""

    WALLET = "wallet"
    CASH = "cash"
    ONLINE = "online"


class FacilityBookingWalletTxnType(str, Enum):
    """Facility booking wallet txn type."""

    TOPUP = "topup"
    PAYMENT = "payment"
    ADJUSTMENT = "adjustment"


INVOICEABLE_LEDGER_TYPES: frozenset[str] = frozenset(
    {
        FacilityBookingLedgerType.CHARGE.value,
        FacilityBookingLedgerType.DEPOSIT.value,
        FacilityBookingLedgerType.BALANCE.value,
        FacilityBookingLedgerType.ADJUSTMENT.value,
        FacilityBookingLedgerType.CANCELLATION_FEE.value,
        FacilityBookingLedgerType.NO_SHOW_FORFEIT.value,
        FacilityBookingLedgerType.MANUAL_CHARGE.value,
    }
)

INVOICE_FREQUENCY_DAYS: dict[str, int] = {
    FacilityBookingInvoiceFrequency.WEEKLY.value: 7,
    FacilityBookingInvoiceFrequency.FORTNIGHTLY.value: 14,
    FacilityBookingInvoiceFrequency.MONTHLY.value: 30,
    FacilityBookingInvoiceFrequency.QUARTERLY.value: 90,
}

LEDGER_ACTIVITY_LABELS: dict[str, str] = {
    FacilityBookingLedgerType.CHARGE.value: "Booking charge",
    FacilityBookingLedgerType.DEPOSIT.value: "Deposit",
    FacilityBookingLedgerType.BALANCE.value: "Balance on check-in",
    FacilityBookingLedgerType.ADJUSTMENT.value: "Price adjustment",
    FacilityBookingLedgerType.CANCELLATION_FEE.value: "Cancellation fee",
    FacilityBookingLedgerType.NO_SHOW_FORFEIT.value: "No-show forfeit",
    FacilityBookingLedgerType.MANUAL_CHARGE.value: "Manual charge",
    FacilityBookingLedgerType.REFUND.value: "Refund",
    FacilityBookingLedgerType.PAYMENT.value: "Payment",
}


ACTIVE_RESERVATION_STATUSES: tuple[str, ...] = (
    FacilityReservationStatus.PENDING_APPROVAL.value,
    FacilityReservationStatus.CONFIRMED.value,
    FacilityReservationStatus.CHECKED_IN.value,
)

WEEKLY_CAP_COUNTABLE_STATUSES: tuple[str, ...] = (
    *ACTIVE_RESERVATION_STATUSES,
    FacilityReservationStatus.COMPLETED.value,
)

# Facility types that can never be booked through the facility booking module.
NON_BOOKABLE_FACILITY_TYPES: frozenset[str] = frozenset(
    {FacilityType.PARKING.value, FacilityType.UTILITY.value}
)

FACILITY_BOOKING_MAX_UNITS = 50
FACILITY_BOOKING_MAX_PARTICIPANTS = 500
FACILITY_BOOKING_MAX_RANGE_DAYS = 60
FACILITY_BOOKING_MONTH_VIEW_DAYS = 42
FACILITY_BOOKING_POLICY_DOCUMENT_MAX_BYTES = 10 * 1024 * 1024
