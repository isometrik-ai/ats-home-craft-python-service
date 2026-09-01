"""Release household occupancy artifacts when a tenant leaves or a new one arrives."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.contact_roles_repository import (
    ContactRolesRepository,
)
from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.db.repositories.contacts_repository import ContactsRepository
from apps.user_service.app.db.repositories.daily_help_repository import (
    DailyHelpRepository,
)
from apps.user_service.app.db.repositories.household_invitations_repository import (
    HouseholdInvitationsRepository,
)
from apps.user_service.app.db.repositories.pass_events_repository import (
    PassEventsRepository,
)
from apps.user_service.app.db.repositories.passes_repository import PassesRepository
from apps.user_service.app.db.repositories.tenant_requests_repository import (
    TenantRequestsRepository,
)
from apps.user_service.app.db.repositories.units_repository import UnitsRepository
from apps.user_service.app.schemas.enums import (
    ClientStatus,
    ContactType,
    ContactUnitRelationship,
    PassActorType,
    PassEventType,
    TenantRequestEventType,
    TenantRequestStatus,
)
from apps.user_service.app.services.vehicles_service import VehiclesService
from apps.user_service.app.services.walk_in_service import WalkInService
from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.app.utils.contact_session_utils import (
    revoke_contact_portal_sessions,
)
from apps.user_service.app.utils.contact_notice_utils import (
    purge_contact_notice_likes,
)
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.status_codes import CustomStatusCode


class UnitOccupancyTurnoverService:
    """Clear outgoing tenant household data on a unit while preserving the owner."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.contact_units_repo = ContactUnitsRepository(db_connection)
        self.contact_roles_repo = ContactRolesRepository(db_connection)
        self.contacts_repo = ContactsRepository(db_connection)
        self.units_repo = UnitsRepository(db_connection)
        self.passes_repo = PassesRepository(db_connection)
        self.pass_events_repo = PassEventsRepository(db_connection)
        self.household_invitations_repo = HouseholdInvitationsRepository(db_connection)
        self.daily_help_repo = DailyHelpRepository(db_connection)
        self.tenant_requests_repo = TenantRequestsRepository(db_connection)

    async def vacate_unit_completely(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str,
        reason: str,
        supersede_reason: str,
        require_open_links: bool = False,
    ) -> dict[str, Any]:
        """Vacate a unit: move out all occupants and clear household artifacts."""
        owner = await self.units_repo.get_unit_owner_contact(
            organization_id=organization_id,
            unit_id=unit_id,
        )
        previous_contact_id = (
            str(owner["contact_id"]) if owner and owner.get("contact_id") else None
        )

        await self._supersede_approved_tenant_for_unit(
            organization_id=organization_id,
            unit_id=unit_id,
            reason=supersede_reason,
        )
        await self._clear_unit_scoped_assets(
            organization_id=organization_id,
            unit_id=unit_id,
            pass_cancel_notes=reason,
        )
        released = await self.contact_units_repo.release_all_open_links_for_unit(
            organization_id=organization_id,
            unit_id=unit_id,
        )
        if require_open_links and not released:
            raise NotFoundException(
                message_key="project_setup.errors.unit_owner_not_assigned",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        if released:
            await self._cancel_household_invitations(
                organization_id=organization_id,
                contact_unit_ids=[str(row["id"]) for row in released if row.get("id")],
            )
            await self.contact_roles_repo.end_active_roles_for_unit(
                organization_id=organization_id,
                unit_id=unit_id,
            )
            released_contact_ids = {
                str(row["contact_id"]) for row in released if row.get("contact_id")
            }
            family_contact_ids = {
                str(row["contact_id"])
                for row in released
                if row.get("contact_id")
                and str(row.get("relationship") or "") != ContactUnitRelationship.SELF.value
            }
            await self._revoke_sessions_without_remaining_links(
                organization_id=organization_id,
                contact_ids=released_contact_ids - family_contact_ids,
            )
            await self._soft_delete_orphaned_household_contacts(
                organization_id=organization_id,
                contact_ids=family_contact_ids,
            )
            if not previous_contact_id:
                for row in released:
                    if str(row.get("relationship") or "") == ContactUnitRelationship.SELF.value:
                        previous_contact_id = str(row["contact_id"])
                        break
                if not previous_contact_id and released:
                    previous_contact_id = str(released[0]["contact_id"])

        unit_status = await self.units_repo.reconcile_unit_inventory_status(
            organization_id=organization_id,
            project_id=project_id,
            unit_id=unit_id,
        )
        return {
            "released": released,
            "released_contact_unit_ids": [str(row["id"]) for row in released if row.get("id")],
            "previous_contact_id": previous_contact_id,
            "unit_status": unit_status or "vacant",
        }

    async def release_outgoing_tenant_household(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str,
        reason: str,
    ) -> list[str]:
        """Move out all non-owner occupants and clear unit household artifacts."""
        exclude_contact_ids = await self._owner_contact_ids(
            organization_id=organization_id,
            unit_id=unit_id,
        )
        released = await self.contact_units_repo.release_occupant_links_excluding_contacts(
            organization_id=organization_id,
            unit_id=unit_id,
            exclude_contact_ids=exclude_contact_ids,
        )
        if not released:
            await self._clear_unit_scoped_assets(
                organization_id=organization_id,
                unit_id=unit_id,
                pass_cancel_notes=reason,
            )
            await self.units_repo.reconcile_unit_inventory_status(
                organization_id=organization_id,
                project_id=project_id,
                unit_id=unit_id,
            )
            return []

        released_contact_ids = {str(row["contact_id"]) for row in released if row.get("contact_id")}
        family_contact_ids = {
            str(row["contact_id"])
            for row in released
            if row.get("contact_id")
            and str(row.get("relationship") or "") != ContactUnitRelationship.SELF.value
        }

        await self._cancel_household_invitations(
            organization_id=organization_id,
            contact_unit_ids=[str(row["id"]) for row in released if row.get("id")],
        )
        await self.contact_roles_repo.end_active_roles_for_unit(
            organization_id=organization_id,
            unit_id=unit_id,
            role_types=[ContactType.TENANT.value, ContactType.FAMILY.value],
        )
        await self._clear_unit_scoped_assets(
            organization_id=organization_id,
            unit_id=unit_id,
            pass_cancel_notes=reason,
        )
        await self._revoke_portal_sessions(
            organization_id=organization_id,
            contact_ids=released_contact_ids,
        )
        await self._soft_delete_orphaned_household_contacts(
            organization_id=organization_id,
            contact_ids=family_contact_ids,
        )
        await self.units_repo.reconcile_unit_inventory_status(
            organization_id=organization_id,
            project_id=project_id,
            unit_id=unit_id,
        )
        return sorted(released_contact_ids)

    async def release_single_occupant(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str,
        contact_id: str,
        reason: str,
    ) -> None:
        """Move out one occupant on a unit without clearing the full household."""
        released = await self.contact_units_repo.release_open_links_for_contact_on_unit(
            organization_id=organization_id,
            contact_id=contact_id,
            unit_id=unit_id,
        )
        if not released:
            return

        await self._cancel_household_invitations(
            organization_id=organization_id,
            contact_unit_ids=[str(row["id"]) for row in released if row.get("id")],
        )
        await self.contact_roles_repo.end_active_roles_for_unit(
            organization_id=organization_id,
            unit_id=unit_id,
            role_types=[ContactType.FAMILY.value, ContactType.TENANT.value],
            contact_id=contact_id,
        )
        vehicles_service = VehiclesService(
            db_connection=self.db_connection,
            user_context=self.user_context,
        )
        await vehicles_service.release_for_move_out(
            contact_id=contact_id,
            unit_id=unit_id,
        )
        pass_ids = await self.passes_repo.list_active_ids_for_host(
            organization_id=organization_id,
            host_contact_id=contact_id,
        )
        for pass_id in pass_ids:
            await self._cancel_pass(
                organization_id=organization_id,
                host_contact_id=contact_id,
                pass_id=pass_id,
                notes=reason,
            )
        is_family = any(
            str(row.get("relationship") or "") != ContactUnitRelationship.SELF.value
            for row in released
        )
        if is_family:
            await self._soft_delete_orphaned_household_contacts(
                organization_id=organization_id,
                contact_ids={contact_id},
            )
        else:
            await self._revoke_portal_sessions(
                organization_id=organization_id,
                contact_ids={contact_id},
            )
        await self.units_repo.reconcile_unit_inventory_status(
            organization_id=organization_id,
            project_id=project_id,
            unit_id=unit_id,
        )

    async def _owner_contact_ids(
        self,
        *,
        organization_id: str,
        unit_id: str,
    ) -> list[str]:
        """Return contact ids that must remain linked to the unit (owner)."""
        owner = await self.units_repo.get_unit_owner_contact(
            organization_id=organization_id,
            unit_id=unit_id,
        )
        if owner and owner.get("contact_id"):
            return [str(owner["contact_id"])]
        return []

    async def _clear_unit_scoped_assets(
        self,
        *,
        organization_id: str,
        unit_id: str,
        pass_cancel_notes: str,
    ) -> None:
        """Release vehicles, cancel passes, and unlink daily help for a unit."""
        vehicles_service = VehiclesService(
            db_connection=self.db_connection,
            user_context=self.user_context,
        )
        await vehicles_service.release_for_move_out(unit_id=unit_id)

        passes = await self.passes_repo.list_active_for_unit(
            organization_id=organization_id,
            unit_id=unit_id,
        )
        for row in passes:
            await self._cancel_pass(
                organization_id=organization_id,
                host_contact_id=str(row["host_contact_id"]),
                pass_id=str(row["id"]),
                notes=pass_cancel_notes,
            )

        await self.daily_help_repo.remove_all_active_links_for_unit(
            organization_id=organization_id,
            unit_id=unit_id,
            removal_reason=pass_cancel_notes,
        )

        walk_in_service = WalkInService(
            db_connection=self.db_connection,
            user_context=self.user_context,
        )
        await walk_in_service.release_open_visit_units_for_unit_turnover(
            unit_id=unit_id,
            reason=pass_cancel_notes,
        )

    async def _cancel_pass(
        self,
        *,
        organization_id: str,
        host_contact_id: str,
        pass_id: str,
        notes: str,
    ) -> None:
        """Cancel one active pass and record the staff-initiated event."""
        cancelled = await self.passes_repo.cancel(
            organization_id=organization_id,
            host_contact_id=host_contact_id,
            pass_id=pass_id,
        )
        if not cancelled:
            return
        await self.pass_events_repo.insert_event(
            {
                "organization_id": organization_id,
                "pass_id": pass_id,
                "event_type": PassEventType.CANCELLED.value,
                "actor_type": PassActorType.STAFF.value,
                "actor_user_id": self.user_context.user_id,
                "notes": notes,
            }
        )

    async def _cancel_household_invitations(
        self,
        *,
        organization_id: str,
        contact_unit_ids: list[str],
    ) -> None:
        """Cancel pending household invitations for released contact-unit links."""
        for contact_unit_id in contact_unit_ids:
            await self.household_invitations_repo.cancel_by_contact_unit(
                organization_id=organization_id,
                contact_unit_id=contact_unit_id,
            )

    async def _supersede_approved_tenant_for_unit(
        self,
        *,
        organization_id: str,
        unit_id: str,
        reason: str,
    ) -> None:
        """Supersede the active approved tenant request on a unit, if any."""
        existing = await self.tenant_requests_repo.find_active_approved_for_unit(
            organization_id=organization_id,
            unit_id=unit_id,
        )
        if not existing:
            return
        now = datetime.now(timezone.utc)
        contact_unit_id = existing.get("contact_unit_id")
        if contact_unit_id:
            await self.contact_units_repo.sync_move_out(
                organization_id=organization_id,
                contact_unit_id=str(contact_unit_id),
                event_date=now,
            )
        await self.tenant_requests_repo.update_request_status(
            organization_id=organization_id,
            tenant_request_id=str(existing["id"]),
            status=TenantRequestStatus.SUPERSEDED.value,
            superseded_at=now,
        )
        await self.tenant_requests_repo.insert_event(
            organization_id=organization_id,
            tenant_request_id=str(existing["id"]),
            event_type=TenantRequestEventType.SUPERSEDED.value,
            actor_user_id=str(self.user_context.user_id) if self.user_context.user_id else None,
            payload={"reason": reason},
        )

    async def _revoke_sessions_without_remaining_links(
        self,
        *,
        organization_id: str,
        contact_ids: set[str],
    ) -> None:
        """Revoke portal sessions for contacts with no remaining unit links."""
        for contact_id in contact_ids:
            remaining_links = await self.contact_units_repo.list_open_links_for_contact(
                organization_id=organization_id,
                contact_id=contact_id,
            )
            if remaining_links:
                continue
            contact = await self.contacts_repo.get_contact_for_update(
                contact_id=contact_id,
                organization_id=organization_id,
            )
            if not contact or not contact.get("user_id"):
                continue
            await revoke_contact_portal_sessions(
                db_connection=self.db_connection,
                organization_id=organization_id,
                user_id=contact.get("user_id"),
            )

    async def _revoke_portal_sessions(
        self,
        *,
        organization_id: str,
        contact_ids: set[str],
    ) -> None:
        """Revoke portal sessions for contacts who lost unit access."""
        for contact_id in contact_ids:
            contact = await self.contacts_repo.get_contact_for_update(
                contact_id=contact_id,
                organization_id=organization_id,
            )
            if not contact or not contact.get("user_id"):
                continue
            await revoke_contact_portal_sessions(
                db_connection=self.db_connection,
                organization_id=organization_id,
                user_id=contact.get("user_id"),
            )

    async def _soft_delete_orphaned_household_contacts(
        self,
        *,
        organization_id: str,
        contact_ids: set[str],
    ) -> None:
        """Soft-delete household contacts with no remaining unit links."""
        for household_contact_id in contact_ids:
            contact = await self.contacts_repo.get_contact_for_update(
                contact_id=household_contact_id,
                organization_id=organization_id,
            )
            if not contact:
                continue
            if str(contact.get("status") or "") == ClientStatus.DELETED.value:
                continue
            remaining_links = await self.contact_units_repo.list_open_links_for_contact(
                organization_id=organization_id,
                contact_id=household_contact_id,
            )
            if remaining_links:
                continue
            await self.contacts_repo.soft_delete_contact(
                contact_id=household_contact_id,
                organization_id=organization_id,
            )
            await purge_contact_notice_likes(
                db_connection=self.db_connection,
                organization_id=organization_id,
                contact_id=household_contact_id,
            )
            await revoke_contact_portal_sessions(
                db_connection=self.db_connection,
                organization_id=organization_id,
                user_id=contact.get("user_id"),
            )
