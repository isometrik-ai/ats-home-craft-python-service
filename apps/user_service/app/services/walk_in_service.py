"""Walk-in visit business logic."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.walk_in_repository import WalkInRepository
from apps.user_service.app.schemas.enums import (
    WalkInActorType,
    WalkInEventType,
    WalkInStatus,
    WalkInVisitUnitStatus,
)
from apps.user_service.app.schemas.walk_in import (
    CreateWalkInRequest,
    RejectWalkInVisitUnitRequest,
    ResidentWalkInVisitUnitListItemResponse,
    ResidentWalkInVisitUnitListQuery,
    WalkInDetailResponse,
    WalkInEventResponse,
    WalkInListQuery,
    WalkInMilestoneResponse,
    WalkInSummaryResponse,
    WalkInVisitUnitResponse,
)
from apps.user_service.app.services.project_setup_service import ProjectSetupService
from apps.user_service.app.utils.common_utils import UserContext, format_iso_datetime
from libs.shared_utils.http_exceptions import (
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode

_ENTER_ALLOWED_HEADER_STATUSES = {
    WalkInStatus.AWAITING.value,
    WalkInStatus.APPROVED.value,
}


class WalkInService:
    """Orchestration for security walk-in visits and resident approvals."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.repo = WalkInRepository(db_connection)
        self.setup_service = ProjectSetupService(
            db_connection=db_connection,
            user_context=user_context,
        )

    @staticmethod
    def _format_contact_name(row: dict[str, Any]) -> str | None:
        """Build display name from contact name parts."""
        parts = [
            str(row.get("prefix") or "").strip(),
            str(row.get("first_name") or "").strip(),
            str(row.get("last_name") or "").strip(),
        ]
        name = " ".join(part for part in parts if part)
        return name or None

    @staticmethod
    def _serialize_visit_unit(row: dict[str, Any]) -> dict[str, Any]:
        """Map a visit unit row to API fields."""
        return WalkInVisitUnitResponse(
            id=str(row["id"]),
            tower_id=str(row["tower_id"]),
            unit_id=str(row["unit_id"]),
            tower_name=row.get("tower_name"),
            unit_code=row.get("unit_code"),
            unit_label=row.get("unit_label"),
            status=str(row.get("status")),
            rejection_reason=row.get("rejection_reason"),
            approved_at=format_iso_datetime(row.get("approved_at")),
            rejected_at=format_iso_datetime(row.get("rejected_at")),
            sort_order=int(row.get("sort_order") or 0),
        ).model_dump()

    @staticmethod
    def _serialize_event(row: dict[str, Any]) -> dict[str, Any]:
        """Map an event row to API fields."""
        payload = row.get("payload")
        if payload is not None and not isinstance(payload, dict):
            payload = {}
        return WalkInEventResponse(
            id=str(row["id"]),
            event_type=str(row["event_type"]),
            actor_type=str(row["actor_type"]) if row.get("actor_type") else None,
            actor_label=row.get("actor_label"),
            occurred_at=format_iso_datetime(row.get("occurred_at")) or "",
            payload=payload or {},
        ).model_dump()

    @staticmethod
    def _serialize_summary(row: dict[str, Any]) -> dict[str, Any]:
        """Map an entry row to list summary fields."""
        return WalkInSummaryResponse(
            id=str(row["id"]),
            project_id=str(row["project_id"]),
            visitor_first_name=row["visitor_first_name"],
            visitor_last_name=row.get("visitor_last_name"),
            visitor_phone_isd_code=row["visitor_phone_isd_code"],
            visitor_phone_number=row["visitor_phone_number"],
            status=str(row["status"]),
            flats_count=int(row["flats_count"]),
            approved_flats_count=int(row.get("approved_flats_count") or 0),
            primary_unit_label=row.get("primary_unit_label"),
            notes=row.get("notes"),
            requested_at=format_iso_datetime(row.get("requested_at")) or "",
            entered_at=format_iso_datetime(row.get("entered_at")),
            exited_at=format_iso_datetime(row.get("exited_at")),
        ).model_dump()

    @staticmethod
    def _derive_milestones(
        *,
        row: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build timeline milestones for Pass Details."""
        requested_at = format_iso_datetime(row.get("requested_at"))
        entered_at = format_iso_datetime(row.get("entered_at"))
        exited_at = format_iso_datetime(row.get("exited_at"))
        approved_at = next(
            (
                format_iso_datetime(event.get("occurred_at"))
                for event in events
                if event.get("event_type") == WalkInEventType.VISIT_UNIT_APPROVED.value
            ),
            None,
        )
        milestones = [
            WalkInMilestoneResponse(
                key="requested",
                label="Entry requested",
                completed=True,
                occurred_at=requested_at,
            ),
            WalkInMilestoneResponse(
                key="approved",
                label="Entry approved",
                completed=bool(approved_at)
                or str(row.get("status"))
                in {
                    WalkInStatus.APPROVED.value,
                    WalkInStatus.ENTERED.value,
                    WalkInStatus.EXITED.value,
                },
                occurred_at=approved_at,
            ),
            WalkInMilestoneResponse(
                key="entered",
                label="User entered",
                completed=str(row.get("status"))
                in {WalkInStatus.ENTERED.value, WalkInStatus.EXITED.value},
                occurred_at=entered_at,
            ),
            WalkInMilestoneResponse(
                key="exited",
                label="Exit",
                completed=str(row.get("status")) == WalkInStatus.EXITED.value,
                occurred_at=exited_at,
            ),
        ]
        return [item.model_dump() for item in milestones]

    async def _get_entry_or_raise(
        self,
        *,
        walk_in_entry_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Load entry scoped to org or raise 404."""
        org_id = self.user_context.organization_id
        assert org_id
        row = await self.repo.get_entry(
            organization_id=org_id,
            walk_in_entry_id=walk_in_entry_id,
            project_id=project_id,
        )
        if not row:
            raise NotFoundException(
                message_key="walk_in.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return row

    async def _serialize_detail(self, row: dict[str, Any]) -> dict[str, Any]:
        """Build full detail payload."""
        org_id = self.user_context.organization_id
        assert org_id
        entry_id = str(row["id"])
        visit_units = await self.repo.list_visit_units(
            organization_id=org_id,
            walk_in_entry_id=entry_id,
        )
        events = await self.repo.list_events(
            organization_id=org_id,
            walk_in_entry_id=entry_id,
        )
        serialized_events = [self._serialize_event(event) for event in events]
        summary = self._serialize_summary({**row, "primary_unit_label": None})
        if visit_units:
            first = visit_units[0]
            summary["primary_unit_label"] = first.get("unit_label") or first.get("unit_code")
        detail = WalkInDetailResponse(
            **summary,
            visitor_photo_paths=list(row.get("visitor_photo_paths") or []),
            vehicle_photo_paths=list(row.get("vehicle_photo_paths") or []),
            visit_units=[self._serialize_visit_unit(unit) for unit in visit_units],
            events=serialized_events,
            milestones=self._derive_milestones(row=row, events=events),
        )
        return detail.model_dump()

    async def _recompute_header_after_visit_unit_action(
        self,
        *,
        walk_in_entry_id: str,
    ) -> None:
        """Update header status and approved count after resident action."""
        org_id = self.user_context.organization_id
        assert org_id
        counts = await self.repo.count_visit_units_by_status(
            organization_id=org_id,
            walk_in_entry_id=walk_in_entry_id,
        )
        approved_count = int(counts.get("approved_count") or 0)
        new_status: str | None = None
        if approved_count > 0:
            entry = await self.repo.get_entry(
                organization_id=org_id,
                walk_in_entry_id=walk_in_entry_id,
            )
            if entry and str(entry.get("status")) == WalkInStatus.AWAITING.value:
                new_status = WalkInStatus.APPROVED.value
        await self.repo.update_entry_header(
            organization_id=org_id,
            walk_in_entry_id=walk_in_entry_id,
            status=new_status,
            approved_flats_count=approved_count,
        )

    async def create_walk_in(
        self,
        *,
        project_id: str,
        body: CreateWalkInRequest,
    ) -> dict[str, Any]:
        """Security creates a walk-in visit for one or more flats."""
        await self.setup_service.ensure_project(project_id=project_id)
        org_id = self.user_context.organization_id
        user_id = self.user_context.user_id
        assert org_id and user_id

        flat_payloads = [
            {"tower_id": flat.tower_id, "unit_id": flat.unit_id} for flat in body.flats
        ]
        validated = await self.repo.fetch_units_for_flats(
            organization_id=org_id,
            project_id=project_id,
            flats=flat_payloads,
        )
        if len(validated) != len(flat_payloads):
            raise ValidationException(
                message_key="walk_in.errors.unit_not_in_project",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        entry = await self.repo.insert_entry(
            organization_id=org_id,
            project_id=project_id,
            visitor_first_name=body.visitor_first_name,
            visitor_last_name=body.visitor_last_name,
            visitor_phone_isd_code=body.visitor_phone_isd_code,
            visitor_phone_number=body.visitor_phone_number,
            visitor_photo_paths=body.visitor_photo_paths,
            vehicle_photo_paths=body.vehicle_photo_paths,
            notes=body.notes,
            flats_count=len(body.flats),
            requested_by_user_id=str(user_id),
            gate_id=body.gate_id,
        )
        entry_id = str(entry["id"])
        for index, flat in enumerate(body.flats):
            await self.repo.insert_visit_unit(
                organization_id=org_id,
                walk_in_entry_id=entry_id,
                tower_id=flat.tower_id,
                unit_id=flat.unit_id,
                sort_order=index,
            )
        await self.repo.insert_event(
            organization_id=org_id,
            walk_in_entry_id=entry_id,
            event_type=WalkInEventType.REQUESTED.value,
            actor_type=WalkInActorType.STAFF.value,
            actor_user_id=str(user_id),
            payload={"flats_count": len(body.flats)},
        )
        return await self._serialize_detail(entry)

    async def list_project_walk_ins(
        self,
        *,
        project_id: str,
        query: WalkInListQuery,
    ) -> list[dict[str, Any]]:
        """List walk-in visits for security."""
        await self.setup_service.ensure_project(project_id=project_id)
        org_id = self.user_context.organization_id
        assert org_id
        rows = await self.repo.list_entries(
            organization_id=org_id,
            project_id=project_id,
            status=query.status.value if query.status else None,
            on_date=query.on_date,
        )
        return [self._serialize_summary(row) for row in rows]

    async def get_project_walk_in(
        self,
        *,
        project_id: str,
        walk_in_entry_id: str,
    ) -> dict[str, Any]:
        """Return walk-in detail for security."""
        row = await self._get_entry_or_raise(
            walk_in_entry_id=walk_in_entry_id,
            project_id=project_id,
        )
        return await self._serialize_detail(row)

    async def enter_walk_in(
        self,
        *,
        project_id: str,
        walk_in_entry_id: str,
    ) -> dict[str, Any]:
        """Mark visitor physically entered."""
        row = await self._get_entry_or_raise(
            walk_in_entry_id=walk_in_entry_id,
            project_id=project_id,
        )
        status = str(row.get("status"))
        if status not in _ENTER_ALLOWED_HEADER_STATUSES:
            raise ValidationException(
                message_key="walk_in.errors.invalid_status_transition",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        approved_count = int(row.get("approved_flats_count") or 0)
        if approved_count < 1:
            raise ValidationException(
                message_key="walk_in.errors.no_approved_visit_units",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        org_id = self.user_context.organization_id
        user_id = self.user_context.user_id
        assert org_id and user_id
        now = datetime.now(timezone.utc)
        updated = await self.repo.update_entry_header(
            organization_id=org_id,
            walk_in_entry_id=walk_in_entry_id,
            status=WalkInStatus.ENTERED.value,
            entered_at=now,
        )
        await self.repo.insert_event(
            organization_id=org_id,
            walk_in_entry_id=walk_in_entry_id,
            event_type=WalkInEventType.ENTERED.value,
            actor_type=WalkInActorType.STAFF.value,
            actor_user_id=str(user_id),
        )
        return await self._serialize_detail(updated or row)

    async def exit_walk_in(
        self,
        *,
        project_id: str,
        walk_in_entry_id: str,
    ) -> dict[str, Any]:
        """Mark visitor exited the premises."""
        row = await self._get_entry_or_raise(
            walk_in_entry_id=walk_in_entry_id,
            project_id=project_id,
        )
        if str(row.get("status")) != WalkInStatus.ENTERED.value:
            raise ValidationException(
                message_key="walk_in.errors.invalid_status_transition",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        org_id = self.user_context.organization_id
        user_id = self.user_context.user_id
        assert org_id and user_id
        now = datetime.now(timezone.utc)
        updated = await self.repo.update_entry_header(
            organization_id=org_id,
            walk_in_entry_id=walk_in_entry_id,
            status=WalkInStatus.EXITED.value,
            exited_at=now,
        )
        await self.repo.insert_event(
            organization_id=org_id,
            walk_in_entry_id=walk_in_entry_id,
            event_type=WalkInEventType.EXITED.value,
            actor_type=WalkInActorType.STAFF.value,
            actor_user_id=str(user_id),
        )
        return await self._serialize_detail(updated or row)

    async def list_resident_visit_units(
        self,
        *,
        contact_id: str,
        query: ResidentWalkInVisitUnitListQuery,
    ) -> list[dict[str, Any]]:
        """List visit units for flats the resident occupies."""
        org_id = self.user_context.organization_id
        assert org_id
        rows = await self.repo.list_resident_visit_units(
            organization_id=org_id,
            contact_id=contact_id,
            status=query.status.value if query.status else None,
        )
        items: list[dict[str, Any]] = []
        for row in rows:
            items.append(
                ResidentWalkInVisitUnitListItemResponse(
                    visit_unit_id=str(row["visit_unit_id"]),
                    walk_in_entry_id=str(row["walk_in_entry_id"]),
                    tower_id=str(row["tower_id"]),
                    unit_id=str(row["unit_id"]),
                    tower_name=row.get("tower_name"),
                    unit_code=row.get("unit_code"),
                    unit_label=row.get("unit_label"),
                    status=str(row.get("status")),
                    visitor_first_name=row["visitor_first_name"],
                    visitor_last_name=row.get("visitor_last_name"),
                    visitor_phone_isd_code=row["visitor_phone_isd_code"],
                    visitor_phone_number=row["visitor_phone_number"],
                    visitor_photo_paths=list(row.get("visitor_photo_paths") or []),
                    notes=row.get("notes"),
                    requested_at=format_iso_datetime(row.get("requested_at")) or "",
                    flats_count=int(row.get("flats_count") or 0),
                ).model_dump()
            )
        return items

    async def get_resident_walk_in(
        self,
        *,
        contact_id: str,
        walk_in_entry_id: str,
    ) -> dict[str, Any]:
        """Resident detail for a walk-in affecting one of their flats."""
        row = await self._get_entry_or_raise(walk_in_entry_id=walk_in_entry_id)
        detail = await self._serialize_detail(row)
        org_id = self.user_context.organization_id
        assert org_id
        accessible = False
        for unit in detail.get("visit_units") or []:
            if await self.repo.resident_can_act_on_unit(
                organization_id=org_id,
                contact_id=contact_id,
                unit_id=str(unit["unit_id"]),
            ):
                accessible = True
                break
        if not accessible:
            raise NotFoundException(
                message_key="walk_in.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return detail

    async def approve_visit_unit(
        self,
        *,
        contact_id: str,
        walk_in_entry_id: str,
        visit_unit_id: str,
        actor_label: str | None = None,
    ) -> dict[str, Any]:
        """Resident approves walk-in for their flat."""
        org_id = self.user_context.organization_id
        assert org_id
        visit_unit = await self.repo.get_visit_unit(
            organization_id=org_id,
            walk_in_entry_id=walk_in_entry_id,
            visit_unit_id=visit_unit_id,
        )
        if not visit_unit:
            raise NotFoundException(
                message_key="walk_in.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        resolved_visit_unit_id = str(visit_unit["id"])
        if str(visit_unit.get("status")) != WalkInVisitUnitStatus.AWAITING.value:
            raise ValidationException(
                message_key="walk_in.errors.visit_unit_not_awaiting",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if not await self.repo.resident_can_act_on_unit(
            organization_id=org_id,
            contact_id=contact_id,
            unit_id=str(visit_unit["unit_id"]),
        ):
            raise ValidationException(
                message_key="walk_in.errors.unit_not_accessible",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        updated = await self.repo.update_visit_unit_status(
            organization_id=org_id,
            walk_in_entry_id=walk_in_entry_id,
            visit_unit_id=resolved_visit_unit_id,
            status=WalkInVisitUnitStatus.APPROVED.value,
            approved_by_contact_id=contact_id,
        )
        if not updated:
            raise ValidationException(
                message_key="walk_in.errors.visit_unit_not_awaiting",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        await self.repo.insert_event(
            organization_id=org_id,
            walk_in_entry_id=walk_in_entry_id,
            walk_in_visit_unit_id=resolved_visit_unit_id,
            event_type=WalkInEventType.VISIT_UNIT_APPROVED.value,
            actor_type=WalkInActorType.RESIDENT.value,
            actor_contact_id=contact_id,
            actor_label=actor_label,
            payload={
                "tower_id": visit_unit.get("tower_id"),
                "unit_id": visit_unit.get("unit_id"),
            },
        )
        await self._recompute_header_after_visit_unit_action(
            walk_in_entry_id=walk_in_entry_id,
        )
        row = await self._get_entry_or_raise(walk_in_entry_id=walk_in_entry_id)
        return await self._serialize_detail(row)

    async def reject_visit_unit(
        self,
        *,
        contact_id: str,
        walk_in_entry_id: str,
        visit_unit_id: str,
        body: RejectWalkInVisitUnitRequest,
        actor_label: str | None = None,
    ) -> dict[str, Any]:
        """Resident rejects walk-in for their flat."""
        org_id = self.user_context.organization_id
        assert org_id
        visit_unit = await self.repo.get_visit_unit(
            organization_id=org_id,
            walk_in_entry_id=walk_in_entry_id,
            visit_unit_id=visit_unit_id,
        )
        if not visit_unit:
            raise NotFoundException(
                message_key="walk_in.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        resolved_visit_unit_id = str(visit_unit["id"])
        if str(visit_unit.get("status")) != WalkInVisitUnitStatus.AWAITING.value:
            raise ValidationException(
                message_key="walk_in.errors.visit_unit_not_awaiting",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if not await self.repo.resident_can_act_on_unit(
            organization_id=org_id,
            contact_id=contact_id,
            unit_id=str(visit_unit["unit_id"]),
        ):
            raise ValidationException(
                message_key="walk_in.errors.unit_not_accessible",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        updated = await self.repo.update_visit_unit_status(
            organization_id=org_id,
            walk_in_entry_id=walk_in_entry_id,
            visit_unit_id=resolved_visit_unit_id,
            status=WalkInVisitUnitStatus.REJECTED.value,
            rejected_by_contact_id=contact_id,
            rejection_reason=body.rejection_reason,
        )
        if not updated:
            raise ValidationException(
                message_key="walk_in.errors.visit_unit_not_awaiting",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        await self.repo.insert_event(
            organization_id=org_id,
            walk_in_entry_id=walk_in_entry_id,
            walk_in_visit_unit_id=resolved_visit_unit_id,
            event_type=WalkInEventType.VISIT_UNIT_REJECTED.value,
            actor_type=WalkInActorType.RESIDENT.value,
            actor_contact_id=contact_id,
            actor_label=actor_label,
            payload={
                "tower_id": visit_unit.get("tower_id"),
                "unit_id": visit_unit.get("unit_id"),
                "rejection_reason": body.rejection_reason,
            },
        )
        await self._recompute_header_after_visit_unit_action(
            walk_in_entry_id=walk_in_entry_id,
        )
        row = await self._get_entry_or_raise(walk_in_entry_id=walk_in_entry_id)
        return await self._serialize_detail(row)
