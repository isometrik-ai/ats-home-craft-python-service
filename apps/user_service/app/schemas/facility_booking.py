"""Facility booking schemas: availability views, quotes and reservation payloads."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.user_service.app.schemas.enums import (
    FACILITY_BOOKING_MAX_PARTICIPANTS,
    FacilityBookingArchetype,
    FacilityBookingInvoiceStatus,
    FacilityBookingLedgerType,
    FacilityBookingPaymentMethod,
    FacilityBookingWalletTxnType,
    FacilityParticipantKind,
    FacilityReservationActorType,
    FacilityReservationEventType,
    FacilityReservationListTab,
    FacilityReservationStatus,
)
from apps.user_service.app.schemas.facility_booking_config import (
    MINUTES_PER_DAY,
    CancellationTier,
    FacilityBookingConfigResponse,
)
from apps.user_service.app.schemas.facility_booking_inventory import (
    BookingUnitResponse,
    ClosureResponse,
    MaintenanceWindowResponse,
    ProjectBookingSettingsResponse,
    SchedulePeriodResponse,
    SlotBlockResponse,
    StaffAssignmentResponse,
)

# ---------------------------------------------------------------------------
# Engine outputs
# ---------------------------------------------------------------------------


class PriceLine(BaseModel):
    """Price line."""

    label: str
    detail: str | None = None
    amount: int


class PriceQuote(BaseModel):
    """Price quote."""

    lines: list[PriceLine] = Field(default_factory=list)
    total: int = 0
    deposit: int = 0
    due_now: int = 0
    due_later: int = 0


class CancelQuote(BaseModel):
    """Cancel quote."""

    tier: CancellationTier | None = None
    hours_before: float
    paid: int
    fee: int
    refund: int
    free: bool


class BookingUnitSummary(BaseModel):
    """Booking unit summary."""

    id: str
    name: str
    room_type: str | None = None


class BusyInterval(BaseModel):
    """Busy interval."""

    start_min: int
    end_min: int
    label: str


class SlotView(BaseModel):
    """Slot view."""

    start_min: int
    end_min: int
    state: str
    reason: str | None = None
    remaining: int | None = None


class UnitDayView(BaseModel):
    """Unit day view."""

    unit: BookingUnitSummary
    slots: list[SlotView]


class MaintenanceWindowView(BaseModel):
    """Maintenance window view."""

    id: str
    on_date: date
    from_min: int
    to_min: int
    note: str


class DayAvailability(BaseModel):
    """Day availability."""

    local_date: date
    open_min: int
    close_min: int
    closed: bool
    blackout: bool
    closure_reason: str | None = None
    past: bool
    maintenance: list[MaintenanceWindowView]
    units: list[UnitDayView]
    busy: list[BusyInterval]


class MonthDayView(BaseModel):
    """Month day view."""

    local_date: date
    state: str
    note: str | None = None


class DaySummary(BaseModel):
    """Day summary."""

    local_date: date
    state: str
    note: str | None = None
    total_slots: float
    available_slots: float
    booked_slots: float
    blocked_slots: int


class RoomAvailabilityItem(BaseModel):
    """Room availability item."""

    unit: BookingUnitSummary
    status: str
    detail: str


class WeeklyUsageResponse(BaseModel):
    """Weekly usage response."""

    usage: int
    cap: int
    week_start: date


class NextAvailabilityResponse(BaseModel):
    """Next availability response."""

    local_date: date
    start_min: int | None = None
    unit_id: str | None = None


class ValidationIssueResponse(BaseModel):
    """Validation issue response."""

    code: str
    message: str


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class ParticipantInput(BaseModel):
    """Co-player / attendee. Residents reference a contact; guests carry a name."""

    model_config = ConfigDict(extra="forbid")

    kind: FacilityParticipantKind
    contact_id: str | None = None
    name: str | None = Field(None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_kind_fields(self) -> ParticipantInput:
        """Require contact_id for residents and name for guests."""
        if self.kind == FacilityParticipantKind.RESIDENT and not self.contact_id:
            raise ValueError("contact_id is required for resident participants")
        if self.kind == FacilityParticipantKind.GUEST and not (self.name and self.name.strip()):
            raise ValueError("name is required for guest participants")
        return self


class _TimeWindow(BaseModel):
    """Shared date and minute fields for draft and reschedule requests."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str | None = None
    local_date: date
    end_local_date: date | None = None
    start_min: int = Field(..., ge=0, le=MINUTES_PER_DAY)
    end_min: int = Field(..., ge=0, le=MINUTES_PER_DAY)

    @property
    def resolved_end_date(self) -> date:
        """Return end_local_date when set, otherwise local_date."""
        return self.end_local_date or self.local_date


