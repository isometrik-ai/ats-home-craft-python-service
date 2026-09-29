"""Resident-facing visitor activities for a single flat."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.db.repositories.daily_help_repository import (
    DailyHelpRepository,
)
from apps.user_service.app.schemas.enums import PassEntryMethod, PassType
from apps.user_service.app.schemas.visitor_activities import (
    ResidentVisitorActivityListItemResponse,
    resident_activity_detail_from_dict,
)
from apps.user_service.app.services.visitor_logs_service import VisitorLogsService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

_RESIDENT_HIDDEN_DETAIL_FIELDS = frozenset(
    {
        "guard_user_id",
        "guard_name",
        "created_by",
        "host_contact_id",
        "created_by_contact_id",
        "organization_id",
        "project_id",
        "pass_image_path",
        "visitor_photo_paths",
        "vehicle_photo_paths",
        "approved_flats_count",
        "primary_unit_label",
        "requested_by",
        "visit_units",
    }
)

_DEFAULT_LOOKBACK_DAYS = 365


class ResidentVisitorActivitiesService:
    """List and detail visitor activities for a resident's flat."""

    def __init__(self, *, db_connection: asyncpg.Connection, user_context: UserContext) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.contact_units_repo = ContactUnitsRepository(db_connection)
        self.daily_help_repo = DailyHelpRepository(db_connection)
        self._visitor_logs = VisitorLogsService(
            db_connection=db_connection,
            user_context=user_context,
        )

    async def _ensure_contact_unit_access(
        self,
        *,
        contact_id: str,
        unit_id: str,
    ) -> dict[str, Any]:
        """Verify the contact occupies the unit and return unit metadata."""
        org_id = self.user_context.organization_id
        assert org_id
        has_unit = await self.contact_units_repo.contact_has_active_unit(
            organization_id=org_id,
            contact_id=contact_id,
            unit_id=unit_id,
        )
        if not has_unit:
            raise ValidationException(
                message_key="passes.errors.unit_not_owned",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        unit = await self.contact_units_repo.get_unit_project(
            organization_id=org_id,
            unit_id=unit_id,
        )
        if not unit or not unit.get("project_id"):
            raise NotFoundException(
                message_key="projects.errors.unit_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return unit

    @staticmethod
    def _resolve_activity_range(
        *,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> tuple[datetime, datetime]:
        """Default resident feed to the last year when no range is supplied."""
        if start_at is not None and end_at is not None:
            start = (
                start_at.astimezone(timezone.utc)
                if start_at.tzinfo
                else start_at.replace(tzinfo=timezone.utc)
            )
            end = (
                end_at.astimezone(timezone.utc)
                if end_at.tzinfo
                else end_at.replace(tzinfo=timezone.utc)
            )
            if end <= start:
                raise ValidationException(
                    message_key="errors.validation_error",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            return start, end
        if start_at is not None or end_at is not None:
            raise ValidationException(
                message_key="errors.validation_error",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=_DEFAULT_LOOKBACK_DAYS)
        return start, end

    @staticmethod
    def _activity_sub_type(row: dict[str, Any]) -> str | None:
        """Map list-row sub_type for passes and walk-ins."""
        explicit = row.get("sub_type")
        if explicit:
            return str(explicit).strip() or None
        if str(row.get("pass_type") or "") == PassType.DAILY_HELP.value:
            category = row.get("daily_help_category_name")
            return str(category).strip() if category else None
        return None

    @staticmethod
    def _allowed_by_from_row(row: dict[str, Any]) -> dict[str, Any] | None:
        """Map pass creator / walk-in approver for resident activity cards."""
        if str(row.get("pass_type") or "") == PassType.DAILY_HELP.value:
            return None
        resident = row.get("resident")
        if isinstance(resident, dict) and resident.get("person_name"):
            return resident
        return None

    @staticmethod
    def _entries_on_day(row: dict[str, Any]) -> int | None:
        """Return same-day check-in count for recurring daily help rows."""
        if str(row.get("pass_type") or "") != PassType.DAILY_HELP.value:
            return None
        count = row.get("daily_check_in_count")
        if count is None:
            return None
        parsed = int(count)
        return parsed if parsed > 1 else None

    @classmethod
    def _normalize_list_item(cls, row: dict[str, Any]) -> dict[str, Any]:
        """Map a visitor log row to the resident activity list shape."""
        return ResidentVisitorActivityListItemResponse(
            source=str(row.get("source") or "pass"),
            id=str(row["pass_id"]),
            type=str(row.get("pass_type") or ""),
            sub_type=cls._activity_sub_type(row),
            visitor_name=(row.get("guest_name") or "").strip() or None,
            visitor_phone_isd_code=row.get("visitor_phone_isd_code"),
            visitor_phone_number=row.get("visitor_phone_number"),
            visit_status=str(row.get("visit_status") or ""),
            visitor_type=str(row.get("visitor_type") or ""),
            pass_code=row.get("pass_code"),
            daily_help_category_name=row.get("daily_help_category_name"),
            daily_help_profile_id=row.get("daily_help_profile_id"),
            validity_type=row.get("validity_type"),
            scheduled_from=row.get("scheduled_from"),
            scheduled_until=row.get("scheduled_until"),
            entry_method=row.get("entry_method"),
            access_status=row.get("access_status"),
            in_time=row.get("in_time"),
            out_time=row.get("out_time"),
            time_spent_minutes=row.get("time_spent_minutes"),
            is_private=bool(row.get("is_private")),
            pass_image_url=row.get("pass_image_url"),
            visitor_photo_urls=list(row.get("visitor_photo_urls") or []),
            vehicle_photo_urls=list(row.get("vehicle_photo_urls") or []),
            allowed_by=cls._allowed_by_from_row(row),
            entries_on_day=cls._entries_on_day(row),
        ).model_dump()

    async def list_activities(
        self,
        *,
        contact_id: str,
        unit_id: str,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        bucket: str | None = None,
        activity_type: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return paginated visitor activities for one resident flat."""
        unit = await self._ensure_contact_unit_access(contact_id=contact_id, unit_id=unit_id)
        range_start, range_end = self._resolve_activity_range(
            start_at=start_at,
            end_at=end_at,
        )
        rows, total = await self._visitor_logs.list_logs(
            start_at=range_start,
            end_at=range_end,
            bucket=bucket,
            pass_type=activity_type,
            project_id=str(unit["project_id"]),
            unit_id=unit_id,
            visible_to_contact_id=contact_id,
            page=page,
            page_size=page_size,
        )
        return [self._normalize_list_item(row) for row in rows], total

    @staticmethod
    def _assert_private_pass_visible(
        *,
        detail: dict[str, Any],
        contact_id: str,
    ) -> None:
        """Hide private passes from household members who did not create them."""
        if not detail.get("is_private"):
            return
        creator_id = str(detail.get("created_by_contact_id") or detail.get("host_contact_id") or "")
        if creator_id and creator_id != contact_id:
            raise NotFoundException(
                message_key="visitor_logs.errors.pass_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

    @staticmethod
    def _normalize_pass_detail(
        *,
        detail: dict[str, Any],
        unit_id: str,
    ) -> dict[str, Any]:
        """Map admin pass detail to the resident activity detail shape."""
        payload = {
            key: value for key, value in detail.items() if key not in _RESIDENT_HIDDEN_DETAIL_FIELDS
        }
        payload.update(
            {
                "source": "pass",
                "id": str(detail.get("id") or ""),
                "unit_id": unit_id,
                "type": str(detail.get("pass_type") or ""),
                "sub_type": detail.get("daily_help_category_name"),
                "daily_help_profile_id": detail.get("daily_help_id"),
                "visitor_name": detail.get("guest_name") or "",
                "visitor_phone_isd_code": detail.get("guest_phone_isd_code"),
                "visitor_phone_number": detail.get("guest_phone_number"),
                "pass_code": detail.get("code"),
                "time_spent_minutes": detail.get("time_spent_minutes"),
                "visit_status": detail.get("visit_status"),
                "visitor_type": detail.get("visitor_type"),
                "entry_method": detail.get("entry_method"),
                "access_status": detail.get("access_status"),
                "in_time": next(
                    (
                        event.get("occurred_at")
                        for event in detail.get("events") or []
                        if event.get("event_type") == "checked_in"
                    ),
                    None,
                ),
                "out_time": next(
                    (
                        event.get("occurred_at")
                        for event in detail.get("events") or []
                        if event.get("event_type") == "checked_out"
                    ),
                    None,
                ),
            }
        )
        return resident_activity_detail_from_dict(payload)

    @staticmethod
    def _normalize_walk_in_detail(
        *,
        detail: dict[str, Any],
        unit_id: str,
    ) -> dict[str, Any]:
        """Map walk-in detail to the resident activity shape for one flat."""
        visit_units = list(detail.get("visit_units") or [])
        visit_unit = next(
            (unit for unit in visit_units if str(unit.get("unit_id")) == unit_id),
            None,
        )
        if visit_unit is None:
            raise NotFoundException(
                message_key="visitor_logs.errors.pass_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        payload = {
            key: value for key, value in detail.items() if key not in _RESIDENT_HIDDEN_DETAIL_FIELDS
        }
        payload.update(
            {
                "source": "walk_in",
                "id": str(detail.get("id") or ""),
                "unit_id": unit_id,
                "type": str(detail.get("type") or "guest"),
                "sub_type": detail.get("sub_type"),
                "visit_status": detail.get("visit_status"),
                "visitor_type": detail.get("visitor_type"),
                "entry_method": PassEntryMethod.MANUAL.value,
                "in_time": detail.get("entered_at"),
                "out_time": detail.get("exited_at"),
                "time_spent_minutes": detail.get("time_spent_minutes"),
                "visit_unit": visit_unit,
            }
        )
        return resident_activity_detail_from_dict(payload)

    async def _assert_pass_visible_on_unit(
        self,
        *,
        detail: dict[str, Any],
        activity_id: str,
        unit_id: str,
    ) -> None:
        """Ensure a pass row belongs to the requested flat."""
        if str(detail.get("unit_id") or "") == unit_id:
            return

        org_id = self.user_context.organization_id
        assert org_id
        pass_type = str(detail.get("pass_type") or "")
        if pass_type != PassType.DAILY_HELP.value:
            raise NotFoundException(
                message_key="visitor_logs.errors.pass_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        pass_row = await self._visitor_logs.passes_repo.get_by_id(
            organization_id=org_id,
            pass_id=activity_id,
        )
        profile_id = pass_row.get("daily_help_id") if pass_row else None
        if not profile_id:
            raise NotFoundException(
                message_key="visitor_logs.errors.pass_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        linked = await self.daily_help_repo.has_active_household_link(
            organization_id=org_id,
            profile_id=str(profile_id),
            unit_id=unit_id,
        )
        if not linked:
            raise NotFoundException(
                message_key="visitor_logs.errors.pass_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

    async def get_activity_detail(
        self,
        *,
        contact_id: str,
        unit_id: str,
        activity_id: str,
    ) -> dict[str, Any]:
        """Return one visitor activity if it belongs to the resident's flat."""
        unit = await self._ensure_contact_unit_access(contact_id=contact_id, unit_id=unit_id)
        detail = await self._visitor_logs.get_log_detail(
            pass_id=activity_id,
            project_id=str(unit["project_id"]),
        )
        source = str(detail.get("source") or "pass")
        if source == "pass":
            await self._assert_pass_visible_on_unit(
                detail=detail,
                activity_id=activity_id,
                unit_id=unit_id,
            )
            self._assert_private_pass_visible(detail=detail, contact_id=contact_id)
            return self._normalize_pass_detail(detail=detail, unit_id=unit_id)
        return self._normalize_walk_in_detail(detail=detail, unit_id=unit_id)
