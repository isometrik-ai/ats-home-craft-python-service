"""Notice board schemas."""

from __future__ import annotations

from datetime import date, datetime, time
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from apps.user_service.app.schemas.enums import (
    NOTICE_ALLOWED_ATTACHMENT_MIMES,
    NOTICE_MAX_ATTACHMENT_BYTES,
    NOTICE_MAX_ATTACHMENTS,
    NOTICE_MAX_CONTACT_RECIPIENTS,
    NOTICE_MAX_DESCRIPTION_LENGTH,
    NOTICE_MAX_TITLE_LENGTH,
    NoticeCategory,
    NoticeListStatus,
    NoticePinDuration,
    NoticePublishMode,
    NoticeRecipientGroup,
    NoticeRecipientInputGroup,
    NoticeRecurrenceFrequency,
    NoticeScopeType,
    NoticeSeriesEndType,
    NoticeStatus,
)
from apps.user_service.app.utils.notice_targeting_utils import (
    NOTICE_TARGETING_ALL,
    normalize_recipient_groups_input,
)


def _normalize_contact_ids_input(
    contact_ids: object,
    *,
    empty_means_omitted: bool,
) -> object:
    """Dedupe contact ids; optionally treat an empty list as omitted (create only)."""
    if contact_ids is None:
        return None
    if not isinstance(contact_ids, list):
        return contact_ids
    normalized = list(dict.fromkeys(str(item).strip() for item in contact_ids if str(item).strip()))
    if empty_means_omitted:
        return normalized or None
    return normalized


def _validate_contact_id_uuids(contact_ids: list[str] | None) -> list[str] | None:
    """Ensure each contact id is a canonical UUID string (lowercase)."""
    if contact_ids is None:
        return None
    try:
        canonical = [str(UUID(contact_id)) for contact_id in contact_ids]
    except ValueError as exc:
        raise ValueError("contact_ids must contain valid UUIDs") from exc
    return list(dict.fromkeys(canonical))


def _validate_repeat_required_fields(repeat: NoticeRepeatInput) -> None:
    """Raise when enabled repeat is missing core fields."""
    if repeat.frequency is None:
        raise ValueError("frequency is required when repeat is enabled")
    if repeat.starts_on is None or repeat.time_of_day is None:
        raise ValueError("starts_on and time_of_day are required when repeat is enabled")
    if repeat.end_type is None:
        raise ValueError("end_type is required when repeat is enabled")
    if repeat.end_type == NoticeSeriesEndType.ON_DATE and repeat.end_on is None:
        raise ValueError("end_on is required when end_type is on_date")
    if repeat.end_type == NoticeSeriesEndType.AFTER_COUNT and repeat.end_after_sends is None:
        raise ValueError("end_after_sends is required when end_type is after_count")


def _validate_repeat_frequency_fields(repeat: NoticeRepeatInput) -> None:
    """Raise when frequency-specific fields are missing."""
    if repeat.frequency == NoticeRecurrenceFrequency.WEEKLY and not repeat.weekly_days:
        raise ValueError("weekly_days required for weekly frequency")
    if repeat.frequency == NoticeRecurrenceFrequency.MONTHLY and repeat.monthly_day is None:
        raise ValueError("monthly_day required for monthly frequency")


def _validate_repeat_end_on_order(repeat: NoticeRepeatInput) -> None:
    """Raise when end_on precedes starts_on."""
    if (
        repeat.end_type == NoticeSeriesEndType.ON_DATE
        and repeat.end_on is not None
        and repeat.starts_on is not None
        and repeat.end_on < repeat.starts_on
    ):
        raise ValueError("end_on must be on or after starts_on")


