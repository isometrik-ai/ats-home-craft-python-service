"""Notice board business logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.notices_repository import NoticesRepository
from apps.user_service.app.schemas.enums import (
    NOTICE_CATEGORY_LABELS,
    NOTICE_MAX_ATTACHMENTS,
    NOTICE_SCHEDULE_MAX_DAYS,
    NoticeCategory,
    NoticePinDuration,
    NoticePublishMode,
    NoticeScopeType,
    NoticeStatus,
)
from apps.user_service.app.schemas.notices import (
    CreateNoticeRequest,
    DeleteNoticeRequest,
    NoticeAttachmentInput,
    NoticeAttachmentResponse,
    NoticeDetailResponse,
    NoticeListItemResponse,
    NoticeListQuery,
    NoticeSummaryResponse,
    PinNoticeRequest,
    ReachEstimateQuery,
    ReachEstimateResponse,
    UpdateNoticeRequest,
    validate_notice_attachment_mimes,
)
from apps.user_service.app.services.notice_recipient_resolution_service import (
    NoticeRecipientResolutionService,
)
from apps.user_service.app.services.push_notification_dispatch import (
    PushNotificationDispatcher,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode


class NoticesService:
    """Admin notice board orchestration."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.repo = NoticesRepository(db_connection=db_connection)
        self.recipient_service = NoticeRecipientResolutionService(db_connection=db_connection)
        self.push_dispatcher = PushNotificationDispatcher(db_connection=db_connection)

    @property
    def organization_id(self) -> str:
        return self.user_context.organization_id

    @property
    def user_id(self) -> str | None:
        return self.user_context.user_id

    async def get_summary(self, *, project_id: str) -> NoticeSummaryResponse:
        """Return dashboard tab counts."""
        counts = await self.repo.get_summary_counts(
            organization_id=self.organization_id,
            project_id=project_id,
        )
        return NoticeSummaryResponse(
            all=counts["all"],
            live=counts["live"],
            scheduled=counts["scheduled"],
            deleted=counts["deleted"],
            live_by_group=counts["live_by_group"],
        )

    async def list_notices(
        self,
        *,
        project_id: str,
        query: NoticeListQuery,
    ) -> tuple[list[NoticeListItemResponse], int]:
        """Return paginated admin notice list."""
        offset = (query.page - 1) * query.page_size
        rows, total = await self.repo.list_notices(
            organization_id=self.organization_id,
            project_id=project_id,
            status=query.status,
            group=query.group.value if query.group else None,
            search=query.search,
            limit=query.page_size,
            offset=offset,
        )
        notice_ids = [str(row["id"]) for row in rows]
        attachments_by_notice = await self.repo.list_attachments_for_notices(
            organization_id=self.organization_id,
            notice_ids=notice_ids,
        )
        return [
            self._to_list_item(
                row,
                attachments=attachments_by_notice.get(str(row["id"]), []),
            )
            for row in rows
        ], total

    async def get_notice(
        self,
        *,
        project_id: str,
        notice_id: str,
    ) -> NoticeDetailResponse:
        """Return full notice detail."""
        row = await self.repo.fetch_notice_by_id(
            organization_id=self.organization_id,
            project_id=project_id,
            notice_id=notice_id,
        )
        if row is None:
            raise NotFoundException(
                message_key="notices.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return await self._to_detail(row)

    async def create_notice(
        self,
        *,
        project_id: str,
        body: CreateNoticeRequest,
    ) -> NoticeDetailResponse:
        """Create draft, scheduled, or live notice."""
        self._validate_content(body.title, body.description)
        self._validate_attachments(body.attachments)

        recipient_groups = self._groups_to_values(body.recipient_groups)
        tower_ids = body.tower_ids or []

        status, publish_at, published_at = self._resolve_publish_state(
            publish_mode=body.publish_mode,
            publish_at=body.publish_at,
            recipient_groups=recipient_groups,
            scope_type=body.scope_type,
            tower_ids=tower_ids,
        )

        sequence_number = await self.repo.allocate_sequence_number(
            organization_id=self.organization_id,
            project_id=project_id,
        )
        display_code = f"NTC-{sequence_number}"

        row = await self.repo.insert_notice(
            organization_id=self.organization_id,
            project_id=project_id,
            display_code=display_code,
            sequence_number=sequence_number,
            title=body.title.strip(),
            description=body.description.strip(),
            category=body.category.value,
            status=status,
            scope_type=body.scope_type.value,
            publish_at=publish_at,
            published_at=published_at,
            duplicate_of_id=None,
            created_by_user_id=self.user_id,
            updated_by_user_id=self.user_id,
        )
        notice_id = str(row["id"])
        await self._replace_children(
            notice_id=notice_id,
            recipient_groups=recipient_groups,
            scope_type=body.scope_type,
            tower_ids=tower_ids,
            attachments=body.attachments,
        )

        if body.pin_to_banner and status == NoticeStatus.LIVE.value:
            await self._pin_notice_internal(
                project_id=project_id,
                notice_id=notice_id,
                slot_index=body.slot_index,
                pin_duration=body.pin_duration,
                confirm_pin_replace=body.confirm_pin_replace,
            )

        if status == NoticeStatus.LIVE.value:
            await self._dispatch_publish_push(
                project_id=project_id,
                notice_id=notice_id,
                title=body.title.strip(),
            )

        return await self.get_notice(project_id=project_id, notice_id=notice_id)

    async def update_notice(
        self,
        *,
        project_id: str,
        notice_id: str,
        body: UpdateNoticeRequest,
    ) -> NoticeDetailResponse:
        """Update draft or scheduled notice."""
        existing = await self.repo.fetch_notice_by_id(
            organization_id=self.organization_id,
            project_id=project_id,
            notice_id=notice_id,
        )
        if existing is None:
            raise NotFoundException(
                message_key="notices.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        self._assert_editable(str(existing["status"]))

        title = body.title.strip() if body.title is not None else existing["title"]
        description = (
            body.description.strip() if body.description is not None else existing["description"]
        )
        self._validate_content(title, description)
        if body.attachments is not None:
            self._validate_attachments(body.attachments)

        current_groups = await self.repo.list_recipient_groups(
            organization_id=self.organization_id,
            notice_id=notice_id,
        )
        current_towers = await self.repo.list_towers_for_notice(
            organization_id=self.organization_id,
            notice_id=notice_id,
        )
        recipient_groups = (
            self._groups_to_values(body.recipient_groups)
            if body.recipient_groups is not None
            else current_groups
        )
        scope_type = (
            body.scope_type
            if body.scope_type is not None
            else NoticeScopeType(str(existing["scope_type"]))
        )
        tower_ids = (
            body.tower_ids
            if body.tower_ids is not None
            else [str(row["tower_id"]) for row in current_towers]
        )

        publish_mode = body.publish_mode
        publish_at_input = body.publish_at
        fields: dict[str, Any] = {
            "title": title,
            "description": description,
        }
        if body.category is not None:
            fields["category"] = body.category.value

        if publish_mode is not None:
            status, publish_at, published_at = self._resolve_publish_state(
                publish_mode=publish_mode,
                publish_at=publish_at_input,
                recipient_groups=recipient_groups,
                scope_type=scope_type,
                tower_ids=tower_ids,
                current_status=str(existing["status"]),
            )
            fields["status"] = status
            fields["publish_at"] = publish_at
            fields["published_at"] = published_at
        elif publish_at_input is not None:
            self._validate_schedule(publish_at_input)
            self._validate_publish_requirements(
                recipient_groups=recipient_groups,
                scope_type=scope_type,
                tower_ids=tower_ids,
            )
            fields["publish_at"] = publish_at_input
            fields["status"] = NoticeStatus.SCHEDULED.value

        if body.scope_type is not None:
            fields["scope_type"] = body.scope_type.value

        fields["updated_by_user_id"] = self.user_id
        await self.repo.update_notice_fields(
            organization_id=self.organization_id,
            project_id=project_id,
            notice_id=notice_id,
            fields=fields,
        )

        if (
            body.recipient_groups is not None
            or body.tower_ids is not None
            or body.scope_type is not None
            or body.attachments is not None
        ):
            await self._replace_children(
                notice_id=notice_id,
                recipient_groups=recipient_groups,
                scope_type=scope_type,
                tower_ids=tower_ids,
                attachments=body.attachments
                if body.attachments is not None
                else await self._attachments_from_db(notice_id),
            )

        updated = await self.get_notice(project_id=project_id, notice_id=notice_id)
        if updated.status == NoticeStatus.LIVE:
            await self._dispatch_publish_push(
                project_id=project_id,
                notice_id=notice_id,
                title=updated.title,
            )
        return updated

    async def delete_notice(
        self,
        *,
        project_id: str,
        notice_id: str,
        body: DeleteNoticeRequest | None = None,
    ) -> NoticeDetailResponse:
        """Soft delete a notice."""
        existing = await self.repo.fetch_notice_by_id(
            organization_id=self.organization_id,
            project_id=project_id,
            notice_id=notice_id,
        )
        if existing is None:
            raise NotFoundException(
                message_key="notices.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if str(existing["status"]) == NoticeStatus.DELETED.value:
            return await self._to_detail(existing)

        row = await self.repo.soft_delete_notice(
            organization_id=self.organization_id,
            project_id=project_id,
            notice_id=notice_id,
            reason=body.reason if body else None,
            updated_by_user_id=self.user_id,
        )
        if row is None:
            raise NotFoundException(
                message_key="notices.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return await self._to_detail(row)

    async def restore_notice(
        self,
        *,
        project_id: str,
        notice_id: str,
    ) -> NoticeDetailResponse:
        """Restore a deleted notice by creating a new draft copy (duplicate semantics)."""
        source = await self.repo.fetch_notice_by_id(
            organization_id=self.organization_id,
            project_id=project_id,
            notice_id=notice_id,
        )
        if source is None:
            raise NotFoundException(
                message_key="notices.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if str(source["status"]) != NoticeStatus.DELETED.value:
            raise ConflictException(
                message_key="notices.errors.not_restorable",
                custom_code=CustomStatusCode.CONFLICT,
            )
        return await self._duplicate_from_source(
            project_id=project_id,
            source_notice_id=notice_id,
            source=source,
        )

    async def duplicate_notice(
        self,
        *,
        project_id: str,
        notice_id: str,
    ) -> NoticeDetailResponse:
        """Duplicate draft or scheduled notice to new draft."""
        source = await self.repo.fetch_notice_by_id(
            organization_id=self.organization_id,
            project_id=project_id,
            notice_id=notice_id,
        )
        if source is None:
            raise NotFoundException(
                message_key="notices.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        self._assert_duplicatable(str(source["status"]))
        return await self._duplicate_from_source(
            project_id=project_id,
            source_notice_id=notice_id,
            source=source,
        )

    async def _duplicate_from_source(
        self,
        *,
        project_id: str,
        source_notice_id: str,
        source: dict[str, Any],
    ) -> NoticeDetailResponse:
        """Copy notice content into a new draft row."""
        recipient_groups = await self.repo.list_recipient_groups(
            organization_id=self.organization_id,
            notice_id=source_notice_id,
        )
        towers = await self.repo.list_towers_for_notice(
            organization_id=self.organization_id,
            notice_id=source_notice_id,
        )
        attachments = await self.repo.list_attachments(
            organization_id=self.organization_id,
            notice_id=source_notice_id,
        )

        sequence_number = await self.repo.allocate_sequence_number(
            organization_id=self.organization_id,
            project_id=project_id,
        )
        display_code = f"NTC-{sequence_number}"

        row = await self.repo.insert_notice(
            organization_id=self.organization_id,
            project_id=project_id,
            display_code=display_code,
            sequence_number=sequence_number,
            title=source["title"],
            description=source["description"],
            category=str(source["category"]),
            status=NoticeStatus.DRAFT.value,
            scope_type=str(source["scope_type"]),
            publish_at=None,
            published_at=None,
            duplicate_of_id=source_notice_id,
            created_by_user_id=self.user_id,
            updated_by_user_id=self.user_id,
        )
        new_id = str(row["id"])
        attachment_inputs = [
            NoticeAttachmentInput(
                file_path=item["file_path"],
                file_name=item.get("file_name"),
                mime_type=item["mime_type"],
                size_bytes=int(item["size_bytes"]),
                sort_order=int(item["sort_order"]),
            )
            for item in attachments
        ]
        await self._replace_children(
            notice_id=new_id,
            recipient_groups=recipient_groups,
            scope_type=NoticeScopeType(str(source["scope_type"])),
            tower_ids=[str(t["tower_id"]) for t in towers],
            attachments=attachment_inputs or None,
        )
        return await self.get_notice(project_id=project_id, notice_id=new_id)

    async def pin_notice(
        self,
        *,
        project_id: str,
        notice_id: str,
        body: PinNoticeRequest,
    ) -> NoticeDetailResponse:
        """Pin a live notice to a banner slot."""
        existing = await self.repo.fetch_notice_by_id(
            organization_id=self.organization_id,
            project_id=project_id,
            notice_id=notice_id,
        )
        if existing is None:
            raise NotFoundException(
                message_key="notices.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        await self._pin_notice_internal(
            project_id=project_id,
            notice_id=notice_id,
            slot_index=body.slot_index,
            pin_duration=body.pin_duration,
            confirm_pin_replace=body.confirm_pin_replace,
        )
        return await self.get_notice(project_id=project_id, notice_id=notice_id)

    async def unpin_notice(
        self,
        *,
        project_id: str,
        notice_id: str,
    ) -> NoticeDetailResponse:
        """Unpin a notice from the banner."""
        existing = await self.repo.fetch_notice_by_id(
            organization_id=self.organization_id,
            project_id=project_id,
            notice_id=notice_id,
        )
        if existing is None:
            raise NotFoundException(
                message_key="notices.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        await self.repo.deactivate_pins_for_notice(
            organization_id=self.organization_id,
            notice_id=notice_id,
        )
        return await self.get_notice(project_id=project_id, notice_id=notice_id)

    async def get_reach_estimate(
        self,
        *,
        project_id: str,
        query: ReachEstimateQuery,
    ) -> ReachEstimateResponse:
        """Estimate audience size for targeting selections."""
        groups = query.parsed_groups()
        tower_ids = query.parsed_tower_ids()
        if query.scope_type == NoticeScopeType.BY_TOWER and not tower_ids:
            raise ValidationException(
                message_key="notices.errors.towers_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        total, breakdown = await self.recipient_service.estimate_reach(
            organization_id=self.organization_id,
            project_id=project_id,
            recipient_groups=groups,
            scope_type=query.scope_type.value,
            tower_ids=tower_ids,
        )
        return ReachEstimateResponse(estimated_recipients=total, breakdown=breakdown)

    async def publish_due_notices(
        self,
        *,
        project_id: str | None = None,
    ) -> list[str]:
        """Promote scheduled notices that are due."""
        published_ids = await self.repo.publish_due_scheduled_notices(
            organization_id=self.organization_id,
            project_id=project_id,
        )
        for notice_id in published_ids:
            row = await self.repo.fetch_notice_by_id_only(
                organization_id=self.organization_id,
                notice_id=notice_id,
            )
            if row:
                await self._dispatch_publish_push(
                    project_id=str(row["project_id"]),
                    notice_id=notice_id,
                    title=str(row["title"]),
                )
        return published_ids

    async def expire_due_pins(self) -> int:
        """Deactivate expired banner pins."""
        return await self.repo.expire_due_pins()

    async def _pin_notice_internal(
        self,
        *,
        project_id: str,
        notice_id: str,
        slot_index: int | None,
        pin_duration: NoticePinDuration,
        confirm_pin_replace: bool,
    ) -> None:
        """Core pin logic shared by create and pin endpoint."""
        existing = await self.repo.fetch_notice_by_id(
            organization_id=self.organization_id,
            project_id=project_id,
            notice_id=notice_id,
        )
        if existing is None:
            raise NotFoundException(
                message_key="notices.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if str(existing["status"]) != NoticeStatus.LIVE.value:
            raise ConflictException(
                message_key="notices.errors.not_pinnable",
                custom_code=CustomStatusCode.CONFLICT,
            )

        active_pin = await self.repo.get_active_pin_for_notice(
            organization_id=self.organization_id,
            notice_id=notice_id,
        )
        if active_pin is not None:
            raise ConflictException(
                message_key="notices.errors.already_pinned",
                custom_code=CustomStatusCode.CONFLICT,
            )

        resolved_slot = slot_index
        if resolved_slot is None:
            resolved_slot = await self.repo.find_first_free_slot(
                organization_id=self.organization_id,
                project_id=project_id,
            )
            if resolved_slot is None:
                raise ValidationException(
                    message_key="notices.errors.pin_slots_full",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
        else:
            occupied = await self.repo.get_active_pin_for_slot(
                organization_id=self.organization_id,
                project_id=project_id,
                slot_index=resolved_slot,
            )
            if occupied is not None and str(occupied["notice_id"]) != notice_id:
                if not confirm_pin_replace:
                    raise ConflictException(
                        message_key="notices.errors.slot_occupied",
                        custom_code=CustomStatusCode.CONFLICT,
                        params={
                            "slot_index": resolved_slot,
                            "current_notice_id": str(occupied["notice_id"]),
                            "current_display_code": str(occupied["display_code"]),
                            "current_title": str(occupied["title"]),
                        },
                    )
                await self.repo.deactivate_pin_on_slot(
                    organization_id=self.organization_id,
                    project_id=project_id,
                    slot_index=resolved_slot,
                )

        expires_at = self._pin_expires_at(pin_duration)
        await self.repo.insert_pin(
            organization_id=self.organization_id,
            project_id=project_id,
            notice_id=notice_id,
            slot_index=resolved_slot,
            pin_duration=pin_duration.value,
            expires_at=expires_at,
        )

    async def _replace_children(
        self,
        *,
        notice_id: str,
        recipient_groups: list[str],
        scope_type: NoticeScopeType,
        tower_ids: list[str],
        attachments: list[NoticeAttachmentInput] | None,
    ) -> None:
        """Replace junction and attachment rows."""
        await self.repo.replace_recipients(
            organization_id=self.organization_id,
            notice_id=notice_id,
            recipient_groups=recipient_groups,
        )
        scoped_towers = tower_ids if scope_type == NoticeScopeType.BY_TOWER else []
        await self.repo.replace_towers(
            organization_id=self.organization_id,
            notice_id=notice_id,
            tower_ids=scoped_towers,
        )
        if attachments is not None:
            await self.repo.replace_attachments(
                organization_id=self.organization_id,
                notice_id=notice_id,
                attachments=[item.model_dump() for item in attachments],
            )

    async def _attachments_from_db(self, notice_id: str) -> list[NoticeAttachmentInput]:
        rows = await self.repo.list_attachments(
            organization_id=self.organization_id,
            notice_id=notice_id,
        )
        return [
            NoticeAttachmentInput(
                file_path=row["file_path"],
                file_name=row.get("file_name"),
                mime_type=row["mime_type"],
                size_bytes=int(row["size_bytes"]),
                sort_order=int(row["sort_order"]),
            )
            for row in rows
        ]

    async def _to_detail(self, row: dict[str, Any]) -> NoticeDetailResponse:
        notice_id = str(row["id"])
        recipient_groups = await self.repo.list_recipient_groups(
            organization_id=self.organization_id,
            notice_id=notice_id,
        )
        towers = await self.repo.list_towers_for_notice(
            organization_id=self.organization_id,
            notice_id=notice_id,
        )
        attachments = await self.repo.list_attachments(
            organization_id=self.organization_id,
            notice_id=notice_id,
        )
        status = NoticeStatus(str(row["status"]))
        category = str(row["category"])
        scope_type = NoticeScopeType(str(row["scope_type"]))
        scope_label = self._scope_label(scope_type, towers)
        return NoticeDetailResponse(
            id=notice_id,
            organization_id=str(row["organization_id"]),
            project_id=str(row["project_id"]),
            display_code=str(row["display_code"]),
            status=status,
            title=str(row["title"]),
            description=str(row["description"]),
            category=NoticeCategory(category),
            category_label=NOTICE_CATEGORY_LABELS.get(category, category),
            recipient_groups=recipient_groups,
            scope_type=scope_type,
            scope_label=scope_label,
            tower_ids=[str(t["tower_id"]) for t in towers],
            tower_names=[str(t["tower_name"]) for t in towers],
            publish_at=row.get("publish_at"),
            published_at=row.get("published_at"),
            deleted_at=row.get("deleted_at"),
            deleted_reason=row.get("deleted_reason"),
            attachments=[NoticeAttachmentResponse(**item) for item in attachments],
            pinned=bool(row.get("pinned")),
            slot_index=int(row["pin_slot_index"]) if row.get("pin_slot_index") else None,
            pin_duration=NoticePinDuration(str(row["pin_duration"]))
            if row.get("pin_duration")
            else None,
            view_count=int(row.get("view_count") or 0),
            like_count=int(row.get("like_count") or 0),
            editable=status in {NoticeStatus.DRAFT, NoticeStatus.SCHEDULED},
            duplicate_of_id=row.get("duplicate_of_id"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            created_by_user_id=row.get("created_by_user_id"),
        )

    def _to_list_item(
        self,
        row: dict[str, Any],
        *,
        attachments: list[dict[str, Any]] | None = None,
    ) -> NoticeListItemResponse:
        status = NoticeStatus(str(row["status"]))
        category = str(row["category"])
        scope_type = NoticeScopeType(str(row["scope_type"]))
        recipient_groups = list(row.get("recipient_groups") or [])
        scope_label = str(row["scope_label"]) if row.get("scope_label") else None
        if scope_type == NoticeScopeType.WHOLE_SOCIETY:
            scope_label = "Whole society"
        return NoticeListItemResponse(
            id=str(row["id"]),
            display_code=str(row["display_code"]),
            status=status,
            title=str(row["title"]),
            description=str(row["description"]),
            category=NoticeCategory(category),
            category_label=NOTICE_CATEGORY_LABELS.get(category, category),
            recipient_groups=recipient_groups,
            scope_type=scope_type,
            scope_label=scope_label,
            published_at=row.get("published_at"),
            publish_at=row.get("publish_at"),
            deleted_at=row.get("deleted_at"),
            pinned=bool(row.get("pinned")),
            slot_index=int(row["pin_slot_index"]) if row.get("pin_slot_index") else None,
            view_count=int(row.get("view_count") or 0),
            like_count=int(row.get("like_count") or 0),
            editable=status in {NoticeStatus.DRAFT, NoticeStatus.SCHEDULED},
            created_at=row["created_at"],
            attachments=[NoticeAttachmentResponse(**item) for item in (attachments or [])],
        )

    def _scope_label(
        self,
        scope_type: NoticeScopeType,
        towers: list[dict[str, Any]],
    ) -> str | None:
        if scope_type == NoticeScopeType.WHOLE_SOCIETY:
            return "Whole society"
        if not towers:
            return None
        return ", ".join(str(t["tower_name"]) for t in towers)

    def _resolve_publish_state(
        self,
        *,
        publish_mode: NoticePublishMode,
        publish_at: datetime | None,
        recipient_groups: list[str],
        scope_type: NoticeScopeType,
        tower_ids: list[str],
        current_status: str | None = None,
    ) -> tuple[str, datetime | None, datetime | None]:
        if publish_mode == NoticePublishMode.DRAFT:
            return NoticeStatus.DRAFT.value, None, None
        self._validate_publish_requirements(
            recipient_groups=recipient_groups,
            scope_type=scope_type,
            tower_ids=tower_ids,
        )
        if publish_mode == NoticePublishMode.NOW:
            return NoticeStatus.LIVE.value, None, datetime.now(timezone.utc)
        if publish_at is None:
            raise ValidationException(
                message_key="notices.errors.schedule_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        self._validate_schedule(publish_at)
        return NoticeStatus.SCHEDULED.value, publish_at, None

    def _validate_content(self, title: str, description: str) -> None:
        if len(title) > 70:
            raise ValidationException(
                message_key="notices.errors.title_too_long",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if len(description) > 600:
            raise ValidationException(
                message_key="notices.errors.description_too_long",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    def _validate_attachments(self, attachments: list[NoticeAttachmentInput] | None) -> None:
        if not attachments:
            return
        if len(attachments) > NOTICE_MAX_ATTACHMENTS:
            raise ValidationException(
                message_key="notices.errors.too_many_attachments",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        try:
            validate_notice_attachment_mimes(attachments)
        except ValueError as exc:
            raise ValidationException(
                message_key="notices.errors.invalid_attachment",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"detail": str(exc)},
            ) from exc
        for item in attachments:
            if item.file_path and not self._is_valid_notice_attachment_path(item.file_path):
                raise ValidationException(
                    message_key="notices.errors.invalid_attachment",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )

    @staticmethod
    def _is_valid_notice_attachment_path(file_path: str) -> bool:
        """Ensure attachment path uses notice upload prefix."""
        normalized = file_path.strip().lower()
        return "/notices/" in normalized or normalized.startswith("notices/")

    def _validate_publish_requirements(
        self,
        *,
        recipient_groups: list[str],
        scope_type: NoticeScopeType,
        tower_ids: list[str],
    ) -> None:
        if not recipient_groups:
            raise ValidationException(
                message_key="notices.errors.recipients_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if scope_type == NoticeScopeType.BY_TOWER and not tower_ids:
            raise ValidationException(
                message_key="notices.errors.towers_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    def _validate_schedule(self, publish_at: datetime) -> None:
        now = datetime.now(timezone.utc)
        if publish_at.tzinfo is None:
            publish_at = publish_at.replace(tzinfo=timezone.utc)
        max_date = now + timedelta(days=NOTICE_SCHEDULE_MAX_DAYS)
        if publish_at > max_date:
            raise ValidationException(
                message_key="notices.errors.schedule_too_far",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    def _assert_editable(self, status: str) -> None:
        if status not in {NoticeStatus.DRAFT.value, NoticeStatus.SCHEDULED.value}:
            raise ConflictException(
                message_key="notices.errors.not_editable",
                custom_code=CustomStatusCode.CONFLICT,
            )

    def _assert_duplicatable(self, status: str) -> None:
        if status == NoticeStatus.LIVE.value:
            raise ConflictException(
                message_key="notices.errors.duplicate_live_forbidden",
                custom_code=CustomStatusCode.CONFLICT,
            )
        if status == NoticeStatus.DELETED.value:
            raise ConflictException(
                message_key="notices.errors.not_editable",
                custom_code=CustomStatusCode.CONFLICT,
            )

    @staticmethod
    def _groups_to_values(groups: list | None) -> list[str]:
        if not groups:
            return []
        return [g.value if hasattr(g, "value") else str(g) for g in groups]

    @staticmethod
    def _pin_expires_at(pin_duration: NoticePinDuration) -> datetime | None:
        now = datetime.now(timezone.utc)
        if pin_duration == NoticePinDuration.HOURS_24:
            return now + timedelta(hours=24)
        if pin_duration == NoticePinDuration.HOURS_72:
            return now + timedelta(hours=72)
        return None

    async def _dispatch_publish_push(
        self,
        *,
        project_id: str,
        notice_id: str,
        title: str,
    ) -> None:
        """Send push notifications when a notice goes live."""
        recipient_groups = await self.repo.list_recipient_groups(
            organization_id=self.organization_id,
            notice_id=notice_id,
        )
        towers = await self.repo.list_towers_for_notice(
            organization_id=self.organization_id,
            notice_id=notice_id,
        )
        notice_row = await self.repo.fetch_notice_by_id(
            organization_id=self.organization_id,
            project_id=project_id,
            notice_id=notice_id,
        )
        if notice_row is None:
            return
        user_ids = await self.recipient_service.resolve_recipient_user_ids(
            organization_id=self.organization_id,
            project_id=project_id,
            notice_id=notice_id,
            recipient_groups=recipient_groups,
            scope_type=str(notice_row["scope_type"]),
            tower_ids=[str(t["tower_id"]) for t in towers],
        )
        for user_id in user_ids:
            await self.push_dispatcher.send_to_user(
                organization_id=self.organization_id,
                recipient_user_id=user_id,
                message_key="notifications.push.notices.published",
                notification_type="notice_published",
                feed_type="notices",
                template_params={"title": title},
            )
