"""Business logic for leads (public.leads)."""

import json
from collections import defaultdict
from typing import Any

import asyncpg
from asyncpg import UniqueViolationError

from apps.user_service.app.db.repositories.client_repository import ClientRepository
from apps.user_service.app.db.repositories.lead_repository import LeadRepository
from apps.user_service.app.db.repositories.lead_stage_repository import (
    LeadStageRepository,
)
from apps.user_service.app.db.repositories.user_repository import UserRepository
from apps.user_service.app.schemas.enums import EntityType, LeadsListMode
from apps.user_service.app.schemas.lead_stages import Unset
from apps.user_service.app.schemas.leads import (
    CreateLeadRequest,
    LeadDetail,
    LeadKanbanStageGroup,
    LeadListItem,
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
)
from libs.shared_utils.status_codes import CustomStatusCode


class LeadService:
    """Create and orchestrate lead operations."""

    def __init__(
        self,
        db_connection: asyncpg.Connection,
        user_context: UserContext | None = None,
        client_repository: ClientRepository | None = None,
        lead_repository: LeadRepository | None = None,
        lead_stage_repository: LeadStageRepository | None = None,
        user_repository: UserRepository | None = None,
    ) -> None:
        self.user_context = user_context
        self.db_connection = db_connection
        self.client_repository = client_repository or ClientRepository(db_connection=db_connection)
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

    async def create_lead(self, body: CreateLeadRequest, external: bool = False) -> dict[str, Any]:
        """Create a lead for an existing client; enforce org scoping and custom field rules."""
        organization_id = self.user_context.organization_id
        user_id = self.user_context.user_id
        client_id = body.client_id

        client_exists = await self.lead_repository.get_client_existence(
            organization_id,
            client_id,
        )
        if not client_exists:
            raise NotFoundException(
                message_key="clients.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        stage = await self.lead_stage_repository.get_stage_by_id(
            organization_id,
            body.stage_id,
        )
        if not stage:
            raise NotFoundException(
                message_key="lead_stages.errors.stage_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        resolved_stage_id = str(stage["id"])

        owner_id = None if external else (body.owner_id if body.owner_id is not None else user_id)
        if (not external) and body.owner_id is not None:
            user_row = await self.user_repository.get_user_details_by_id(owner_id, ["id"])
            if not user_row:
                raise NotFoundException(
                    message_key="users.errors.user_not_found",
                    custom_code=CustomStatusCode.NOT_FOUND,
                    params={"user_id": owner_id},
                )

        point_of_contact: str | None = body.point_of_contact
        if point_of_contact is not None:
            poc_ok = await self.client_repository.client_exists_in_organization(
                organization_id,
                point_of_contact,
            )
            if not poc_ok:
                raise NotFoundException(
                    message_key="clients.errors.not_found",
                    custom_code=CustomStatusCode.NOT_FOUND,
                )

        lead_row: dict[str, Any] = {
            "client_id": client_id,
            "organization_id": organization_id,
            "name": body.name,
            "stage_id": resolved_stage_id,
            "lead_status": body.lead_status.value if body.lead_status is not None else None,
            "intake_stage": body.intake_stage,
            "lead_source": body.lead_source,
            "referral_source": body.referral_source,
            "lead_score": body.lead_score,
            "close_date": body.close_date,
            "converted_at": body.converted_at,
            "notes": body.notes,
            "amount": body.amount,
            "created_by": user_id,
            "description": body.description,
            "owner_id": owner_id,
            "point_of_contact": point_of_contact,
            "custom_fields": [],
        }

        await self._apply_custom_fields_if_needed(lead_row, body)

        try:
            return await self.lead_repository.create_lead(lead_row)
        except UniqueViolationError as exc:
            raise DuplicateValueException(
                message_key="leads.errors.lead_already_exists",
                custom_code=CustomStatusCode.DUPLICATE_ENTRY,
            ) from exc

    @staticmethod
    def _apply_lead_scalar_updates(body: UpdateLeadRequest, update_data: dict[str, Any]) -> None:
        """Map PATCH body to DB columns (``Unset`` omitted)."""
        for attr in (
            "name",
            "stage_id",
            "intake_stage",
            "lead_source",
            "referral_source",
            "lead_score",
            "close_date",
            "converted_at",
            "amount",
            "description",
            "owner_id",
            "point_of_contact",
            "notes",
        ):
            val = getattr(body, attr)
            if not isinstance(val, Unset):
                update_data[attr] = val
        status = body.lead_status
        if not isinstance(status, Unset):
            update_data["lead_status"] = status.value if status is not None else None

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

        new_stage_row: dict[str, Any] | None = None
        if not isinstance(body.stage_id, Unset) and body.stage_id is not None:
            new_stage_row = await self.lead_stage_repository.get_stage_by_id(
                organization_id,
                body.stage_id,
            )
            if not new_stage_row:
                raise NotFoundException(
                    message_key="lead_stages.errors.stage_not_found",
                    custom_code=CustomStatusCode.NOT_FOUND,
                )

        if not isinstance(body.owner_id, Unset) and body.owner_id is not None:
            user_row = await self.user_repository.get_user_details_by_id(body.owner_id, ["id"])
            if not user_row:
                raise NotFoundException(
                    message_key="users.errors.user_not_found",
                    custom_code=CustomStatusCode.NOT_FOUND,
                    params={"user_id": body.owner_id},
                )

        if not isinstance(body.point_of_contact, Unset) and body.point_of_contact is not None:
            poc_ok = await self.client_repository.client_exists_in_organization(
                organization_id,
                body.point_of_contact,
            )
            if not poc_ok:
                raise NotFoundException(
                    message_key="clients.errors.not_found",
                    custom_code=CustomStatusCode.NOT_FOUND,
                )

        if not update_data:
            return current, current

        updated = await self.lead_repository.update_lead(organization_id, lead_id, update_data)
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
            client_id=str(row["client_id"]),
            client_name=row["client_name"] or "",
            name=row.get("name"),
            stage_id=self._uuid_str(row.get("stage_id")),
            stage_name=row.get("stage_name"),
            lead_score=row.get("lead_score"),
            close_date=row.get("close_date"),
            amount=row.get("amount"),
            owner_id=self._uuid_str(row.get("owner_id")),
            owner_name=row.get("owner_name") or None,
            point_of_contact_id=self._uuid_str(row.get("point_of_contact_id")),
            point_of_contact=row.get("point_of_contact"),
            created_at=format_iso_datetime(row.get("created_at")) or "",
            updated_at=format_iso_datetime(row.get("updated_at")) or "",
        )
        return item.model_dump(mode="json")

    def _build_lead_detail(self, row: dict[str, Any]) -> dict[str, Any]:
        """Map a detail row to ``LeadDetail`` (excludes ``lead_status``)."""
        custom = row.get("custom_fields")
        if isinstance(custom, str):
            custom = json.loads(custom) if custom else {}
        elif custom is None:
            custom = {}

        detail = LeadDetail(
            id=str(row["id"]),
            client_id=str(row["client_id"]),
            client_name=row.get("client_name") or "",
            name=row.get("name"),
            stage_id=self._uuid_str(row.get("stage_id")),
            stage_name=row.get("stage_name"),
            intake_stage=row.get("intake_stage"),
            lead_source=row.get("lead_source"),
            referral_source=row.get("referral_source"),
            lead_score=row.get("lead_score"),
            close_date=row.get("close_date"),
            converted_at=format_iso_datetime(row.get("converted_at")),
            notes=row.get("notes"),
            amount=row.get("amount"),
            created_by=self._uuid_str(row.get("created_by")),
            description=row.get("description"),
            owner_id=self._uuid_str(row.get("owner_id")),
            owner_name=row.get("owner_name") or None,
            point_of_contact_id=self._uuid_str(row.get("point_of_contact_id")),
            point_of_contact=row.get("point_of_contact"),
            custom_fields=custom if isinstance(custom, list) else [],
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
            total = await self.lead_repository.count_leads_filtered(
                organization_id,
                stage_id=stage_id,
                search=search,
            )
            offset = (query.page - 1) * query.limit
            rows = await self.lead_repository.list_leads_page(
                organization_id,
                stage_id=stage_id,
                search=search,
                limit=query.limit,
                offset=offset,
            )
            items = [self._build_list_item(r) for r in rows]
            return items, total, query.page

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
        row = await self.lead_repository.get_lead_detail_by_id(organization_id, lead_id)
        if not row:
            raise NotFoundException(
                message_key="leads.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        detail = self._build_lead_detail(row)
        detail["custom_fields"] = await self._resolve_lead_custom_fields_for_response(row)
        return detail

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
        """Hard-delete a lead for the current organization (client row is not deleted)."""
        organization_id = self.user_context.organization_id
        deleted = await self.lead_repository.delete_lead(organization_id, lead_id)
        if not deleted:
            raise NotFoundException(
                message_key="leads.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return deleted
