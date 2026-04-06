"""Business logic for leads (public.leads, public.lead_contacts)."""

import json
from collections import defaultdict
from typing import Any

import asyncpg
from asyncpg import UniqueViolationError

from apps.user_service.app.db.repositories.lead_repository import LeadRepository
from apps.user_service.app.db.repositories.lead_stage_repository import (
    LeadStageRepository,
)
from apps.user_service.app.db.repositories.user_repository import UserRepository
from apps.user_service.app.schemas.enums import ClientType, EntityType, LeadsListMode
from apps.user_service.app.schemas.lead_stages import Unset
from apps.user_service.app.schemas.leads import (
    CreateLeadRequest,
    LeadContactDetail,
    LeadDetail,
    LeadKanbanStageGroup,
    LeadListItem,
    LeadNoteItem,
    LeadsListQueryParams,
    UpdateLeadRequest,
)
from apps.user_service.app.services.custom_field_service import CustomFieldService
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
    parse_json_field,
)
from libs.shared_utils.http_exceptions import (
    DuplicateValueException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode


class LeadService:
    """Create and orchestrate lead operations."""

    def __init__(
        self,
        db_connection: asyncpg.Connection,
        user_context: UserContext | None = None,
        lead_repository: LeadRepository | None = None,
        lead_stage_repository: LeadStageRepository | None = None,
        user_repository: UserRepository | None = None,
    ) -> None:
        self.user_context = user_context
        self.db_connection = db_connection
        self.lead_repository = lead_repository or LeadRepository(db_connection=db_connection)
        self.lead_stage_repository = lead_stage_repository or LeadStageRepository(
            db_connection=db_connection
        )
        self.user_repository = user_repository or UserRepository(db_connection=db_connection)

    async def _apply_custom_fields_if_needed(
        self,
        lead_data: dict[str, Any],
        request_data: CreateLeadRequest,
    ) -> None:
        """Validate and serialize custom fields (same rules as client create)."""
        if not (self.user_context and self.user_context.organization_id):
            return

        entity_type = EntityType.LEAD
        custom_field_service = CustomFieldService(
            db_connection=self.db_connection,
            user_context=self.user_context,
        )
        validated = await custom_field_service.validate_for_create(
            request_data.custom_fields,
            entity_type,
        )
        lead_data["custom_fields"] = validated

    @staticmethod
    def _unique_client_ids_for_refs(
        company_id: str | None,
        contact_client_ids: list[str],
    ) -> list[str]:
        """Distinct client ids for ``fetch_lead_reference_validation`` (stable order)."""
        parts = ([company_id] if company_id is not None else []) + contact_client_ids
        return list(dict.fromkeys(parts))

    @staticmethod
    def _parse_create_contacts(
        body: CreateLeadRequest,
    ) -> tuple[list[str], list[tuple[str, str | None]]]:
        """Reject duplicate contact ids; return (ordered ids, ``lead_contacts`` insert rows)."""
        if not body.contacts:
            return [], []

        seen: set[str] = set()
        ordered: list[str] = []
        for company in body.contacts:
            cid = company.contact_client_id
            if cid in seen:
                raise ValidationException(
                    message_key="leads.errors.contacts_duplicate",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            seen.add(cid)
            ordered.append(cid)

        rows = [(c.contact_client_id, c.label) for c in body.contacts]
        return ordered, rows

    @staticmethod
    def _require_person_clients(types_map: dict[str, str], contact_client_ids: list[str]) -> None:
        """Ensure every id exists in the org and is a person client."""
        for cid in contact_client_ids:
            client_type = types_map.get(cid)
            if not client_type:
                raise NotFoundException(
                    message_key="clients.errors.not_found",
                    custom_code=CustomStatusCode.NOT_FOUND,
                )
            if client_type != ClientType.PERSON.value:
                raise ValidationException(
                    message_key="leads.errors.contact_must_be_person",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )

    @staticmethod
    def _ensure_company_from_types_map(types_map: dict[str, str], company_id: str) -> None:
        """Validate optional company FK is an existing company client."""
        client_type = types_map.get(company_id)
        if client_type is None:
            raise NotFoundException(
                message_key="clients.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if client_type != ClientType.COMPANY.value:
            raise ValidationException(
                message_key="leads.errors.company_must_be_company",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    @staticmethod
    def _raise_if_stage_missing(stage_id: str | None, stage_ok: bool | None) -> None:
        """No-op when ``stage_id`` is omitted; raise when a requested stage does not exist."""
        if stage_id is None or stage_ok is None:
            return
        if not stage_ok:
            raise NotFoundException(
                message_key="lead_stages.errors.stage_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

    async def _fetch_and_validate_lead_references(
        self,
        organization_id: str,
        *,
        unique_client_ids: list[str],
        stage_id_to_validate: str | None,
        company_id: str | None,
        contact_client_ids: list[str],
    ) -> None:
        """Single round trip for pipeline stage + client types; enforce company/person rules."""
        if not unique_client_ids and stage_id_to_validate is None:
            return

        stage_ok, types_map = await self.lead_repository.fetch_lead_reference_validation(
            organization_id,
            unique_client_ids,
            stage_id=stage_id_to_validate,
        )
        LeadService._raise_if_stage_missing(stage_id_to_validate, stage_ok)
        if company_id:
            self._ensure_company_from_types_map(types_map, company_id)
        if contact_client_ids:
            self._require_person_clients(types_map, contact_client_ids)

    async def _ensure_user_exists(self, user_id: str) -> None:
        """Ensure the user exists in the organization."""
        user_row = await self.user_repository.get_user_details_by_id(user_id, ["id"])
        if not user_row:
            raise NotFoundException(
                message_key="users.errors.user_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
                params={"user_id": user_id},
            )

    async def _resolve_create_owner_id(
        self,
        body: CreateLeadRequest,
        external: bool,
        user_id: str,
    ) -> str | None:
        """Resolve owner id for create; validate explicit owner when not external."""
        if external:
            return None
        owner_id = body.owner_id if body.owner_id is not None else user_id
        if body.owner_id is not None:
            await self._ensure_user_exists(owner_id)
        return owner_id

    @staticmethod
    def _lead_row_dict_for_create(
        organization_id: str,
        body: CreateLeadRequest,
        owner_id: str | None,
    ) -> dict[str, Any]:
        """Build the insert payload for ``create_lead`` (before custom fields)."""
        notes_payload = [n.model_dump() for n in body.notes]
        return {
            "organization_id": organization_id,
            "name": body.name,
            "stage_id": body.stage_id,
            "client_company_id": body.client_company_id,
            "lead_source": body.lead_source,
            "referral_source": body.referral_source,
            "lead_score": body.lead_score,
            "deal_type": body.deal_type.value if body.deal_type is not None else None,
            "priority": body.priority.value if body.priority is not None else None,
            "close_date": body.close_date,
            "amount": body.amount,
            "description": body.description,
            "notes": notes_payload,
            "custom_fields": [],
            "owner_id": owner_id,
        }

    async def create_lead(self, body: CreateLeadRequest, external: bool = False) -> dict[str, Any]:
        """Create a lead (v2); optional company and ``lead_contacts``."""
        organization_id = self.user_context.organization_id
        user_id = self.user_context.user_id

        contact_ids_ordered, contact_rows = LeadService._parse_create_contacts(body)
        unique_client_ids = LeadService._unique_client_ids_for_refs(
            body.client_company_id,
            contact_ids_ordered,
        )
        await self._fetch_and_validate_lead_references(
            organization_id,
            unique_client_ids=unique_client_ids,
            stage_id_to_validate=body.stage_id,
            company_id=body.client_company_id,
            contact_client_ids=contact_ids_ordered,
        )
        owner_id = await self._resolve_create_owner_id(body, external, user_id)
        lead_row = self._lead_row_dict_for_create(organization_id, body, owner_id)

        await self._apply_custom_fields_if_needed(lead_row, body)

        try:
            return await self.lead_repository.create_lead(lead_row, contacts=contact_rows)
        except UniqueViolationError as exc:
            raise DuplicateValueException(
                message_key="leads.errors.duplicate_contact",
                custom_code=CustomStatusCode.DUPLICATE_ENTRY,
            ) from exc

    @staticmethod
    def _normalize_patch_contacts(
        contacts: list[Any],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Dedupe contact ids and build sync rows in one pass."""
        seen: set[str] = set()
        ids: list[str] = []
        rows: list[dict[str, Any]] = []
        for entry in contacts:
            cid = getattr(entry, "contact_client_id", None)
            if not cid:
                raise ValidationException(
                    message_key="leads.errors.contact_client_id_required",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            if cid in seen:
                raise ValidationException(
                    message_key="leads.errors.contacts_duplicate",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            seen.add(cid)
            ids.append(cid)
            rows.append({"contact_client_id": cid, "label": getattr(entry, "label", None)})
        return ids, rows

    @staticmethod
    def _apply_lead_scalar_updates(body: UpdateLeadRequest, update_data: dict[str, Any]) -> None:
        """Map PATCH body to DB columns (``Unset`` omitted)."""
        for attr in (
            "name",
            "stage_id",
            "lead_source",
            "referral_source",
            "lead_score",
            "close_date",
            "amount",
            "description",
            "owner_id",
            "client_company_id",
        ):
            val = getattr(body, attr)
            if not isinstance(val, Unset):
                update_data[attr] = val

        if not isinstance(body.deal_type, Unset):
            update_data["deal_type"] = body.deal_type.value if body.deal_type is not None else None
        if not isinstance(body.priority, Unset):
            update_data["priority"] = body.priority.value if body.priority is not None else None

        if not isinstance(body.notes, Unset):
            notes_list = body.notes
            update_data["notes"] = (
                [n.model_dump() for n in notes_list] if notes_list is not None else []
            )

    async def _merge_custom_fields_into_lead_update(
        self,
        body: UpdateLeadRequest,
        current: dict[str, Any],
        update_data: dict[str, Any],
    ) -> None:
        """Merge ``custom_fields`` with stored JSONB using current definitions."""
        if isinstance(body.custom_fields, Unset):
            return
        raw = current.get("custom_fields")
        existing = parse_json_field(raw) if raw is not None else []
        if not isinstance(existing, list):
            existing = []
        custom_field_service = CustomFieldService(
            db_connection=self.db_connection,
            user_context=self.user_context,
        )
        merged = await custom_field_service.merge_for_update(
            body.custom_fields,
            existing,
            EntityType.LEAD,
        )
        if json.dumps(merged, sort_keys=True, default=str) != json.dumps(
            existing,
            sort_keys=True,
            default=str,
        ):
            update_data["custom_fields"] = merged

    async def update_lead(
        self,
        lead_id: str,
        body: UpdateLeadRequest,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Partially update a lead and return (previous_state, updated_state)."""
        organization_id = self.user_context.organization_id
        current = await self.lead_repository.get_lead_detail_by_id(organization_id, lead_id)
        if not current:
            raise NotFoundException(
                message_key="leads.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        update_data: dict[str, Any] = {}
        self._apply_lead_scalar_updates(body, update_data)
        await self._merge_custom_fields_into_lead_update(body, current, update_data)

        contacts_changed = not isinstance(body.contacts, Unset)
        contacts_payload: list[dict[str, Any]] | None = None
        contact_ids_for_batch: list[str] = []

        if contacts_changed:
            # Replace semantics: UNSET => no change; None/[] => clear all; list => full replace.
            if body.contacts is None or (isinstance(body.contacts, list) and not body.contacts):
                contacts_payload = []
            else:
                contact_ids_for_batch, contacts_payload = self._normalize_patch_contacts(
                    body.contacts
                )

        company_id_for_batch: str | None = None
        if not isinstance(body.client_company_id, Unset) and body.client_company_id is not None:
            company_id_for_batch = body.client_company_id

        stage_id_to_validate: str | None = None
        if not isinstance(body.stage_id, Unset) and body.stage_id is not None:
            stage_id_to_validate = body.stage_id

        unique_client_ids = LeadService._unique_client_ids_for_refs(
            company_id_for_batch,
            contact_ids_for_batch,
        )
        needs_ref_fetch = bool(unique_client_ids) or stage_id_to_validate is not None
        if needs_ref_fetch:
            await self._fetch_and_validate_lead_references(
                organization_id,
                unique_client_ids=unique_client_ids,
                stage_id_to_validate=stage_id_to_validate,
                company_id=company_id_for_batch,
                contact_client_ids=contact_ids_for_batch,
            )

        # If nothing is changing (no scalar/custom_fields and no contacts), short-circuit.
        if not update_data and not contacts_changed:
            return current, current

        if not isinstance(body.owner_id, Unset) and body.owner_id is not None:
            await self._ensure_user_exists(body.owner_id)

        sync_contacts = contacts_payload if contacts_changed else None
        updated = await self.lead_repository.update_lead_with_contacts(
            organization_id,
            lead_id,
            update_data,
            sync_contacts,
        )
        if not updated:
            raise NotFoundException(
                message_key="leads.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        return current, updated

    @staticmethod
    def _uuid_str(value: Any) -> str | None:
        """Normalize UUID-like values to strings for JSON responses."""
        if value is None:
            return None
        return str(value)

    def _build_list_item(self, row: dict[str, Any]) -> dict[str, Any]:
        """Map a joined list/kanban row to ``LeadListItem`` JSON."""
        item = LeadListItem(
            id=str(row["id"]),
            client_company_id=self._uuid_str(row.get("client_company_id")),
            company_name=row.get("company_name") or "",
            name=row.get("name"),
            stage_id=self._uuid_str(row.get("stage_id")),
            stage_name=row.get("stage_name"),
            deal_type=row.get("deal_type"),
            priority=row.get("priority"),
            lead_score=row.get("lead_score"),
            close_date=row.get("close_date"),
            amount=row.get("amount"),
            owner_id=self._uuid_str(row.get("owner_id")),
            owner_name=row.get("owner_name") or None,
            created_at=format_iso_datetime(row.get("created_at")) or "",
            updated_at=format_iso_datetime(row.get("updated_at")) or "",
        )
        return item.model_dump(mode="json")

    @staticmethod
    def _normalize_notes_for_detail(raw_notes: Any) -> list[LeadNoteItem]:
        """Normalize notes field to ``LeadNoteItem`` list."""
        try:
            parsed = parse_json_field(raw_notes) or []
        except json.JSONDecodeError:
            parsed = []
        items = parsed if isinstance(parsed, list) else []
        out: list[LeadNoteItem] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or "").strip()
            content = (item.get("content") or "").strip()
            if not title or not content:
                continue
            out.append(LeadNoteItem(title=title, content=content))
        return out

    def _build_lead_detail(
        self,
        row: dict[str, Any],
        contacts: list[dict[str, Any]],
        *,
        custom_fields: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Map a detail row and contact rows to ``LeadDetail`` JSON."""
        custom_fields_payload = custom_fields or []

        contact_models = [
            LeadContactDetail(
                contact_client_id=str(c["contact_client_id"]),
                label=c.get("label"),
                contact_name=c.get("contact_name"),
            )
            for c in contacts
        ]

        detail = LeadDetail(
            id=str(row["id"]),
            client_company_id=self._uuid_str(row.get("client_company_id")),
            company_name=row.get("company_name") or "",
            name=row.get("name"),
            stage_id=self._uuid_str(row.get("stage_id")),
            stage_name=row.get("stage_name"),
            deal_type=row.get("deal_type"),
            priority=row.get("priority"),
            lead_source=row.get("lead_source"),
            referral_source=row.get("referral_source"),
            lead_score=row.get("lead_score"),
            close_date=row.get("close_date"),
            notes=self._normalize_notes_for_detail(row.get("notes")),
            amount=row.get("amount"),
            description=row.get("description"),
            owner_id=self._uuid_str(row.get("owner_id")),
            owner_name=row.get("owner_name") or None,
            contacts=contact_models,
            custom_fields=custom_fields_payload,
            created_at=format_iso_datetime(row.get("created_at")) or "",
            updated_at=format_iso_datetime(row.get("updated_at")) or "",
        )
        return detail.model_dump(mode="json")

    async def list_leads(
        self,
        query: LeadsListQueryParams,
    ) -> tuple[list[dict[str, Any]], int, int] | list[dict[str, Any]]:
        """List leads: list mode returns ``(items, total, page)``; kanban returns column groups."""
        organization_id = self.user_context.organization_id
        stage_id = query.stage_id
        search = query.search

        if query.mode == LeadsListMode.LIST:
            offset = (query.page - 1) * query.limit
            rows, total = await self.lead_repository.list_leads_page_with_total(
                organization_id,
                stage_id=stage_id,
                search=search,
                limit=query.limit,
                offset=offset,
            )
            return [self._build_list_item(r) for r in rows], total, query.page

        # KANBAN — unchanged
        stage_rows = await self.lead_stage_repository.list_stages_by_organization(organization_id)
        lead_rows = await self.lead_repository.list_leads_for_kanban(
            organization_id,
            stage_id=stage_id,
            search=search,
        )

        by_stage: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
        for row in lead_rows:
            key = str(row["stage_id"]) if row.get("stage_id") is not None else None
            by_stage[key].append(self._build_list_item(row))

        groups: list[dict[str, Any]] = []
        max_order = 0
        for stage in stage_rows:
            sid = str(stage["id"])
            order = int(stage["sort_order"])
            max_order = max(max_order, order)
            items = by_stage.pop(sid, [])
            lead_items = [LeadListItem.model_validate(x) for x in items]
            groups.append(
                LeadKanbanStageGroup(
                    stage_id=sid,
                    stage_name=stage["stage_name"],
                    sort_order=order,
                    total=len(lead_items),
                    leads=lead_items,
                ).model_dump(mode="json")
            )

        unassigned = by_stage.pop(None, None)
        if unassigned:
            lead_items = [LeadListItem.model_validate(x) for x in unassigned]
            groups.append(
                LeadKanbanStageGroup(
                    stage_id=None,
                    stage_name="Unassigned",
                    sort_order=max_order + 1,
                    total=len(lead_items),
                    leads=lead_items,
                ).model_dump(mode="json")
            )

        return groups

    async def get_lead(self, lead_id: str) -> dict[str, Any]:
        """Return one lead by id for the current organization."""
        organization_id = self.user_context.organization_id
        row = await self.lead_repository.get_lead_detail_with_contacts_by_id(
            organization_id, lead_id
        )
        if not row:
            raise NotFoundException(
                message_key="leads.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        contacts_raw = row.get("contacts")
        if isinstance(contacts_raw, str):
            contacts = json.loads(contacts_raw) if contacts_raw else []
        elif isinstance(contacts_raw, list):
            contacts = contacts_raw
        else:
            contacts = []

        resolved_custom_fields = await self._resolve_lead_custom_fields_for_response(row)
        return self._build_lead_detail(
            row,
            contacts,
            custom_fields=resolved_custom_fields,
        )

    async def _resolve_lead_custom_fields_for_response(
        self,
        row: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Resolve stored FieldCell array to read shape."""
        custom = row.get("custom_fields")
        if isinstance(custom, str):
            raw = json.loads(custom) if custom else []
        elif isinstance(custom, list):
            raw = custom
        else:
            raw = []
        if not raw or not (self.user_context and self.user_context.organization_id):
            return raw if isinstance(raw, list) else []
        cfs = CustomFieldService(
            db_connection=self.db_connection,
            user_context=self.user_context,
        )
        definitions, _ = await cfs.get_custom_fields_list(EntityType.LEAD)
        id_to_def = {str(d.id): d for d in definitions}
        return cfs.resolve_fields_for_read(raw, id_to_def)

    async def delete_lead(self, lead_id: str) -> dict[str, Any]:
        """Hard-delete a lead for the current organization (client rows are not deleted)."""
        organization_id = self.user_context.organization_id
        deleted = await self.lead_repository.delete_lead(organization_id, lead_id)
        if not deleted:
            raise NotFoundException(
                message_key="leads.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return deleted
