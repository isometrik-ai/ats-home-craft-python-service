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
from apps.user_service.app.db.repositories.parking_slots_repository import (
    ParkingSlotsRepository,
)
from apps.user_service.app.db.repositories.pass_events_repository import (
    PassEventsRepository,
)
from apps.user_service.app.db.repositories.passes_repository import PassesRepository
from apps.user_service.app.db.repositories.tenant_requests_repository import (
    TenantRequestsRepository,
)
from apps.user_service.app.db.repositories.units_repository import UnitsRepository
from apps.user_service.app.db.repositories.vehicles_repository import VehiclesRepository
from apps.user_service.app.schemas.enums import (
    ClientStatus,
    ContactType,
    PassActorType,
    PassEventType,
    TenantRequestEventType,
    TenantRequestStatus,
    VehicleStatus,
)
from apps.user_service.app.utils.common_utils import UserContext


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
        self.units_repo = UnitsRepository(db_connection)
        self.vehicles_repo = VehiclesRepository(db_connection)
        self.parking_slots_repo = ParkingSlotsRepository(db_connection)
        self.passes_repo = PassesRepository(db_connection)
        self.pass_events_repo = PassEventsRepository(db_connection)
        self.tenant_requests_repo = TenantRequestsRepository(db_connection)
        self.household_invitations_repo = HouseholdInvitationsRepository(db_connection)

    async def cascade_before_soft_delete(
        self,
        *,
        contact_id: str,
        contact: dict[str, Any],
    ) -> None:
        """Clean up operational links before the contact row is soft-deleted."""
        org_id = self.user_context.organization_id
        assert org_id

        contact_type = str(contact.get("contact_type") or "")

        if contact_type == ContactType.OWNER.value:
            await self._release_owner_holdings(
                organization_id=org_id,
                contact_id=contact_id,
            )
            await self._cancel_inflight_tenant_requests(
                organization_id=org_id,
                submitted_by_contact_id=contact_id,
            )
        elif contact_type == ContactType.FAMILY.value:
            released = await self.contact_units_repo.release_open_links_for_contact(
                organization_id=org_id,
                contact_id=contact_id,
            )
            await self._cancel_household_invitations(
                organization_id=org_id,
                contact_unit_ids=[row["id"] for row in released],
            )
        else:
            await self._release_vehicles(organization_id=org_id, contact_id=contact_id)
            await self._cancel_passes(organization_id=org_id, contact_id=contact_id)
            released = await self.contact_units_repo.release_open_links_for_contact(
                organization_id=org_id,
                contact_id=contact_id,
            )
            await self._cancel_household_invitations(
                organization_id=org_id,
                contact_unit_ids=[row["id"] for row in released],
            )
            if contact_type == ContactType.TENANT.value:
                await self._supersede_approved_tenant_requests(
                    organization_id=org_id,
                    tenant_contact_id=contact_id,
                )

    async def _release_vehicles(self, *, organization_id: str, contact_id: str) -> None:
        """Withdraw pending vehicles and soft-remove approved ones with slot release."""
        vehicles = await self.vehicles_repo.list_by_contact(
            organization_id=organization_id,
            contact_id=contact_id,
        )
        await self._release_vehicle_rows(organization_id=organization_id, vehicles=vehicles)

    async def _release_vehicles_for_unit(
        self,
        *,
        organization_id: str,
        unit_id: str,
    ) -> None:
        """Withdraw all vehicles registered against a unit (any household member)."""
        vehicles = await self.vehicles_repo.list_by_unit(
            organization_id=organization_id,
            unit_id=unit_id,
        )
        await self._release_vehicle_rows(organization_id=organization_id, vehicles=vehicles)

    async def _release_vehicle_rows(
        self,
        *,
        organization_id: str,
        vehicles: list[dict[str, Any]],
    ) -> None:
        """Shared vehicle cleanup for contact-scoped or unit-scoped deletes."""
        for vehicle in vehicles:
            contact_id = str(vehicle["contact_id"])
            vehicle_id = str(vehicle["id"])
            status = str(vehicle.get("status") or "")
            if status == VehicleStatus.PENDING.value:
                await self.vehicles_repo.delete(
                    organization_id=organization_id,
                    contact_id=contact_id,
                    vehicle_id=vehicle_id,
                )
                continue
            if status != VehicleStatus.APPROVED.value:
                continue
            slot_id = vehicle.get("parking_slot_id")
            if slot_id:
                await self.parking_slots_repo.release_slot(
                    organization_id=organization_id,
                    project_id=str(vehicle["project_id"]),
                    slot_id=str(slot_id),
                )
            await self.vehicles_repo.soft_remove(
                organization_id=organization_id,
                contact_id=contact_id,
                vehicle_id=vehicle_id,
            )

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

    async def _cancel_passes_for_unit(
        self,
        *,
        organization_id: str,
        unit_id: str,
    ) -> None:
        """Cancel all active visitor passes for a unit (any household member)."""
        passes = await self.passes_repo.list_active_for_unit(
            organization_id=organization_id,
            unit_id=unit_id,
        )
        for row in passes:
            await self._cancel_pass(
                organization_id=organization_id,
                host_contact_id=str(row["host_contact_id"]),
                pass_id=str(row["id"]),
                notes="Cancelled because the unit owner was deleted.",
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
        """Cancel open tenant requests submitted by a deleted owner."""
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

    async def _release_owner_holdings(
        self,
        *,
        organization_id: str,
        contact_id: str,
    ) -> None:
        """Vacate owned units and move out all occupants on those units."""
        owner_links = await self.contact_units_repo.list_open_links_for_contact(
            organization_id=organization_id,
            contact_id=contact_id,
        )
        household_members = await self.contact_units_repo.list_household_by_primary(
            organization_id=organization_id,
            primary_contact_id=contact_id,
        )
        family_contact_ids = {
            str(row["contact_id"]) for row in household_members if row.get("contact_id")
        }
        unit_projects: dict[str, str] = {}
        for link in owner_links:
            unit_projects[str(link["unit_id"])] = str(link["project_id"])

        for unit_id, project_id in unit_projects.items():
            await self._supersede_approved_tenant_for_unit(
                organization_id=organization_id,
                unit_id=unit_id,
            )
            await self._release_vehicles_for_unit(
                organization_id=organization_id,
                unit_id=unit_id,
            )
            await self._cancel_passes_for_unit(
                organization_id=organization_id,
                unit_id=unit_id,
            )
            released = await self.contact_units_repo.release_all_open_links_for_unit(
                organization_id=organization_id,
                unit_id=unit_id,
            )
            await self._cancel_household_invitations(
                organization_id=organization_id,
                contact_unit_ids=[row["id"] for row in released],
            )
            await self.units_repo.mark_unit_vacant(
                organization_id=organization_id,
                project_id=project_id,
                unit_id=unit_id,
            )

        await self._soft_delete_orphaned_family_contacts(
            organization_id=organization_id,
            contact_ids=family_contact_ids,
        )

    async def _soft_delete_orphaned_family_contacts(
        self,
        *,
        organization_id: str,
        contact_ids: set[str],
    ) -> None:
        """Soft-delete household family contacts with no remaining unit links."""
        for family_contact_id in contact_ids:
            contact = await self.contacts_repo.get_contact_for_update(
                contact_id=family_contact_id,
                organization_id=organization_id,
            )
            if not contact:
                continue
            if str(contact.get("status") or "") == ClientStatus.DELETED.value:
                continue
            if str(contact.get("contact_type") or "") != ContactType.FAMILY.value:
                continue
            remaining_links = await self.contact_units_repo.list_open_links_for_contact(
                organization_id=organization_id,
                contact_id=family_contact_id,
            )
            if remaining_links:
                continue
            await self.contacts_repo.soft_delete_contact(
                contact_id=family_contact_id,
                organization_id=organization_id,
            )

    async def _supersede_approved_tenant_for_unit(
        self,
        *,
        organization_id: str,
        unit_id: str,
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
            payload={"reason": "owner_contact_deleted"},
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
