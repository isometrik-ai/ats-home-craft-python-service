"""Request and response models for app access control."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from apps.user_service.app.schemas.enums.app_access import (
    AppAccessMatchMode,
    AppAccessOverrideKind,
    AppAccessStatus,
)


class AppAccessImpact(BaseModel):
    """Counts for the staff Rule and Customers tabs."""

    model_config = ConfigDict(extra="forbid")

    blocked: int = 0
    watch: int = 0
    notdue: int = 0
    released: int = 0
    clear: int = 0
    blocked_amount: str = "0.00"
    overdue_amount: str = "0.00"


class AppAccessRuleDocument(BaseModel):
    """Saved rule plus impact. Missing row uses exists=false and version=0."""

    model_config = ConfigDict(extra="forbid")

    exists: bool
    enabled: bool
    by_amount: bool
    amount_threshold: str
    by_days: bool
    days_threshold: int
    match_mode: AppAccessMatchMode
    version: int
    impact: AppAccessImpact


class UpsertAppAccessRuleRequest(BaseModel):
    """Create-or-replace the project rule. version 0 inserts."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    by_amount: bool
    amount_threshold: Decimal = Field(..., ge=0)
    by_days: bool
    days_threshold: int = Field(..., ge=1, le=365)
    match_mode: AppAccessMatchMode = AppAccessMatchMode.ALL
    version: int = Field(..., ge=0)


class AppAccessOverrideRequest(BaseModel):
    """Block now or release one unit."""

    model_config = ConfigDict(extra="forbid")

    kind: AppAccessOverrideKind


class AppAccessUnitItem(BaseModel):
    """One Customers-tab row."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    unit_code: str
    display_name: str
    outstanding_amount: str
    oldest_invoice_date: date | None = None
    days_past_due: int | None = None
    status: AppAccessStatus
    blocked_on: datetime | None = None
    blocked_by: str | None = None
    reason: str
    override_kind: AppAccessOverrideKind | None = None
    evaluated_at: datetime | None = None


class ResidentAppAccess(BaseModel):
    """Hold payload for one unit in the resident app."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    unit_code: str | None = None
    status: AppAccessStatus
    held: bool
    outstanding_amount: str | None = None
    days_past_due: int | None = None
    blocked_on: datetime | None = None
    reason_key: str


class OnboardingAppAccessUnit(BaseModel):
    """One active unit on the onboarding status payload."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    unit_code: str
    status: AppAccessStatus
    held: bool
    outstanding_amount: str | None = None
    days_past_due: int | None = None
    reason_key: str


class OnboardingAppAccess(BaseModel):
    """Hold summary for every active unit of the contact."""

    model_config = ConfigDict(extra="forbid")

    any_held: bool
    held_unit_ids: list[str]
    units: list[OnboardingAppAccessUnit]


class AppAccessRuleApiResponse(BaseModel):
    """API envelope for GET/PUT rule."""

    status: str
    message: str
    statusCode: int
    code: str
    data: AppAccessRuleDocument


class AppAccessUnitListApiResponse(BaseModel):
    """API envelope for GET units."""

    status: str
    message: str
    statusCode: int
    code: str
    data: list[AppAccessUnitItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class AppAccessUnitItemApiResponse(BaseModel):
    """API envelope for override POST/DELETE."""

    status: str
    message: str
    statusCode: int
    code: str
    data: AppAccessUnitItem


class ResidentAppAccessApiResponse(BaseModel):
    """API envelope for GET /units/{unit_id}/app-access."""

    status: str
    message: str
    statusCode: int
    code: str
    data: ResidentAppAccess
