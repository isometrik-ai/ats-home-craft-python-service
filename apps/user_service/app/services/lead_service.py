"""Business logic for leads (public.leads, public.lead_contacts, public.lead_companies)."""

import json
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

import asyncpg
from asyncpg import UniqueViolationError

from apps.user_service.app.db.repositories.contact_companies_repository import (
    ContactCompaniesRepository,
)
from apps.user_service.app.db.repositories.lead_repository import LeadRepository
from apps.user_service.app.db.repositories.lead_stage_repository import (
    LeadStageRepository,
)
from apps.user_service.app.db.repositories.user_repository import UserRepository
from apps.user_service.app.schemas.enums import EntityType, LeadsListMode
from apps.user_service.app.schemas.lead_stages import Unset
from apps.user_service.app.schemas.leads import (
    CreateLeadRequest,
    LeadCompaniesUpdate,
    LeadCompanyListItem,
    LeadContactDetail,
    LeadContactsUpdate,
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
    coerce_json_list,
    format_iso_datetime,
    parse_json_field,
)
from libs.shared_utils.custom_field_filtering import normalize_dropdown_filters_payload
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
    def _parse_create_contacts(
        body: CreateLeadRequest,
    ) -> tuple[list[str], list[tuple[str, str | None]]]:
        """Reject duplicate contact ids; return (ordered ids, ``lead_contacts`` insert rows)."""
        if not body.contacts:
            return [], []

        seen: set[str] = set()
        ordered: list[str] = []
        for entry in body.contacts:
            cid = entry.contact_id
            if cid in seen:
                raise ValidationException(
                    message_key="leads.errors.contacts_duplicate",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            seen.add(cid)
            ordered.append(cid)

        rows = [(c.contact_id, c.label) for c in body.contacts]
        return ordered, rows

    @staticmethod
    def _build_create_lead_company_rows(
        body: CreateLeadRequest,
        contact_company_ids: list[str],
    ) -> list[tuple[str, str | None]]:
        """Explicit ``body.company`` first"""
        rows: list[tuple[str, str | None]] = []
        seen: set[str] = set()
        if body.company is not None and body.company.company_id is not None:
            cid = body.company.company_id
            if cid not in seen:
                seen.add(cid)
                rows.append((cid, body.company.label))
        for cid in contact_company_ids:
            if cid not in seen:
                seen.add(cid)
                rows.append((cid, None))
        return rows

    @staticmethod
    def _raise_if_stage_missing(stage_id: str | None, stage_ok: bool | None) -> None:
        """`stage_id`` is omitted; raise when a requested stage does not exist."""
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
        stage_id_to_validate: str | None,
        company_ids: list[str],
        contact_ids: list[str],
    ) -> None:
        """Validate pipeline stage, contacts, and companies in one round trip."""
        if stage_id_to_validate is None and not company_ids and not contact_ids:
            return

        (
            stage_ok,
            found_contacts,
            found_companies,
        ) = await self.lead_repository.fetch_lead_reference_validation(
            organization_id,
            stage_id=stage_id_to_validate,
            contact_ids=contact_ids or None,
            company_ids=company_ids or None,
        )
        LeadService._raise_if_stage_missing(stage_id_to_validate, stage_ok)

        missing_contacts = set(contact_ids) - found_contacts
        if missing_contacts:
            raise NotFoundException(
                message_key="contacts.errors.contact_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        missing_companies = set(company_ids) - found_companies
        if missing_companies:
            raise NotFoundException(
                message_key="companies.errors.company_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

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
            "lead_source": body.lead_source,
            "referral_source": body.referral_source,
            "lead_score": body.lead_score,
            "deal_type": body.deal_type.value if body.deal_type is not None else None,
            "priority": body.priority.value if body.priority is not None else None,
            "close_date": body.close_date,
            "amount": body.amount,
            "currency": body.currency.value if body.currency is not None else None,
            "description": body.description,
            "notes": notes_payload,
            "custom_fields": [],
            "owner_id": owner_id,
        }

    async def create_lead(self, body: CreateLeadRequest, external: bool = False) -> dict[str, Any]:
        """Create a lead; optional companies (``lead_companies``) and ``lead_contacts``."""
        organization_id = self.user_context.organization_id
        user_id = self.user_context.user_id

        contact_ids_ordered, contact_rows = LeadService._parse_create_contacts(body)

        contact_company_ids: list[str] = []
        if contact_ids_ordered:
            cc_repo = ContactCompaniesRepository(self.db_connection)
            contact_company_ids = await cc_repo.list_distinct_company_ids_for_contacts(
                organization_id=organization_id,
                contact_ids=contact_ids_ordered,
            )
        company_rows = LeadService._build_create_lead_company_rows(body, contact_company_ids)
        company_ids = [c for c, _ in company_rows]

        await self._fetch_and_validate_lead_references(
            organization_id,
            stage_id_to_validate=body.stage_id,
            company_ids=company_ids,
            contact_ids=contact_ids_ordered,
        )
        owner_id = await self._resolve_create_owner_id(body, external, user_id)
        lead_row = self._lead_row_dict_for_create(organization_id, body, owner_id)

        await self._apply_custom_fields_if_needed(lead_row, body)

        try:
            return await self.lead_repository.create_lead(
                lead_row,
                contacts=contact_rows,
                companies=company_rows if company_rows else None,
            )
        except UniqueViolationError as exc:
            cname = getattr(exc, "constraint_name", "") or ""
            if "uq_lead_company" in cname or "lead_companies" in cname:
                raise DuplicateValueException(
                    message_key="leads.errors.duplicate_company",
                    custom_code=CustomStatusCode.DUPLICATE_ENTRY,
                ) from exc
            raise DuplicateValueException(
                message_key="leads.errors.duplicate_contact",
                custom_code=CustomStatusCode.DUPLICATE_ENTRY,
            ) from exc

    @staticmethod
    def _apply_lead_scalar_updates(
        body: UpdateLeadRequest,
        update_data: dict[str, Any],
    ) -> None:
        """Map PATCH body to DB columns (``Unset`` omitted)."""
        for attr in (
            "name",
            "stage_id",
            "lead_source",
            "referral_source",
            "lead_score",
            "close_date",
            "amount",
            "currency",
            "description",
            "owner_id",
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
        """Merge body.custom_fields with current, validate, and set on payload."""
        if isinstance(body.custom_fields, Unset):
            return

        existing = parse_json_field(current.get("custom_fields"))
        if not isinstance(existing, list):
            existing = []

        custom_field_service = CustomFieldService(
            db_connection=self.db_connection,
            user_context=self.user_context,
        )
        entity_type = EntityType.LEAD

        merged = await custom_field_service.merge_for_update(
            body.custom_fields,
            existing,
            entity_type,
        )
        if json.dumps(merged, sort_keys=True, default=str) != json.dumps(
            existing,
            sort_keys=True,
            default=str,
        ):
            update_data["custom_fields"] = merged

    def _prepare_lead_patch_contacts(
        self,
        current: dict[str, Any],
        body: UpdateLeadRequest,
    ) -> tuple[bool, list[dict[str, Any]] | None, list[str]]:
        """Resolve PATCH contacts into sync payload and ids for reference validation."""
        contacts_changed = body.contacts_update is not None
        contacts_payload: list[dict[str, Any]] | None = None
        contact_ids_for_batch: list[str] = []

        if not contacts_changed:
            return contacts_changed, contacts_payload, contact_ids_for_batch

        contacts_payload, contact_ids_for_batch = self._apply_contact_delta(
            current=current,
            delta=body.contacts_update,
        )
        return contacts_changed, contacts_payload, contact_ids_for_batch

    async def _prepare_lead_patch_companies(
        self,
        organization_id: str,
        current: dict[str, Any],
        body: UpdateLeadRequest,
    ) -> tuple[bool, list[dict[str, Any]] | None, list[str]]:
        """Resolve PATCH companies and auto-link companies for newly added contacts."""
        companies_changed = body.companies_update is not None
        companies_payload: list[dict[str, Any]] | None = None
        company_ids_for_batch: list[str] = []

        if companies_changed:
            companies_payload, company_ids_for_batch = self._apply_company_delta(
                current=current,
                delta=body.companies_update,
            )

        added_contact_ids: list[str] = []
        if body.contacts_update is not None:
            added_contact_ids = [
                item.contact_id for item in (body.contacts_update.add_associations or [])
            ]
        if added_contact_ids:
            cc_repo = ContactCompaniesRepository(self.db_connection)
            contact_company_ids = await cc_repo.list_distinct_company_ids_for_contacts(
                organization_id=organization_id,
                contact_ids=added_contact_ids,
            )
            if contact_company_ids:
                base = (
                    companies_payload
                    if companies_changed
                    else self._existing_company_links(current)
                )
                companies_payload, new_company_ids = self._append_missing_company_links(
                    base,
                    contact_company_ids,
                )
                if new_company_ids:
                    companies_changed = True
                    company_ids_for_batch = sorted(
                        set(company_ids_for_batch) | set(new_company_ids)
                    )

        return companies_changed, companies_payload, company_ids_for_batch

    @staticmethod
    def _existing_contact_links(current: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse `contacts` list from a repository row."""
        return coerce_json_list(current.get("contacts"))

    @staticmethod
    def _existing_company_links(current: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse `companies` list from a repository row."""
        return coerce_json_list(current.get("companies"))

    @staticmethod
    def _build_ordered_link_map(
        *,
        existing: list[dict[str, Any]],
        id_key: str,
    ) -> tuple[list[str], dict[str, dict[str, Any]]]:
        """Build (ordered_ids, by_id) from existing association rows."""
        ordered_ids: list[str] = []
        by_id: dict[str, dict[str, Any]] = {}
        for item in existing:
            if not isinstance(item, dict):
                continue
            raw_id = item.get(id_key)
            if raw_id is None:
                continue
            id_str = str(raw_id)
            if id_str in by_id:
                continue
            ordered_ids.append(id_str)
            by_id[id_str] = {id_key: id_str, "label": item.get("label")}
        return ordered_ids, by_id

    @staticmethod
    def _append_missing_company_links(
        company_links: list[dict[str, Any]],
        company_ids: Iterable[str],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Append companies not already linked; return (payload, newly added ids)."""
        ordered_ids, by_id = LeadService._build_ordered_link_map(
            existing=company_links,
            id_key="company_id",
        )
        new_ids: list[str] = []
        for company_id in company_ids:
            if company_id in by_id:
                continue
            by_id[company_id] = {"company_id": company_id, "label": ""}
            ordered_ids.append(company_id)
            new_ids.append(company_id)
        if not new_ids:
            return company_links, []
        return [by_id[cid] for cid in ordered_ids if cid in by_id], new_ids

    @staticmethod
    def _apply_removals(
        *, ordered_ids: list[str], by_id: dict[str, dict[str, Any]], ids: list[str]
    ) -> None:
        """Remove ids from both the map and the order list."""
        for remove_id in ids or []:
            by_id.pop(remove_id, None)
            if remove_id in ordered_ids:
                ordered_ids.remove(remove_id)

    def _apply_contact_delta(
        self,
        *,
        current: dict[str, Any],
        delta: LeadContactsUpdate,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Apply contact delta to current row and return (sync payload, ids to validate)."""
        existing = self._existing_contact_links(current)
        ordered_ids, by_id = self._build_ordered_link_map(existing=existing, id_key="contact_id")
        self._apply_removals(
            ordered_ids=ordered_ids,
            by_id=by_id,
            ids=delta.remove_associations,
        )

        ids_to_validate: set[str] = set()

        for item in delta.update_associations or []:
            cid = item.contact_id
            ids_to_validate.add(cid)
            if cid not in by_id:
                raise ValidationException(
                    message_key="leads.errors.contact_not_linked",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            by_id[cid]["label"] = item.label

        for item in delta.add_associations or []:
            cid = item.contact_id
            ids_to_validate.add(cid)
            if cid in by_id:
                raise ValidationException(
                    message_key="leads.errors.contacts_duplicate",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            by_id[cid] = {"contact_id": cid, "label": item.label}
            ordered_ids.append(cid)

        payload = [by_id[cid] for cid in ordered_ids if cid in by_id]
        return payload, sorted(ids_to_validate)

    def _apply_company_delta(
        self,
        *,
        current: dict[str, Any],
        delta: LeadCompaniesUpdate,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Apply company delta to current row and return (sync payload, ids to validate)."""
        existing = self._existing_company_links(current)
        ordered_ids, by_id = self._build_ordered_link_map(existing=existing, id_key="company_id")
        self._apply_removals(
            ordered_ids=ordered_ids,
            by_id=by_id,
            ids=delta.remove_associations,
        )

        ids_to_validate: set[str] = set()

        for item in delta.update_associations or []:
            cid = item.company_id
            ids_to_validate.add(cid)
            if cid not in by_id:
                raise ValidationException(
                    message_key="leads.errors.company_not_linked",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            by_id[cid]["label"] = item.label

        for item in delta.add_associations or []:
            cid = item.company_id
            ids_to_validate.add(cid)
            if cid in by_id:
                raise ValidationException(
                    message_key="leads.errors.companies_duplicate",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            by_id[cid] = {"company_id": cid, "label": item.label}
            ordered_ids.append(cid)

        payload = [by_id[cid] for cid in ordered_ids if cid in by_id]
        return payload, sorted(ids_to_validate)

    @staticmethod
    def _stage_id_for_lead_update_validation(body: UpdateLeadRequest) -> str | None:
        """Return stage id when the PATCH sets a non-null stage; else None."""
        if not isinstance(body.stage_id, Unset) and body.stage_id is not None:
            return body.stage_id
        return None

    @staticmethod
    def _raise_duplicate_for_lead_update_unique_violation(exc: UniqueViolationError) -> None:
        """Map a unique violation from lead update to company vs contact duplicate errors."""
        cname = getattr(exc, "constraint_name", "") or ""
        if "uq_lead_company" in cname or "lead_companies" in cname:
            raise DuplicateValueException(
                message_key="leads.errors.duplicate_company",
                custom_code=CustomStatusCode.DUPLICATE_ENTRY,
            ) from exc
        raise DuplicateValueException(
            message_key="leads.errors.duplicate_contact",
            custom_code=CustomStatusCode.DUPLICATE_ENTRY,
        ) from exc

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

        contacts_changed, contacts_payload, contact_ids_for_batch = (
            self._prepare_lead_patch_contacts(current, body)
        )
        (
            companies_changed,
            companies_payload,
            company_ids_for_batch,
        ) = await self._prepare_lead_patch_companies(organization_id, current, body)
        stage_id_to_validate = self._stage_id_for_lead_update_validation(body)

        needs_ref_fetch = bool(company_ids_for_batch or contact_ids_for_batch) or (
            stage_id_to_validate is not None
        )
        if needs_ref_fetch:
            await self._fetch_and_validate_lead_references(
                organization_id,
                stage_id_to_validate=stage_id_to_validate,
                company_ids=company_ids_for_batch,
                contact_ids=contact_ids_for_batch,
            )

        if not update_data and not contacts_changed and not companies_changed:
            return current, current

        if not isinstance(body.owner_id, Unset) and body.owner_id is not None:
            await self._ensure_user_exists(body.owner_id)

        sync_contacts = contacts_payload if contacts_changed else None
        sync_companies = companies_payload if companies_changed else None
        try:
            updated = await self.lead_repository.update_lead_with_associations(
                organization_id,
                lead_id,
                update_data,
                sync_contacts,
                companies_payload=sync_companies,
            )
        except UniqueViolationError as exc:
            self._raise_duplicate_for_lead_update_unique_violation(exc)
        if not updated:
            raise NotFoundException(
                message_key="leads.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        return current, updated

    @staticmethod
    def _normalize_lead_audit_snapshot(row: dict[str, Any]) -> dict[str, Any]:
        """Ensure audit snapshots include stable association keys.

        Audit logging diffs keys in `raw_audit_old_data` vs `raw_audit_new_data`.
        For leads we require consistent `contacts` / `companies` keys in snapshots.
        """
        normalized = dict(row)
        normalized["contacts"] = coerce_json_list(normalized.get("contacts"))
        normalized["companies"] = coerce_json_list(normalized.get("companies"))

        return normalized

    @staticmethod
    def _uuid_str(value: Any) -> str | None:
        """Normalize UUID-like values to strings for JSON responses."""
        if value is None:
            return None
        return str(value)

    @staticmethod
    def _parse_companies_from_row(raw: Any) -> list[LeadCompanyListItem]:
        """Parse `companies` list from a repository row."""
        items = coerce_json_list(raw)
        out: list[LeadCompanyListItem] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            cid = item.get("company_id")
            if cid is None:
                continue
            out.append(
                LeadCompanyListItem(
                    company_id=str(cid),
                    label=item.get("label"),
                    company_name=(item.get("company_name") or "") or "",
                    profile_photo_url=item.get("profile_photo_url"),
                )
            )
        return out

    @staticmethod
    def _parse_contacts_from_row(raw: Any) -> list[LeadContactDetail]:
        """Parse `contacts` list from a repository row."""
        items = coerce_json_list(raw)
        out: list[LeadContactDetail] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            cid = item.get("contact_id")
            if cid is None:
                continue
            out.append(
                LeadContactDetail(
                    contact_id=str(cid),
                    label=item.get("label"),
                    contact_name=item.get("contact_name"),
                    email=item.get("email"),
                    phones=item.get("phones") if isinstance(item.get("phones"), list) else [],
                    profile_photo_url=item.get("profile_photo_url"),
                )
            )
        return out

    def _build_list_item(self, row: dict[str, Any]) -> dict[str, Any]:
        """Map a joined list/kanban row to ``LeadListItem`` JSON."""
        companies = self._parse_companies_from_row(row.get("companies"))
        contacts = self._parse_contacts_from_row(row.get("contacts"))
        item = LeadListItem(
            id=str(row["id"]),
            companies=companies,
            contacts=contacts,
            name=row.get("name"),
            stage_id=self._uuid_str(row.get("stage_id")),
            stage_name=row.get("stage_name"),
            deal_type=row.get("deal_type"),
            priority=row.get("priority"),
            lead_score=row.get("lead_score"),
            close_date=row.get("close_date"),
            amount=row.get("amount"),
            currency=row.get("currency"),
            owner_id=self._uuid_str(row.get("owner_id")),
            owner_name=row.get("owner_name") or None,
            created_at=format_iso_datetime(row.get("created_at")) or "",
            updated_at=format_iso_datetime(row.get("updated_at")) or "",
        )
        return item.model_dump(mode="json")

    @staticmethod
    def _normalize_notes_for_detail(raw_notes: Any) -> list[LeadNoteItem]:
        """Normalize notes field to ``LeadNoteItem`` list."""
        items = coerce_json_list(raw_notes)
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
        companies = self._parse_companies_from_row(row.get("companies"))

        contact_models = [
            LeadContactDetail(
                contact_id=str(c["contact_id"]),
                label=c.get("label"),
                contact_name=c.get("contact_name"),
                email=c.get("email"),
                phones=c.get("phones") or [],
                profile_photo_url=c.get("profile_photo_url"),
            )
            for c in contacts
        ]

        detail = LeadDetail(
            id=str(row["id"]),
            companies=companies,
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
            currency=row.get("currency"),
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
        owner_id: str | None = None,
        dropdown_filters: Any = None,
    ) -> tuple[list[dict[str, Any]], int, int] | list[dict[str, Any]]:
        """List leads: list mode returns ``(items, total, page)``; kanban returns column groups."""
        organization_id = self.user_context.organization_id
        stage_id = query.stage_id
        search = query.search
        start_date = query.start_date
        end_date = query.end_date

        parsed_filters = normalize_dropdown_filters_payload(dropdown_filters)
        cfs = CustomFieldService(
            db_connection=self.db_connection,
            user_context=self.user_context,
        )
        await cfs.validate_dropdown_filters_for_entity(EntityType.LEAD, parsed_filters)

        if query.mode == LeadsListMode.LIST:
            offset = (query.page - 1) * query.limit
            rows, total = await self.lead_repository.list_leads_page_with_total(
                organization_id,
                stage_id=stage_id,
                search=search,
                owner_id=owner_id,
                start_date=start_date,
                end_date=end_date,
                dropdown_filters=parsed_filters or None,
                limit=query.limit,
                offset=offset,
            )
            return [self._build_list_item(r) for r in rows], total, query.page

        stage_rows = await self.lead_stage_repository.list_stages_by_organization(organization_id)
        lead_rows = await self.lead_repository.list_leads_for_kanban(
            organization_id,
            stage_id=stage_id,
            search=search,
            owner_id=owner_id,
            start_date=start_date,
            end_date=end_date,
            dropdown_filters=parsed_filters or None,
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

    async def get_lead(self, lead_id: str, owner_id: str | None = None) -> dict[str, Any]:
        """Return one lead by id for the current organization."""
        organization_id = self.user_context.organization_id
        row = await self.lead_repository.get_lead_detail_with_contacts_by_id(
            organization_id,
            lead_id,
            owner_id=owner_id,
        )
        if not row:
            raise NotFoundException(
                message_key="leads.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        contacts = coerce_json_list(row.get("contacts"))

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
        raw = coerce_json_list(row.get("custom_fields"))
        if not raw or not (self.user_context and self.user_context.organization_id):
            return raw
        cfs = CustomFieldService(
            db_connection=self.db_connection,
            user_context=self.user_context,
        )
        definitions, _ = await cfs.get_custom_fields_list(EntityType.LEAD)
        id_to_def = {str(d.id): d for d in definitions}
        return cfs.resolve_fields_for_read(raw, id_to_def)

    async def delete_lead(self, lead_id: str) -> dict[str, Any]:
        """Hard-delete a lead for the current organization."""
        organization_id = self.user_context.organization_id
        deleted = await self.lead_repository.delete_lead(organization_id, lead_id)
        if not deleted:
            raise NotFoundException(
                message_key="leads.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return deleted
