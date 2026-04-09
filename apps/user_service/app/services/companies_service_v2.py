"""Companies v2 service.

Implements the operations defined in ADR `clients_operations.md` against:
- `companies`
- `contacts`
- `contact_companies`
- `company_addresses`

Key rule (service enforced):
- Before setting `companies.primary_contact_id = contact_id`, ensure that contact is a
  member of that company in `contact_companies`.
"""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg

from apps.user_service.app.config.app_settings import app_settings, shared_settings
from apps.user_service.app.db.repositories import (
    CompaniesRepository,
    ContactCompaniesRepository,
    ContactsRepository,
)
from apps.user_service.app.schemas.companies_v2 import (
    CompanyPrimaryContactChange,
    CreateCompanyRequest,
    UpdateCompanyRequest,
)
from apps.user_service.app.schemas.contacts_v2 import CreateContactRequest
from apps.user_service.app.schemas.enums import ClientStatus, EntityType
from apps.user_service.app.search.client_typesense_schema import (
    EMAIL_SEARCH_PARAMS,
    PHONE_SEARCH_PARAMS,
    SEARCH_PARAMS,
)
from apps.user_service.app.services.custom_field_service import CustomFieldService
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
    parse_json_field,
)
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode
from libs.shared_utils.typesense_service import TypesenseService

logger = get_logger("companies_service_v2")


