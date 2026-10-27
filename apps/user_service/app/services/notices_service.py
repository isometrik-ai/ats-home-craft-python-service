"""Notice board business logic."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
from fastapi import BackgroundTasks

from apps.user_service.app.db.repositories.notices_repository import NoticesRepository
from apps.user_service.app.schemas.enums import (
    INVOICE_UNPAID_RECIPIENT_GROUPS,
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
    NoticeRepeatInput,
    NoticeRepeatResponse,
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
from apps.user_service.app.services.notice_repeat import (
    WEEKDAY_NAMES,
    NoticeRepeatRule,
    activate_repeat,
    empty_repeat_columns,
    first_occurrence,
    parse_repeat_input,
    repeat_columns,
    rule_from_row,
    summary_text,
    upcoming_sends,
)
from apps.user_service.app.services.push_notification_dispatch import (
    PushNotificationDispatcher,
)
from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.app.utils.notice_targeting_utils import expand_recipient_groups
from libs.shared_db.drivers.asyncpg_client import AcquireConnection, get_pool
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("notices_service")


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
        """Organization id from context."""
        return self.user_context.organization_id

    @property
    def user_id(self) -> str | None:
        """Authenticated user id from context."""
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
        background_tasks: BackgroundTasks | None = None,
    ) -> NoticeDetailResponse:
        """Create draft, scheduled, or live notice."""
        self._validate_content(body.title, body.description)
        self._validate_attachments(body.attachments)

        recipient_groups = self._groups_to_values(body.recipient_groups)
        contact_ids: list[str] = []
        tower_ids = body.tower_ids or []
        unit_ids = await self._resolved_notice_unit_ids(
            project_id=project_id,
            recipient_groups=recipient_groups,
            scope_type=body.scope_type,
            tower_ids=tower_ids,
            unit_ids=list(body.unit_ids or []),
        )
        await self._validate_invoice_unpaid_audience(
            project_id=project_id,
            category=body.category,
            recipient_groups=recipient_groups,
            contact_ids=contact_ids,
            scope_type=body.scope_type,
            tower_ids=tower_ids,
            unit_ids=unit_ids,
            publishing=body.publish_mode in {NoticePublishMode.NOW, NoticePublishMode.SCHEDULE},
        )

        status, publish_at, published_at, repeat_fields, fired_at = self._schedule_for_write(
            publish_mode=body.publish_mode,
            publish_at=body.publish_at,
            repeat=body.repeat,
            recipient_groups=recipient_groups,
            contact_ids=contact_ids,
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
            is_flash=body.is_flash,
            repeat=repeat_fields,
        )
        notice_id = str(row["id"])
        await self._replace_children(
            notice_id=notice_id,
            recipient_groups=recipient_groups,
            scope_type=body.scope_type,
            tower_ids=tower_ids,
            unit_ids=unit_ids,
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
                occurrence_at=fired_at,
                background_tasks=background_tasks,
            )

        return await self.get_notice(project_id=project_id, notice_id=notice_id)

    async def update_notice(
        self,
        *,
        project_id: str,
        notice_id: str,
        body: UpdateNoticeRequest,
        background_tasks: BackgroundTasks | None = None,
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
        contact_ids: list[str] = []
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
        if body.unit_ids is not None:
            requested_units = body.unit_ids
        elif scope_type != NoticeScopeType.BY_TOWER:
            requested_units = []
        else:
            requested_units = await self.repo.list_unit_ids(
                organization_id=self.organization_id,
                notice_id=notice_id,
            )
        unit_ids = await self._resolved_notice_unit_ids(
            project_id=project_id,
            recipient_groups=recipient_groups,
            scope_type=scope_type,
            tower_ids=tower_ids,
            unit_ids=requested_units,
        )
        category = body.category or NoticeCategory(str(existing["category"]))
        await self._validate_invoice_unpaid_audience(
            project_id=project_id,
            category=category,
            recipient_groups=recipient_groups,
            contact_ids=contact_ids,
            scope_type=scope_type,
            tower_ids=tower_ids,
            unit_ids=unit_ids,
            publishing=body.publish_mode in {NoticePublishMode.NOW, NoticePublishMode.SCHEDULE},
        )

        fields: dict[str, Any] = {
            "title": title,
            "description": description,
        }
        if body.category is not None:
            fields["category"] = body.category.value
        if body.is_flash is not None:
            fields["is_flash"] = body.is_flash

        fired_at = self._apply_update_schedule(
            existing=existing,
            fields=fields,
            publish_mode=body.publish_mode,
            publish_at=body.publish_at,
            repeat_changed="repeat" in body.model_fields_set,
            repeat=body.repeat,
            recipient_groups=recipient_groups,
            contact_ids=contact_ids,
            scope_type=scope_type,
            tower_ids=tower_ids,
        )

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
            or body.unit_ids is not None
            or body.tower_ids is not None
            or body.scope_type is not None
            or body.attachments is not None
        ):
            await self._replace_children(
                notice_id=notice_id,
                recipient_groups=recipient_groups,
                scope_type=scope_type,
                tower_ids=tower_ids,
                unit_ids=unit_ids,
                attachments=body.attachments
                if body.attachments is not None
                else await self._attachments_from_db(notice_id),
            )

        updated = await self.get_notice(project_id=project_id, notice_id=notice_id)
        if fields.get("status") == NoticeStatus.LIVE.value:
            await self._dispatch_publish_push(
                project_id=project_id,
                notice_id=notice_id,
                title=updated.title,
                occurrence_at=fired_at,
                background_tasks=background_tasks,
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
        unit_ids = await self.repo.list_unit_ids(
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
            is_flash=bool(source.get("is_flash")),
            repeat=self._draft_repeat_columns(source),
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
            unit_ids=unit_ids,
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
        if not groups:
            raise ValidationException(
                message_key="notices.errors.recipients_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        invalid_groups = query.invalid_recipient_groups()
        if invalid_groups:
            raise ValidationException(
                message_key="notices.errors.invalid_recipient_groups",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"groups": ", ".join(invalid_groups)},
            )
        groups = expand_recipient_groups(groups)
        tower_ids = query.parsed_tower_ids()
        if query.scope_type == NoticeScopeType.BY_TOWER and not tower_ids:
            raise ValidationException(
                message_key="notices.errors.towers_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        unit_ids = await self._resolved_notice_unit_ids(
            project_id=project_id,
            recipient_groups=groups,
            scope_type=query.scope_type,
            tower_ids=tower_ids,
            unit_ids=query.parsed_unit_ids(),
        )
        unpaid_only = query.category == NoticeCategory.INVOICE_UNPAID
        await self._validate_invoice_unpaid_audience(
            project_id=project_id,
            category=query.category or NoticeCategory.GENERAL,
            recipient_groups=groups,
            contact_ids=[],
            scope_type=query.scope_type,
            tower_ids=tower_ids,
            unit_ids=unit_ids,
            publishing=False,
        )
        total, breakdown = await self.recipient_service.estimate_reach(
            organization_id=self.organization_id,
            project_id=project_id,
            recipient_groups=groups,
            scope_type=query.scope_type.value,
            tower_ids=tower_ids,
            unit_ids=unit_ids,
            require_unpaid_invoice=unpaid_only,
        )
        return ReachEstimateResponse(estimated_recipients=total, breakdown=breakdown)

    async def publish_due_notices(
        self,
        *,
        project_id: str | None = None,
        background_tasks: BackgroundTasks | None = None,
    ) -> list[str]:
        """Promote scheduled notices and re-send live repeats that are due."""
        sends = await self.repo.claim_due_notice_sends(
            organization_id=self.organization_id,
            project_id=project_id,
        )
        published_ids: list[str] = []
        for send in sends:
            notice_id = str(send["id"])
            published_ids.append(notice_id)
            row = await self.repo.fetch_notice_by_id_only(
                organization_id=self.organization_id,
                notice_id=notice_id,
            )
            if row:
                await self._dispatch_publish_push(
                    project_id=str(row["project_id"]),
                    notice_id=notice_id,
                    title=str(row["title"]),
                    occurrence_at=send["fired_at"] if send["repeating"] else None,
                    background_tasks=background_tasks,
                )
        return published_ids

    async def preview_repeat(self, body: NoticeRepeatInput) -> NoticeRepeatResponse:
        """Preview the next sends for a repeat rule before it is saved."""
        rule = parse_repeat_input(body)
        first_send = first_occurrence(rule)
        if first_send is None:
            raise ValidationException(
                message_key="notices.errors.repeat_has_no_sends",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self._repeat_response(
            rule,
            occurrences_sent=0,
            next_publish_at=first_send,
        )

    async def stop_repeat(
        self,
        *,
        project_id: str,
        notice_id: str,
    ) -> NoticeDetailResponse:
        """Cancel future sends. A notice that already went out stays live."""
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
        if rule_from_row(existing) is None:
            raise ValidationException(
                message_key="notices.errors.repeat_not_set",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if str(existing["status"]) == NoticeStatus.DELETED.value:
            raise ConflictException(
                message_key="notices.errors.not_editable",
                custom_code=CustomStatusCode.CONFLICT,
            )
        fields: dict[str, Any] = {
            "next_publish_at": None,
            "repeat_cancelled_at": datetime.now(timezone.utc),
            "updated_by_user_id": self.user_id,
        }
        if str(existing["status"]) == NoticeStatus.SCHEDULED.value:
            fields["status"] = NoticeStatus.DRAFT.value
            fields["publish_at"] = None
        await self.repo.update_notice_fields(
            organization_id=self.organization_id,
            project_id=project_id,
            notice_id=notice_id,
            fields=fields,
        )
        return await self.get_notice(project_id=project_id, notice_id=notice_id)

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
        unit_ids: list[str],
        attachments: list[NoticeAttachmentInput] | None,
    ) -> None:
        """Replace junction and attachment rows."""
        await self.repo.replace_recipients(
            organization_id=self.organization_id,
            notice_id=notice_id,
            recipient_groups=recipient_groups,
            unit_ids=unit_ids if scope_type == NoticeScopeType.BY_TOWER else [],
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
        """Load notice attachments from the database."""
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
        """Map a notice row to admin detail."""
        notice_id = str(row["id"])
        recipient_groups = await self.repo.list_recipient_groups(
            organization_id=self.organization_id,
            notice_id=notice_id,
        )
        contact_ids: list[str] = []
        unit_ids = await self.repo.list_unit_ids(
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
            contact_ids=contact_ids,
            scope_type=scope_type,
            unit_ids=unit_ids,
            scope_label=scope_label,
            tower_ids=[str(t["tower_id"]) for t in towers],
            tower_names=[str(t["tower_name"]) for t in towers],
            publish_at=row.get("publish_at"),
            published_at=row.get("published_at"),
            deleted_at=row.get("deleted_at"),
            deleted_reason=row.get("deleted_reason"),
            repeat=self._repeat_response_from_row(row),
            attachments=[NoticeAttachmentResponse(**item) for item in attachments],
            pinned=bool(row.get("pinned")),
            slot_index=int(row["pin_slot_index"]) if row.get("pin_slot_index") else None,
            is_flash=bool(row.get("is_flash")),
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
        """Map a notice row to admin list item."""
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
            repeat=self._repeat_response_from_row(row),
            pinned=bool(row.get("pinned")),
            slot_index=int(row["pin_slot_index"]) if row.get("pin_slot_index") else None,
            is_flash=bool(row.get("is_flash")),
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
        """Return a human-readable scope label for a notice."""
        if scope_type == NoticeScopeType.WHOLE_SOCIETY:
            return "Whole society"
        if not towers:
            return None
        return ", ".join(str(t["tower_name"]) for t in towers)

    def _apply_update_schedule(
        self,
        *,
        existing: dict[str, Any],
        fields: dict[str, Any],
        publish_mode: NoticePublishMode | None,
        publish_at: datetime | None,
        repeat_changed: bool,
        repeat: NoticeRepeatInput | None,
        recipient_groups: list[str],
        contact_ids: list[str],
        scope_type: NoticeScopeType,
        tower_ids: list[str],
    ) -> datetime | None:
        """Merge publish and repeat changes into `fields`. Return the fired slot."""
        active_repeat = repeat if repeat_changed else self._repeat_input_from_row(existing)
        mode = self._publish_mode_for_repeat_edit(
            existing=existing,
            publish_mode=publish_mode,
            repeat_changed=repeat_changed,
            active_repeat=active_repeat,
        )
        if mode is not None:
            return self._write_activated_schedule(
                fields=fields,
                publish_mode=mode,
                publish_at=publish_at,
                repeat=active_repeat if repeat_changed or active_repeat is not None else None,
                include_repeat=repeat_changed or active_repeat is not None,
                recipient_groups=recipient_groups,
                contact_ids=contact_ids,
                scope_type=scope_type,
                tower_ids=tower_ids,
            )
        if repeat_changed and active_repeat is None:
            fields.update(empty_repeat_columns())
            if str(existing["status"]) == NoticeStatus.SCHEDULED.value:
                fields["next_publish_at"] = None
            return None
        if publish_at is not None and rule_from_row(existing) is None:
            self._validate_schedule(publish_at)
            self._validate_publish_requirements(
                recipient_groups=recipient_groups,
                contact_ids=contact_ids,
                scope_type=scope_type,
                tower_ids=tower_ids,
            )
            fields["publish_at"] = publish_at
            fields["status"] = NoticeStatus.SCHEDULED.value
            return None
        if repeat_changed and active_repeat is not None:
            fields.update(
                repeat_columns(
                    parse_repeat_input(active_repeat),
                    occurrences_sent=0,
                    next_publish_at=None,
                )
            )
        return None

    def _publish_mode_for_repeat_edit(
        self,
        *,
        existing: dict[str, Any],
        publish_mode: NoticePublishMode | None,
        repeat_changed: bool,
        active_repeat: NoticeRepeatInput | None,
    ) -> NoticePublishMode | None:
        """Re-activate a scheduled notice when its repeat rule changes."""
        if publish_mode is not None or not repeat_changed or active_repeat is None:
            return publish_mode
        if str(existing["status"]) == NoticeStatus.SCHEDULED.value:
            return NoticePublishMode.SCHEDULE
        return publish_mode

    def _write_activated_schedule(
        self,
        *,
        fields: dict[str, Any],
        publish_mode: NoticePublishMode,
        publish_at: datetime | None,
        repeat: NoticeRepeatInput | None,
        include_repeat: bool,
        recipient_groups: list[str],
        contact_ids: list[str],
        scope_type: NoticeScopeType,
        tower_ids: list[str],
    ) -> datetime | None:
        """Store the status chosen by publishing or scheduling."""
        status, resolved_publish_at, published_at, repeat_fields, fired_at = (
            self._schedule_for_write(
                publish_mode=publish_mode,
                publish_at=publish_at,
                repeat=repeat,
                recipient_groups=recipient_groups,
                contact_ids=contact_ids,
                scope_type=scope_type,
                tower_ids=tower_ids,
            )
        )
        fields["status"] = status
        fields["publish_at"] = resolved_publish_at
        fields["published_at"] = published_at
        if include_repeat:
            fields.update(repeat_fields)
        return fired_at

    def _schedule_for_write(
        self,
        *,
        publish_mode: NoticePublishMode,
        publish_at: datetime | None,
        repeat: NoticeRepeatInput | None,
        recipient_groups: list[str],
        contact_ids: list[str],
        scope_type: NoticeScopeType,
        tower_ids: list[str],
    ) -> tuple[str, datetime | None, datetime | None, dict[str, Any], datetime | None]:
        """Resolve status, timestamps, and repeat columns for create or update."""
        if repeat is None:
            status, resolved_publish_at, published_at = self._resolve_publish_state(
                publish_mode=publish_mode,
                publish_at=publish_at,
                recipient_groups=recipient_groups,
                contact_ids=contact_ids,
                scope_type=scope_type,
                tower_ids=tower_ids,
            )
            return status, resolved_publish_at, published_at, empty_repeat_columns(), None

        rule = parse_repeat_input(repeat)
        if publish_mode == NoticePublishMode.DRAFT:
            return (
                NoticeStatus.DRAFT.value,
                None,
                None,
                repeat_columns(rule, occurrences_sent=0, next_publish_at=None),
                None,
            )
        self._validate_publish_requirements(
            recipient_groups=recipient_groups,
            contact_ids=contact_ids,
            scope_type=scope_type,
            tower_ids=tower_ids,
        )
        activation = activate_repeat(rule, datetime.now(timezone.utc))
        return (
            activation.status,
            activation.publish_at,
            activation.published_at,
            repeat_columns(
                rule,
                occurrences_sent=activation.occurrences_sent,
                next_publish_at=activation.next_publish_at,
            ),
            activation.fired_at,
        )

    def _draft_repeat_columns(self, source: dict[str, Any]) -> dict[str, Any]:
        """Copy a repeat rule onto a new draft without a pending send."""
        rule = rule_from_row(source)
        if rule is None:
            return empty_repeat_columns()
        return repeat_columns(rule, occurrences_sent=0, next_publish_at=None)

    @staticmethod
    def _repeat_input_from_row(row: dict[str, Any]) -> NoticeRepeatInput | None:
        """Rebuild API input from a stored rule so an edit can re-activate it."""
        rule = rule_from_row(row)
        if rule is None:
            return None
        return NoticeRepeatInput(
            frequency=rule.frequency,  # type: ignore[arg-type]
            weekdays=[WEEKDAY_NAMES[day - 1] for day in rule.weekdays] or None,
            day_of_month=rule.day_of_month,
            last_day_of_month=rule.last_day_of_month,
            time_of_day=rule.time_of_day.strftime("%H:%M"),
            starts_on=rule.starts_on,
            ends_on=rule.ends_on,
            end_after_count=rule.end_after_count,
        )

    @staticmethod
    def _repeat_response(
        rule: NoticeRepeatRule,
        *,
        occurrences_sent: int,
        next_publish_at: datetime | None,
        cancelled_at: datetime | None = None,
    ) -> NoticeRepeatResponse:
        """Build the repeat block returned to the admin UI."""
        if cancelled_at is not None or (next_publish_at is None and occurrences_sent > 0):
            upcoming: list[datetime] = []
        elif next_publish_at is not None:
            upcoming = upcoming_sends(rule, start_at=next_publish_at)
        else:
            upcoming = upcoming_sends(rule, start_at=None)
        return NoticeRepeatResponse(
            frequency=rule.frequency,  # type: ignore[arg-type]
            weekdays=[WEEKDAY_NAMES[day - 1] for day in rule.weekdays],
            day_of_month=rule.day_of_month,
            last_day_of_month=rule.last_day_of_month,
            time_of_day=rule.time_of_day.strftime("%H:%M"),
            starts_on=rule.starts_on,
            ends_on=rule.ends_on,
            end_after_count=rule.end_after_count,
            occurrences_sent=occurrences_sent,
            next_publish_at=None if cancelled_at is not None else next_publish_at,
            cancelled_at=cancelled_at,
            summary=summary_text(rule),
            upcoming=upcoming,
        )

    def _repeat_response_from_row(self, row: dict[str, Any]) -> NoticeRepeatResponse | None:
        """Map stored repeat columns onto the API response."""
        rule = rule_from_row(row)
        if rule is None:
            return None
        return self._repeat_response(
            rule,
            occurrences_sent=int(row.get("repeat_occurrences_sent") or 0),
            next_publish_at=row.get("next_publish_at"),
            cancelled_at=row.get("repeat_cancelled_at"),
        )

    def _resolve_publish_state(
        self,
        *,
        publish_mode: NoticePublishMode,
        publish_at: datetime | None,
        recipient_groups: list[str],
        contact_ids: list[str],
        scope_type: NoticeScopeType,
        tower_ids: list[str],
        _current_status: str | None = None,
    ) -> tuple[str, datetime | None, datetime | None]:
        """Resolve notice status and publish timestamps from publish mode."""
        if publish_mode == NoticePublishMode.DRAFT:
            return NoticeStatus.DRAFT.value, None, None
        self._validate_publish_requirements(
            recipient_groups=recipient_groups,
            contact_ids=contact_ids,
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
        """Validate notice title and description length limits."""
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
        """Validate notice attachment count, mime types, and paths."""
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

    async def _validate_invoice_unpaid_audience(
        self,
        *,
        project_id: str,
        category: NoticeCategory,
        recipient_groups: list[str],
        contact_ids: list[str],
        scope_type: NoticeScopeType,
        tower_ids: list[str],
        unit_ids: list[str] | None = None,
        publishing: bool,
    ) -> None:
        """Invoice-unpaid notices go only to owners or tenants who still owe."""
        if category != NoticeCategory.INVOICE_UNPAID:
            return
        if any(group not in INVOICE_UNPAID_RECIPIENT_GROUPS for group in recipient_groups):
            raise ValidationException(
                message_key="notices.errors.invoice_unpaid_groups",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if contact_ids and not recipient_groups:
            raise ValidationException(
                message_key="notices.errors.invoice_unpaid_groups",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        allowed: set[str] = set()
        if recipient_groups and (contact_ids or publishing):
            allowed = await self.recipient_service.unpaid_invoice_contact_ids(
                organization_id=self.organization_id,
                project_id=project_id,
                recipient_groups=recipient_groups,
                scope_type=scope_type.value,
                tower_ids=tower_ids,
                unit_ids=unit_ids,
            )
        if contact_ids:
            invalid = [contact_id for contact_id in contact_ids if contact_id not in allowed]
            if invalid:
                raise ValidationException(
                    message_key="notices.errors.invoice_unpaid_contacts",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"contact_ids": ", ".join(invalid[:10])},
                )
        if publishing and not recipient_groups:
            raise ValidationException(
                message_key="notices.errors.invoice_unpaid_groups",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if publishing and not contact_ids and not allowed:
            raise ValidationException(
                message_key="notices.errors.invoice_unpaid_none",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    async def _resolved_notice_unit_ids(
        self,
        *,
        project_id: str,
        recipient_groups: list[str],
        scope_type: NoticeScopeType,
        tower_ids: list[str],
        unit_ids: list[str],
    ) -> list[str]:
        """Units to store for a by-tower Owner or Tenant notice. Empty means every unit."""
        if scope_type != NoticeScopeType.BY_TOWER:
            if unit_ids:
                raise ValidationException(
                    message_key="notices.errors.unit_ids_require_by_tower",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            return []
        if recipient_groups and any(
            group not in INVOICE_UNPAID_RECIPIENT_GROUPS for group in recipient_groups
        ):
            raise ValidationException(
                message_key="notices.errors.scope_owner_tenant_only",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if not unit_ids:
            return []
        if not tower_ids:
            raise ValidationException(
                message_key="notices.errors.towers_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        valid = await self.repo.filter_project_unit_ids(
            organization_id=self.organization_id,
            project_id=project_id,
            unit_ids=unit_ids,
            tower_ids=tower_ids,
        )
        invalid = [unit_id for unit_id in unit_ids if unit_id not in valid]
        if invalid:
            raise ValidationException(
                message_key="notices.errors.invalid_units",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"unit_ids": ", ".join(invalid[:10])},
            )
        return valid

    def _validate_publish_requirements(
        self,
        *,
        recipient_groups: list[str],
        contact_ids: list[str],
        scope_type: NoticeScopeType,
        tower_ids: list[str],
    ) -> None:
        """Ensure recipients and tower scope are set for publish."""
        if not recipient_groups and not contact_ids:
            raise ValidationException(
                message_key="notices.errors.recipients_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if scope_type == NoticeScopeType.BY_TOWER and recipient_groups and not tower_ids:
            raise ValidationException(
                message_key="notices.errors.towers_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    def _validate_schedule(self, publish_at: datetime) -> None:
        """Ensure scheduled publish date is within allowed window."""
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
        """Raise if notice status does not allow editing."""
        if status not in {NoticeStatus.DRAFT.value, NoticeStatus.SCHEDULED.value}:
            raise ConflictException(
                message_key="notices.errors.not_editable",
                custom_code=CustomStatusCode.CONFLICT,
            )

    def _assert_duplicatable(self, status: str) -> None:
        """Raise if notice status does not allow duplication."""
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
        """Convert recipient group enums to persisted string values.

        ``All`` expands to Owner, Tenant, Staff, and Security.
        """
        return expand_recipient_groups(groups)

    @staticmethod
    def _pin_expires_at(pin_duration: NoticePinDuration) -> datetime | None:
        """Return pin expiry time for a banner pin duration."""
        now = datetime.now(timezone.utc)
        if pin_duration == NoticePinDuration.HOURS_24:
            return now + timedelta(hours=24)
        if pin_duration == NoticePinDuration.HOURS_72:
            return now + timedelta(hours=72)
        return None

    @staticmethod
    def _notice_publish_push_fields(
        *,
        project_id: str,
        notice_id: str,
        recipient_user_id: str,
        occurrence_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Deep-link payload fields for notice publish push notifications."""
        idempotency_key = f"notice:{notice_id}:published:{recipient_user_id}"
        if occurrence_at is not None:
            if occurrence_at.tzinfo is None:
                occurrence_at = occurrence_at.replace(tzinfo=timezone.utc)
            token = occurrence_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            idempotency_key = f"notice:{notice_id}:published:{token}:{recipient_user_id}"
        return {
            "data": {
                "notice_id": notice_id,
                "project_id": project_id,
                "screen": "notice_detail",
            },
            "entity": {"kind": "notice", "id": notice_id},
            "options": {
                "click_action": "OPEN_NOTICE",
                "idempotency_key": idempotency_key,
            },
        }

    async def _dispatch_publish_push(
        self,
        *,
        project_id: str,
        notice_id: str,
        title: str,
        occurrence_at: datetime | None = None,
        background_tasks: BackgroundTasks | None = None,
    ) -> None:
        """Send push when a notice goes live, or defer until after the HTTP response."""
        if background_tasks is not None:
            background_tasks.add_task(
                dispatch_notice_publish_push_background,
                organization_id=self.organization_id,
                project_id=project_id,
                notice_id=notice_id,
                title=title,
                occurrence_at=occurrence_at,
            )
            return
        await dispatch_notice_publish_push(
            self.db_connection,
            organization_id=self.organization_id,
            project_id=project_id,
            notice_id=notice_id,
            title=title,
            occurrence_at=occurrence_at,
            repo=self.repo,
            recipient_service=self.recipient_service,
            push_dispatcher=self.push_dispatcher,
        )


