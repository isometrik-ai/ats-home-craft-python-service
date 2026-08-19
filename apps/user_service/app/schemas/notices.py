"""Notice board schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.user_service.app.schemas.enums import (
    NOTICE_ALLOWED_ATTACHMENT_MIMES,
    NOTICE_MAX_ATTACHMENT_BYTES,
    NOTICE_MAX_ATTACHMENTS,
    NOTICE_MAX_DESCRIPTION_LENGTH,
    NOTICE_MAX_TITLE_LENGTH,
    NoticeCategory,
    NoticeListStatus,
    NoticePinDuration,
    NoticePublishMode,
    NoticeRecipientGroup,
    NoticeScopeType,
    NoticeStatus,
)


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
    recipient_groups: list[NoticeRecipientGroup] | None = None
    scope_type: NoticeScopeType = NoticeScopeType.WHOLE_SOCIETY
    tower_ids: list[str] | None = None
    publish_mode: NoticePublishMode = NoticePublishMode.DRAFT
    publish_at: datetime | None = None
    pin_to_banner: bool = False
    slot_index: int | None = Field(None, ge=1, le=6)
    pin_duration: NoticePinDuration = NoticePinDuration.MANUAL
    confirm_pin_replace: bool = False
    attachments: list[NoticeAttachmentInput] | None = None

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
    recipient_groups: list[NoticeRecipientGroup] | None = None
    scope_type: NoticeScopeType | None = None
    tower_ids: list[str] | None = None
    publish_mode: NoticePublishMode | None = None
    publish_at: datetime | None = None
    attachments: list[NoticeAttachmentInput] | None = None

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

    groups: str = Field(..., min_length=1, description="Comma-separated recipient groups")
    scope_type: NoticeScopeType = NoticeScopeType.WHOLE_SOCIETY
    tower_ids: str | None = Field(None, description="Comma-separated tower UUIDs")

    def parsed_groups(self) -> list[str]:
        """Parse comma-separated groups."""
        return [part.strip() for part in self.groups.split(",") if part.strip()]

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