class CompaniesServiceV2:
    """Business logic for v2 companies."""

    def __init__(self, *, db_connection: asyncpg.Connection, user_context: UserContext) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.companies_repo = CompaniesRepository(db_connection)
        self.contacts_repo = ContactsRepository(db_connection)
        self.cc_repo = ContactCompaniesRepository(db_connection)
        self._typesense: TypesenseService | None = None

    @property
    def typesense(self) -> TypesenseService:
        if self._typesense is None:
            self._typesense = TypesenseService.from_settings(
                collection_name=app_settings.shared_settings.typesense.companies_collection_name
            )
        return self._typesense

    async def create_company(self, body: CreateCompanyRequest) -> dict[str, Any]:
        """Create a company (ADR section 2).

        Supports:
        - 2a company only
        - 2b company + existing contact as primary (membership is created first)
        - 2c company + create new contact as primary
        """
        org_id = self.user_context.organization_id
        if not org_id:
            raise ValidationException(
                message_key="organizations.errors.not_found",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        # Validate custom fields exactly like v1 create flow.
        if body.custom_fields:
            cfs = CustomFieldService(db_connection=self.db_connection, user_context=self.user_context)
            validated_custom_fields = await cfs.validate_for_create(body.custom_fields, EntityType.COMPANY)
        else:
            validated_custom_fields = []

        [company] = await self.companies_repo.create_companies(
            [
                {
                    "organization_id": org_id,
                    "status": ClientStatus.ACTIVE.value,
                    "name": body.name.strip(),
                    "industry": body.industry,
                    "profile_photo_url": body.profile_photo_url,
                    "portal_access": body.portal_access,
                    "tags": body.tags,
                    "websites": [w.model_dump(mode="json") for w in body.websites],
                    "billing_preferences": body.billing_preferences.model_dump(mode="json")
                    if body.billing_preferences
                    else None,
                    "social_pages": [p.model_dump(mode="json") for p in body.social_pages],
                    "target_market_segments": body.target_market_segments,
                    "current_tech_stack": body.current_tech_stack,
                    "preferred_communication_channels": body.preferred_communication_channels,
                    "industry_specific_terminologies": body.industry_specific_terminologies,
                    "description": body.description,
                    "custom_fields": validated_custom_fields,
                    "additional_data": body.additional_data,
                    "primary_contact_id": None,
                }
            ]
        )
        company_id = str(company["id"])
        created_contact_row: dict[str, Any] | None = None
        created_contact_id: str | None = None

        # Addresses (single bulk insert).
        if body.addresses:
            await self.companies_repo.create_company_addresses(
                [{"company_id": company_id, **a.model_dump(exclude_none=True)} for a in body.addresses]
            )

        # Primary contact.
        if body.primary_contact:
            contact_id = body.primary_contact.contact_id
            if body.primary_contact.create_contact:
                contact_id, created_contact_row = await self._create_contact_for_company_primary(
                    create_contact=body.primary_contact.create_contact,
                )
            if not contact_id:
                raise ValidationException(
                    message_key="clients.errors.bad_request",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            created_contact_id = contact_id if body.primary_contact.create_contact else None

            # Membership must exist before setting primary.
            await self.cc_repo.link_contact_to_company(
                organization_id=org_id,
                contact_id=contact_id,
                company_id=company_id,
            )
            await self.companies_repo.update_company(
                company_id=company_id,
                organization_id=org_id,
                update_data={"primary_contact_id": contact_id},
            )

        # Enrichment targets (no extra DB reads).
        enrichment_targets: list[dict[str, Any]] = []
        addresses_payload = [{"country": a.country} for a in (body.addresses or []) if a.country]
        enrichment_targets.append(
            {
                "entity_table": "companies",
                "client_id": company_id,
                "organization_id": org_id,
                "client_type": "company",
                "payload_data": {
                    "name": body.name.strip(),
                    "industry": body.industry,
                    "email": None,
                    "websites": [w.model_dump(mode="json") for w in body.websites],
                    "social_pages": [p.model_dump(mode="json") for p in body.social_pages],
                    "addresses": addresses_payload,
                },
            }
        )
        if created_contact_row is not None:
            primary_phone = None
            phones = created_contact_row.get("phones") or []
            if isinstance(phones, list) and phones:
                primary_phone = next((p for p in phones if isinstance(p, dict) and p.get("is_primary") is True), phones[0])
            person_payload: dict[str, Any] = {
                "first_name": created_contact_row.get("first_name") or "",
                "middle_name": created_contact_row.get("middle_name") or "",
                "last_name": created_contact_row.get("last_name") or "",
                "email": created_contact_row.get("email"),
                "company": body.name.strip(),
                "addresses": [],  # contact addresses are optional in nested create; keep empty
            }
            if isinstance(primary_phone, dict):
                person_payload["phone_isd_code"] = primary_phone.get("phone_isd_code")
                person_payload["phone_number"] = primary_phone.get("phone_number")
            enrichment_targets.append(
                {
                    "entity_table": "contacts",
                    "client_id": str(created_contact_row.get("id")),
                    "organization_id": org_id,
                    "client_type": "person",
                    "payload_data": person_payload,
                }
            )

        created_entities: list[dict[str, str]] = [
            {
                "entity_table": "companies",
                "entity_id": str(company_id),
                "action": "create",
            }
        ]
        if created_contact_id:
            created_entities.append(
                {
                    "entity_table": "contacts",
                    "entity_id": str(created_contact_id),
                    "action": "create_contact",
                }
            )

        return {
            "company_id": company_id,
            "old_data": None,
            "new_data": company,
            "enrichment_targets": enrichment_targets,
            "created_entities": created_entities,
        }

    async def _create_contact_for_company_primary(
        self, *, create_contact: CreateContactRequest
    ) -> tuple[str, dict[str, Any]]:
        """Create a contact as part of company flows.

        Company association fields inside the nested CreateContactRequest are ignored;
        membership is handled at the company operation layer.
        """
        org_id = self.user_context.organization_id
        if not org_id:
            raise ValidationException(
                message_key="organizations.errors.not_found",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        if create_contact.custom_fields:
            cfs = CustomFieldService(db_connection=self.db_connection, user_context=self.user_context)
            validated_custom_fields = await cfs.validate_for_create(
                create_contact.custom_fields,
                EntityType.CONTACT,
            )
        else:
            validated_custom_fields = []

        rows = await self.contacts_repo.create_contacts(
            [
                {
                    "organization_id": org_id,
                    "status": ClientStatus.ACTIVE.value,
                    "prefix": create_contact.prefix,
                    "first_name": create_contact.first_name,
                    "middle_name": create_contact.middle_name,
                    "last_name": create_contact.last_name,
                    "title": create_contact.title,
                    "date_of_birth": create_contact.date_of_birth,
                    "profile_photo_url": create_contact.profile_photo_url,
                    "email": create_contact.email,
                    "phones": [p.model_dump(mode="json") for p in create_contact.phones],
                    "tags": create_contact.tags,
                    "custom_fields": validated_custom_fields,
                    "additional_data": create_contact.additional_data,
                    "social_pages": [p.model_dump(mode="json") for p in create_contact.social_pages],
                }
            ]
        )
        if not rows:
            raise ValidationException(
                message_key="clients.errors.creation_failed",
                custom_code=CustomStatusCode.SERVICE_UNAVAILABLE,
            )
        contact_row = rows[0]
        contact_id = str(contact_row["id"])
        if create_contact.addresses:
            await self.contacts_repo.create_contact_addresses(
                [{"contact_id": contact_id, **a.model_dump(exclude_none=True)} for a in create_contact.addresses]
            )
        return contact_id, dict(contact_row)

    async def get_company_details(self, *, company_id: str) -> dict[str, Any]:
        """Return company details with primary contact, member contacts, and addresses."""
        org_id = self.user_context.organization_id
        details = await self.companies_repo.get_company_details(
            company_id=company_id,
            organization_id=org_id,
        )
        if not details:
            raise NotFoundException(message_key="clients.errors.not_found", custom_code=CustomStatusCode.NOT_FOUND)

        details["created_at"] = format_iso_datetime(details.get("created_at")) or ""
        details["updated_at"] = format_iso_datetime(details.get("updated_at")) or ""
        details["last_enriched_at"] = format_iso_datetime(details.get("last_enriched_at"))
        return details

    async def list_companies(
        self,
        *,
        search: str | None,
        status: str | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        """List companies from PostgreSQL with pagination."""
        org_id = self.user_context.organization_id
        rows, total = await self.companies_repo.list_companies(
            organization_id=org_id,
            search=search,
            status=status,
            page=page,
            page_size=page_size,
        )
        for r in rows:
            r["created_at"] = format_iso_datetime(r.get("created_at")) or ""
            r["updated_at"] = format_iso_datetime(r.get("updated_at")) or ""
        return {"items": rows, "total": total}

    async def soft_delete_company(self, *, company_id: str) -> None:
        """Soft-delete a company (sets status='deleted')."""
        org_id = self.user_context.organization_id
        await self.companies_repo.soft_delete_company(company_id=company_id, organization_id=org_id)

    async def update_company(self, *, company_id: str, body: UpdateCompanyRequest) -> None:
        """Patch a company (scalar fields + JSONB lists + addresses table).

        Notes:
        - `websites` and `social_pages` use delta semantics (add/update/remove) matching v1.
        - `billing_preferences` is merged (PATCH semantics).
        - `custom_fields` uses the same merge/validation logic as v1.
        - `addresses` are stored in `company_addresses` table, updated via delta ops.
        """
        org_id = self.user_context.organization_id
        current = await self.companies_repo.get_company_for_update(company_id=company_id, organization_id=org_id)
        if not current:
            raise NotFoundException(message_key="clients.errors.not_found", custom_code=CustomStatusCode.NOT_FOUND)

        update_data: dict[str, Any] = {}
        scalar_fields = (
            ("status", "status"),
            ("name", "name"),
            ("industry", "industry"),
            ("profile_photo_url", "profile_photo_url"),
            ("portal_access", "portal_access"),
            ("tags", "tags"),
            ("target_market_segments", "target_market_segments"),
            ("current_tech_stack", "current_tech_stack"),
            ("preferred_communication_channels", "preferred_communication_channels"),
            ("industry_specific_terminologies", "industry_specific_terminologies"),
            ("description", "description"),
        )
        for body_attr, col in scalar_fields:
            value = getattr(body, body_attr, None)
            if value is not None:
                update_data[col] = value

        if body.additional_data is not None:
            update_data["additional_data"] = body.additional_data

        if body.billing_preferences is not None:
            existing = parse_json_field(current.get("billing_preferences")) or {}
            update_data["billing_preferences"] = {
                **(existing if isinstance(existing, dict) else {}),
                **body.billing_preferences.model_dump(exclude_none=True),
            }

        if body.websites is not None:
            await self._apply_jsonb_list_delta(
                update_obj=body.websites,
                current=current,
                payload=update_data,
                field_name="websites",
                not_found_message_key="clients.errors.website_not_found",
            )

        if body.social_pages is not None:
            await self._apply_jsonb_list_delta(
                update_obj=body.social_pages,
                current=current,
                payload=update_data,
                field_name="social_pages",
                not_found_message_key="clients.errors.social_page_not_found",
            )

        if body.custom_fields is not None:
            cfs = CustomFieldService(db_connection=self.db_connection, user_context=self.user_context)
            # merge_for_update expects existing roots list
            existing_cf = parse_json_field(current.get("custom_fields"))
            merged = existing_cf if isinstance(existing_cf, list) else []
            merged = await cfs.merge_for_update(body.custom_fields, merged, EntityType.COMPANY)
            update_data["custom_fields"] = merged

        if update_data:
            await self.companies_repo.update_company(
                company_id=company_id,
                organization_id=org_id,
                update_data=update_data,
            )

        # Addresses delta support can be added similarly to v1 once address delta semantics are confirmed.
        if body.addresses is not None:
            await self._apply_company_addresses_delta(
                company_id=company_id,
                addresses=body.addresses,
            )

        # optional: primary contact change (ADR section 4)
        if body.primary_contact is not None:
            await self.change_primary_contact(company_id=company_id, body=body.primary_contact)

    async def _apply_company_addresses_delta(self, *, company_id: str, addresses: Any) -> None:
        """Apply AddressesUpdate to `company_addresses` table."""
        if addresses is None:
            return
        # remove
        if addresses.remove:
            await self.companies_repo.delete_company_addresses(company_id=company_id, address_ids=addresses.remove)
        # update
        if addresses.update:
            for item in addresses.update:
                await self.companies_repo.update_company_address(
                    company_id=company_id,
                    address_id=item.id,
                    update_data=item.model_dump(exclude={"id"}, exclude_none=True),
                )
        # add
        if addresses.add:
            await self.companies_repo.create_company_addresses(
                [{"company_id": company_id, **a.model_dump(exclude_none=True)} for a in addresses.add]
            )

    @staticmethod
    def _ensure_list_item_ids(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return a copy of list items with an id set on each (generated if missing)."""
        result: list[dict[str, Any]] = []
        for item in items:
            row = dict(item)
            if not row.get("id"):
                row["id"] = str(uuid.uuid4())
            result.append(row)
        return result

    async def _apply_jsonb_list_delta(
        self,
        *,
        update_obj: Any,
        current: dict[str, Any],
        payload: dict[str, Any],
        field_name: str,
        not_found_message_key: str,
    ) -> None:
        """Apply delta updates for JSONB list fields (add/update/remove) similar to v1."""
        existing = parse_json_field(current.get(field_name))
        current_items: list[dict[str, Any]] = (
            [i for i in existing if isinstance(i, dict)] if isinstance(existing, list) else []
        )

        # Remove
        remove_ids = set(update_obj.remove or [])
        if remove_ids:
            current_items = [i for i in current_items if str(i.get("id") or "") not in remove_ids]

        # Update
        if update_obj.update:
            index = {str(i.get("id")): i for i in current_items if i.get("id")}
            for update_item in update_obj.update:
                upd = update_item.model_dump(exclude_none=True)
                item_id = str(upd.get("id") or "")
                if not item_id or item_id not in index:
                    raise NotFoundException(
                        message_key=not_found_message_key,
                        custom_code=CustomStatusCode.NOT_FOUND,
                    )
                merged = {**index[item_id], **{k: v for k, v in upd.items() if k != "id"}}
                index[item_id] = merged
            # rebuild preserving order
            current_items = [index.get(str(i.get("id"))) or i for i in current_items]

        # Add
        if update_obj.add:
            add_items = [a.model_dump(exclude_none=True) for a in update_obj.add]
            current_items.extend(self._ensure_list_item_ids(add_items))

        payload[field_name] = current_items

    async def change_primary_contact(
        self,
        *,
        company_id: str,
        body: CompanyPrimaryContactChange,
    ) -> dict[str, Any]:
        """Apply ADR section 4 primary-contact operations for a company."""
        org_id = self.user_context.organization_id
        current = await self.companies_repo.get_company_for_update(company_id=company_id, organization_id=org_id)
        if not current:
            raise NotFoundException(message_key="clients.errors.not_found", custom_code=CustomStatusCode.NOT_FOUND)

        if body.unset:
            await self.companies_repo.update_company(
                company_id=company_id,
                organization_id=org_id,
                update_data={"primary_contact_id": None},
            )
            return {"ok": True}

        contact_id = body.contact_id
        if body.create_contact:
            contact_id = await self._create_contact_for_company_primary(create_contact=body.create_contact)
            await self.cc_repo.link_contact_to_company(
                organization_id=org_id,
                contact_id=contact_id,
                company_id=company_id,
            )

        if not contact_id:
            raise ValidationException(
                message_key="clients.errors.bad_request",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        # Enforce membership rule (service-layer).
        is_member = await self.cc_repo.is_contact_member_of_company(
            organization_id=org_id,
            contact_id=contact_id,
            company_id=company_id,
        )
        if not is_member:
            raise ValidationException(
                message_key="clients.errors.bad_request",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        await self.companies_repo.update_company(
            company_id=company_id,
            organization_id=org_id,
            update_data={"primary_contact_id": contact_id},
        )
        return {"ok": True, "primary_contact_id": contact_id}

    async def search_companies(
        self,
        *,
        query: str,
        page: int,
        page_size: int,
        status: str | None,
    ) -> dict[str, Any]:
        """Search companies via Typesense (companies collection)."""
        org_id = self.user_context.organization_id
        filters = [f"organization_id:={org_id}", "client_type:=company"]
        if status:
            filters.append(f"status:={status}")
        filter_by = " && ".join(filters)

        q = query.strip()
        params: dict[str, Any] = {
            "q": q,
            "per_page": page_size,
            "page": page,
            "filter_by": filter_by,
            "exclude_fields": "embedding",
        }
        if "@" in q:
            params.update(EMAIL_SEARCH_PARAMS)
        elif sum(c.isdigit() for c in q) >= 5:
            params.update(PHONE_SEARCH_PARAMS)
        else:
            params.update(SEARCH_PARAMS)

        embedding = await self.typesense.embed_query_text(q)
        if embedding is not None:
            vector = ",".join(map(str, embedding))
            distance_threshold = getattr(shared_settings.typesense, "vector_distance_threshold", None)
            if distance_threshold is not None and float(distance_threshold) > 0:
                params["vector_query"] = (
                    f"embedding:([{vector}], alpha:0.7, distance_threshold:{distance_threshold})"
                )
            else:
                params["vector_query"] = f"embedding:([{vector}], alpha:0.7)"

        raw = await self.typesense.search(params)
        return {"hits": raw.get("hits") or [], "total": raw.get("found", 0)}