class ReservationDraftRequest(_TimeWindow):
    """Draft used for quotes/validation and as the base of create requests."""

    facility_id: str
    participants: list[ParticipantInput] = Field(
        default_factory=list, max_length=FACILITY_BOOKING_MAX_PARTICIPANTS
    )


class ResidentReservationDraftRequest(ReservationDraftRequest):
    """Resident quote preview."""


class StaffReservationDraftRequest(ReservationDraftRequest):
    """Staff quote preview on behalf of a resident."""

    host_contact_id: str


class CreateResidentReservationRequest(ReservationDraftRequest):
    """Create resident reservation request."""

    host_unit_id: str | None = None
    notes: str | None = Field(None, max_length=1000)


class CreateStaffReservationRequest(ReservationDraftRequest):
    """Create staff reservation request."""

    host_contact_id: str
    host_unit_id: str | None = None
    notes: str | None = Field(None, max_length=1000)


class RescheduleReservationRequest(_TimeWindow):
    """Reschedule reservation request."""

    participants: list[ParticipantInput] | None = Field(
        None, max_length=FACILITY_BOOKING_MAX_PARTICIPANTS
    )


class CancelReservationRequest(BaseModel):
    """Cancel reservation request."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(None, max_length=500)


class RejectReservationRequest(BaseModel):
    """Reject reservation request."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=500)


