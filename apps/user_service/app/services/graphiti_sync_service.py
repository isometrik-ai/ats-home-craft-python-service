"""Build CRM entity snapshots and sync them into Graphiti on FalkorDB."""

from __future__ import annotations

from typing import Any, Literal

import asyncpg

from apps.user_service.app.db.repositories import (
    CompaniesRepository,
    ContactsRepository,
)
from apps.user_service.app.db.repositories.lead_repository import LeadRepository
from apps.user_service.app.services.organization_memory_service import (
    is_organization_memory_enabled,
)
from apps.user_service.app.services.supermemory_sync_service import (
    resolve_sync_targets,
)
from apps.user_service.app.services.typesense_index_service import (
    _extract_contact_company_linkage,
)
from apps.user_service.app.utils.common_utils import parse_json_field
from libs.shared_utils.graphiti_crm_models import (
    ContactSnapshot,
    CrmSnapshot,
    custom_id_for_entity,
)
from libs.shared_utils.graphiti_service import (
    GraphitiCrmService,
    container_tag_for_organization,
)
from libs.shared_utils.graphiti_snapshot_builders import (
    build_company_snapshot,
    build_contact_snapshot,
    build_lead_snapshot,
)
from libs.shared_utils.logger import get_logger

logger = get_logger("graphiti_sync_service")

EntityType = Literal["contact", "company", "lead"]