async def dispatch_notice_publish_push(
    db_connection: asyncpg.Connection,
    *,
    organization_id: str,
    project_id: str,
    notice_id: str,
    title: str,
    occurrence_at: datetime | None = None,
    notice_row: Mapping[str, Any] | None = None,
    repo: NoticesRepository | None = None,
    recipient_service: NoticeRecipientResolutionService | None = None,
    push_dispatcher: PushNotificationDispatcher | None = None,
) -> None:
    """Resolve recipients and send push notifications for a live notice."""
    repo = repo or NoticesRepository(db_connection=db_connection)
    recipient_service = recipient_service or NoticeRecipientResolutionService(
        db_connection=db_connection
    )
    push_dispatcher = push_dispatcher or PushNotificationDispatcher(db_connection=db_connection)

    recipient_groups = await repo.list_recipient_groups(
        organization_id=organization_id,
        notice_id=notice_id,
    )
    unit_ids = await repo.list_unit_ids(
        organization_id=organization_id,
        notice_id=notice_id,
    )
    towers = await repo.list_towers_for_notice(
        organization_id=organization_id,
        notice_id=notice_id,
    )
    resolved_notice = notice_row
    if resolved_notice is None or resolved_notice.get("scope_type") is None:
        resolved_notice = await repo.fetch_notice_by_id(
            organization_id=organization_id,
            project_id=project_id,
            notice_id=notice_id,
        )
    if resolved_notice is None:
        return
    unpaid_only = str(resolved_notice.get("category") or "") == NoticeCategory.INVOICE_UNPAID.value
    tower_ids = [str(tower["tower_id"]) for tower in towers]
    user_ids: set[str] = set(
        await recipient_service.resolve_recipient_user_ids(
            organization_id=organization_id,
            project_id=project_id,
            notice_id=notice_id,
            recipient_groups=recipient_groups,
            scope_type=str(resolved_notice["scope_type"]),
            tower_ids=tower_ids,
            unit_ids=unit_ids,
            require_unpaid_invoice=unpaid_only,
        )
    )
    for user_id in user_ids:
        push_fields = NoticesService._notice_publish_push_fields(
            project_id=project_id,
            notice_id=notice_id,
            recipient_user_id=user_id,
            occurrence_at=occurrence_at,
        )
        await push_dispatcher.send_to_user(
            organization_id=organization_id,
            recipient_user_id=user_id,
            message_key="notifications.push.notices.published",
            notification_type="notice_published",
            feed_type="notices",
            params={"title": title},
            data=push_fields["data"],
            entity=push_fields["entity"],
            options=push_fields["options"],
        )


async def dispatch_notice_publish_push_background(
    *,
    organization_id: str,
    project_id: str,
    notice_id: str,
    title: str,
    occurrence_at: datetime | None = None,
) -> None:
    """Best-effort publish push using a dedicated DB connection."""
    try:
        pool = await get_pool()
        async with AcquireConnection(pool) as conn:
            await dispatch_notice_publish_push(
                conn,
                organization_id=organization_id,
                project_id=project_id,
                notice_id=notice_id,
                title=title,
                occurrence_at=occurrence_at,
            )
    except Exception:
        logger.exception(
            "notice_publish_push_background_failed",
            extra={
                "organization_id": organization_id,
                "project_id": project_id,
                "notice_id": notice_id,
            },
        )