class NoticeRepeatInput(BaseModel):
    """Recurring delivery schedule (optional on create/update)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    frequency: NoticeRecurrenceFrequency | None = None
    weekly_days: list[int] | None = Field(
        None,
        description="ISO weekday 1=Monday … 7=Sunday",
    )
    monthly_day: int | None = Field(
        None,
        ge=0,
        le=31,
        description="1–31, or 0 for last day of month",
    )
    starts_on: date | None = None
    time_of_day: time | None = None
    end_type: NoticeSeriesEndType | None = None
    end_on: date | None = None
    end_after_sends: int | None = Field(None, ge=1)

    @field_validator("weekly_days")
    @classmethod
    def validate_weekly_days(cls, weekly_days: list[int] | None) -> list[int] | None:
        """Ensure weekday integers are in ISO range."""
        if weekly_days is None:
            return None
        for day in weekly_days:
            if day < 1 or day > 7:
                raise ValueError("weekly_days must be between 1 and 7")
        return sorted(set(weekly_days))

    @model_validator(mode="after")
    def validate_repeat_block(self) -> NoticeRepeatInput:
        """Validate recurrence fields when enabled."""
        if not self.enabled:
            return self
        _validate_repeat_required_fields(self)
        _validate_repeat_frequency_fields(self)
        _validate_repeat_end_on_order(self)
        return self


class NoticeSeriesOccurrenceResponse(BaseModel):
    """One recurring send in history."""

    occurrence_index: int
    planned_at: datetime
    published_at: datetime | None = None


class NoticeAttachmentInput(BaseModel):
    """Attachment metadata on create or update."""

    model_config = ConfigDict(extra="forbid")

    file_path: str = Field(..., min_length=1, max_length=2000)
    file_name: str | None = Field(None, max_length=255)
    mime_type: str = Field(..., min_length=1, max_length=100)
    size_bytes: int = Field(..., ge=1, le=NOTICE_MAX_ATTACHMENT_BYTES)
    sort_order: int = Field(..., ge=0, le=3)


class NoticeAttachmentResponse(BaseModel):
    """Attachment row in notice detail."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    file_path: str
    file_name: str | None = None
    mime_type: str
    size_bytes: int
    sort_order: int


class CreateNoticeRequest(BaseModel):
    """Create a notice (draft, publish now, or schedule)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=NOTICE_MAX_TITLE_LENGTH)
    description: str = Field("", max_length=NOTICE_MAX_DESCRIPTION_LENGTH)
    category: NoticeCategory = NoticeCategory.GENERAL
    recipient_groups: list[NoticeRecipientInputGroup] | None = Field(
        None,
        description=(
            "Who receives this notice. Pass All to select Owner, Tenant, Staff, and Security."
        ),
    )
    contact_ids: list[str] | None = Field(
        None,
        description=(
            "Send the notice to specific residents by contact id (one or many). "
            "Can be combined with recipient_groups."
        ),
    )
    scope_type: NoticeScopeType = NoticeScopeType.WHOLE_SOCIETY
    tower_ids: list[str] | None = None
    publish_mode: NoticePublishMode = NoticePublishMode.DRAFT
    publish_at: datetime | None = None
    pin_to_banner: bool = False
    slot_index: int | None = Field(None, ge=1, le=6)
    pin_duration: NoticePinDuration = NoticePinDuration.MANUAL
    confirm_pin_replace: bool = False
    attachments: list[NoticeAttachmentInput] | None = None
    repeat: NoticeRepeatInput | None = None
    preview_recurrence: bool = False

    @field_validator("recipient_groups", mode="before")
    @classmethod
    def normalize_all_recipient_shortcut(cls, recipient_groups: object) -> object:
        """Accept All/all as a select-all recipient shortcut."""
        return normalize_recipient_groups_input(recipient_groups)

    @field_validator("contact_ids", mode="before")
    @classmethod
    def normalize_contact_ids(cls, contact_ids: object) -> object:
        """Dedupe and drop blank contact ids."""
        return _normalize_contact_ids_input(contact_ids, empty_means_omitted=True)

    @model_validator(mode="after")
    def validate_repeat_publish_mode(self) -> CreateNoticeRequest:
        """Disallow one-time publish modes when repeat is enabled."""
        if self.repeat and self.repeat.enabled:
            if self.publish_mode in (NoticePublishMode.NOW, NoticePublishMode.SCHEDULE):
                raise ValueError("use draft or start_series when repeat is enabled")
        return self

    @field_validator("contact_ids")
    @classmethod
    def validate_contact_ids(cls, contact_ids: list[str] | None) -> list[str] | None:
        """Validate UUID shape, count, and max direct contact recipients."""
        contact_ids = _validate_contact_id_uuids(contact_ids)
        if contact_ids is not None and len(contact_ids) > NOTICE_MAX_CONTACT_RECIPIENTS:
            raise ValueError(f"contact_ids cannot exceed {NOTICE_MAX_CONTACT_RECIPIENTS}")
        return contact_ids

    @field_validator("attachments")
    @classmethod
    def validate_attachment_count(
        cls,
        attachments: list[NoticeAttachmentInput] | None,
    ) -> list[NoticeAttachmentInput] | None:
        """Enforce max attachment count."""
        if attachments is not None and len(attachments) > NOTICE_MAX_ATTACHMENTS:
            raise ValueError(f"attachments cannot exceed {NOTICE_MAX_ATTACHMENTS}")
        return attachments


class UpdateNoticeRequest(BaseModel):
    """Update a draft or scheduled notice."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(None, min_length=1, max_length=NOTICE_MAX_TITLE_LENGTH)
    description: str | None = Field(None, max_length=NOTICE_MAX_DESCRIPTION_LENGTH)
    category: NoticeCategory | None = None
    recipient_groups: list[NoticeRecipientInputGroup] | None = Field(
        None,
        description=(
            "Who receives this notice. Pass All to select Owner, Tenant, Staff, and Security."
        ),
    )
    contact_ids: list[str] | None = Field(
        None,
        description="Send the notice to specific residents by contact id (one or many).",
    )
    scope_type: NoticeScopeType | None = None
    tower_ids: list[str] | None = None
    publish_mode: NoticePublishMode | None = None
    publish_at: datetime | None = None
    attachments: list[NoticeAttachmentInput] | None = None
    repeat: NoticeRepeatInput | None = None
    preview_recurrence: bool = False

    @field_validator("recipient_groups", mode="before")
    @classmethod
    def normalize_all_recipient_shortcut(cls, recipient_groups: object) -> object:
        """Accept All/all as a select-all recipient shortcut."""
        return normalize_recipient_groups_input(recipient_groups)

    @field_validator("contact_ids", mode="before")
    @classmethod
    def normalize_contact_ids(cls, contact_ids: object) -> object:
        """Dedupe and drop blank contact ids; preserve explicit empty list for PATCH."""
        return _normalize_contact_ids_input(contact_ids, empty_means_omitted=False)

    @model_validator(mode="after")
    def validate_repeat_publish_mode(self) -> UpdateNoticeRequest:
        """Disallow one-time publish modes when repeat is enabled."""
        if self.repeat and self.repeat.enabled:
            if self.publish_mode in (NoticePublishMode.NOW, NoticePublishMode.SCHEDULE):
                raise ValueError("use draft, start_series, or end_series when repeat is enabled")
        return self

    @field_validator("contact_ids")
    @classmethod
    def validate_contact_ids(cls, contact_ids: list[str] | None) -> list[str] | None:
        """Validate UUID shape, count, and max direct contact recipients."""
        contact_ids = _validate_contact_id_uuids(contact_ids)
        if contact_ids is not None and len(contact_ids) > NOTICE_MAX_CONTACT_RECIPIENTS:
            raise ValueError(f"contact_ids cannot exceed {NOTICE_MAX_CONTACT_RECIPIENTS}")
        return contact_ids

    @field_validator("attachments")
    @classmethod
    def validate_attachment_count(
        cls,
        attachments: list[NoticeAttachmentInput] | None,
    ) -> list[NoticeAttachmentInput] | None:
        """Enforce max attachment count."""
        if attachments is not None and len(attachments) > NOTICE_MAX_ATTACHMENTS:
            raise ValueError(f"attachments cannot exceed {NOTICE_MAX_ATTACHMENTS}")
        return attachments


