"""Notice board business logic."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
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
    NoticeRecurrenceEndType,
    NoticeRecurrenceFrequency,
    NoticeScopeType,
    NoticeSeriesAction,
    NoticeSeriesStatus,
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
    NoticeRecurrencePreviewRequest,
    NoticeRecurrencePreviewResponse,
    NoticeRepeatInput,
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
from apps.user_service.app.utils.notice_recurrence import (
    RecurrenceRule,
    exceeds_after_count,
    exceeds_end_on_date,
    first_occurrence,
    next_occurrence_after,
    preview_recurrence,
    rule_from_notice_row,
)
from apps.user_service.app.utils.notice_targeting_utils import expand_recipient_groups
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode

DEFAULT_RECURRENCE_TIMEZONE = "Asia/Kolkata"


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
    ) -> NoticeDetailResponse:
        """Create draft, scheduled, or live notice."""
        if body.repeat is not None:
            return await self._create_recurring_notice(project_id=project_id, body=body)
        self._validate_content(body.title, body.description)
        self._validate_attachments(body.attachments)

        recipient_groups = self._groups_to_values(body.recipient_groups)
        contact_ids = await self._validate_project_contact_ids(
            project_id=project_id,
            contact_ids=list(body.contact_ids or []),
        )
        tower_ids = body.tower_ids or []

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

        return await self.get_notice(project_id=project_id, notice_id=notice_id)

    async def update_notice(
        self,
        *,
        project_id: str,
        notice_id: str,
        body: UpdateNoticeRequest,
    ) -> NoticeDetailResponse:
        """Update draft or scheduled notice."""
        # pylint: disable=too-complex
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
        if existing.get("is_recurring_template"):
            return await self._update_recurring_template(
                project_id=project_id,
                notice_id=notice_id,
                existing=existing,
                body=body,
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
                contact_ids=contact_ids,
                scope_type=scope_type,
                tower_ids=tower_ids,
                _current_status=str(existing["status"]),
            )
            fields["status"] = status
            fields["publish_at"] = publish_at
            fields["published_at"] = published_at
        elif publish_at_input is not None:
            self._validate_schedule(publish_at_input)
            self._validate_publish_requirements(
                recipient_groups=recipient_groups,
                contact_ids=contact_ids,
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
            or body.contact_ids is not None
            or body.tower_ids is not None
            or body.scope_type is not None
            or body.attachments is not None
        ):
            await self._replace_children(
                notice_id=notice_id,
                recipient_groups=recipient_groups,
                contact_ids=contact_ids,
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
        if source.get("is_recurring_template") and str(source.get("series_status")) not in {
            NoticeSeriesStatus.DRAFT.value,
            NoticeSeriesStatus.PAUSED.value,
        }:
            raise ConflictException(
                message_key="notices.errors.series_not_editable",
                custom_code=CustomStatusCode.CONFLICT,
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
        recurring_ids = await self.materialize_due_recurring_templates(project_id=project_id)
        return published_ids + recurring_ids

    async def preview_recurrence(
        self,
        *,
        body: NoticeRecurrencePreviewRequest,
    ) -> NoticeRecurrencePreviewResponse:
        """Return human summary and upcoming sends for a repeat block."""
        recurrence_tz = body.timezone or DEFAULT_RECURRENCE_TIMEZONE
        rule = self._repeat_input_to_rule(body.repeat)
        self._validate_repeat_rule(rule)
        preview = preview_recurrence(rule, timezone=recurrence_tz, count=4)
        return NoticeRecurrencePreviewResponse(
            recurrence_summary=preview.recurrence_summary,
            first_send_at=preview.first_send_at,
            upcoming_sends=preview.upcoming_sends,
        )

    async def materialize_due_recurring_templates(
        self,
        *,
        project_id: str | None = None,
    ) -> list[str]:
        """Materialize at most one live notice per due recurring template."""
        templates = await self.repo.lock_due_recurring_templates(
            organization_id=self.organization_id,
            project_id=project_id,
            limit=50,
        )
        instance_ids: list[str] = []
        for template in templates:
            if str(template.get("organization_id")) != self.organization_id:
                continue
            instance_id = await self._materialize_recurring_template(template)
            if instance_id:
                instance_ids.append(instance_id)
        return instance_ids

    async def _create_recurring_notice(
        self,
        *,
        project_id: str,
        body: CreateNoticeRequest,
    ) -> NoticeDetailResponse:
        """Create a recurring notice template."""
        self._validate_content(body.title, body.description)
        self._validate_attachments(body.attachments)
        if body.publish_mode in {NoticePublishMode.NOW, NoticePublishMode.SCHEDULE}:
            raise ValidationException(
                message_key="notices.errors.repeat_with_schedule",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if body.publish_mode not in {NoticePublishMode.DRAFT, NoticePublishMode.SERIES_START}:
            raise ValidationException(
                message_key="notices.errors.invalid_publish_mode",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        assert body.repeat is not None
        recipient_groups = self._groups_to_values(body.recipient_groups)
        contact_ids = await self._validate_project_contact_ids(
            project_id=project_id,
            contact_ids=list(body.contact_ids or []),
        )
        tower_ids = body.tower_ids or []
        self._validate_publish_requirements(
            recipient_groups=recipient_groups,
            contact_ids=contact_ids,
            scope_type=body.scope_type,
            tower_ids=tower_ids,
        )
        rule = self._repeat_input_to_rule(body.repeat)
        self._validate_repeat_rule(rule)
        recurrence_tz = DEFAULT_RECURRENCE_TIMEZONE
        series_status = NoticeSeriesStatus.DRAFT.value
        next_at: datetime | None = None
        if body.publish_mode == NoticePublishMode.SERIES_START:
            series_status = NoticeSeriesStatus.ACTIVE.value
            next_at = first_occurrence(rule, timezone=recurrence_tz)

        sequence_number = await self.repo.allocate_sequence_number(
            organization_id=self.organization_id,
            project_id=project_id,
        )
        display_code = f"NTC-{sequence_number}"
        pin_duration: str | None = body.pin_duration.value if body.pin_to_banner else None
        row = await self.repo.insert_recurring_template(
            organization_id=self.organization_id,
            project_id=project_id,
            display_code=display_code,
            sequence_number=sequence_number,
            title=body.title.strip(),
            description=body.description.strip(),
            category=body.category.value,
            scope_type=body.scope_type.value,
            series_status=series_status,
            recurrence_frequency=rule.frequency.value,
            recurrence_weekly_days=list(rule.weekly_days) if rule.weekly_days else None,
            recurrence_monthly_day=rule.monthly_day,
            recurrence_monthly_use_last_day=rule.monthly_use_last_day,
            recurrence_time_of_day=rule.time_of_day,
            recurrence_timezone=recurrence_tz,
            recurrence_starts_on=rule.starts_on,
            recurrence_end_type=rule.end_type.value,
            recurrence_end_date=rule.end_date,
            recurrence_max_occurrences=rule.max_occurrences,
            next_occurrence_at=next_at,
            series_pin_to_banner=body.pin_to_banner,
            series_slot_index=body.slot_index,
            series_pin_duration=pin_duration,
            series_pin_confirm_replace=body.confirm_pin_replace,
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
        if next_at is not None and next_at <= datetime.now(timezone.utc):
            await self._materialize_recurring_template(
                await self.repo.fetch_notice_by_id(
                    organization_id=self.organization_id,
                    project_id=project_id,
                    notice_id=notice_id,
                )
                or row
            )
        return await self.get_notice(project_id=project_id, notice_id=notice_id)

    async def _update_recurring_template(
        self,
        *,
        project_id: str,
        notice_id: str,
        existing: dict[str, Any],
        body: UpdateNoticeRequest,
    ) -> NoticeDetailResponse:
        """Update or control lifecycle of a recurring template."""
        # pylint: disable=too-complex
        series_status = str(existing.get("series_status") or "")
        if body.series_action is not None:
            return await self._apply_series_action(
                project_id=project_id,
                notice_id=notice_id,
                existing=existing,
                action=body.series_action,
            )
        if body.publish_mode is not None:
            raise ValidationException(
                message_key="notices.errors.repeat_with_schedule",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if series_status not in {
            NoticeSeriesStatus.DRAFT.value,
            NoticeSeriesStatus.PAUSED.value,
        }:
            raise ConflictException(
                message_key="notices.errors.series_not_editable",
                custom_code=CustomStatusCode.CONFLICT,
            )

        title = body.title.strip() if body.title is not None else existing["title"]
        description = (
            body.description.strip() if body.description is not None else existing["description"]
        )
        self._validate_content(title, description)
        if body.attachments is not None:
            self._validate_attachments(body.attachments)

        fields: dict[str, Any] = {
            "title": title,
            "description": description,
            "updated_by_user_id": self.user_id,
        }
        if body.category is not None:
            fields["category"] = body.category.value
        if body.scope_type is not None:
            fields["scope_type"] = body.scope_type.value
        if body.repeat is not None:
            rule = self._repeat_input_to_rule(body.repeat)
            self._validate_repeat_rule(rule)
            recurrence_tz = str(existing.get("recurrence_timezone") or DEFAULT_RECURRENCE_TIMEZONE)
            fields.update(self._recurrence_fields_from_rule(rule, timezone=recurrence_tz))
            if series_status == NoticeSeriesStatus.PAUSED.value:
                fields["next_occurrence_at"] = first_occurrence(rule, timezone=recurrence_tz)

        await self.repo.update_notice_fields(
            organization_id=self.organization_id,
            project_id=project_id,
            notice_id=notice_id,
            fields=fields,
        )

        current_groups = await self.repo.list_recipient_groups(
            organization_id=self.organization_id,
            notice_id=notice_id,
        )
        current_towers = await self.repo.list_towers_for_notice(
            organization_id=self.organization_id,
            notice_id=notice_id,
        )
        current_contact_ids = await self.repo.list_contact_recipient_ids(
            organization_id=self.organization_id,
            notice_id=notice_id,
        )
        recipient_groups = (
            self._groups_to_values(body.recipient_groups)
            if body.recipient_groups is not None
            else current_groups
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
        if (
            body.recipient_groups is not None
            or body.contact_ids is not None
            or body.tower_ids is not None
            or body.scope_type is not None
            or body.attachments is not None
        ):
            await self._replace_children(
                notice_id=notice_id,
                recipient_groups=recipient_groups,
                contact_ids=contact_ids,
                scope_type=scope_type,
                tower_ids=tower_ids,
                attachments=body.attachments
                if body.attachments is not None
                else await self._attachments_from_db(notice_id),
            )
        return await self.get_notice(project_id=project_id, notice_id=notice_id)

    async def _apply_series_action(
        self,
        *,
        project_id: str,
        notice_id: str,
        existing: dict[str, Any],
        action: NoticeSeriesAction,
    ) -> NoticeDetailResponse:
        """Pause, resume, or cancel an active recurring template."""
        recurrence_tz = str(existing.get("recurrence_timezone") or DEFAULT_RECURRENCE_TIMEZONE)
        fields: dict[str, Any] = {"updated_by_user_id": self.user_id}
        if action == NoticeSeriesAction.PAUSE:
            if str(existing.get("series_status")) != NoticeSeriesStatus.ACTIVE.value:
                raise ConflictException(
                    message_key="notices.errors.series_not_editable",
                    custom_code=CustomStatusCode.CONFLICT,
                )
            fields["series_status"] = NoticeSeriesStatus.PAUSED.value
        elif action == NoticeSeriesAction.RESUME:
            if str(existing.get("series_status")) != NoticeSeriesStatus.PAUSED.value:
                raise ConflictException(
                    message_key="notices.errors.series_not_editable",
                    custom_code=CustomStatusCode.CONFLICT,
                )
            rule = rule_from_notice_row(existing)
            fields["series_status"] = NoticeSeriesStatus.ACTIVE.value
            fields["next_occurrence_at"] = first_occurrence(rule, timezone=recurrence_tz)
        elif action == NoticeSeriesAction.CANCEL:
            fields["series_status"] = NoticeSeriesStatus.CANCELLED.value
            fields["next_occurrence_at"] = None
        await self.repo.update_notice_fields(
            organization_id=self.organization_id,
            project_id=project_id,
            notice_id=notice_id,
            fields=fields,
        )
        refreshed = await self.repo.fetch_notice_by_id(
            organization_id=self.organization_id,
            project_id=project_id,
            notice_id=notice_id,
        )
        if (
            action == NoticeSeriesAction.RESUME
            and refreshed
            and refreshed.get("next_occurrence_at")
            and refreshed["next_occurrence_at"] <= datetime.now(timezone.utc)
        ):
            await self._materialize_recurring_template(refreshed)
        return await self.get_notice(project_id=project_id, notice_id=notice_id)

    async def _materialize_recurring_template(self, template: dict[str, Any]) -> str | None:
        """Create one live notice from a recurring template row."""
        # pylint: disable=too-complex
        template_id = str(template["id"])
        project_id = str(template["project_id"])
        org_id = str(template["organization_id"])
        if str(template.get("series_status")) != NoticeSeriesStatus.ACTIVE.value:
            return None
        next_at = template.get("next_occurrence_at")
        if next_at is None:
            return None
        recurrence_tz = str(template.get("recurrence_timezone") or DEFAULT_RECURRENCE_TIMEZONE)
        rule = rule_from_notice_row(template)
        sent = int(template.get("occurrences_sent") or 0)
        if exceeds_after_count(rule, occurrences_sent=sent):
            await self.repo.update_notice_fields(
                organization_id=org_id,
                project_id=project_id,
                notice_id=template_id,
                fields={
                    "series_status": NoticeSeriesStatus.COMPLETED.value,
                    "next_occurrence_at": None,
                    "updated_by_user_id": self.user_id,
                },
            )
            return None
        if exceeds_end_on_date(rule, candidate=next_at, timezone=recurrence_tz):
            await self.repo.update_notice_fields(
                organization_id=org_id,
                project_id=project_id,
                notice_id=template_id,
                fields={
                    "series_status": NoticeSeriesStatus.COMPLETED.value,
                    "next_occurrence_at": None,
                    "updated_by_user_id": self.user_id,
                },
            )
            return None
        if (
            rule.end_type == NoticeRecurrenceEndType.AFTER_COUNT
            and rule.max_occurrences is not None
            and sent + 1 > rule.max_occurrences
        ):
            await self.repo.update_notice_fields(
                organization_id=org_id,
                project_id=project_id,
                notice_id=template_id,
                fields={
                    "series_status": NoticeSeriesStatus.COMPLETED.value,
                    "next_occurrence_at": None,
                    "updated_by_user_id": self.user_id,
                },
            )
            return None

        sequence_number = await self.repo.allocate_sequence_number(
            organization_id=org_id,
            project_id=project_id,
        )
        display_code = f"NTC-{sequence_number}"
        published_at = next_at
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        instance = await self.repo.insert_notice(
            organization_id=org_id,
            project_id=project_id,
            display_code=display_code,
            sequence_number=sequence_number,
            title=str(template["title"]),
            description=str(template["description"]),
            category=str(template["category"]),
            status=NoticeStatus.LIVE.value,
            scope_type=str(template["scope_type"]),
            publish_at=None,
            published_at=published_at,
            duplicate_of_id=None,
            created_by_user_id=self.user_id,
            updated_by_user_id=self.user_id,
            recurring_source_notice_id=template_id,
            series_occurrence_index=sent + 1,
        )
        instance_id = str(instance["id"])
        await self.repo.copy_notice_children(
            organization_id=org_id,
            source_notice_id=template_id,
            target_notice_id=instance_id,
        )

        if template.get("series_pin_to_banner"):
            pin_duration = NoticePinDuration.MANUAL
            raw_duration = template.get("series_pin_duration")
            if raw_duration:
                pin_duration = NoticePinDuration(str(raw_duration))
            await self._pin_notice_internal(
                project_id=project_id,
                notice_id=instance_id,
                slot_index=int(template["series_slot_index"])
                if template.get("series_slot_index")
                else None,
                pin_duration=pin_duration,
                confirm_pin_replace=bool(template.get("series_pin_confirm_replace")),
            )

        new_sent = sent + 1
        following = next_occurrence_after(rule, after=published_at, timezone=recurrence_tz)
        template_fields: dict[str, Any] = {
            "occurrences_sent": new_sent,
            "updated_by_user_id": self.user_id,
        }
        if following is None or exceeds_end_on_date(
            rule, candidate=following, timezone=recurrence_tz
        ):
            template_fields["series_status"] = NoticeSeriesStatus.COMPLETED.value
            template_fields["next_occurrence_at"] = None
        elif exceeds_after_count(rule, occurrences_sent=new_sent):
            template_fields["series_status"] = NoticeSeriesStatus.COMPLETED.value
            template_fields["next_occurrence_at"] = None
        elif (
            rule.end_type == NoticeRecurrenceEndType.AFTER_COUNT
            and rule.max_occurrences is not None
            and new_sent >= rule.max_occurrences
        ):
            template_fields["series_status"] = NoticeSeriesStatus.COMPLETED.value
            template_fields["next_occurrence_at"] = None
        else:
            template_fields["next_occurrence_at"] = following

        await self.repo.update_notice_fields(
            organization_id=org_id,
            project_id=project_id,
            notice_id=template_id,
            fields=template_fields,
        )
        await self._dispatch_publish_push(
            project_id=project_id,
            notice_id=instance_id,
            title=str(template["title"]),
        )
        return instance_id

    @staticmethod
    def _repeat_input_to_rule(repeat: NoticeRepeatInput) -> RecurrenceRule:
        """Map API repeat input to a normalized recurrence rule."""
        weekly: tuple[int, ...] | None = None
        if repeat.weekly_days:
            weekly = tuple(sorted(set(repeat.weekly_days)))
        return RecurrenceRule(
            frequency=repeat.frequency,
            weekly_days=weekly,
            monthly_day=repeat.monthly_day,
            monthly_use_last_day=repeat.monthly_use_last_day,
            time_of_day=repeat.time_of_day,
            starts_on=repeat.starts_on,
            end_type=repeat.end_type,
            end_date=repeat.end_date,
            max_occurrences=repeat.max_occurrences,
        )

    @staticmethod
    def _recurrence_fields_from_rule(rule: RecurrenceRule, *, timezone: str) -> dict[str, Any]:
        """Build notices-table column dict from a recurrence rule."""
        return {
            "recurrence_frequency": rule.frequency.value,
            "recurrence_weekly_days": list(rule.weekly_days) if rule.weekly_days else None,
            "recurrence_monthly_day": rule.monthly_day,
            "recurrence_monthly_use_last_day": rule.monthly_use_last_day,
            "recurrence_time_of_day": rule.time_of_day,
            "recurrence_timezone": timezone,
            "recurrence_starts_on": rule.starts_on,
            "recurrence_end_type": rule.end_type.value,
            "recurrence_end_date": rule.end_date,
            "recurrence_max_occurrences": rule.max_occurrences,
        }

    def _validate_repeat_rule(self, rule: RecurrenceRule) -> None:
        """Raise ValidationException when repeat fields are inconsistent."""
        if rule.frequency == NoticeRecurrenceFrequency.WEEKLY and not rule.weekly_days:
            raise ValidationException(
                message_key="notices.errors.weekly_days_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if rule.frequency == NoticeRecurrenceFrequency.MONTHLY:
            if not rule.monthly_use_last_day and rule.monthly_day is None:
                raise ValidationException(
                    message_key="notices.errors.invalid_monthly_day",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
        if rule.end_type == NoticeRecurrenceEndType.ON_DATE:
            if rule.end_date is None:
                raise ValidationException(
                    message_key="notices.errors.invalid_end_date",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            first = first_occurrence(rule, timezone=DEFAULT_RECURRENCE_TIMEZONE)
            if rule.end_date < first.date():
                raise ValidationException(
                    message_key="notices.errors.invalid_end_date",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
        if rule.end_type == NoticeRecurrenceEndType.AFTER_COUNT and (
            rule.max_occurrences is None or rule.max_occurrences < 1
        ):
            raise ValidationException(
                message_key="notices.errors.invalid_max_occurrences",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

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
        is_template = bool(row.get("is_recurring_template"))
        series_status_raw = row.get("series_status")
        series_status = NoticeSeriesStatus(str(series_status_raw)) if series_status_raw else None
        editable = status in {NoticeStatus.DRAFT, NoticeStatus.SCHEDULED} and not is_template
        if is_template and series_status in {
            NoticeSeriesStatus.DRAFT,
            NoticeSeriesStatus.PAUSED,
        }:
            editable = True
        recurrence_summary: str | None = None
        upcoming: list[datetime] = []
        repeat_out: NoticeRepeatInput | None = None
        if is_template and row.get("recurrence_frequency"):
            try:
                rule = rule_from_notice_row(row)
                recurrence_tz = str(row.get("recurrence_timezone") or DEFAULT_RECURRENCE_TIMEZONE)
                preview = preview_recurrence(rule, timezone=recurrence_tz, count=4)
                recurrence_summary = preview.recurrence_summary
                upcoming = preview.upcoming_sends
                repeat_out = self._repeat_input_from_row(row)
            except (ValueError, KeyError):
                pass
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
            is_recurring_template=is_template,
            series_status=series_status,
            next_occurrence_at=row.get("next_occurrence_at"),
            recurring_source_notice_id=row.get("recurring_source_notice_id"),
            series_occurrence_index=row.get("series_occurrence_index"),
            recurrence_summary=recurrence_summary,
            upcoming_sends=upcoming,
            repeat=repeat_out,
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
        is_template = bool(row.get("is_recurring_template"))
        series_status_raw = row.get("series_status")
        series_status = NoticeSeriesStatus(str(series_status_raw)) if series_status_raw else None
        editable = status in {NoticeStatus.DRAFT, NoticeStatus.SCHEDULED} and not is_template
        if is_template and series_status in {
            NoticeSeriesStatus.DRAFT,
            NoticeSeriesStatus.PAUSED,
        }:
            editable = True
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
            is_recurring_template=is_template,
            series_status=series_status,
            next_occurrence_at=row.get("next_occurrence_at"),
            recurring_source_notice_id=row.get("recurring_source_notice_id"),
            series_occurrence_index=row.get("series_occurrence_index"),
        )

    @staticmethod
    def _repeat_input_from_row(row: dict[str, Any]) -> NoticeRepeatInput | None:
        """Reconstruct repeat input from a template row for detail responses."""
        freq = row.get("recurrence_frequency")
        if not freq:
            return None
        weekly = row.get("recurrence_weekly_days")
        tod = row.get("recurrence_time_of_day")
        if not isinstance(tod, time):
            tod = time(9, 0)
        starts = row.get("recurrence_starts_on")
        if not isinstance(starts, date):
            return None
        end_type = row.get("recurrence_end_type") or NoticeRecurrenceEndType.NEVER.value
        return NoticeRepeatInput(
            frequency=NoticeRecurrenceFrequency(str(freq)),
            weekly_days=list(weekly) if weekly else None,
            monthly_day=row.get("recurrence_monthly_day"),
            monthly_use_last_day=bool(row.get("recurrence_monthly_use_last_day")),
            time_of_day=tod,
            starts_on=starts,
            end_type=NoticeRecurrenceEndType(str(end_type)),
            end_date=row.get("recurrence_end_date"),
            max_occurrences=row.get("recurrence_max_occurrences"),
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
