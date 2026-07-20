"""Gate pass verification schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from apps.user_service.app.schemas.enums import PassAccessStatus, PassEntryMethod


class VerifyPassRequest(BaseModel):
    """Lookup a pass by 4-digit code at the gate."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=4, max_length=4, pattern=r"^\d{4}$")
    gate_id: str | None = None


class VerifyPassResponse(BaseModel):
    """Pass snapshot shown to the operator before admitting."""

    model_config = ConfigDict(extra="ignore")

    pass_id: str
    code: str
    guest_name: str
    guest_phone: str | None = None
    visitor_count: int
    vehicle_number: str | None = None
    pass_type: str
    unit_label: str | None = None
    tower_name: str | None = None
    host_name: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    is_private: bool = False
    access_status: str
    can_check_in: bool
    too_early: bool = False


class CheckInRequest(BaseModel):
    """Record guest entry at the gate."""

    model_config = ConfigDict(extra="forbid")

    gate_id: str
    entry_method: PassEntryMethod
    access_status: PassAccessStatus
    notes: str | None = Field(None, max_length=1000)


class CheckOutRequest(BaseModel):
    """Record guest exit at the gate."""

    model_config = ConfigDict(extra="forbid")

    gate_id: str
    notes: str | None = Field(None, max_length=1000)


class GatePassEventResponse(BaseModel):
    """Gate check-in/out event payload."""

    model_config = ConfigDict(extra="ignore")

    id: str
    event_type: str
    gate_id: str | None = None
    actor_type: str | None = None
    actor_user_id: str | None = None
    actor_label: str | None = None
    occurred_at: str | None = None
    notes: str | None = None
    entry_method: str | None = None
    access_status: str | None = None


class CheckInResponse(BaseModel):
    """Check-in result."""

    model_config = ConfigDict(extra="ignore")

    event: GatePassEventResponse
    entry_count: int
    pass_status: str


class CheckOutResponse(BaseModel):
    """Check-out result."""

    model_config = ConfigDict(extra="ignore")

    event: GatePassEventResponse
    pass_status: str
