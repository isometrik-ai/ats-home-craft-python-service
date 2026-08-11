"""Daily help registry business logic."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone
from typing import Any

import asyncpg
from asyncpg import UniqueViolationError

from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.db.repositories.daily_help_categories_repository import (
    DailyHelpCategoriesRepository,
)
from apps.user_service.app.db.repositories.daily_help_repository import (
    DailyHelpRepository,
)
from apps.user_service.app.db.repositories.organization_member_repository import (
    OrganizationMemberRepository,
)
from apps.user_service.app.db.repositories.pass_events_repository import (
    PassEventsRepository,
)
from apps.user_service.app.db.repositories.passes_repository import PassesRepository
from apps.user_service.app.schemas.daily_help import (
    AddDailyHelpDocumentRequest,
    CreateDailyHelpCategoryRequest,
    CreateDailyHelpRatingRequest,
    CreateDailyHelpRequest,
    CreateDailyHelpResponse,
    DailyHelpAvailabilitySlotResponse,
    DailyHelpCategoryResponse,
    DailyHelpDetailResponse,
    DailyHelpDocumentResponse,
    DailyHelpEventResponse,
    DailyHelpExportQuery,
    DailyHelpHouseholdLinkResponse,
    DailyHelpListItemResponse,
    DailyHelpListQuery,
    DailyHelpMessageResponse,
    DailyHelpRatingSummaryResponse,
    DailyHelpSummaryResponse,
    ReplaceDailyHelpAvailabilityRequest,
    ResidentDailyHelpCategoryStatsResponse,
    ResidentDailyHelpListItemResponse,
    ResidentDailyHelpListQuery,
    ResidentDailyHelpSearchQuery,
    UpdateDailyHelpCategoryRequest,
    UpdateDailyHelpRequest,
)
from apps.user_service.app.schemas.enums import (
    DEFAULT_DAILY_HELP_CATEGORY_NAMES,
    DailyHelpActorType,
    DailyHelpCategoryStatus,
    DailyHelpEventType,
    DailyHelpStatus,
    PassEventType,
    PassStatus,
    PassType,
    PassValidityType,
)
from apps.user_service.app.services.project_setup_service import ProjectSetupService
from apps.user_service.app.utils.common_utils import UserContext, format_iso_datetime
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode

_NEWLY_ADDED_DAYS = 30
_RECURRING_PASS_YEARS = 10


class DailyHelpService:
    """Admin registry and resident directory for daily help profiles."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
        daily_help_repository: DailyHelpRepository | None = None,
        categories_repository: DailyHelpCategoriesRepository | None = None,
        passes_repository: PassesRepository | None = None,
        pass_events_repository: PassEventsRepository | None = None,
        contact_units_repository: ContactUnitsRepository | None = None,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.repo = daily_help_repository or DailyHelpRepository(db_connection)
        self.categories_repo = categories_repository or DailyHelpCategoriesRepository(db_connection)
        self.passes_repo = passes_repository or PassesRepository(db_connection)
        self.events_repo = pass_events_repository or PassEventsRepository(db_connection)
        self.contact_units_repo = contact_units_repository or ContactUnitsRepository(db_connection)
        self.members_repo = OrganizationMemberRepository(db_connection)
        self.setup_service = ProjectSetupService(
            db_connection=db_connection,
            user_context=user_context,
        )

    @property
    def organization_id(self) -> str:
        org_id = self.user_context.organization_id
        assert org_id
        return org_id

    @staticmethod
    def _build_display_name(
        *,
        initials: str | None,
        first_name: str,
        middle_name: str | None,
        last_name: str,
    ) -> str:
        """Build display name from name parts."""
        parts = [
            part.strip()
            for part in (initials, first_name, middle_name, last_name)
            if part and str(part).strip()
        ]
        return " ".join(parts)

    @staticmethod
    def _format_phone(*, isd_code: str | None, phone_number: str | None) -> str | None:
        """Format phone for API responses."""
        if not phone_number:
            return None
        if isd_code:
            return f"{isd_code} {phone_number}".strip()
        return phone_number

    @staticmethod
    def _mask_phone_number(phone_number: str) -> str:
        """Mask phone leaving only the last four digits visible."""
        digits = phone_number.strip()
        if len(digits) <= 4:
            return "X" * len(digits)
        return ("X" * (len(digits) - 4)) + digits[-4:]

    @staticmethod
    def _format_date(value: Any) -> str | None:
        """Format a date value for API responses."""
        if value is None:
            return None
        if isinstance(value, date):
            return value.isoformat()
        return str(value)

    async def _ensure_project(self, *, project_id: str) -> None:
        """Raise when the project is missing or outside the organization."""
        await self.setup_service.ensure_project(project_id=project_id)

    async def _ensure_resident_unit(
        self,
        *,
        contact_id: str,
        unit_id: str,
    ) -> str:
        """Validate resident unit access and return the unit's project_id."""
        has_unit = await self.contact_units_repo.contact_has_active_unit(
            organization_id=self.organization_id,
            contact_id=contact_id,
            unit_id=unit_id,
        )
        if not has_unit:
            raise ValidationException(
                message_key="daily_help.errors.unit_not_accessible",
                custom_code=CustomStatusCode.FORBIDDEN,
            )
        unit_row = await self.contact_units_repo.get_unit_project(
            organization_id=self.organization_id,
            unit_id=unit_id,
        )
        if not unit_row:
            raise ValidationException(
                message_key="daily_help.errors.unit_not_accessible",
                custom_code=CustomStatusCode.FORBIDDEN,
            )
        project_id = str(unit_row["project_id"])
        await self._ensure_project(project_id=project_id)
        return project_id

    async def _resolve_created_by_name(self, user_id: str | None) -> str | None:
        """Resolve staff display name from organization membership."""
        if not user_id:
            return None
        member = await self.members_repo.get_user_profile_by_id(
            user_id=user_id,
            organization_id=self.organization_id,
        )
        if not member:
            return None
        parts = [
            str(member.get("first_name") or "").strip(),
            str(member.get("last_name") or "").strip(),
        ]
        name = " ".join(part for part in parts if part)
        return name or None

    async def _get_active_category_or_raise(
        self,
        *,
        project_id: str,
        category_id: str,
    ) -> dict[str, Any]:
        """Validate category belongs to project and is active."""
        category = await self.categories_repo.get_by_id(
            organization_id=self.organization_id,
            project_id=project_id,
            category_id=category_id,
        )
        if not category:
            raise ValidationException(
                message_key="daily_help.errors.invalid_category",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if str(category.get("status")) != DailyHelpCategoryStatus.ACTIVE.value:
            raise ValidationException(
                message_key="daily_help.errors.invalid_category",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return category

    async def _get_profile_or_raise(
        self,
        *,
        project_id: str,
        profile_id: str,
    ) -> dict[str, Any]:
        """Fetch profile scoped to project or raise not found."""
        await self._ensure_project(project_id=project_id)
        row = await self.repo.get_profile(
            organization_id=self.organization_id,
            project_id=project_id,
            profile_id=profile_id,
        )
        if not row:
            raise NotFoundException(
                message_key="daily_help.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return row

    def _ensure_not_deleted(self, row: dict[str, Any]) -> None:
        """Block mutations on soft-deleted profiles."""
        if str(row.get("status")) == DailyHelpStatus.DELETED.value:
            raise ConflictException(
                message_key="daily_help.errors.already_deleted",
                custom_code=CustomStatusCode.CONFLICT,
            )

    async def _issue_recurring_pass(
        self,
        *,
        project_id: str,
        profile_id: str,
        display_name: str,
        phone_isd_code: str,
        phone_number: str,
        gate_passcode: str,
        photo_path: str | None,
    ) -> dict[str, Any]:
        """Create a recurring daily help pass for a profile."""
        now = datetime.now(timezone.utc)
        valid_until = now + timedelta(days=365 * _RECURRING_PASS_YEARS)
        user_id = self.user_context.user_id
        return await self.passes_repo.insert_daily_help(
            {
                "organization_id": self.organization_id,
                "project_id": project_id,
                "daily_help_id": profile_id,
                "pass_type": PassType.DAILY_HELP.value,
                "guest_name": display_name,
                "guest_phone_isd_code": phone_isd_code,
                "guest_phone_number": phone_number,
                "valid_from": now,
                "valid_until": valid_until,
                "validity_type": PassValidityType.RECURRING.value,
                "allow_multiple_entries": True,
                "is_private": False,
                "code": gate_passcode,
                "pass_image_path": photo_path,
                "created_by_user_id": str(user_id) if user_id else None,
            }
        )

    async def _sync_pass_guest_snapshot(self, *, row: dict[str, Any]) -> None:
        """Update linked pass guest fields after profile edit."""
        pass_id = row.get("linked_pass_id")
        if not pass_id:
            return
        await self.passes_repo.update_daily_help_guest_snapshot(
            organization_id=self.organization_id,
            pass_id=str(pass_id),
            guest_name=str(row.get("display_name") or ""),
            guest_phone_isd_code=row.get("phone_isd_code"),
            guest_phone_number=row.get("phone_number"),
            pass_image_path=row.get("photo_path"),
        )

    async def _cancel_linked_pass(self, *, pass_id: str | None) -> None:
        """Cancel the linked recurring pass when profile is deactivated/deleted."""
        if not pass_id:
            return
        await self.passes_repo.cancel_by_pass_id(
            organization_id=self.organization_id,
            pass_id=str(pass_id),
        )

    async def _append_event(
        self,
        *,
        profile_id: str,
        event_type: str,
        actor_type: str = DailyHelpActorType.STAFF.value,
        actor_user_id: str | None = None,
        actor_contact_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Append an audit event on a profile."""
        await self.repo.insert_event(
            organization_id=self.organization_id,
            profile_id=profile_id,
            event_type=event_type,
            actor_type=actor_type,
            actor_user_id=actor_user_id,
            actor_contact_id=actor_contact_id,
            payload=payload,
        )

    async def _profile_is_inside(self, *, linked_pass_id: str | None) -> bool:
        """True when the linked pass has an open check-in."""
        if not linked_pass_id:
            return False
        return await self.events_repo.has_open_check_in(
            organization_id=self.organization_id,
            pass_id=str(linked_pass_id),
        )

    async def _viewer_has_household_link(
        self,
        *,
        unit_id: str,
        profile_id: str,
    ) -> bool:
        """True when the given unit has an active household link to the profile."""
        links = await self.repo.list_links_for_units(
            organization_id=self.organization_id,
            unit_ids=[unit_id],
            profile_id=profile_id,
        )
        return bool(links)

    def _serialize_category(self, row: dict[str, Any]) -> DailyHelpCategoryResponse:
        """Map a category row to API shape."""
        return DailyHelpCategoryResponse(
            id=str(row["id"]),
            organization_id=str(row["organization_id"]),
            project_id=str(row["project_id"]),
            name=str(row["name"]),
            sort_order=int(row.get("sort_order") or 0),
            status=str(row["status"]),
            created_at=format_iso_datetime(row.get("created_at")),
            updated_at=format_iso_datetime(row.get("updated_at")),
        )

    def _serialize_document(self, row: dict[str, Any]) -> DailyHelpDocumentResponse:
        """Map a document row to API shape."""
        return DailyHelpDocumentResponse(
            id=str(row["id"]),
            document_type=str(row["document_type"]),
            label=row.get("label"),
            file_path=str(row["file_path"]),
            file_name=row.get("file_name"),
            mime_type=row.get("mime_type"),
            file_size_bytes=row.get("file_size_bytes"),
            sort_order=int(row.get("sort_order") or 0),
            created_at=format_iso_datetime(row.get("created_at")),
            updated_at=format_iso_datetime(row.get("updated_at")),
        )

    def _serialize_event(self, row: dict[str, Any]) -> DailyHelpEventResponse:
        """Map an audit event row to API shape."""
        payload = row.get("payload")
        if isinstance(payload, str):
            payload = {}
        return DailyHelpEventResponse(
            id=str(row["id"]),
            event_type=str(row["event_type"]),
            actor_type=row.get("actor_type"),
            actor_user_id=row.get("actor_user_id"),
            actor_contact_id=row.get("actor_contact_id"),
            payload=dict(payload or {}),
            occurred_at=format_iso_datetime(row.get("occurred_at")),
        )

    def _serialize_household_link(self, row: dict[str, Any]) -> DailyHelpHouseholdLinkResponse:
        """Map a household link row to API shape."""
        return DailyHelpHouseholdLinkResponse(
            id=str(row["id"]),
            unit_id=str(row["unit_id"]),
            linked_by_contact_id=row.get("linked_by_contact_id"),
            status=str(row["status"]),
            started_at=format_iso_datetime(row.get("started_at")),
            removed_at=format_iso_datetime(row.get("removed_at")),
            unit_code=row.get("unit_code"),
            unit_label=row.get("unit_label"),
        )

    def _serialize_slot(self, row: dict[str, Any]) -> DailyHelpAvailabilitySlotResponse:
        """Map an availability slot row to API shape."""
        start_time = row.get("start_time")
        end_time = row.get("end_time")
        return DailyHelpAvailabilitySlotResponse(
            id=str(row["id"]),
            period=str(row["period"]),
            start_time=start_time.isoformat()
            if hasattr(start_time, "isoformat")
            else str(start_time),
            end_time=end_time.isoformat() if hasattr(end_time, "isoformat") else str(end_time),
            sort_order=int(row.get("sort_order") or 0),
        )

    def _serialize_admin_list_item(self, row: dict[str, Any]) -> DailyHelpListItemResponse:
        """Map a profile row to admin list shape."""
        created_at = format_iso_datetime(row.get("created_at"))
        return DailyHelpListItemResponse(
            id=str(row["id"]),
            organization_id=str(row["organization_id"]),
            project_id=str(row["project_id"]),
            display_name=str(row["display_name"]),
            gender=row.get("gender"),
            category_id=str(row["category_id"]),
            category_name=row.get("category_name"),
            phone_isd_code=str(row["phone_isd_code"]),
            phone_number=str(row["phone_number"]),
            phone=self._format_phone(
                isd_code=row.get("phone_isd_code"),
                phone_number=row.get("phone_number"),
            ),
            document_count=int(row.get("document_count") or 0),
            household_link_count=int(row.get("household_link_count") or 0),
            status=str(row["status"]),
            gate_passcode=row.get("gate_passcode"),
            open_to_work=bool(row.get("open_to_work")),
            created_at=created_at,
            created_on=created_at,
        )

    async def _serialize_resident_list_item(
        self,
        *,
        row: dict[str, Any],
        unit_id: str,
        rating_summary: dict[str, Any] | None = None,
    ) -> ResidentDailyHelpListItemResponse:
        """Map a profile row to resident directory card shape."""
        has_link = await self._viewer_has_household_link(
            unit_id=unit_id,
            profile_id=str(row["id"]),
        )
        phone_number = str(row.get("phone_number") or "")
        phone_isd = row.get("phone_isd_code")
        if has_link:
            phone = self._format_phone(isd_code=phone_isd, phone_number=phone_number)
            phone_masked = False
        else:
            phone = self._mask_phone_number(phone_number) if phone_number else None
            phone_masked = bool(phone_number)

        created_at = row.get("created_at")
        is_newly_added = False
        if isinstance(created_at, datetime):
            cutoff = datetime.now(timezone.utc) - timedelta(days=_NEWLY_ADDED_DAYS)
            created_cmp = created_at
            if created_cmp.tzinfo is None:
                created_cmp = created_cmp.replace(tzinfo=timezone.utc)
            is_newly_added = created_cmp >= cutoff

        summary = rating_summary or {}
        return ResidentDailyHelpListItemResponse(
            id=str(row["id"]),
            display_name=str(row["display_name"]),
            category_id=str(row["category_id"]),
            category_name=row.get("category_name"),
            photo_path=row.get("photo_path"),
            phone=phone,
            phone_masked=phone_masked,
            gate_passcode=row.get("gate_passcode"),
            household_link_count=int(row.get("household_link_count") or 0),
            open_to_work=bool(row.get("open_to_work")),
            average_stars=float(summary.get("average_stars") or 0) or None,
            is_inside=await self._profile_is_inside(
                linked_pass_id=row.get("linked_pass_id"),
            ),
            is_newly_added=is_newly_added,
            has_household_link=has_link,
        )

    async def _serialize_detail(
        self,
        *,
        row: dict[str, Any],
        include_documents: bool = True,
        include_events: bool = True,
        include_links: bool = True,
        include_slots: bool = True,
        include_ratings: bool = True,
        mask_phone: bool = False,
    ) -> DailyHelpDetailResponse:
        """Map a profile row to full detail shape."""
        documents: list[DailyHelpDocumentResponse] = []
        events: list[DailyHelpEventResponse] = []
        links: list[DailyHelpHouseholdLinkResponse] = []
        slots: list[DailyHelpAvailabilitySlotResponse] = []
        rating_summary: DailyHelpRatingSummaryResponse | None = None

        profile_id = str(row["id"])
        if include_documents:
            doc_rows = await self.repo.list_documents(
                organization_id=self.organization_id,
                profile_id=profile_id,
            )
            documents = [self._serialize_document(doc) for doc in doc_rows]
        if include_events:
            event_rows = await self.repo.list_events(
                organization_id=self.organization_id,
                profile_id=profile_id,
            )
            events = [self._serialize_event(event) for event in event_rows]
        if include_links:
            link_rows = await self.repo.list_active_links_for_profile(
                organization_id=self.organization_id,
                profile_id=profile_id,
            )
            links = [self._serialize_household_link(link) for link in link_rows]
        if include_slots:
            slot_rows = await self.repo.list_slots(
                organization_id=self.organization_id,
                profile_id=profile_id,
            )
            slots = [self._serialize_slot(slot) for slot in slot_rows]
        if include_ratings:
            summary = await self.repo.get_rating_summary(
                organization_id=self.organization_id,
                profile_id=profile_id,
            )
            rating_summary = DailyHelpRatingSummaryResponse(
                rating_count=int(summary.get("rating_count") or 0),
                average_stars=float(summary.get("average_stars") or 0),
                trait_counts=dict(summary.get("trait_counts") or {}),
            )

        phone_number = str(row.get("phone_number") or "")
        phone_isd = row.get("phone_isd_code")
        if mask_phone and phone_number:
            phone = self._mask_phone_number(phone_number)
        else:
            phone = self._format_phone(isd_code=phone_isd, phone_number=phone_number)

        created_by_name = await self._resolve_created_by_name(row.get("created_by_user_id"))

        return DailyHelpDetailResponse(
            id=profile_id,
            organization_id=str(row["organization_id"]),
            project_id=str(row["project_id"]),
            initials=row.get("initials"),
            first_name=str(row["first_name"]),
            middle_name=row.get("middle_name"),
            last_name=str(row["last_name"]),
            display_name=str(row["display_name"]),
            phone_isd_code=str(row["phone_isd_code"]),
            phone_number=phone_number,
            phone=phone,
            alternate_phone_isd_code=row.get("alternate_phone_isd_code"),
            alternate_phone_number=row.get("alternate_phone_number"),
            category_id=str(row["category_id"]),
            category_name=row.get("category_name"),
            gender=row.get("gender"),
            date_of_birth=self._format_date(row.get("date_of_birth")),
            photo_path=row.get("photo_path"),
            gate_passcode=str(row["gate_passcode"]),
            status=str(row["status"]),
            open_to_work=bool(row.get("open_to_work")),
            linked_pass_id=row.get("linked_pass_id"),
            document_count=int(row.get("document_count") or len(documents)),
            household_link_count=int(row.get("household_link_count") or len(links)),
            documents=documents,
            events=events,
            household_links=links,
            availability_slots=slots,
            rating_summary=rating_summary,
            created_by_user_id=row.get("created_by_user_id"),
            created_by_name=created_by_name,
            created_at=format_iso_datetime(row.get("created_at")),
            updated_at=format_iso_datetime(row.get("updated_at")),
            deleted_at=format_iso_datetime(row.get("deleted_at")),
        )

    # ------------------------------------------------------------------
    # Categories
    # ------------------------------------------------------------------

    async def seed_default_categories(self, *, project_id: str) -> list[DailyHelpCategoryResponse]:
        """Insert default category labels when a project has none."""
        await self._ensure_project(project_id=project_id)
        existing = await self.categories_repo.list_by_project(
            organization_id=self.organization_id,
            project_id=project_id,
        )
        if existing:
            return [self._serialize_category(row) for row in existing]

        user_id = self.user_context.user_id
        created: list[DailyHelpCategoryResponse] = []
        for index, name in enumerate(DEFAULT_DAILY_HELP_CATEGORY_NAMES):
            row = await self.categories_repo.insert(
                organization_id=self.organization_id,
                project_id=project_id,
                name=name,
                sort_order=index,
                status=DailyHelpCategoryStatus.ACTIVE.value,
                created_by_user_id=str(user_id) if user_id else None,
            )
            created.append(self._serialize_category(row))
        return created

    async def list_categories(
        self,
        *,
        project_id: str,
        status: str | None = None,
    ) -> list[DailyHelpCategoryResponse]:
        """List project categories."""
        await self._ensure_project(project_id=project_id)
        rows = await self.categories_repo.list_by_project(
            organization_id=self.organization_id,
            project_id=project_id,
            status=status,
        )
        return [self._serialize_category(row) for row in rows]

    async def create_category(
        self,
        *,
        project_id: str,
        body: CreateDailyHelpCategoryRequest,
    ) -> DailyHelpCategoryResponse:
        """Create a project category."""
        await self._ensure_project(project_id=project_id)
        user_id = self.user_context.user_id
        try:
            row = await self.categories_repo.insert(
                organization_id=self.organization_id,
                project_id=project_id,
                name=body.name.strip(),
                sort_order=body.sort_order,
                status=DailyHelpCategoryStatus.ACTIVE.value,
                created_by_user_id=str(user_id) if user_id else None,
            )
        except UniqueViolationError as exc:
            raise ConflictException(
                message_key="daily_help.errors.duplicate_category_name",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc
        return self._serialize_category(row)

    async def update_category(
        self,
        *,
        project_id: str,
        category_id: str,
        body: UpdateDailyHelpCategoryRequest,
    ) -> DailyHelpCategoryResponse:
        """Patch a project category."""
        await self._ensure_project(project_id=project_id)
        existing = await self.categories_repo.get_by_id(
            organization_id=self.organization_id,
            project_id=project_id,
            category_id=category_id,
        )
        if not existing:
            raise NotFoundException(
                message_key="daily_help.errors.invalid_category",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        fields = body.model_dump(exclude_unset=True)
        if "name" in fields and fields["name"] is not None:
            fields["name"] = fields["name"].strip()
        if "status" in fields and fields["status"] is not None:
            fields["status"] = fields["status"].value

        user_id = self.user_context.user_id
        try:
            row = await self.categories_repo.update(
                organization_id=self.organization_id,
                project_id=project_id,
                category_id=category_id,
                fields=fields,
                updated_by_user_id=str(user_id) if user_id else None,
            )
        except UniqueViolationError as exc:
            raise ConflictException(
                message_key="daily_help.errors.duplicate_category_name",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc
        if not row:
            raise NotFoundException(
                message_key="daily_help.errors.invalid_category",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return self._serialize_category(row)

    # ------------------------------------------------------------------
    # Admin profiles
    # ------------------------------------------------------------------

    async def get_summary(self, *, project_id: str) -> DailyHelpSummaryResponse:
        """Return dashboard summary card counts."""
        await self._ensure_project(project_id=project_id)
        counts = await self.repo.get_summary(
            organization_id=self.organization_id,
            project_id=project_id,
        )
        return DailyHelpSummaryResponse(**counts)

    async def list_profiles(
        self,
        *,
        project_id: str,
        query: DailyHelpListQuery,
    ) -> tuple[list[DailyHelpListItemResponse], int]:
        """Paginated admin list with filters."""
        await self._ensure_project(project_id=project_id)
        offset = (query.page - 1) * query.page_size
        rows, total = await self.repo.list_profiles(
            organization_id=self.organization_id,
            project_id=project_id,
            status=query.status.value if query.status else None,
            category_id=query.category_id,
            search=query.search,
            limit=query.page_size,
            offset=offset,
        )
        items = [self._serialize_admin_list_item(row) for row in rows]
        return items, total

    async def create_profile(
        self,
        *,
        project_id: str,
        body: CreateDailyHelpRequest,
    ) -> CreateDailyHelpResponse:
        """Create profile, documents, recurring pass, and audit events."""
        await self._ensure_project(project_id=project_id)
        category = await self._get_active_category_or_raise(
            project_id=project_id,
            category_id=body.category_id,
        )
        display_name = self._build_display_name(
            initials=body.initials,
            first_name=body.first_name,
            middle_name=body.middle_name,
            last_name=body.last_name,
        )
        gate_passcode = await self.repo.generate_unique_passcode(
            organization_id=self.organization_id,
            project_id=project_id,
        )
        user_id = self.user_context.user_id

        async with self.db_connection.transaction():
            profile = await self.repo.insert_profile(
                organization_id=self.organization_id,
                project_id=project_id,
                initials=body.initials,
                first_name=body.first_name,
                middle_name=body.middle_name,
                last_name=body.last_name,
                display_name=display_name,
                phone_isd_code=body.phone_isd_code,
                phone_number=body.phone_number,
                alternate_phone_isd_code=body.alternate_phone_isd_code,
                alternate_phone_number=body.alternate_phone_number,
                category_id=body.category_id,
                gender=body.gender,
                date_of_birth=body.date_of_birth,
                photo_path=body.photo_path,
                gate_passcode=gate_passcode,
                status=DailyHelpStatus.ACTIVE.value,
                created_by_user_id=str(user_id) if user_id else None,
            )
            profile_id = str(profile["id"])

            for doc in body.documents:
                await self.repo.insert_document(
                    organization_id=self.organization_id,
                    profile_id=profile_id,
                    document_type=doc.document_type.value,
                    label=doc.label,
                    file_path=doc.file_path,
                    file_name=doc.file_name,
                    mime_type=doc.mime_type,
                    file_size_bytes=doc.file_size_bytes,
                    sort_order=doc.sort_order,
                    uploaded_by_user_id=str(user_id) if user_id else None,
                )

            pass_row = await self._issue_recurring_pass(
                project_id=project_id,
                profile_id=profile_id,
                display_name=display_name,
                phone_isd_code=body.phone_isd_code,
                phone_number=body.phone_number,
                gate_passcode=gate_passcode,
                photo_path=body.photo_path,
            )
            pass_id = str(pass_row["id"])
            await self.repo.link_pass_id(
                organization_id=self.organization_id,
                project_id=project_id,
                profile_id=profile_id,
                pass_id=pass_id,
            )

            await self._append_event(
                profile_id=profile_id,
                event_type=DailyHelpEventType.CREATED.value,
                actor_user_id=str(user_id) if user_id else None,
            )
            await self._append_event(
                profile_id=profile_id,
                event_type=DailyHelpEventType.PASS_ISSUED.value,
                actor_user_id=str(user_id) if user_id else None,
                payload={"pass_id": pass_id},
            )

        created_by_name = await self._resolve_created_by_name(user_id)
        return CreateDailyHelpResponse(
            id=profile_id,
            display_name=display_name,
            category_id=body.category_id,
            category_name=category.get("name"),
            status=DailyHelpStatus.ACTIVE.value,
            gate_passcode=gate_passcode,
            document_count=len(body.documents),
            linked_pass_id=pass_id,
            created_at=format_iso_datetime(profile.get("created_at")),
            created_by_name=created_by_name,
        )

    async def get_detail(
        self,
        *,
        project_id: str,
        profile_id: str,
    ) -> DailyHelpDetailResponse:
        """Return full admin profile detail."""
        row = await self._get_profile_or_raise(project_id=project_id, profile_id=profile_id)
        return await self._serialize_detail(row=row)

    async def update_profile(
        self,
        *,
        project_id: str,
        profile_id: str,
        body: UpdateDailyHelpRequest,
    ) -> DailyHelpDetailResponse:
        """Patch profile identity fields and sync linked pass snapshot."""
        row = await self._get_profile_or_raise(project_id=project_id, profile_id=profile_id)
        self._ensure_not_deleted(row)

        patch = body.model_dump(exclude_unset=True)
        if "category_id" in patch and patch["category_id"]:
            await self._get_active_category_or_raise(
                project_id=project_id,
                category_id=patch["category_id"],
            )

        name_fields = {"initials", "first_name", "middle_name", "last_name"}
        if name_fields & patch.keys():
            patch["display_name"] = self._build_display_name(
                initials=patch.get("initials", row.get("initials")),
                first_name=patch.get("first_name", row["first_name"]),
                middle_name=patch.get("middle_name", row.get("middle_name")),
                last_name=patch.get("last_name", row["last_name"]),
            )

        user_id = self.user_context.user_id
        async with self.db_connection.transaction():
            updated = await self.repo.update_profile(
                organization_id=self.organization_id,
                project_id=project_id,
                profile_id=profile_id,
                fields=patch,
                updated_by_user_id=str(user_id) if user_id else None,
            )
            if not updated:
                raise NotFoundException(
                    message_key="daily_help.errors.not_found",
                    custom_code=CustomStatusCode.NOT_FOUND,
                )
            identity_changed = bool(
                name_fields | {"phone_isd_code", "phone_number", "photo_path"} & patch.keys()
            )
            if identity_changed:
                await self._sync_pass_guest_snapshot(row=updated)
            await self._append_event(
                profile_id=profile_id,
                event_type=DailyHelpEventType.UPDATED.value,
                actor_user_id=str(user_id) if user_id else None,
                payload={"fields": sorted(patch.keys())},
            )

        return await self._serialize_detail(row=updated)

    async def deactivate_profile(
        self,
        *,
        project_id: str,
        profile_id: str,
    ) -> DailyHelpMessageResponse:
        """Mark profile inactive and cancel linked pass."""
        row = await self._get_profile_or_raise(project_id=project_id, profile_id=profile_id)
        self._ensure_not_deleted(row)
        if str(row.get("status")) == DailyHelpStatus.INACTIVE.value:
            return DailyHelpMessageResponse(id=profile_id, status=DailyHelpStatus.INACTIVE.value)

        user_id = self.user_context.user_id
        async with self.db_connection.transaction():
            updated = await self.repo.update_profile(
                organization_id=self.organization_id,
                project_id=project_id,
                profile_id=profile_id,
                fields={"status": DailyHelpStatus.INACTIVE.value},
                updated_by_user_id=str(user_id) if user_id else None,
            )
            await self._cancel_linked_pass(pass_id=row.get("linked_pass_id"))
            await self._append_event(
                profile_id=profile_id,
                event_type=DailyHelpEventType.STATUS_CHANGED.value,
                actor_user_id=str(user_id) if user_id else None,
                payload={"status": DailyHelpStatus.INACTIVE.value},
            )
            await self._append_event(
                profile_id=profile_id,
                event_type=DailyHelpEventType.PASS_CANCELLED.value,
                actor_user_id=str(user_id) if user_id else None,
            )

        return DailyHelpMessageResponse(
            id=profile_id,
            status=str((updated or row).get("status") or DailyHelpStatus.INACTIVE.value),
        )

    async def delete_profile(
        self,
        *,
        project_id: str,
        profile_id: str,
    ) -> DailyHelpMessageResponse:
        """Soft-delete profile and cancel linked pass."""
        row = await self._get_profile_or_raise(project_id=project_id, profile_id=profile_id)
        if str(row.get("status")) == DailyHelpStatus.DELETED.value:
            return DailyHelpMessageResponse(id=profile_id, status=DailyHelpStatus.DELETED.value)

        user_id = self.user_context.user_id
        now = datetime.now(timezone.utc)
        async with self.db_connection.transaction():
            updated = await self.repo.update_profile(
                organization_id=self.organization_id,
                project_id=project_id,
                profile_id=profile_id,
                fields={
                    "status": DailyHelpStatus.DELETED.value,
                    "deleted_at": now,
                },
                updated_by_user_id=str(user_id) if user_id else None,
            )
            await self._cancel_linked_pass(pass_id=row.get("linked_pass_id"))
            await self._append_event(
                profile_id=profile_id,
                event_type=DailyHelpEventType.DELETED.value,
                actor_user_id=str(user_id) if user_id else None,
            )
            await self._append_event(
                profile_id=profile_id,
                event_type=DailyHelpEventType.PASS_CANCELLED.value,
                actor_user_id=str(user_id) if user_id else None,
            )

        return DailyHelpMessageResponse(
            id=profile_id,
            status=str((updated or row).get("status") or DailyHelpStatus.DELETED.value),
        )

    async def restore_profile(
        self,
        *,
        project_id: str,
        profile_id: str,
    ) -> DailyHelpMessageResponse:
        """Restore a deleted profile and re-issue pass when needed."""
        row = await self._get_profile_or_raise(project_id=project_id, profile_id=profile_id)
        if str(row.get("status")) != DailyHelpStatus.DELETED.value:
            raise ValidationException(
                message_key="daily_help.errors.not_deleted",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        user_id = self.user_context.user_id
        async with self.db_connection.transaction():
            updated = await self.repo.update_profile(
                organization_id=self.organization_id,
                project_id=project_id,
                profile_id=profile_id,
                fields={
                    "status": DailyHelpStatus.ACTIVE.value,
                    "deleted_at": None,
                },
                updated_by_user_id=str(user_id) if user_id else None,
            )
            assert updated

            linked_pass_id = updated.get("linked_pass_id")
            needs_pass = True
            if linked_pass_id:
                pass_row = await self.passes_repo.get_by_id(
                    organization_id=self.organization_id,
                    pass_id=str(linked_pass_id),
                )
                needs_pass = not pass_row or str(pass_row.get("status")) != PassStatus.ACTIVE.value

            if needs_pass:
                pass_row = await self._issue_recurring_pass(
                    project_id=project_id,
                    profile_id=profile_id,
                    display_name=str(updated["display_name"]),
                    phone_isd_code=str(updated["phone_isd_code"]),
                    phone_number=str(updated["phone_number"]),
                    gate_passcode=str(updated["gate_passcode"]),
                    photo_path=updated.get("photo_path"),
                )
                await self.repo.link_pass_id(
                    organization_id=self.organization_id,
                    project_id=project_id,
                    profile_id=profile_id,
                    pass_id=str(pass_row["id"]),
                )
                await self._append_event(
                    profile_id=profile_id,
                    event_type=DailyHelpEventType.PASS_ISSUED.value,
                    actor_user_id=str(user_id) if user_id else None,
                    payload={"pass_id": str(pass_row["id"])},
                )

            await self._append_event(
                profile_id=profile_id,
                event_type=DailyHelpEventType.RESTORED.value,
                actor_user_id=str(user_id) if user_id else None,
            )

        return DailyHelpMessageResponse(
            id=profile_id,
            status=DailyHelpStatus.ACTIVE.value,
        )

    async def add_document(
        self,
        *,
        project_id: str,
        profile_id: str,
        body: AddDailyHelpDocumentRequest,
    ) -> DailyHelpDocumentResponse:
        """Add one document to a profile."""
        row = await self._get_profile_or_raise(project_id=project_id, profile_id=profile_id)
        self._ensure_not_deleted(row)
        user_id = self.user_context.user_id
        doc = await self.repo.insert_document(
            organization_id=self.organization_id,
            profile_id=profile_id,
            document_type=body.document_type.value,
            label=body.label,
            file_path=body.file_path,
            file_name=body.file_name,
            mime_type=body.mime_type,
            file_size_bytes=body.file_size_bytes,
            sort_order=body.sort_order,
            uploaded_by_user_id=str(user_id) if user_id else None,
        )
        await self._append_event(
            profile_id=profile_id,
            event_type=DailyHelpEventType.DOCUMENT_ADDED.value,
            actor_user_id=str(user_id) if user_id else None,
            payload={"document_id": str(doc["id"]), "document_type": body.document_type.value},
        )
        return self._serialize_document(doc)

    async def delete_document(
        self,
        *,
        project_id: str,
        profile_id: str,
        document_id: str,
    ) -> DailyHelpDocumentResponse:
        """Remove one document from a profile."""
        row = await self._get_profile_or_raise(project_id=project_id, profile_id=profile_id)
        self._ensure_not_deleted(row)
        deleted = await self.repo.delete_document(
            organization_id=self.organization_id,
            profile_id=profile_id,
            document_id=document_id,
        )
        if not deleted:
            raise NotFoundException(
                message_key="daily_help.errors.document_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        user_id = self.user_context.user_id
        await self._append_event(
            profile_id=profile_id,
            event_type=DailyHelpEventType.DOCUMENT_REMOVED.value,
            actor_user_id=str(user_id) if user_id else None,
            payload={"document_id": document_id},
        )
        return DailyHelpDocumentResponse(
            id=str(deleted["id"]),
            document_type=str(deleted["document_type"]),
            label=None,
            file_path="",
        )

    async def export_csv(
        self,
        *,
        project_id: str,
        query: DailyHelpExportQuery,
    ) -> str:
        """Export filtered profiles as CSV text."""
        if query.format != "csv":
            raise ValidationException(
                message_key="daily_help.errors.unsupported_export_format",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        rows, _total = await self.repo.list_profiles(
            organization_id=self.organization_id,
            project_id=project_id,
            status=query.status.value if query.status else None,
            category_id=query.category_id,
            search=query.search,
            limit=10_000,
            offset=0,
        )
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "id",
                "display_name",
                "category",
                "phone",
                "gender",
                "status",
                "gate_passcode",
                "document_count",
                "household_link_count",
                "created_at",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.get("id"),
                    row.get("display_name"),
                    row.get("category_name"),
                    self._format_phone(
                        isd_code=row.get("phone_isd_code"),
                        phone_number=row.get("phone_number"),
                    ),
                    row.get("gender"),
                    row.get("status"),
                    row.get("gate_passcode"),
                    row.get("document_count"),
                    row.get("household_link_count"),
                    format_iso_datetime(row.get("created_at")),
                ]
            )
        return buffer.getvalue()

    # ------------------------------------------------------------------
    # Resident directory
    # ------------------------------------------------------------------

    async def list_resident_categories(
        self,
        *,
        contact_id: str,
        unit_id: str,
    ) -> list[ResidentDailyHelpCategoryStatsResponse]:
        """Return active categories with footer stats for resident home."""
        project_id = await self._ensure_resident_unit(
            contact_id=contact_id,
            unit_id=unit_id,
        )
        categories = await self.categories_repo.list_by_project(
            organization_id=self.organization_id,
            project_id=project_id,
            status=DailyHelpCategoryStatus.ACTIVE.value,
        )
        cutoff = datetime.now(timezone.utc) - timedelta(days=_NEWLY_ADDED_DAYS)
        stats: list[ResidentDailyHelpCategoryStatsResponse] = []

        for category in categories:
            category_id = str(category["id"])
            rows, _total = await self.repo.list_profiles(
                organization_id=self.organization_id,
                project_id=project_id,
                status=DailyHelpStatus.ACTIVE.value,
                category_id=category_id,
                limit=5000,
                offset=0,
            )
            inside_count = 0
            open_to_work_count = 0
            newly_added_count = 0
            for row in rows:
                if bool(row.get("open_to_work")):
                    open_to_work_count += 1
                created_at = row.get("created_at")
                if isinstance(created_at, datetime):
                    created_cmp = created_at
                    if created_cmp.tzinfo is None:
                        created_cmp = created_cmp.replace(tzinfo=timezone.utc)
                    if created_cmp >= cutoff:
                        newly_added_count += 1
                if await self._profile_is_inside(linked_pass_id=row.get("linked_pass_id")):
                    inside_count += 1

            stats.append(
                ResidentDailyHelpCategoryStatsResponse(
                    category_id=category_id,
                    category_name=str(category["name"]),
                    inside_count=inside_count,
                    open_to_work_count=open_to_work_count,
                    newly_added_count=newly_added_count,
                    profile_count=len(rows),
                )
            )
        return stats

    async def list_resident_profiles(
        self,
        *,
        contact_id: str,
        query: ResidentDailyHelpListQuery,
    ) -> tuple[list[ResidentDailyHelpListItemResponse], int]:
        """Paginated active profile directory for residents."""
        project_id = await self._ensure_resident_unit(
            contact_id=contact_id,
            unit_id=query.unit_id,
        )
        offset = (query.page - 1) * query.page_size
        rows, total = await self.repo.list_profiles(
            organization_id=self.organization_id,
            project_id=project_id,
            status=DailyHelpStatus.ACTIVE.value,
            category_id=query.category_id,
            limit=query.page_size,
            offset=offset,
        )
        items = [
            await self._serialize_resident_list_item(
                row=row,
                unit_id=query.unit_id,
            )
            for row in rows
        ]
        return items, total

    async def search_resident_profiles(
        self,
        *,
        contact_id: str,
        query: ResidentDailyHelpSearchQuery,
    ) -> tuple[list[ResidentDailyHelpListItemResponse], int]:
        """Search active profiles by name, mobile, or passcode."""
        project_id = await self._ensure_resident_unit(
            contact_id=contact_id,
            unit_id=query.unit_id,
        )
        offset = (query.page - 1) * query.page_size
        rows, total = await self.repo.list_profiles(
            organization_id=self.organization_id,
            project_id=project_id,
            status=DailyHelpStatus.ACTIVE.value,
            search=query.q,
            limit=query.page_size,
            offset=offset,
        )
        items = [
            await self._serialize_resident_list_item(
                row=row,
                unit_id=query.unit_id,
            )
            for row in rows
        ]
        return items, total

    async def get_resident_detail(
        self,
        *,
        contact_id: str,
        unit_id: str,
        profile_id: str,
    ) -> DailyHelpDetailResponse:
        """Return resident profile detail with optional phone masking."""
        project_id = await self._ensure_resident_unit(
            contact_id=contact_id,
            unit_id=unit_id,
        )
        row = await self._get_profile_or_raise(project_id=project_id, profile_id=profile_id)
        if str(row.get("status")) != DailyHelpStatus.ACTIVE.value:
            raise NotFoundException(
                message_key="daily_help.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        has_link = await self._viewer_has_household_link(
            unit_id=unit_id,
            profile_id=profile_id,
        )
        return await self._serialize_detail(
            row=row,
            include_events=False,
            mask_phone=not has_link,
        )

    async def add_household_link(
        self,
        *,
        contact_id: str,
        unit_id: str,
        profile_id: str,
    ) -> DailyHelpHouseholdLinkResponse:
        """Link a daily help profile to the resident's unit."""
        project_id = await self._ensure_resident_unit(
            contact_id=contact_id,
            unit_id=unit_id,
        )
        row = await self._get_profile_or_raise(project_id=project_id, profile_id=profile_id)
        if str(row.get("status")) != DailyHelpStatus.ACTIVE.value:
            raise NotFoundException(
                message_key="daily_help.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        if await self.repo.has_active_link(
            organization_id=self.organization_id,
            profile_id=profile_id,
            unit_id=unit_id,
        ):
            raise ConflictException(
                message_key="daily_help.errors.duplicate_household_link",
                custom_code=CustomStatusCode.CONFLICT,
            )

        link = await self.repo.insert_link(
            organization_id=self.organization_id,
            project_id=project_id,
            profile_id=profile_id,
            unit_id=unit_id,
            linked_by_contact_id=contact_id,
        )
        await self._append_event(
            profile_id=profile_id,
            event_type=DailyHelpEventType.HOUSEHOLD_LINKED.value,
            actor_type=DailyHelpActorType.RESIDENT.value,
            actor_contact_id=contact_id,
            payload={"unit_id": unit_id, "link_id": str(link["id"])},
        )
        return self._serialize_household_link(link)

    async def remove_household_link(
        self,
        *,
        contact_id: str,
        unit_id: str,
        profile_id: str,
        link_id: str,
    ) -> DailyHelpHouseholdLinkResponse:
        """Remove a resident household link."""
        project_id = await self._ensure_resident_unit(
            contact_id=contact_id,
            unit_id=unit_id,
        )
        await self._get_profile_or_raise(project_id=project_id, profile_id=profile_id)
        links = await self.repo.list_active_links_for_profile(
            organization_id=self.organization_id,
            profile_id=profile_id,
        )
        target = next((link for link in links if str(link["id"]) == link_id), None)
        if not target or str(target["unit_id"]) != unit_id:
            raise NotFoundException(
                message_key="daily_help.errors.link_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        removed = await self.repo.remove_link(
            organization_id=self.organization_id,
            profile_id=profile_id,
            link_id=link_id,
        )
        if not removed:
            raise NotFoundException(
                message_key="daily_help.errors.link_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        await self._append_event(
            profile_id=profile_id,
            event_type=DailyHelpEventType.HOUSEHOLD_REMOVED.value,
            actor_type=DailyHelpActorType.RESIDENT.value,
            actor_contact_id=contact_id,
            payload={"link_id": link_id, "unit_id": str(target["unit_id"])},
        )
        return self._serialize_household_link({**target, **removed})

    # ------------------------------------------------------------------
    # Phase 3 — engagement
    # ------------------------------------------------------------------

    async def create_rating(
        self,
        *,
        contact_id: str,
        unit_id: str,
        profile_id: str,
        body: CreateDailyHelpRatingRequest,
    ) -> DailyHelpRatingSummaryResponse:
        """Record a resident rating for a profile."""
        project_id = await self._ensure_resident_unit(
            contact_id=contact_id,
            unit_id=unit_id,
        )
        row = await self._get_profile_or_raise(project_id=project_id, profile_id=profile_id)
        if str(row.get("status")) != DailyHelpStatus.ACTIVE.value:
            raise NotFoundException(
                message_key="daily_help.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        traits = [trait.value for trait in body.traits]
        await self.repo.insert_rating(
            organization_id=self.organization_id,
            project_id=project_id,
            profile_id=profile_id,
            unit_id=unit_id,
            rated_by_contact_id=contact_id,
            stars=body.stars,
            comment=body.comment,
            traits=traits,
        )
        return await self.get_rating_summary(
            contact_id=contact_id,
            unit_id=unit_id,
            profile_id=profile_id,
        )

    async def get_rating_summary(
        self,
        *,
        project_id: str | None = None,
        profile_id: str,
        contact_id: str | None = None,
        unit_id: str | None = None,
    ) -> DailyHelpRatingSummaryResponse:
        """Return aggregated rating stats for a profile."""
        if contact_id and unit_id:
            project_id = await self._ensure_resident_unit(
                contact_id=contact_id,
                unit_id=unit_id,
            )
        assert project_id
        await self._get_profile_or_raise(project_id=project_id, profile_id=profile_id)
        summary = await self.repo.get_rating_summary(
            organization_id=self.organization_id,
            profile_id=profile_id,
        )
        return DailyHelpRatingSummaryResponse(
            rating_count=int(summary.get("rating_count") or 0),
            average_stars=float(summary.get("average_stars") or 0),
            trait_counts=dict(summary.get("trait_counts") or {}),
        )

    async def replace_availability_slots(
        self,
        *,
        project_id: str,
        profile_id: str,
        body: ReplaceDailyHelpAvailabilityRequest,
    ) -> list[DailyHelpAvailabilitySlotResponse]:
        """Replace all availability slots on a profile."""
        row = await self._get_profile_or_raise(project_id=project_id, profile_id=profile_id)
        self._ensure_not_deleted(row)
        slot_rows = [
            {
                "period": slot.period.value,
                "start_time": slot.start_time,
                "end_time": slot.end_time,
                "sort_order": slot.sort_order,
            }
            for slot in body.slots
        ]
        records = await self.repo.replace_slots(
            organization_id=self.organization_id,
            profile_id=profile_id,
            slots=slot_rows,
        )
        return [self._serialize_slot(row) for row in records]

    async def get_attendance(
        self,
        *,
        profile_id: str,
        project_id: str | None = None,
        contact_id: str | None = None,
        unit_id: str | None = None,
    ) -> dict[str, Any]:
        """Return check-in count from linked pass events."""
        if contact_id and unit_id:
            project_id = await self._ensure_resident_unit(
                contact_id=contact_id,
                unit_id=unit_id,
            )
        assert project_id
        row = await self._get_profile_or_raise(project_id=project_id, profile_id=profile_id)
        linked_pass_id = row.get("linked_pass_id")
        if not linked_pass_id:
            return {"check_in_count": 0, "events": []}

        events = await self.events_repo.list_by_pass(
            organization_id=self.organization_id,
            pass_id=str(linked_pass_id),
        )
        check_ins = [
            {
                "id": str(event["id"]),
                "occurred_at": format_iso_datetime(event.get("occurred_at")),
            }
            for event in events
            if str(event.get("event_type")) == PassEventType.CHECKED_IN.value
            and str(event.get("access_status") or "") != "denied"
        ]
        return {"check_in_count": len(check_ins), "events": check_ins}

    async def get_verify_profile_summary(
        self,
        *,
        project_id: str,
        profile_id: str,
    ) -> dict[str, Any]:
        """Compact profile summary for gate verify responses."""
        row = await self.repo.get_profile(
            organization_id=self.organization_id,
            project_id=project_id,
            profile_id=profile_id,
        )
        if not row:
            return {}
        return {
            "id": str(row["id"]),
            "display_name": str(row["display_name"]),
            "category_name": row.get("category_name"),
            "photo_path": row.get("photo_path"),
            "gate_passcode": row.get("gate_passcode"),
        }
