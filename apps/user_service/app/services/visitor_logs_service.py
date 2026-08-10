"""Visitor logs admin business logic."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import asyncpg

from apps.user_service.app.config.app_settings import shared_settings
from apps.user_service.app.db.repositories.organization_member_repository import (
    OrganizationMemberRepository,
)
from apps.user_service.app.db.repositories.pass_events_repository import (
    PassEventsRepository,
)
from apps.user_service.app.db.repositories.passes_repository import PassesRepository
from apps.user_service.app.db.repositories.units_repository import UnitsRepository
from apps.user_service.app.db.repositories.visitor_logs_repository import (
    VisitorLogsRepository,
)
from apps.user_service.app.schemas.enums import (
    ContactType,
    PassEventType,
    PassType,
    VisitorType,
    WalkInEventType,
)
from apps.user_service.app.services.passes_service import PassesService
from apps.user_service.app.services.walk_in_service import WalkInService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.status_codes import CustomStatusCode


class VisitorLogsService:
    """Admin-facing visitor logs operations."""

    _RESIDENT_ROLES = frozenset(
        {
            ContactType.OWNER.value,
            ContactType.TENANT.value,
            ContactType.FAMILY.value,
        }
    )

    def __init__(self, *, db_connection: asyncpg.Connection, user_context: UserContext) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.logs_repo = VisitorLogsRepository(db_connection)
        self.units_repo = UnitsRepository(db_connection)
        self.passes_repo = PassesRepository(db_connection)
        self.events_repo = PassEventsRepository(db_connection)
        self._passes_service = PassesService(
            db_connection=db_connection,
            user_context=user_context,
        )
        self._walk_in_service = WalkInService(
            db_connection=db_connection,
            user_context=user_context,
        )
        self._members_repo = OrganizationMemberRepository(db_connection)

    @staticmethod
    def _format_person_name(
        *,
        salutation: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> str | None:
        """Build a display name from name parts."""
        parts = [
            str(salutation or "").strip(),
            str(first_name or "").strip(),
            str(last_name or "").strip(),
        ]
        name = " ".join(part for part in parts if part)
        return name or None

    @classmethod
    def _contact_created_by(cls, row: dict[str, Any]) -> str | None:
        """Build resident creator display name from joined contact fields."""
        return cls._format_person_name(
            first_name=row.get("creator_first_name"),
            last_name=row.get("creator_last_name"),
        )

    async def _staff_display_name(
        self,
        *,
        user_id: str | None,
        organization_id: str,
    ) -> str | None:
        """Resolve a staff member display name from organization_members."""
        if not user_id:
            return None
        profile = await self._members_repo.get_user_profile_by_id(
            user_id=str(user_id),
            organization_id=organization_id,
        )
        if not profile:
            return None
        name = self._format_person_name(
            salutation=profile.get("salutation"),
            first_name=profile.get("first_name"),
            last_name=profile.get("last_name"),
        )
        if name:
            return name
        email = str(profile.get("email") or "").strip()
        return email or None

    async def _guard_from_pass_events(
        self,
        *,
        event_rows: list[dict[str, Any]],
        organization_id: str,
    ) -> tuple[str | None, str | None]:
        """Resolve guard id/name from the latest pass check-in event."""
        check_ins = [
            row for row in event_rows if row.get("event_type") == PassEventType.CHECKED_IN.value
        ]
        if not check_ins:
            return None, None

        latest = max(
            check_ins,
            key=lambda row: self._parse_dt(row.get("occurred_at"))
            or datetime.min.replace(tzinfo=timezone.utc),
        )
        guard_user_id = latest.get("actor_user_id")
        if not guard_user_id:
            return None, None
        guard_name = await self._staff_display_name(
            user_id=str(guard_user_id),
            organization_id=organization_id,
        )
        if not guard_name:
            guard_name = str(latest.get("actor_label") or "").strip() or None
        return str(guard_user_id), guard_name

    async def _guard_from_walk_in_events(
        self,
        *,
        events: list[dict[str, Any]],
        organization_id: str,
    ) -> tuple[str | None, str | None]:
        """Resolve guard id/name from the latest walk-in enter event."""
        entered_events = [
            event for event in events if event.get("event_type") == WalkInEventType.ENTERED.value
        ]
        if not entered_events:
            return None, None

        latest = entered_events[-1]
        guard_user_id = latest.get("actor_user_id")
        if not guard_user_id:
            return None, None
        guard_name = await self._staff_display_name(
            user_id=str(guard_user_id),
            organization_id=organization_id,
        )
        if not guard_name:
            guard_name = str(latest.get("actor_label") or "").strip() or None
        return str(guard_user_id), guard_name

    @staticmethod
    def _build_resident(
        *,
        contact_id: Any,
        person_name: Any,
        role: Any,
    ) -> dict[str, Any] | None:
        """Build the person who requested or approved the visit."""
        if not contact_id:
            return None
        name = str(person_name or "").strip() or None
        if not name:
            return None
        role_value = str(role or "").strip()
        payload: dict[str, Any] = {
            "contact_id": str(contact_id),
            "person_name": name,
        }
        if role_value in VisitorLogsService._RESIDENT_ROLES:
            payload["role"] = role_value
        else:
            payload["role"] = None
        return payload

    @classmethod
    def _resident_from_row(cls, row: dict[str, Any]) -> dict[str, dict[str, Any] | None]:
        """Map repository resident columns to a single response object."""
        return {
            "resident": cls._build_resident(
                contact_id=row.get("resident_contact_id"),
                person_name=row.get("resident_person_name"),
                role=row.get("resident_role"),
            ),
        }

    async def _resolve_resident(
        self,
        *,
        organization_id: str,
        contact_id: str | None,
        unit_id: str | None,
        person_name: str | None = None,
    ) -> dict[str, Any] | None:
        """Resolve requester/approver for detail, including when role on flat is missing."""
        if not contact_id:
            return None

        if unit_id:
            key = UnitsRepository._resident_pair_key(contact_id, unit_id)
            residents_by_pair = await self.units_repo.get_contact_residents_batch(
                organization_id=organization_id,
                contact_unit_pairs=[(contact_id, unit_id)],
            )
            resident = residents_by_pair.get(key)
            if resident is not None:
                return resident

        resolved_name = str(person_name or "").strip() or None
        if not resolved_name:
            names_by_id = await self.units_repo.get_contact_person_names_batch(
                organization_id=organization_id,
                contact_ids=[contact_id],
            )
            resolved_name = names_by_id.get(contact_id)

        return self._build_resident(
            contact_id=contact_id,
            person_name=resolved_name,
            role=None,
        )

    async def _attach_resident_to_detail(
        self,
        *,
        detail: dict[str, Any],
        organization_id: str,
    ) -> dict[str, Any]:
        """Attach the requesting/approving flat resident to a detail payload."""
        if str(detail.get("source") or "") == "walk_in":
            visit_units = list(detail.get("visit_units") or [])
            walk_in_entry_id = str(detail.get("id") or "")
            if walk_in_entry_id:
                raw_units = await self._walk_in_service.repo.list_visit_units(
                    organization_id=organization_id,
                    walk_in_entry_id=walk_in_entry_id,
                )
                approved_by_by_id = {
                    str(row["id"]): row.get("approved_by_contact_id") for row in raw_units
                }
                visit_units = [
                    {
                        **unit,
                        "approved_by_contact_id": approved_by_by_id.get(str(unit.get("id"))),
                    }
                    for unit in visit_units
                ]
            pairs = [
                (str(unit["approved_by_contact_id"]), str(unit["unit_id"]))
                for unit in visit_units
                if unit.get("approved_by_contact_id") and unit.get("unit_id")
            ]
            residents_by_pair = await self.units_repo.get_contact_residents_batch(
                organization_id=organization_id,
                contact_unit_pairs=pairs,
            )
            contact_ids = [
                str(unit["approved_by_contact_id"])
                for unit in visit_units
                if unit.get("approved_by_contact_id")
            ]
            names_by_id = await self.units_repo.get_contact_person_names_batch(
                organization_id=organization_id,
                contact_ids=contact_ids,
            )
            enriched_units: list[dict[str, Any]] = []
            primary_resident: dict[str, Any] | None = None
            for unit in visit_units:
                contact_id = str(unit.get("approved_by_contact_id") or "")
                unit_id = str(unit.get("unit_id") or "")
                resident = None
                if contact_id and unit_id:
                    key = UnitsRepository._resident_pair_key(contact_id, unit_id)
                    resident = residents_by_pair.get(key)
                if resident is None and contact_id:
                    resident = self._build_resident(
                        contact_id=contact_id,
                        person_name=names_by_id.get(contact_id),
                        role=None,
                    )
                if primary_resident is None and resident is not None:
                    primary_resident = resident
                enriched_units.append({**unit, "resident": resident})
            detail["visit_units"] = enriched_units
            detail["resident"] = primary_resident
            return detail

        contact_id = str(detail.get("created_by_contact_id") or detail.get("host_contact_id") or "")
        unit_id = str(detail.get("unit_id") or "")
        detail["resident"] = await self._resolve_resident(
            organization_id=organization_id,
            contact_id=contact_id or None,
            unit_id=unit_id or None,
            person_name=self._contact_created_by(detail),
        )
        return detail

    @staticmethod
    def _visitor_phone_fields(row: dict[str, Any]) -> dict[str, str | None]:
        """Normalize visitor phone fields across pass and walk-in rows."""
        isd = row.get("visitor_phone_isd_code") or row.get("guest_phone_isd_code")
        number = row.get("visitor_phone_number") or row.get("guest_phone_number")
        isd_value = str(isd).strip() if isd else None
        number_value = str(number).strip() if number else None
        return {
            "visitor_phone_isd_code": isd_value or None,
            "visitor_phone_number": number_value or None,
        }

    def _enrich_detail_fields(
        self,
        *,
        detail: dict[str, Any],
        created_by: str | None = None,
        guard_user_id: str | None = None,
        guard_name: str | None = None,
        image_urls: list[str] | None = None,
        pass_image_url: str | None = None,
        visitor_photo_urls: list[str] | None = None,
        vehicle_photo_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        """Attach visitor-log summary fields to a detail payload."""
        payload: dict[str, Any] = {
            **detail,
            "created_by": created_by,
            "guard_user_id": guard_user_id,
            "guard_name": guard_name,
            "image_urls": image_urls or [],
            **self._visitor_phone_fields(detail),
        }
        if pass_image_url is not None:
            payload["pass_image_url"] = pass_image_url
        if visitor_photo_urls is not None:
            payload["visitor_photo_urls"] = visitor_photo_urls
        if vehicle_photo_urls is not None:
            payload["vehicle_photo_urls"] = vehicle_photo_urls
        return payload

    @staticmethod
    def _public_media_url(path: str | None) -> str | None:
        """Convert a storage object key/path to a public media URL."""
        if not path:
            return None
        cleaned = str(path).strip()
        if not cleaned:
            return None
        if cleaned.startswith(("http://", "https://")):
            return cleaned
        base = shared_settings.cloudflare_r2.media_url.rstrip("/")
        return f"{base}/{cleaned.lstrip('/')}"

    @classmethod
    def _public_media_urls(cls, paths: list[str] | None) -> list[str]:
        """Convert storage paths to public media URLs."""
        urls: list[str] = []
        for path in paths or []:
            url = cls._public_media_url(path)
            if url:
                urls.append(url)
        return urls

    def _pass_image_fields(self, detail: dict[str, Any]) -> dict[str, Any]:
        """Build image URL fields for a pass detail payload."""
        pass_image_url = self._public_media_url(detail.get("pass_image_path"))
        return {
            "pass_image_url": pass_image_url,
            "image_urls": [pass_image_url] if pass_image_url else [],
        }

    def _walk_in_image_fields(self, detail: dict[str, Any]) -> dict[str, Any]:
        """Build image URL fields for a walk-in detail payload."""
        visitor_photo_urls = self._public_media_urls(detail.get("visitor_photo_paths"))
        vehicle_photo_urls = self._public_media_urls(detail.get("vehicle_photo_paths"))
        return {
            "visitor_photo_urls": visitor_photo_urls,
            "vehicle_photo_urls": vehicle_photo_urls,
            "image_urls": visitor_photo_urls + vehicle_photo_urls,
        }

    def _list_image_fields(self, row: dict[str, Any]) -> dict[str, Any]:
        """Build source-specific image URL fields for a visitor log list row."""
        if str(row.get("source") or "") == "walk_in":
            return {
                "pass_image_url": None,
                "visitor_photo_urls": self._public_media_urls(row.get("visitor_photo_paths")),
                "vehicle_photo_urls": self._public_media_urls(row.get("vehicle_photo_paths")),
            }
        return {
            "pass_image_url": self._public_media_url(row.get("pass_image_path")),
            "visitor_photo_urls": [],
            "vehicle_photo_urls": [],
        }

    @staticmethod
    def _parse_dt(value: Any) -> datetime | None:
        """Parse a DB datetime value."""
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value
        return None

    @classmethod
    def _time_spent_minutes(cls, in_time: Any, out_time: Any) -> int | None:
        """Derive visit duration in minutes."""
        start = cls._parse_dt(in_time)
        end = cls._parse_dt(out_time)
        if not start or not end or end < start:
            return None
        return int((end - start).total_seconds() // 60)

    @staticmethod
    def _format_guard_name(row: dict[str, Any]) -> str | None:
        """Build guard display name from joined member profile, with legacy fallback."""
        parts = [
            str(row.get("guard_salutation") or "").strip(),
            str(row.get("guard_first_name") or "").strip(),
            str(row.get("guard_last_name") or "").strip(),
        ]
        name = " ".join(part for part in parts if part)
        if name:
            return name
        fallback = str(row.get("guard_name_fallback") or "").strip()
        return fallback or None

    @staticmethod
    def _format_unit_label(row: dict[str, Any]) -> str | None:
        """Format unit label, including multi-flat suffix for walk-ins."""
        unit_label = str(row.get("unit_label") or "").strip() or None
        if str(row.get("source") or "") != "walk_in" or not unit_label:
            return unit_label
        flats_count = int(row.get("flats_count") or 1)
        if flats_count > 1:
            return f"{unit_label} (+{flats_count - 1} more)"
        return unit_label

    @staticmethod
    def _visitor_type_from_row(row: dict[str, Any]) -> str:
        """Derive high-level visitor category from pass type."""
        pass_type = str(row.get("pass_type") or "")
        if pass_type == PassType.GUEST.value:
            return VisitorType.GUEST.value
        return VisitorType.VISITOR.value

    def _normalize_list_item(self, row: dict[str, Any]) -> dict[str, Any]:
        """Map a repository row to the visitor log list item shape."""
        from apps.user_service.app.utils.common_utils import format_iso_datetime

        in_time = row.get("in_time")
        out_time = row.get("out_time")
        created_by = (row.get("created_by") or "").strip() or None
        validity_type = row.get("validity_type")
        return {
            "source": str(row.get("source") or "pass"),
            "pass_id": str(row["pass_id"]),
            "pass_type": row.get("pass_type"),
            "guest_name": (row.get("guest_name") or "").strip() or None,
            **self._visitor_phone_fields(row),
            "unit_label": self._format_unit_label(row),
            "tower_name": row.get("tower_name"),
            **self._resident_from_row(row),
            "created_by": created_by,
            "scheduled_from": format_iso_datetime(row.get("scheduled_from")),
            "scheduled_until": format_iso_datetime(row.get("scheduled_until")),
            "validity_type": str(validity_type) if validity_type else None,
            "entry_method": row.get("entry_method"),
            "guard_user_id": row.get("guard_user_id"),
            "guard_name": self._format_guard_name(row),
            "access_status": row.get("access_status"),
            "visit_status": str(row.get("visit_status") or ""),
            "visitor_type": self._visitor_type_from_row(row),
            "pass_code": row.get("pass_code"),
            "is_private": bool(row.get("is_private") or False),
            "in_time": format_iso_datetime(in_time),
            "out_time": format_iso_datetime(out_time),
            "time_spent_minutes": self._time_spent_minutes(in_time, out_time),
            **self._list_image_fields(row),
        }

    async def list_logs(
        self,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        search: str | None = None,
        bucket: str | None = None,
        visitor_type: str | None = None,
        pass_type: str | None = None,
        entry_method: str | None = None,
        access_status: str | None = None,
        tower_id: str | None = None,
        guard_user_id: str | None = None,
        project_id: str | None = None,
        unit_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return paginated visitor log rows."""
        org_id = self.user_context.organization_id
        assert org_id
        rows, total = await self.logs_repo.list_logs(
            organization_id=org_id,
            start_at=start_at,
            end_at=end_at,
            search=search,
            bucket=bucket,
            visitor_type=visitor_type,
            pass_type=pass_type,
            entry_method=entry_method,
            access_status=access_status,
            tower_id=tower_id,
            guard_user_id=guard_user_id,
            project_id=project_id,
            unit_id=unit_id,
            page=page,
            page_size=page_size,
        )
        return [self._normalize_list_item(row) for row in rows], total

    async def get_overview(
        self,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        project_id: str | None = None,
        unit_id: str | None = None,
    ) -> dict[str, Any]:
        """Return overview card metrics."""
        from apps.user_service.app.utils.common_utils import format_iso_datetime

        org_id = self.user_context.organization_id
        assert org_id
        result = await self.logs_repo.get_overview(
            organization_id=org_id,
            start_at=start_at,
            end_at=end_at,
            project_id=project_id,
            unit_id=unit_id,
        )
        return {
            **result,
            "start_at": format_iso_datetime(result.get("start_at")),
            "end_at": format_iso_datetime(result.get("end_at")),
        }

    async def get_log_detail(self, *, pass_id: str) -> dict[str, Any]:
        """Return pass or walk-in detail with full timeline for admin."""
        org_id = self.user_context.organization_id
        assert org_id

        row = await self.passes_repo.get_by_id(
            organization_id=org_id,
            pass_id=pass_id,
        )
        if row:
            event_rows = await self.events_repo.list_by_pass(
                organization_id=org_id,
                pass_id=pass_id,
            )
            events = [self._passes_service._normalize_event(event_row) for event_row in event_rows]
            detail = self._passes_service._normalize_pass(
                row,
                events=events,
                include_events=True,
            )
            guard_user_id, guard_name = await self._guard_from_pass_events(
                event_rows=event_rows,
                organization_id=org_id,
            )
            image_fields = self._pass_image_fields(detail)
            detail = await self._attach_resident_to_detail(
                detail={
                    "source": "pass",
                    **detail,
                    "created_by_contact_id": row.get("created_by_contact_id"),
                },
                organization_id=org_id,
            )
            return self._enrich_detail_fields(
                detail=detail,
                created_by=self._contact_created_by(row),
                guard_user_id=guard_user_id,
                guard_name=guard_name,
                pass_image_url=image_fields["pass_image_url"],
                image_urls=image_fields["image_urls"],
            )

        walk_in_row = await self._walk_in_service.repo.get_entry(
            organization_id=org_id,
            walk_in_entry_id=pass_id,
        )
        if walk_in_row:
            detail = await self._walk_in_service._serialize_detail(walk_in_row)
            guard_user_id, guard_name = await self._guard_from_walk_in_events(
                events=detail.get("events") or [],
                organization_id=org_id,
            )
            created_by = await self._staff_display_name(
                user_id=str(walk_in_row.get("requested_by_user_id") or "") or None,
                organization_id=org_id,
            )
            image_fields = self._walk_in_image_fields(detail)
            detail = await self._attach_resident_to_detail(
                detail={"source": "walk_in", **detail},
                organization_id=org_id,
            )
            return self._enrich_detail_fields(
                detail=detail,
                created_by=created_by,
                guard_user_id=guard_user_id,
                guard_name=guard_name,
                visitor_photo_urls=image_fields["visitor_photo_urls"],
                vehicle_photo_urls=image_fields["vehicle_photo_urls"],
                image_urls=image_fields["image_urls"],
            )

        raise NotFoundException(
            message_key="visitor_logs.errors.pass_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )
