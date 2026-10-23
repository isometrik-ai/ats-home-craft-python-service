"""Notice board business logic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

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
    NoticeSeriesOccurrenceResponse,
    NoticeSummaryResponse,
    PinNoticeRequest,
    ReachEstimateQuery,
    ReachEstimateResponse,
    UpdateNoticeRequest,
    validate_notice_attachment_mimes,
)
from apps.user_service.app.services import notice_recurring_support as recurring
from apps.user_service.app.services.notice_recipient_resolution_service import (
    NoticeRecipientResolutionService,
)
from apps.user_service.app.services.push_notification_dispatch import (
    PushNotificationDispatcher,
)
from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.app.utils.notice_targeting_utils import expand_recipient_groups
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode


@dataclass(frozen=True)
class _NoticeUpdateTargeting:
    """Resolved recipients and scope for a notice PATCH."""

    recipient_groups: list[str]
    contact_ids: list[str]
    scope_type: NoticeScopeType
    tower_ids: list[str]


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
        recurring_ids = [str(row["id"]) for row in rows if recurring.has_series(row)]
        weekly_by_notice = await self.repo.list_series_weekly_days_for_notices(
            organization_id=self.organization_id,
            notice_ids=recurring_ids,
        )
        project_tz = recurring.parse_org_timezone(
            await self.repo.fetch_project_timezone(
                organization_id=self.organization_id,
                project_id=project_id,
            )
        )
        items: list[NoticeListItemResponse] = []
        for row in rows:
            notice_id = str(row["id"])
            items.append(
                await self._to_list_item(
                    row,
                    attachments=attachments_by_notice.get(notice_id, []),
                    weekly_days=weekly_by_notice.get(notice_id, []),
                    project_tz=project_tz,
                )
            )
        return items, total

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
        contact_ids = await self._validate_project_contact_ids(
            project_id=project_id,
            contact_ids=list(body.contact_ids or []),
        )
        tower_ids = body.tower_ids or []

        repeat = body.repeat
        if repeat and repeat.enabled:
            if body.publish_mode == NoticePublishMode.START_SERIES:
                pass  # validated via repeat.enabled
            status, publish_at, published_at = self._resolve_recurring_publish_state(
                publish_mode=body.publish_mode,
                recipient_groups=recipient_groups,
                contact_ids=contact_ids,
                scope_type=body.scope_type,
                tower_ids=tower_ids,
            )
        else:
            status, publish_at, published_at = self._resolve_publish_state(
                publish_mode=body.publish_mode,
                publish_at=body.publish_at,
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
        )
        notice_id = str(row["id"])
        await self._replace_children(
            notice_id=notice_id,
            recipient_groups=recipient_groups,
            contact_ids=contact_ids,
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

        if repeat and repeat.enabled:
            await recurring.repeat_to_storage(
                self.repo,
                organization_id=self.organization_id,
                project_id=project_id,
                notice_id=notice_id,
                repeat=repeat,
                start_series=body.publish_mode == NoticePublishMode.START_SERIES,
            )

        detail = await self.get_notice(project_id=project_id, notice_id=notice_id)
        if repeat and repeat.enabled and (body.preview_recurrence or body.publish_mode):
            detail.next_sends_preview = await recurring.build_preview(
                self.repo,
                organization_id=self.organization_id,
                project_id=project_id,
                repeat=repeat,
            )
        return detail

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
        self._assert_editable(str(existing["status"]), existing)

        if body.publish_mode == NoticePublishMode.END_SERIES:
            return await self._update_notice_end_series(
                project_id=project_id,
                notice_id=notice_id,
                existing=existing,
            )

        title = body.title.strip() if body.title is not None else existing["title"]
        description = (
            body.description.strip() if body.description is not None else existing["description"]
        )
        self._validate_content(title, description)
        if body.attachments is not None:
            self._validate_attachments(body.attachments)

        targeting = await self._collect_notice_update_targeting(
            body=body,
            existing=existing,
            project_id=project_id,
            notice_id=notice_id,
        )
        fields = self._build_notice_update_fields(
            body=body,
            existing=existing,
            title=title,
            description=description,
            targeting=targeting,
        )
        await self.repo.update_notice_fields(
            organization_id=self.organization_id,
            project_id=project_id,
            notice_id=notice_id,
            fields=fields,
        )

        if self._notice_update_replaces_children(body):
            attachments = body.attachments
            if attachments is None:
                attachments = await self._attachments_from_db(notice_id)
            await self._replace_children(
                notice_id=notice_id,
                recipient_groups=targeting.recipient_groups,
                contact_ids=targeting.contact_ids,
                scope_type=targeting.scope_type,
                tower_ids=targeting.tower_ids,
                attachments=attachments,
            )

        await self._sync_notice_update_repeat(
            body=body,
            existing=existing,
            project_id=project_id,
            notice_id=notice_id,
        )
        return await self._complete_notice_update(
            body=body,
            existing=existing,
            project_id=project_id,
            notice_id=notice_id,
        )

    async def _update_notice_end_series(
        self,
        *,
        project_id: str,
        notice_id: str,
        existing: dict[str, Any],
    ) -> NoticeDetailResponse:
        """Stop future recurring sends and return refreshed detail."""
        if not existing.get("series_frequency"):
            raise ValidationException(
                message_key="notices.errors.not_recurring",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        await self.repo.end_notice_series(
            organization_id=self.organization_id,
            notice_id=notice_id,
        )
        return await self.get_notice(project_id=project_id, notice_id=notice_id)

    async def _collect_notice_update_targeting(
        self,
        *,
        body: UpdateNoticeRequest,
        existing: dict[str, Any],
        project_id: str,
        notice_id: str,
    ) -> _NoticeUpdateTargeting:
        """Merge PATCH targeting fields with stored notice recipients."""
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
        current_contact_ids = await self.repo.list_contact_recipient_ids(
            organization_id=self.organization_id,
            notice_id=notice_id,
        )
        if body.contact_ids is not None:
            contact_ids = await self._validate_project_contact_ids(
                project_id=project_id,
                contact_ids=list(body.contact_ids),
            )
        else:
            contact_ids = current_contact_ids
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
        return _NoticeUpdateTargeting(
            recipient_groups=recipient_groups,
            contact_ids=contact_ids,
            scope_type=scope_type,
            tower_ids=tower_ids,
        )

    def _build_notice_update_fields(
        self,
        *,
        body: UpdateNoticeRequest,
        existing: dict[str, Any],
        title: str,
        description: str,
        targeting: _NoticeUpdateTargeting,
    ) -> dict[str, Any]:
        """Assemble column updates for PATCH."""
        fields: dict[str, Any] = {
            "title": title,
            "description": description,
        }
        if body.category is not None:
            fields["category"] = body.category.value
        self._apply_notice_update_publish_fields(
            body=body,
            existing=existing,
            fields=fields,
            targeting=targeting,
        )
        if body.scope_type is not None:
            fields["scope_type"] = body.scope_type.value
        fields["updated_by_user_id"] = self.user_id
        return fields

    def _apply_notice_update_publish_fields(
        self,
        *,
        body: UpdateNoticeRequest,
        existing: dict[str, Any],
        fields: dict[str, Any],
        targeting: _NoticeUpdateTargeting,
    ) -> None:
        """Apply publish_mode / publish_at to the update fields dict."""
        publish_mode = body.publish_mode
        publish_at_input = body.publish_at
        if publish_mode is not None:
            if body.repeat and body.repeat.enabled:
                status, publish_at, published_at = self._resolve_recurring_publish_state(
                    publish_mode=publish_mode,
                    recipient_groups=targeting.recipient_groups,
                    contact_ids=targeting.contact_ids,
                    scope_type=targeting.scope_type,
                    tower_ids=targeting.tower_ids,
                )
            else:
                status, publish_at, published_at = self._resolve_publish_state(
                    publish_mode=publish_mode,
                    publish_at=publish_at_input,
                    recipient_groups=targeting.recipient_groups,
                    contact_ids=targeting.contact_ids,
                    scope_type=targeting.scope_type,
                    tower_ids=targeting.tower_ids,
                    _current_status=str(existing["status"]),
                )
            fields["status"] = status
            fields["publish_at"] = publish_at
            fields["published_at"] = published_at
            return
        if publish_at_input is None:
            return
        self._validate_schedule(publish_at_input)
        self._validate_publish_requirements(
            recipient_groups=targeting.recipient_groups,
            contact_ids=targeting.contact_ids,
            scope_type=targeting.scope_type,
            tower_ids=targeting.tower_ids,
        )
        fields["publish_at"] = publish_at_input
        fields["status"] = NoticeStatus.SCHEDULED.value

    @staticmethod
    def _notice_update_replaces_children(body: UpdateNoticeRequest) -> bool:
        """True when PATCH replaces recipients, scope, towers, or attachments."""
        return (
            body.recipient_groups is not None
            or body.contact_ids is not None
            or body.tower_ids is not None
            or body.scope_type is not None
            or body.attachments is not None
        )

    async def _sync_notice_update_repeat(
        self,
        *,
        body: UpdateNoticeRequest,
        existing: dict[str, Any],
        project_id: str,
        notice_id: str,
    ) -> None:
        """Persist repeat block changes and refresh next_send_at."""
        if not body.repeat or not body.repeat.enabled:
            return
        if existing.get("series_repeat_ended_at"):
            raise ConflictException(
                message_key="notices.errors.recurrence_ended",
                custom_code=CustomStatusCode.CONFLICT,
            )
        start_series = body.publish_mode == NoticePublishMode.START_SERIES
        await recurring.repeat_to_storage(
            self.repo,
            organization_id=self.organization_id,
            project_id=project_id,
            notice_id=notice_id,
            repeat=body.repeat,
            start_series=start_series,
        )
        refreshed = await self.repo.fetch_notice_by_id(
            organization_id=self.organization_id,
            project_id=project_id,
            notice_id=notice_id,
        )
        if refreshed is None or not refreshed.get("series_frequency"):
            return
        if not start_series and refreshed.get("series_next_send_at") is None:
            return
        next_at = await recurring.recompute_next_send_from_row(
            self.repo,
            organization_id=self.organization_id,
            project_id=project_id,
            notice_id=notice_id,
            row=refreshed,
        )
        await self.repo.update_notice_series(
            organization_id=self.organization_id,
            notice_id=notice_id,
            fields={"next_send_at": next_at},
        )

    async def _complete_notice_update(
        self,
        *,
        body: UpdateNoticeRequest,
        existing: dict[str, Any],
        project_id: str,
        notice_id: str,
    ) -> NoticeDetailResponse:
        """Load detail, optional preview, and one-time publish push after PATCH."""
        updated = await self.get_notice(project_id=project_id, notice_id=notice_id)
        if body.repeat and body.repeat.enabled and body.preview_recurrence:
            updated.next_sends_preview = await recurring.build_preview(
                self.repo,
                organization_id=self.organization_id,
                project_id=project_id,
                repeat=body.repeat,
            )
        if updated.status == NoticeStatus.LIVE and not recurring.series_active(existing):
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

        if existing.get("series_frequency") and existing.get("series_repeat_ended_at") is None:
            await self.repo.end_notice_series(
                organization_id=self.organization_id,
                notice_id=notice_id,
            )

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
        if await self.repo.notice_has_series(
            organization_id=self.organization_id,
            notice_id=notice_id,
        ):
            raise ConflictException(
                message_key="notices.errors.duplicate_recurring_forbidden",
                custom_code=CustomStatusCode.CONFLICT,
            )
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
        contact_ids = await self.repo.list_contact_recipient_ids(
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
            contact_ids=contact_ids,
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
        """Promote due one-time scheduled notices and deliver due recurring notices."""
        from apps.user_service.app.jobs.publish_scheduled_notices import (
            process_recurring_notices as run_recurring_job,
        )

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
        recurring_ids = await run_recurring_job(
            self.db_connection,
            organization_id=self.organization_id,
            project_id=project_id,
        )
        return list(dict.fromkeys([*published_ids, *recurring_ids]))

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
        contact_ids: list[str],
        scope_type: NoticeScopeType,
        tower_ids: list[str],
        attachments: list[NoticeAttachmentInput] | None,
    ) -> None:
        """Replace junction and attachment rows."""
        await self.repo.replace_recipients(
            organization_id=self.organization_id,
            notice_id=notice_id,
            recipient_groups=recipient_groups,
            contact_ids=contact_ids,
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
        contact_ids = await self.repo.list_contact_recipient_ids(
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
        is_recurring = recurring.has_series(row)
        repeats_label = None
        if is_recurring:
            repeats_label = await recurring.repeats_label_for_row(
                self.repo,
                organization_id=self.organization_id,
                project_id=str(row["project_id"]),
                notice_id=notice_id,
                row=row,
            )
        occurrences = []
        if row.get("series_frequency"):
            occ_rows = await self.repo.list_series_occurrences(
                organization_id=self.organization_id,
                notice_id=notice_id,
            )
            occurrences = [
                NoticeSeriesOccurrenceResponse(
                    occurrence_index=int(item["occurrence_index"]),
                    planned_at=item["planned_at"],
                    published_at=item.get("published_at"),
                )
                for item in occ_rows
            ]
        editable = status in {
            NoticeStatus.DRAFT,
            NoticeStatus.SCHEDULED,
        } or recurring.series_active(row)
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
            editable=editable,
            duplicate_of_id=row.get("duplicate_of_id"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            created_by_user_id=row.get("created_by_user_id"),
            is_recurring=is_recurring,
            repeats_label=repeats_label,
            next_send_at=row.get("series_next_send_at"),
            series_starts_on=row.get("series_starts_on"),
            series_ends_label=recurring.format_series_ends_label(row),
            occurrences=occurrences,
        )

    async def _to_list_item(
        self,
        row: dict[str, Any],
        *,
        attachments: list[dict[str, Any]] | None = None,
        weekly_days: list[int] | None = None,
        project_tz: ZoneInfo | None = None,
    ) -> NoticeListItemResponse:
        """Map a notice row to admin list item."""
        status = NoticeStatus(str(row["status"]))
        category = str(row["category"])
        scope_type = NoticeScopeType(str(row["scope_type"]))
        recipient_groups = list(row.get("recipient_groups") or [])
        scope_label = str(row["scope_label"]) if row.get("scope_label") else None
        if scope_type == NoticeScopeType.WHOLE_SOCIETY:
            scope_label = "Whole society"
        is_recurring = recurring.has_series(row)
        repeats_label = None
        if is_recurring and project_tz is not None:
            repeats_label = recurring.repeats_label_for_stored_row(
                row=row,
                weekly_days=weekly_days,
                project_tz=project_tz,
            )
        footer = recurring.recurrence_footer(row, repeats_label)
        editable = status in {
            NoticeStatus.DRAFT,
            NoticeStatus.SCHEDULED,
        } or recurring.series_active(row)
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
            editable=editable,
            created_at=row["created_at"],
            attachments=[NoticeAttachmentResponse(**item) for item in (attachments or [])],
            is_recurring=is_recurring,
            repeats_label=repeats_label,
            next_send_at=row.get("series_next_send_at"),
            recurrence_footer=footer,
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

    def _resolve_recurring_publish_state(
        self,
        *,
        publish_mode: NoticePublishMode,
        recipient_groups: list[str],
        contact_ids: list[str],
        scope_type: NoticeScopeType,
        tower_ids: list[str],
    ) -> tuple[str, datetime | None, datetime | None]:
        """Resolve status for recurring create/update."""
        if publish_mode in (NoticePublishMode.NOW, NoticePublishMode.SCHEDULE):
            raise ValidationException(
                message_key="notices.errors.invalid_publish_mode_for_repeat",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if publish_mode == NoticePublishMode.DRAFT:
            return NoticeStatus.DRAFT.value, None, None
        if publish_mode == NoticePublishMode.END_SERIES:
            raise ValidationException(
                message_key="notices.errors.not_recurring",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        self._validate_publish_requirements(
            recipient_groups=recipient_groups,
            contact_ids=contact_ids,
            scope_type=scope_type,
            tower_ids=tower_ids,
        )
        return NoticeStatus.SCHEDULED.value, None, None

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

    def _assert_editable(self, status: str, row: dict[str, Any] | None = None) -> None:
        """Raise if notice status does not allow editing."""
        if status == NoticeStatus.DELETED.value:
            raise ConflictException(
                message_key="notices.errors.not_editable",
                custom_code=CustomStatusCode.CONFLICT,
            )
        if status == NoticeStatus.LIVE.value:
            if row and recurring.series_active(row):
                return
            raise ConflictException(
                message_key="notices.errors.not_editable",
                custom_code=CustomStatusCode.CONFLICT,
            )
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

    async def _validate_project_contact_ids(
        self,
        *,
        project_id: str,
        contact_ids: list[str],
    ) -> list[str]:
        """Ensure contact ids belong to the project or raise validation error."""
        if not contact_ids:
            return []
        valid_ids = await self.recipient_service.filter_valid_project_contact_ids(
            organization_id=self.organization_id,
            project_id=project_id,
            contact_ids=contact_ids,
        )
        valid_set = set(valid_ids)
        invalid = [contact_id for contact_id in contact_ids if contact_id not in valid_set]
        if invalid:
            raise ValidationException(
                message_key="notices.errors.invalid_contact_recipients",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"contact_ids": ", ".join(invalid[:10])},
            )
        return valid_ids

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
    ) -> dict[str, Any]:
        """Deep-link payload fields for notice publish push notifications."""
        return {
            "data": {
                "notice_id": notice_id,
                "project_id": project_id,
                "screen": "notice_detail",
            },
            "entity": {"kind": "notice", "id": notice_id},
            "options": {
                "click_action": "OPEN_NOTICE",
                "idempotency_key": f"notice:{notice_id}:published:{recipient_user_id}",
            },
        }

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
        contact_ids = await self.repo.list_contact_recipient_ids(
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
        user_ids: set[str] = set(
            await self.recipient_service.resolve_recipient_user_ids(
                organization_id=self.organization_id,
                project_id=project_id,
                notice_id=notice_id,
                recipient_groups=recipient_groups,
                scope_type=str(notice_row["scope_type"]),
                tower_ids=[str(t["tower_id"]) for t in towers],
            )
        )
        if contact_ids:
            user_ids.update(
                await self.recipient_service.resolve_user_ids_for_contact_ids(
                    organization_id=self.organization_id,
                    contact_ids=contact_ids,
                )
            )
        for user_id in user_ids:
            push_fields = self._notice_publish_push_fields(
                project_id=project_id,
                notice_id=notice_id,
                recipient_user_id=user_id,
            )
            await self.push_dispatcher.send_to_user(
                organization_id=self.organization_id,
                recipient_user_id=user_id,
                message_key="notifications.push.notices.published",
                notification_type="notice_published",
                feed_type="notices",
                params={"title": title},
                data=push_fields["data"],
                entity=push_fields["entity"],
                options=push_fields["options"],
            )
