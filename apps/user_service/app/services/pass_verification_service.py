"""Gate pass verification and check-in/out business logic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.pass_events_repository import (
    PassEventsRepository,
)
from apps.user_service.app.db.repositories.passes_repository import PassesRepository
from apps.user_service.app.db.repositories.towers_repository import TowersRepository
from apps.user_service.app.schemas.enums import (
    PassAccessStatus,
    PassActorType,
    PassEventType,
    PassStatus,
    PassValidityType,
)
from apps.user_service.app.schemas.gate_passes import CheckInRequest, CheckOutRequest
from apps.user_service.app.services.daily_help_notification_service import (
    DailyHelpNotificationService,
)
from apps.user_service.app.services.daily_help_service import DailyHelpService
from apps.user_service.app.services.push_notification_dispatch import (
    PushNotificationDispatcher,
)
from apps.user_service.app.utils.common_utils import UserContext, format_iso_datetime
from libs.shared_utils.http_exceptions import (
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode


@dataclass(frozen=True)
class _Admissibility:
    """Computed gate decision for a pass."""

    access_status: str
    can_check_in: bool
    too_early: bool = False
    max_entries_reached: bool = False


class PassVerificationService:
    """Permission-gated pass verify / check-in / check-out operations."""

    def __init__(self, *, db_connection: asyncpg.Connection, user_context: UserContext) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.passes_repo = PassesRepository(db_connection)
        self.events_repo = PassEventsRepository(db_connection)
        self.towers_repo = TowersRepository(db_connection)
        self._push_dispatcher: PushNotificationDispatcher | None = None

    def _push(self) -> PushNotificationDispatcher:
        if self._push_dispatcher is None:
            self._push_dispatcher = PushNotificationDispatcher(db_connection=self.db_connection)
        return self._push_dispatcher

    async def _notify_household_pass_event(
        self,
        *,
        pass_row: dict[str, Any],
        message_key: str,
        idempotency_suffix: str,
    ) -> None:
        """Notify household members on the pass unit when entry is not private."""
        if bool(pass_row.get("is_private")):
            return
        org_id = self.user_context.organization_id
        assert org_id
        unit_id = str(pass_row.get("unit_id") or "")
        pass_id = str(pass_row.get("id") or "")
        host_contact_id = str(pass_row.get("host_contact_id") or "")
        if not unit_id or not pass_id:
            return
        visitor_name = str(pass_row.get("guest_name") or "Visitor").strip() or "Visitor"
        await self._push().send_to_unit_residents(
            organization_id=org_id,
            unit_id=unit_id,
            exclude_contact_ids={host_contact_id} if host_contact_id else None,
            message_key=message_key,
            notification_type="NOTIFICATION_TYPE_PASS",
            feed_type="pass",
            params={"visitor_name": visitor_name},
            data={
                "pass_id": pass_id,
                "unit_id": unit_id,
                "screen": "pass_detail",
            },
            entity={"kind": "pass", "id": pass_id},
            options={
                "click_action": "OPEN_PASS",
                "idempotency_key": f"pass:{pass_id}:{idempotency_suffix}",
            },
        )

    @staticmethod
    def _now() -> datetime:
        """Return current UTC time."""
        return datetime.now(timezone.utc)

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

    @staticmethod
    def _format_contact_name(first_name: str | None, last_name: str | None) -> str | None:
        """Build a display name from contact name parts."""
        parts = [part.strip() for part in (first_name, last_name) if part and part.strip()]
        if not parts:
            return None
        return " ".join(parts)

    @staticmethod
    def _format_guest_phone(isd_code: str | None, phone_number: str | None) -> str | None:
        """Build a display phone string."""
        if not phone_number:
            return None
        if isd_code:
            return f"{isd_code} {phone_number}".strip()
        return phone_number

    @classmethod
    def _compute_admissibility(
        cls,
        row: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> _Admissibility:
        """Compute gate decision without writing to the database."""
        now = now or cls._now()
        status = str(row.get("status") or "")

        if status == PassStatus.CANCELLED.value:
            return _Admissibility(
                access_status=PassAccessStatus.DENIED.value,
                can_check_in=False,
            )

        valid_from = cls._parse_dt(row.get("valid_from"))
        valid_until = cls._parse_dt(row.get("valid_until"))
        if status == PassStatus.EXPIRED.value or (valid_until and now > valid_until):
            return _Admissibility(
                access_status=PassAccessStatus.EXPIRED.value,
                can_check_in=False,
            )

        entry_count = int(row.get("entry_count") or 0)
        max_entries = row.get("max_entries")
        validity_type = str(row.get("validity_type") or "")
        if validity_type == PassValidityType.ONE_TIME.value and entry_count > 0:
            return _Admissibility(
                access_status=PassAccessStatus.DENIED.value,
                can_check_in=False,
            )
        if max_entries is not None and entry_count >= int(max_entries):
            return _Admissibility(
                access_status=PassAccessStatus.DENIED.value,
                can_check_in=False,
                max_entries_reached=True,
            )

        if valid_from and now < valid_from:
            return _Admissibility(
                access_status=PassAccessStatus.APPROVED.value,
                can_check_in=False,
                too_early=True,
            )

        return _Admissibility(
            access_status=PassAccessStatus.APPROVED.value,
            can_check_in=True,
        )

    def _normalize_verify_response(
        self,
        row: dict[str, Any],
        decision: _Admissibility,
        *,
        daily_help_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Map a pass row to the verify snapshot shown before check-in."""
        payload = {
            "pass_id": str(row["id"]),
            "code": row.get("code"),
            "guest_name": row.get("guest_name"),
            "guest_phone": self._format_guest_phone(
                row.get("guest_phone_isd_code"),
                row.get("guest_phone_number"),
            ),
            "visitor_count": int(row.get("visitor_count") or 1),
            "vehicle_number": row.get("vehicle_number"),
            "pass_type": row.get("pass_type"),
            "unit_label": row.get("unit_label"),
            "tower_name": row.get("tower_name"),
            "host_name": self._format_contact_name(
                row.get("host_first_name"),
                row.get("host_last_name"),
            ),
            "valid_from": format_iso_datetime(row.get("valid_from")),
            "valid_until": format_iso_datetime(row.get("valid_until")),
            "validity_type": str(row.get("validity_type") or ""),
            "is_private": bool(row.get("is_private")),
            "access_status": decision.access_status,
            "can_check_in": decision.can_check_in,
            "too_early": decision.too_early,
        }
        if daily_help_profile:
            payload["daily_help_profile"] = daily_help_profile
        return payload

    def _normalize_event(self, row: dict[str, Any]) -> dict[str, Any]:
        """Map a pass_events row to gate API shape."""
        return {
            "id": str(row["id"]),
            "event_type": row.get("event_type"),
            "gate_id": row.get("gate_id"),
            "actor_type": row.get("actor_type"),
            "actor_user_id": row.get("actor_user_id"),
            "actor_label": row.get("actor_label"),
            "occurred_at": format_iso_datetime(row.get("occurred_at")),
            "notes": row.get("notes"),
            "entry_method": row.get("entry_method"),
            "access_status": row.get("access_status"),
        }

    async def _get_pass_or_404(self, *, pass_id: str) -> dict[str, Any]:
        """Load an org-scoped pass or raise 404."""
        org_id = self.user_context.organization_id
        assert org_id
        row = await self.passes_repo.get_by_id(
            organization_id=org_id,
            pass_id=pass_id,
        )
        if not row:
            raise NotFoundException(
                message_key="passes.errors.pass_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return row

    async def _get_gate_or_404(self, *, gate_id: str) -> dict[str, Any]:
        """Load an org-scoped tower gate or raise 404."""
        org_id = self.user_context.organization_id
        assert org_id
        row = await self.towers_repo.get_gate_by_id(
            organization_id=org_id,
            gate_id=gate_id,
        )
        if not row:
            raise NotFoundException(
                message_key="passes.errors.gate_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return row

    async def _ensure_gate_if_provided(self, *, gate_id: str | None) -> None:
        """Validate tower gate when the client supplies gate_id."""
        if gate_id:
            await self._get_gate_or_404(gate_id=gate_id)

    async def verify(self, *, code: str, gate_id: str | None = None) -> dict[str, Any]:
        """Read-only lookup of a pass by 4-digit code."""
        del gate_id  # reserved for future gate-scoped rules
        org_id = self.user_context.organization_id
        assert org_id
        row = await self.passes_repo.get_by_code(
            organization_id=org_id,
            code=code,
        )
        if not row:
            raise NotFoundException(
                message_key="passes.errors.pass_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        decision = self._compute_admissibility(row)
        daily_help_profile: dict[str, Any] | None = None
        daily_help_id = row.get("daily_help_id")
        project_id = row.get("project_id")
        if daily_help_id and project_id:
            daily_help_service = DailyHelpService(
                db_connection=self.db_connection,
                user_context=self.user_context,
            )
            daily_help_profile = await daily_help_service.get_verify_profile_summary(
                project_id=str(project_id),
                profile_id=str(daily_help_id),
            )
        return self._normalize_verify_response(
            row,
            decision,
            daily_help_profile=daily_help_profile,
        )

    async def check_in(
        self,
        *,
        pass_id: str,
        body: CheckInRequest,
    ) -> dict[str, Any]:
        """Record guest entry at the gate."""
        org_id = self.user_context.organization_id
        assert org_id
        await self._ensure_gate_if_provided(gate_id=body.gate_id)
        row = await self._get_pass_or_404(pass_id=pass_id)
        decision = self._compute_admissibility(row)
        override = body.access_status == PassAccessStatus.GRANTED

        if not decision.can_check_in and not override:
            refusal_status = decision.access_status
            if decision.max_entries_reached:
                refusal_status = PassAccessStatus.DENIED.value
            await self.events_repo.insert_event(
                {
                    "organization_id": org_id,
                    "pass_id": pass_id,
                    "event_type": PassEventType.CHECKED_IN.value,
                    "gate_id": body.gate_id,
                    "actor_type": PassActorType.STAFF.value,
                    "actor_user_id": self.user_context.user_id,
                    "notes": body.notes,
                    "entry_method": body.entry_method.value,
                    "access_status": refusal_status,
                }
            )
            if decision.max_entries_reached:
                raise ValidationException(
                    message_key="passes.errors.max_entries_reached",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            raise ValidationException(
                message_key="passes.errors.pass_invalid_or_expired",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        event = await self.events_repo.insert_event(
            {
                "organization_id": org_id,
                "pass_id": pass_id,
                "event_type": PassEventType.CHECKED_IN.value,
                "gate_id": body.gate_id,
                "actor_type": PassActorType.STAFF.value,
                "actor_user_id": self.user_context.user_id,
                "notes": body.notes,
                "entry_method": body.entry_method.value,
                "access_status": body.access_status.value,
            }
        )
        updated = await self.passes_repo.increment_entry_count(
            organization_id=org_id,
            pass_id=pass_id,
        )
        refreshed = await self._get_pass_or_404(pass_id=pass_id)
        if refreshed.get("daily_help_id"):
            await DailyHelpNotificationService(
                db_connection=self.db_connection,
            ).notify_linked_unit_holders(
                organization_id=org_id,
                profile_id=str(refreshed["daily_help_id"]),
                pass_id=pass_id,
                event_id=str(event["id"]),
                message_key="notifications.push.daily_help.checked_in",
                idempotency_suffix="checked_in",
                helper_name=str(refreshed.get("guest_name") or ""),
                project_id=str(refreshed.get("project_id") or "") or None,
            )
        else:
            await self._notify_household_pass_event(
                pass_row=refreshed,
                message_key="notifications.push.pass.checked_in",
                idempotency_suffix="checked_in",
            )
        return {
            "event": self._normalize_event(event),
            "entry_count": int(
                (updated or {}).get("entry_count") or refreshed.get("entry_count") or 0
            ),
            "pass_status": str(refreshed.get("status") or ""),
        }

    async def check_out(
        self,
        *,
        pass_id: str,
        body: CheckOutRequest,
    ) -> dict[str, Any]:
        """Record guest exit at the gate."""
        org_id = self.user_context.organization_id
        assert org_id
        await self._ensure_gate_if_provided(gate_id=body.gate_id)
        row = await self._get_pass_or_404(pass_id=pass_id)
        has_open = await self.events_repo.has_open_check_in(
            organization_id=org_id,
            pass_id=pass_id,
        )
        if not has_open:
            raise ValidationException(
                message_key="passes.errors.not_checked_in",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        event = await self.events_repo.insert_event(
            {
                "organization_id": org_id,
                "pass_id": pass_id,
                "event_type": PassEventType.CHECKED_OUT.value,
                "gate_id": body.gate_id,
                "actor_type": PassActorType.STAFF.value,
                "actor_user_id": self.user_context.user_id,
                "notes": body.notes,
            }
        )

        if str(row.get("validity_type") or "") == PassValidityType.ONE_TIME.value:
            await self.passes_repo.complete(
                organization_id=org_id,
                pass_id=pass_id,
            )

        refreshed = await self._get_pass_or_404(pass_id=pass_id)
        if refreshed.get("daily_help_id"):
            await DailyHelpNotificationService(
                db_connection=self.db_connection,
            ).notify_linked_unit_holders(
                organization_id=org_id,
                profile_id=str(refreshed["daily_help_id"]),
                pass_id=pass_id,
                event_id=str(event["id"]),
                message_key="notifications.push.daily_help.checked_out",
                idempotency_suffix="checked_out",
                helper_name=str(refreshed.get("guest_name") or ""),
                project_id=str(refreshed.get("project_id") or "") or None,
            )
        else:
            await self._notify_household_pass_event(
                pass_row=refreshed,
                message_key="notifications.push.pass.checked_out",
                idempotency_suffix="checked_out",
            )
        return {
            "event": self._normalize_event(event),
            "pass_status": str(refreshed.get("status") or ""),
        }
