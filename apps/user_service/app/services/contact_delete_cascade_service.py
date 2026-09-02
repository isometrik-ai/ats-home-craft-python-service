"""Cascade cleanup before soft-deleting a property-management contact."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.db.repositories.contacts_repository import ContactsRepository
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
from apps.user_service.app.schemas.enums import (
    ClientStatus,
    ContactUnitRelationship,
    PassActorType,
    PassEventType,
    TenantRequestEventType,
    TenantRequestStatus,
)
from apps.user_service.app.services.unit_occupancy_turnover_service import (
    UnitOccupancyTurnoverService,
)
from apps.user_service.app.services.vehicles_service import VehiclesService
from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.app.utils.contact_notice_utils import (
    purge_contact_notice_likes,
)
from apps.user_service.app.utils.contact_session_utils import (
    revoke_contact_portal_sessions,
)


class ContactDeleteCascadeService:
    """Release units, parking, passes, and related links before contact delete."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.contacts_repo = ContactsRepository(db_connection)
        self.contact_units_repo = ContactUnitsRepository(db_connection)
        self.passes_repo = PassesRepository(db_connection)
        self.pass_events_repo = PassEventsRepository(db_connection)
        self.tenant_requests_repo = TenantRequestsRepository(db_connection)
        self.household_invitations_repo = HouseholdInvitationsRepository(db_connection)

    @staticmethod
    def _split_open_links(
        open_links: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Partition open links into primary occupant (self) vs household."""
        primary: list[dict[str, Any]] = []
        household: list[dict[str, Any]] = []
        for link in open_links:
            if str(link.get("relationship") or "") == ContactUnitRelationship.SELF.value:
                primary.append(link)
            else:
                household.append(link)
        return primary, household

    async def cascade_before_soft_delete(
        self,
        *,
        contact_id: str,
        contact: dict[str, Any],
    ) -> None:
        """Clean up operational links before the contact row is soft-deleted."""
        org_id = self.user_context.organization_id
        assert org_id
        del contact

        open_links = await self.contact_units_repo.list_open_links_for_contact(
            organization_id=org_id,
            contact_id=contact_id,
        )
        primary_links, household_links = self._split_open_links(open_links)
        is_approved_tenant = await self._is_approved_tenant_contact(
            organization_id=org_id,
            contact_id=contact_id,
        )

        if primary_links and not is_approved_tenant:
            await self._release_primary_holdings(
                organization_id=org_id,
                contact_id=contact_id,
                primary_links=primary_links,
            )
            await self._cancel_inflight_tenant_requests(
                organization_id=org_id,
                submitted_by_contact_id=contact_id,
            )
        elif primary_links and is_approved_tenant:
            await self._release_outgoing_tenant_households(
                organization_id=org_id,
                primary_links=primary_links,
            )
            await self._supersede_approved_tenant_requests(
                organization_id=org_id,
                tenant_contact_id=contact_id,
            )
        elif household_links:
            await self._release_household_members(
                organization_id=org_id,
                contact_id=contact_id,
                household_links=household_links,
            )
        else:
            await self._release_occupant_scoped_assets(
                organization_id=org_id,
                contact_id=contact_id,
            )
            await self._supersede_approved_tenant_requests(
                organization_id=org_id,
                tenant_contact_id=contact_id,
            )

    async def _is_approved_tenant_contact(
        self,
        *,
        organization_id: str,
        contact_id: str,
    ) -> bool:
        """True when the contact is the active approved tenant on a unit."""
        existing = await self.tenant_requests_repo.find_active_approved_for_unit_by_tenant(
            organization_id=organization_id,
            tenant_contact_id=contact_id,
        )
        return existing is not None

    async def _release_outgoing_tenant_households(
        self,
        *,
        organization_id: str,
        primary_links: list[dict[str, Any]],
    ) -> None:
        """Mirror admin tenant move-out turnover for each unit the tenant occupies."""
        turnover_service = UnitOccupancyTurnoverService(
            db_connection=self.db_connection,
            user_context=self.user_context,
        )
        seen_unit_ids: set[str] = set()
        for link in primary_links:
            unit_id = str(link["unit_id"])
            if unit_id in seen_unit_ids:
                continue
            seen_unit_ids.add(unit_id)
            await turnover_service.release_outgoing_tenant_household(
                organization_id=organization_id,
                project_id=str(link["project_id"]),
                unit_id=unit_id,
                reason="Contact deleted; clearing outgoing household.",
            )

    async def _release_household_members(
        self,
        *,
        organization_id: str,
        contact_id: str,
        household_links: list[dict[str, Any]],
    ) -> None:
        """Mirror admin single-occupant move-out for each household link."""
        turnover_service = UnitOccupancyTurnoverService(
            db_connection=self.db_connection,
            user_context=self.user_context,
        )
        seen_unit_ids: set[str] = set()
        for link in household_links:
            unit_id = str(link["unit_id"])
            if unit_id in seen_unit_ids:
                continue
            seen_unit_ids.add(unit_id)
            await turnover_service.release_single_occupant(
                organization_id=organization_id,
                project_id=str(link["project_id"]),
                unit_id=unit_id,
                contact_id=contact_id,
                reason="Contact deleted; clearing occupant.",
            )

    async def _release_occupant_scoped_assets(
        self,
        *,
        organization_id: str,
        contact_id: str,
    ) -> None:
        """Release vehicles, passes, and unit links scoped to one occupant."""
        await self._release_vehicles(_organization_id=organization_id, contact_id=contact_id)
        await self._cancel_passes(organization_id=organization_id, contact_id=contact_id)
        released = await self.contact_units_repo.release_open_links_for_contact(
            organization_id=organization_id,
            contact_id=contact_id,
        )
        await self._cancel_household_invitations(
            organization_id=organization_id,
            contact_unit_ids=[row["id"] for row in released],
        )

    async def _release_vehicles(self, *, _organization_id: str, contact_id: str) -> None:
        """Withdraw pending vehicles and soft-remove approved ones with slot release."""
        vehicles_service = VehiclesService(
            db_connection=self.db_connection,
            user_context=self.user_context,
        )
        await vehicles_service.release_for_move_out(contact_id=contact_id)

    async def _cancel_passes(self, *, organization_id: str, contact_id: str) -> None:
        """Cancel active visitor passes hosted by the contact."""
        pass_ids = await self.passes_repo.list_active_ids_for_host(
            organization_id=organization_id,
            host_contact_id=contact_id,
        )
        for pass_id in pass_ids:
            await self._cancel_pass(
                organization_id=organization_id,
                host_contact_id=contact_id,
                pass_id=pass_id,
                notes="Cancelled because the host contact was deleted.",
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

    async def _cancel_inflight_tenant_requests(
        self,
        *,
        organization_id: str,
        submitted_by_contact_id: str,
    ) -> None:
        """Cancel open tenant requests submitted by a deleted primary occupant."""
        now = datetime.now(timezone.utc)
        request_ids = await self.tenant_requests_repo.list_inflight_ids_for_submitter(
            organization_id=organization_id,
            submitted_by_contact_id=submitted_by_contact_id,
        )
        for tenant_request_id in request_ids:
            await self.tenant_requests_repo.update_request_status(
                organization_id=organization_id,
                tenant_request_id=tenant_request_id,
                status=TenantRequestStatus.CANCELLED.value,
                cancelled_at=now,
            )
            await self.tenant_requests_repo.insert_event(
                organization_id=organization_id,
                tenant_request_id=tenant_request_id,
                event_type=TenantRequestEventType.CANCELLED.value,
                actor_user_id=str(self.user_context.user_id) if self.user_context.user_id else None,
                actor_contact_id=submitted_by_contact_id,
            )

    async def _supersede_approved_tenant_requests(
        self,
        *,
        organization_id: str,
        tenant_contact_id: str,
    ) -> None:
        """Supersede approved tenant requests when the tenant contact is deleted."""
        now = datetime.now(timezone.utc)
        existing = await self.tenant_requests_repo.find_active_approved_for_unit_by_tenant(
            organization_id=organization_id,
            tenant_contact_id=tenant_contact_id,
        )
        if not existing:
            return
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
            payload={"reason": "tenant_contact_deleted"},
        )

    async def _release_primary_holdings(
        self,
        *,
        organization_id: str,
        contact_id: str,
        primary_links: list[dict[str, Any]],
    ) -> None:
        """Vacate units where the contact is primary occupant and move out all occupants."""
        household_members = await self.contact_units_repo.list_household_by_primary(
            organization_id=organization_id,
            primary_contact_id=contact_id,
        )
        family_contact_ids = {
            str(row["contact_id"]) for row in household_members if row.get("contact_id")
        }
        unit_projects: dict[str, str] = {}
        for link in primary_links:
            unit_projects[str(link["unit_id"])] = str(link["project_id"])

        for unit_id, project_id in unit_projects.items():
            from apps.user_service.app.services.move_events_service import (
                MoveEventsService,
            )

            move_events_service = MoveEventsService(
                db_connection=self.db_connection,
                user_context=self.user_context,
            )
            tenant_move_event_id = (
                await move_events_service.record_tenant_move_out_for_owner_change(
                    unit_id=unit_id,
                    project_id=project_id,
                    notes="Tenant move-out recorded because the unit owner was deleted.",
                )
            )
            turnover_service = UnitOccupancyTurnoverService(
                db_connection=self.db_connection,
                user_context=self.user_context,
            )
            await turnover_service.vacate_unit_completely(
                organization_id=organization_id,
                project_id=project_id,
                unit_id=unit_id,
                reason="Cancelled because the unit owner was deleted.",
                supersede_reason="owner_contact_deleted",
                tenant_already_moved_out=tenant_move_event_id is not None,
            )

        await self._soft_delete_orphaned_household_contacts(
            organization_id=organization_id,
            contact_ids=family_contact_ids,
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