class PinNoticeRequest(BaseModel):
    """Pin a live notice to a banner slot."""

    model_config = ConfigDict(extra="forbid")

    slot_index: int | None = Field(None, ge=1, le=6)
    pin_duration: NoticePinDuration = NoticePinDuration.MANUAL
    confirm_pin_replace: bool = False


class DeleteNoticeRequest(BaseModel):
    """Soft-delete a notice."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(None, max_length=500)


class NoticeListQuery(BaseModel):
    """Admin list filters."""

    model_config = ConfigDict(extra="forbid")

    status: NoticeListStatus = NoticeListStatus.ALL
    group: NoticeRecipientGroup | None = None
    search: str | None = Field(None, max_length=200)
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


class ReachEstimateQuery(BaseModel):
    """Reach estimate query params."""

    model_config = ConfigDict(extra="forbid")

    groups: str = Field(
        ...,
        min_length=1,
        description=(
            "Comma-separated recipient groups. "
            "Use All to include Owner, Tenant, Staff, and Security."
        ),
    )
    scope_type: NoticeScopeType = NoticeScopeType.WHOLE_SOCIETY
    tower_ids: str | None = Field(None, description="Comma-separated tower UUIDs")

    def parsed_groups(self) -> list[str]:
        """Parse comma-separated groups."""
        return [part.strip() for part in self.groups.split(",") if part.strip()]

    def invalid_recipient_groups(self) -> list[str]:
        """Return unsupported group names from the parsed query string."""
        allowed = {group.value for group in NoticeRecipientInputGroup}
        invalid: set[str] = set()
        for group in self.parsed_groups():
            if group.strip().lower() == NOTICE_TARGETING_ALL.lower():
                continue
            if group not in allowed:
                invalid.add(group)
        return sorted(invalid)

    def parsed_tower_ids(self) -> list[str]:
        """Parse comma-separated tower ids."""
        if not self.tower_ids:
            return []
        return [part.strip() for part in self.tower_ids.split(",") if part.strip()]


class NoticeSummaryResponse(BaseModel):
    """Dashboard tab counts."""

    all: int
    live: int
    scheduled: int
    deleted: int
    live_by_group: dict[str, int]


class NoticeListItemResponse(BaseModel):
    """Notice card in admin list."""

    id: str
    display_code: str
    status: NoticeStatus
    title: str
    description: str
    category: NoticeCategory
    category_label: str
    recipient_groups: list[str]
    scope_type: NoticeScopeType
    scope_label: str | None = None
    published_at: datetime | None = None
    publish_at: datetime | None = None
    deleted_at: datetime | None = None
    pinned: bool = False
    slot_index: int | None = None
    view_count: int = 0
    like_count: int = 0
    editable: bool = False
    created_at: datetime
    attachments: list[NoticeAttachmentResponse] = Field(default_factory=list)
    is_recurring: bool = False
    repeats_label: str | None = None
    next_send_at: datetime | None = None
    recurrence_footer: str | None = None


class NoticeDetailResponse(BaseModel):
    """Full notice detail."""

    id: str
    organization_id: str
    project_id: str
    display_code: str
    status: NoticeStatus
    title: str
    description: str
    category: NoticeCategory
    category_label: str
    recipient_groups: list[str]
    contact_ids: list[str] = Field(default_factory=list)
    scope_type: NoticeScopeType
    scope_label: str | None = None
    tower_ids: list[str] = Field(default_factory=list)
    tower_names: list[str] = Field(default_factory=list)
    publish_at: datetime | None = None
    published_at: datetime | None = None
    deleted_at: datetime | None = None
    deleted_reason: str | None = None
    attachments: list[NoticeAttachmentResponse] = Field(default_factory=list)
    pinned: bool = False
    slot_index: int | None = None
    pin_duration: NoticePinDuration | None = None
    view_count: int = 0
    like_count: int = 0
    editable: bool = False
    duplicate_of_id: str | None = None
    created_at: datetime
    updated_at: datetime
    created_by_user_id: str | None = None
    is_recurring: bool = False
    repeats_label: str | None = None
    next_send_at: datetime | None = None
    series_starts_on: date | None = None
    series_ends_label: str | None = None
    next_sends_preview: list[datetime] = Field(default_factory=list)
    occurrences: list[NoticeSeriesOccurrenceResponse] = Field(default_factory=list)


class ReachEstimateResponse(BaseModel):
    """Audience size estimate."""

    estimated_recipients: int
    breakdown: dict[str, int] = Field(default_factory=dict)


class SlotOccupiedErrorData(BaseModel):
    """Extra data when pin target slot is occupied."""

    slot_index: int
    current_notice_id: str
    current_display_code: str
    current_title: str


class ResidentNoticeListQuery(BaseModel):
    """Resident feed filters."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    category: NoticeCategory | None = None
    search: str | None = Field(None, max_length=200)
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