class ReservationNoteRequest(BaseModel):
    """Reservation note request."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(..., min_length=1, max_length=1000)


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class CancelInfo(BaseModel):
    """Cancel info."""

    fee: int
    refund: int
    at: datetime
    reason: str | None = None
    by: str | None = None


class ReservationParticipantResponse(BaseModel):
    """Reservation participant response."""

    kind: FacilityParticipantKind
    contact_id: str | None = None
    name: str


class ReservationEventResponse(BaseModel):
    """Reservation event response."""

    id: str
    event_type: FacilityReservationEventType
    actor_type: FacilityReservationActorType
    actor_user_id: str | None = None
    actor_contact_id: str | None = None
    message: str
    occurred_at: datetime


class FacilityReservationResponse(BaseModel):
    """Facility reservation response."""

    id: str
    facility_id: str
    facility_name: str
    archetype: FacilityBookingArchetype
    unit_id: str | None = None
    unit_name: str | None = None
    host_contact_id: str
    host_name: str
    host_unit_id: str | None = None
    local_date: date
    end_local_date: date
    start_min: int
    end_min: int
    starts_at: datetime
    ends_at: datetime
    status: FacilityReservationStatus
    quote: PriceQuote
    participants: list[ReservationParticipantResponse] = Field(default_factory=list)
    rescheduled_from_id: str | None = None
    rescheduled_to_id: str | None = None
    reject_reason: str | None = None
    cancel_info: CancelInfo | None = None
    notes: str | None = None
    booked_by_actor: FacilityReservationActorType
    reschedule_cutoff_hours: int
    no_show_fee_percent: int
    can_reschedule: bool = False
    reschedule_blocked_reason: str | None = None
    created_at: datetime
    approved_at: datetime | None = None
    checked_in_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    events: list[ReservationEventResponse] | None = None


class DraftEvaluationResponse(BaseModel):
    """Draft evaluation response."""

    ok: bool
    errors: list[ValidationIssueResponse]
    quote: PriceQuote | None = None


class BookableFacilityResponse(BaseModel):
    """Resident/staff catalog card for a bookable facility."""

    id: str
    name: str
    facility_type: str
    archetype: FacilityBookingArchetype
    description: str
    slot_minutes: int
    accepting_bookings: bool
    requires_approval: bool
    advance_booking_days: int
    price_mode: str
    resident_rate: int
    unit_label: str
    units: list[BookingUnitSummary] = Field(default_factory=list)
    tower_id: str | None = None
    location_notes: str | None = None


class StaffReservationListQuery(BaseModel):
    """Staff reservation list query."""

    model_config = ConfigDict(extra="forbid")

    facility_id: str | None = None
    status: FacilityReservationStatus | None = None
    contact_id: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    search: str | None = Field(None, min_length=1, max_length=120)
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


class ResidentReservationListQuery(BaseModel):
    """Resident reservation list query."""

    model_config = ConfigDict(extra="forbid")

    tab: FacilityReservationListTab = FacilityReservationListTab.UPCOMING
    facility_id: str | None = None
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


class LedgerEntryResponse(BaseModel):
    """Ledger entry response."""

    id: str
    contact_id: str
    contact_name: str | None = None
    reservation_id: str | None = None
    facility_name: str | None = None
    invoice_id: str | None = None
    entry_type: FacilityBookingLedgerType
    activity_label: str
    description: str
    amount: int
    method: str | None = None
    posted_at: datetime


class LedgerStatementResponse(BaseModel):
    """Ledger statement response."""

    entries: list[LedgerEntryResponse]
    balance: int


class UnbilledContactResponse(BaseModel):
    """Unbilled contact response."""

    contact_id: str
    contact_name: str
    amount: int
    count: int
    oldest_at: datetime


class UnbilledLedgerResponse(BaseModel):
    """Unbilled ledger response."""

    unbilled_total: int
    unbilled_contacts: int
    contacts: list[UnbilledContactResponse]
    entries: list[LedgerEntryResponse]


class RaiseChargeRequest(BaseModel):
    """Raise charge request."""

    model_config = ConfigDict(extra="forbid")

    contact_id: str
    reservation_id: str | None = None
    description: str = Field(..., min_length=4, max_length=240)
    amount: float = Field(..., gt=0)


class InvoiceLineResponse(BaseModel):
    """Invoice line response."""

    description: str
    amount: int
    reservation_id: str | None = None


class BookingInvoiceResponse(BaseModel):
    """Booking invoice response."""

    id: str
    contact_id: str
    contact_name: str | None = None
    number: str
    status: FacilityBookingInvoiceStatus
    lines: list[InvoiceLineResponse] = Field(default_factory=list)
    total: int
    period_label: str
    due_date: date
    paid_at: datetime | None = None
    paid_via: FacilityBookingPaymentMethod | None = None
    payment_ref: str | None = None
    collected_by: str | None = None
    notes: str | None = None
    created_at: datetime | None = None


class GenerateInvoicesRequest(BaseModel):
    """Generate invoices request."""

    model_config = ConfigDict(extra="forbid")

    contact_ids: list[str] | None = None


class PayInvoiceRequest(BaseModel):
    """Pay invoice request."""

    model_config = ConfigDict(extra="forbid")

    method: FacilityBookingPaymentMethod
    reference: str | None = Field(None, max_length=80)
    collected_by: str | None = Field(None, max_length=120)
    notes: str | None = Field(None, max_length=500)


class WalletTransactionResponse(BaseModel):
    """Wallet transaction response."""

    id: str
    contact_id: str
    entry_type: FacilityBookingWalletTxnType
    amount: int
    method: FacilityBookingPaymentMethod | None = None
    description: str
    posted_at: datetime


class WalletResponse(BaseModel):
    """Wallet response."""

    contact_id: str
    contact_name: str | None = None
    balance: int
    credit_limit: int
    custom_limit: bool
    transactions: list[WalletTransactionResponse] = Field(default_factory=list)


class WalletTopUpRequest(BaseModel):
    """Wallet top up request."""

    model_config = ConfigDict(extra="forbid")

    contact_id: str
    amount: int = Field(..., gt=0)
    method: FacilityBookingPaymentMethod


class ResidentWalletTopUpRequest(BaseModel):
    """Resident wallet top up request."""

    model_config = ConfigDict(extra="forbid")

    amount: int = Field(..., gt=0)
    method: FacilityBookingPaymentMethod


class WalletAdjustRequest(BaseModel):
    """Wallet adjust request."""

    model_config = ConfigDict(extra="forbid")

    contact_id: str
    amount: int
    reason: str = Field(..., min_length=1, max_length=240)


class WalletLimitRequest(BaseModel):
    """Wallet limit request."""

    model_config = ConfigDict(extra="forbid")

    limit: int | None = Field(None, ge=0)


class PaymentOverviewResponse(BaseModel):
    """Payment overview response."""

    unbilled_total: int
    unbilled_contacts: int
    outstanding_total: int
    outstanding_count: int
    collected_this_month: int
    wallet_float: int
    collected_by_method: dict[str, int]
    invoices: list[BookingInvoiceResponse] = Field(default_factory=list)
    wallet_balances: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# OpenAPI response envelopes
# ---------------------------------------------------------------------------


class DeletedResourceResponse(BaseModel):
    """Identifier of a deleted inventory or assignment row."""

    id: str


class GenerateInvoicesResultResponse(BaseModel):
    """Result of batch invoice generation."""

    created: int
    amount: int
    invoices: list[BookingInvoiceResponse]


class FacilityBookingWorkspaceResponse(BaseModel):
    """Full staff/resident workspace for one bookable facility."""

    config: FacilityBookingConfigResponse
    units: list[BookingUnitResponse]
    schedules: list[SchedulePeriodResponse]
    slot_blocks: list[SlotBlockResponse]
    closures: list[ClosureResponse]
    maintenance: list[MaintenanceWindowResponse]
    unit_count: int


class BookableFacilityListApiResponse(BaseModel):
    """API envelope for facility catalog list."""

    status: str
    message: str
    statusCode: int
    code: str
    data: list[BookableFacilityResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class FacilityBookingWorkspaceApiResponse(BaseModel):
    """API envelope for facility booking workspace."""

    status: str
    message: str
    statusCode: int
    code: str
    data: FacilityBookingWorkspaceResponse


class BookingUnitApiResponse(BaseModel):
    """API envelope for a booking unit."""

    status: str
    message: str
    statusCode: int
    code: str
    data: BookingUnitResponse


class SchedulePeriodApiResponse(BaseModel):
    """API envelope for a schedule period."""

    status: str
    message: str
    statusCode: int
    code: str
    data: SchedulePeriodResponse


class SlotBlockApiResponse(BaseModel):
    """API envelope for a slot block."""

    status: str
    message: str
    statusCode: int
    code: str
    data: SlotBlockResponse


class ClosureApiResponse(BaseModel):
    """API envelope for a closure day."""

    status: str
    message: str
    statusCode: int
    code: str
    data: ClosureResponse


class MaintenanceWindowApiResponse(BaseModel):
    """API envelope for a maintenance window."""

    status: str
    message: str
    statusCode: int
    code: str
    data: MaintenanceWindowResponse


class DeletedResourceApiResponse(BaseModel):
    """API envelope for delete operations returning an id."""

    status: str
    message: str
    statusCode: int
    code: str
    data: DeletedResourceResponse


class DayAvailabilityApiResponse(BaseModel):
    """API envelope for day slot availability."""

    status: str
    message: str
    statusCode: int
    code: str
    data: DayAvailability


class MonthOverviewListApiResponse(BaseModel):
    """API envelope for month calendar overview."""

    status: str
    message: str
    statusCode: int
    code: str
    data: list[MonthDayView]


class MonthSummaryListApiResponse(BaseModel):
    """API envelope for month availability summary."""

    status: str
    message: str
    statusCode: int
    code: str
    data: list[DaySummary]


class NextAvailabilityApiResponse(BaseModel):
    """API envelope for next bookable slot (null when none)."""

    status: str
    message: str
    statusCode: int
    code: str
    data: NextAvailabilityResponse | None


class RoomAvailabilityListApiResponse(BaseModel):
    """API envelope for room stay availability."""

    status: str
    message: str
    statusCode: int
    code: str
    data: list[RoomAvailabilityItem]


class WeeklyUsageApiResponse(BaseModel):
    """API envelope for weekly usage against cap."""

    status: str
    message: str
    statusCode: int
    code: str
    data: WeeklyUsageResponse


class DraftEvaluationApiResponse(BaseModel):
    """API envelope for draft validation / quote preview."""

    status: str
    message: str
    statusCode: int
    code: str
    data: DraftEvaluationResponse


class FacilityReservationApiResponse(BaseModel):
    """API envelope for a single reservation."""

    status: str
    message: str
    statusCode: int
    code: str
    data: FacilityReservationResponse


class FacilityReservationListApiResponse(BaseModel):
    """API envelope for paginated reservation lists."""

    status: str
    message: str
    statusCode: int
    code: str
    data: list[FacilityReservationResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class StaffAssignmentApiResponse(BaseModel):
    """API envelope for a staff assignment."""

    status: str
    message: str
    statusCode: int
    code: str
    data: StaffAssignmentResponse


class StaffAssignmentListApiResponse(BaseModel):
    """API envelope for staff assignment list."""

    status: str
    message: str
    statusCode: int
    code: str
    data: list[StaffAssignmentResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class ProjectBookingSettingsApiResponse(BaseModel):
    """API envelope for project booking settings."""

    status: str
    message: str
    statusCode: int
    code: str
    data: ProjectBookingSettingsResponse


class LedgerEntryApiResponse(BaseModel):
    """API envelope for a single ledger entry."""

    status: str
    message: str
    statusCode: int
    code: str
    data: LedgerEntryResponse


class LedgerEntryListApiResponse(BaseModel):
    """API envelope for paginated ledger entry lists."""

    status: str
    message: str
    statusCode: int
    code: str
    data: list[LedgerEntryResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class LedgerStatementApiResponse(BaseModel):
    """API envelope for a contact ledger statement."""

    status: str
    message: str
    statusCode: int
    code: str
    data: LedgerStatementResponse


class UnbilledLedgerApiResponse(BaseModel):
    """API envelope for unbilled ledger totals."""

    status: str
    message: str
    statusCode: int
    code: str
    data: UnbilledLedgerResponse


class PaymentOverviewApiResponse(BaseModel):
    """API envelope for billing overview metrics."""

    status: str
    message: str
    statusCode: int
    code: str
    data: PaymentOverviewResponse


class BookingInvoiceApiResponse(BaseModel):
    """API envelope for a single invoice."""

    status: str
    message: str
    statusCode: int
    code: str
    data: BookingInvoiceResponse


class BookingInvoiceListApiResponse(BaseModel):
    """API envelope for paginated invoice lists."""

    status: str
    message: str
    statusCode: int
    code: str
    data: list[BookingInvoiceResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class GenerateInvoicesApiResponse(BaseModel):
    """API envelope for batch invoice generation."""

    status: str
    message: str
    statusCode: int
    code: str
    data: GenerateInvoicesResultResponse


class WalletApiResponse(BaseModel):
    """API envelope for a contact booking wallet."""

    status: str
    message: str
    statusCode: int
    code: str
    data: WalletResponse


class CancelQuoteApiResponse(BaseModel):
    """API envelope for cancellation fee preview."""

    status: str
    message: str
    statusCode: int
    code: str
    data: CancelQuote