class GraphitiSyncService:
    """Load CRM entities from Postgres and push snapshots into Graphiti."""

    def __init__(self, *, graphiti: GraphitiCrmService | None = None) -> None:
        self._graphiti = graphiti or GraphitiCrmService()

    async def process_crm_event(
        self,
        db_connection: asyncpg.Connection,
        event: dict[str, Any],
    ) -> None:
        """Handle one CRM Kafka event envelope."""
        organization_id = str(event.get("organization_id") or "")
        event_type = str(event.get("event_type") or "")
        aggregate_id = str(event.get("aggregate_id") or "")
        event_id = str(event.get("event_id") or "")

        if not organization_id:
            logger.info(
                "graphiti_sync_noop missing organization_id event_id=%s "
                "event_type=%s aggregate_id=%s",
                event_id,
                event_type,
                aggregate_id,
            )
            return
        if not await is_organization_memory_enabled(db_connection, organization_id):
            logger.info(
                "graphiti_sync_noop organization_memory disabled "
                "organization_id=%s event_id=%s event_type=%s aggregate_id=%s",
                organization_id,
                event_id,
                event_type,
                aggregate_id,
            )
            return
        payload = event.get("payload")
        payload_dict = payload if isinstance(payload, dict) else {}

        targets = resolve_sync_targets(
            event_type=event_type,
            aggregate_id=aggregate_id,
            payload=payload_dict,
        )
        if not targets:
            logger.info(
                "graphiti_sync_noop no sync targets event_id=%s event_type=%s "
                "aggregate_id=%s organization_id=%s",
                event_id,
                event_type,
                aggregate_id,
                organization_id,
            )
            return

        targets_label = ",".join(f"{entity_type}:{entity_id}" for entity_type, entity_id in targets)
        logger.info(
            "graphiti_sync_processing event_id=%s event_type=%s organization_id=%s targets=%s",
            event_id,
            event_type,
            organization_id,
            targets_label,
        )
        for entity_type, entity_id in targets:
            await self.sync_entity(
                db_connection,
                organization_id=organization_id,
                entity_type=entity_type,
                entity_id=entity_id,
            )
            if entity_type == "contact":
                await self._cascade_contact_associations(
                    db_connection,
                    organization_id=organization_id,
                    contact_id=entity_id,
                )
            elif entity_type == "company":
                await self._cascade_company_associations(
                    db_connection,
                    organization_id=organization_id,
                    company_id=entity_id,
                )
            elif entity_type == "lead":
                await self._cascade_lead_associations(
                    db_connection,
                    organization_id=organization_id,
                    lead_id=entity_id,
                )

        await self._sync_association_targets_from_payload(
            db_connection,
            organization_id=organization_id,
            payload=payload_dict,
        )

    async def _sync_association_targets_from_payload(
        self,
        db_connection: asyncpg.Connection,
        *,
        organization_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Sync companies and contacts referenced in event side-effect lists."""
        seen: set[tuple[EntityType, str]] = set()
        for company_id in payload.get("affected_company_ids") or []:
            cid = str(company_id or "").strip()
            if not cid:
                continue
            key = ("company", cid)
            if key in seen:
                continue
            seen.add(key)
            await self.sync_entity(
                db_connection,
                organization_id=organization_id,
                entity_type="company",
                entity_id=cid,
            )
        for contact_id in payload.get("affected_contact_ids") or []:
            cid = str(contact_id or "").strip()
            if not cid:
                continue
            key = ("contact", cid)
            if key in seen:
                continue
            seen.add(key)
            await self.sync_entity(
                db_connection,
                organization_id=organization_id,
                entity_type="contact",
                entity_id=cid,
            )

    async def _cascade_contact_associations(
        self,
        db_connection: asyncpg.Connection,
        *,
        organization_id: str,
        contact_id: str,
    ) -> None:
        """Re-sync companies and leads linked to a contact."""
        repo = ContactsRepository(db_connection=db_connection)
        details = await repo.get_contact_details(
            contact_id=contact_id,
            organization_id=organization_id,
        )
        if not details:
            return
        # CRM contact_companies links only — work_history company names are not synced.
        company_ids, _ = _extract_contact_company_linkage(details)
        for company_id in company_ids:
            await self.sync_entity(
                db_connection,
                organization_id=organization_id,
                entity_type="company",
                entity_id=company_id,
            )

        leads = details.get("leads") or []
        if isinstance(leads, list):
            for lead in leads:
                if not isinstance(lead, dict):
                    continue
                lead_id = str(lead.get("id") or "").strip()
                if not lead_id:
                    continue
                await self.sync_entity(
                    db_connection,
                    organization_id=organization_id,
                    entity_type="lead",
                    entity_id=lead_id,
                )

    async def _cascade_company_associations(
        self,
        db_connection: asyncpg.Connection,
        *,
        organization_id: str,
        company_id: str,
    ) -> None:
        """Re-sync contacts linked to a company."""
        repo = CompaniesRepository(db_connection=db_connection)
        details = await repo.get_company_details(
            company_id=company_id,
            organization_id=organization_id,
        )
        if not details:
            return
        contacts = details.get("contacts") or []
        if not isinstance(contacts, list):
            return
        for contact in contacts:
            if not isinstance(contact, dict):
                continue
            contact_id = contact.get("id")
            if contact_id:
                await self.sync_entity(
                    db_connection,
                    organization_id=organization_id,
                    entity_type="contact",
                    entity_id=str(contact_id),
                )

    async def _cascade_lead_associations(
        self,
        db_connection: asyncpg.Connection,
        *,
        organization_id: str,
        lead_id: str,
    ) -> None:
        """Re-sync companies and contacts linked to a lead."""
        lead_repo = LeadRepository(db_connection=db_connection)
        row = await lead_repo.get_lead_detail_with_contacts_by_id(
            organization_id,
            lead_id,
            owner_id=None,
        )
        if not row:
            return
        companies = parse_json_field(row.get("companies")) or []
        if isinstance(companies, list):
            for company in companies:
                if not isinstance(company, dict):
                    continue
                cid = company.get("company_id") or company.get("id")
                if cid:
                    await self.sync_entity(
                        db_connection,
                        organization_id=organization_id,
                        entity_type="company",
                        entity_id=str(cid),
                    )
        contacts = row.get("contacts") or []
        if isinstance(contacts, list):
            for contact in contacts:
                if not isinstance(contact, dict):
                    continue
                cid = contact.get("contact_id")
                if cid:
                    await self.sync_entity(
                        db_connection,
                        organization_id=organization_id,
                        entity_type="contact",
                        entity_id=str(cid),
                    )

    async def load_contact_snapshot(
        self,
        db_connection: asyncpg.Connection,
        *,
        organization_id: str,
        contact_id: str,
    ) -> ContactSnapshot | None:
        """Load contact snapshot from canonical CRM state."""
        return await build_contact_snapshot(
            db_connection,
            organization_id=organization_id,
            contact_id=contact_id,
        )

    async def sync_entity(
        self,
        db_connection: asyncpg.Connection,
        *,
        organization_id: str,
        entity_type: EntityType,
        entity_id: str,
    ) -> None:
        """Load canonical entity state and upsert into Graphiti."""
        if not await is_organization_memory_enabled(db_connection, organization_id):
            return
        if not self._graphiti.is_configured:
            logger.warning(
                "graphiti_sync_skipped_not_configured type=%s id=%s", entity_type, entity_id
            )
            return

        snapshot = await self._load_snapshot(
            db_connection,
            organization_id=organization_id,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        if snapshot is None:
            logger.info(
                "graphiti_sync_skipped_missing_entity type=%s id=%s org=%s",
                entity_type,
                entity_id,
                organization_id,
            )
            return

        group_id = container_tag_for_organization(organization_id)
        try:
            await self._graphiti.sync_snapshot(group_id=group_id, snapshot=snapshot)
        except Exception:
            logger.exception(
                "graphiti_sync_failed type=%s id=%s org=%s custom_id=%s",
                entity_type,
                entity_id,
                organization_id,
                custom_id_for_entity(entity_type, entity_id),
            )
            raise

    async def sync_contact_with_associations(
        self,
        db_connection: asyncpg.Connection,
        *,
        organization_id: str,
        contact_id: str,
    ) -> dict[str, Any]:
        """Sync a contact and its linked companies/leads into Graphiti.

        Mirrors the CRM Kafka consumer cascade for a single contact:
        contact snapshot → linked companies → linked leads.
        """
        snapshot = await build_contact_snapshot(
            db_connection,
            organization_id=organization_id,
            contact_id=contact_id,
        )
        if snapshot is None:
            raise LookupError(f"Contact not found: {contact_id}")

        await self.sync_entity(
            db_connection,
            organization_id=organization_id,
            entity_type="contact",
            entity_id=contact_id,
        )
        await self._cascade_contact_associations(
            db_connection,
            organization_id=organization_id,
            contact_id=contact_id,
        )

        company_ids = [
            (company.company_id or "").strip()
            for company in snapshot.linked_companies
            if (company.company_id or "").strip()
        ]
        lead_ids = [
            (lead.lead_id or "").strip()
            for lead in snapshot.linked_leads
            if (lead.lead_id or "").strip()
        ]

        return {
            "contact_id": contact_id,
            "organization_id": organization_id,
            "company_ids": company_ids,
            "lead_ids": lead_ids,
            "snapshot": snapshot,
        }

    async def _load_snapshot(
        self,
        db_connection: asyncpg.Connection,
        *,
        organization_id: str,
        entity_type: EntityType,
        entity_id: str,
    ) -> CrmSnapshot | None:
        """Load the canonical snapshot for one CRM entity type."""
        if entity_type == "contact":
            return await build_contact_snapshot(
                db_connection,
                organization_id=organization_id,
                contact_id=entity_id,
            )
        if entity_type == "company":
            return await build_company_snapshot(
                db_connection,
                organization_id=organization_id,
                company_id=entity_id,
            )
        return await build_lead_snapshot(
            db_connection,
            organization_id=organization_id,
            lead_id=entity_id,
        )