class ResidentBannerQuery(BaseModel):
    """Resident banner query."""

    model_config = ConfigDict(extra="forbid")

    project_id: str


class ResidentNoticeListItemResponse(BaseModel):
    """Notice in resident feed (no recipient groups exposed)."""

    id: str
    display_code: str
    title: str
    description: str
    category: NoticeCategory
    category_label: str
    published_at: datetime | None = None
    attachments: list[NoticeAttachmentResponse] = Field(default_factory=list)
    view_count: int = 0
    like_count: int = 0
    liked_by_me: bool = False
    pinned: bool = False
    slot_index: int | None = None


class ResidentNoticeDetailResponse(BaseModel):
    """Resident notice detail."""

    id: str
    display_code: str
    title: str
    description: str
    category: NoticeCategory
    category_label: str
    scope_label: str | None = None
    published_at: datetime | None = None
    attachments: list[NoticeAttachmentResponse] = Field(default_factory=list)
    view_count: int = 0
    like_count: int = 0
    liked_by_me: bool = False
    pinned: bool = False
    slot_index: int | None = None


def validate_notice_attachment_mimes(attachments: list[NoticeAttachmentInput] | None) -> None:
    """Validate attachment MIME types."""
    if not attachments:
        return
    for item in attachments:
        if item.mime_type not in NOTICE_ALLOWED_ATTACHMENT_MIMES:
            raise ValueError(f"unsupported mime type: {item.mime_type}")
