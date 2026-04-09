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
from fastapi import BackgroundTasks
from supabase import AsyncClient

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
from apps.user_service.app.search.company_typesense_schema import (
    COMPANY_EMAIL_SEARCH_PARAMS,
    COMPANY_PHONE_SEARCH_PARAMS,
    COMPANY_SEARCH_PARAMS,
)
from apps.user_service.app.services.custom_field_service import CustomFieldService
from apps.user_service.app.services.client_enrichment_service import ClientEnrichmentService
from apps.user_service.app.services.contacts_service_v2 import ContactsServiceV2
from apps.user_service.app.services.event_service import EventService
from apps.user_service.app.services.typesense_index_service_v2 import index_companies_background
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
    parse_json_field,
    serialize_jsonb_param,
)
from apps.user_service.app.db.repositories.companies_repository import COMPANY_JSONB_COLUMNS
from apps.user_service.app.utils.email_utils import send_client_creation_email
from libs.shared_utils.http_exceptions import (
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode
from libs.shared_utils.typesense_service import TypesenseService


logger = get_logger("companies_service_v2")


class CompaniesServiceV2:
    """Business logic for v2 companies."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
        supabase_client: AsyncClient | None = None,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.supabase_client = supabase_client
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

    @staticmethod
    def schedule_company_update_background_tasks(
        *,
        background_tasks: BackgroundTasks,
        company_id: str,
        organization_id: str,
        body: UpdateCompanyRequest,
        update_event: dict[str, Any] | None,
        event_key: str,
        event_topics: list[Any],
    ) -> None:
        """Schedule background tasks after a company update (parity with contacts_v2)."""
        if update_event is not None:
            background_tasks.add_task(
                EventService.publish_event_background,
                event=update_event,
                key=event_key,
                topics=event_topics,
            )

        background_tasks.add_task(
            index_companies_background,
            [(company_id, organization_id)],
        )

        enrichment_input_fields = (
            "name",
            "industry",
            "websites",
            "social_pages",
            "addresses",
            "description",
        )
        enrichment_inputs_changed = any(getattr(body, f) is not None for f in enrichment_input_fields)
        if enrichment_inputs_changed:
            enrichment_service = ClientEnrichmentService.from_settings()
            payload_data: dict[str, Any] = {}
            if body.name is not None:
                payload_data["name"] = body.name
            if body.industry is not None:
                payload_data["industry"] = body.industry
            if body.description is not None:
                payload_data["description"] = body.description

            # Keep request payloads developer-friendly; enrichment is best-effort.
            if body.websites is not None:
                payload_data["websites"] = body.websites.model_dump(exclude_none=True)
            if body.social_pages is not None:
                payload_data["social_pages"] = body.social_pages.model_dump(exclude_none=True)
            if body.addresses is not None:
                payload_data["addresses"] = body.addresses.model_dump(exclude_none=True)

            background_tasks.add_task(
                enrichment_service.run_client_enrichment,
                client_id=str(company_id),
                organization_id=str(organization_id),
                client_type="company",
                payload_data=payload_data,
                entity_table="companies",
            )

    async def create_company(self, body: CreateCompanyRequest) -> dict[str, Any]:
        """Create a company (ADR section 2).

        Supports:
        - 2a company only
        - 2b company + existing contact (primary/non-primary)
        - 2c company + create new contact + link (primary/non-primary)
        """
        org_id = self.user_context.organization_id

        validated_company_custom_fields = await self._validate_custom_fields_for_create(
            custom_fields=body.custom_fields,
            entity_type=EntityType.COMPANY,
        )

        websites_payload, social_pages_payload, contact_phones_payload, contact_social_pages_payload = (
            self._build_create_company_list_payloads(body=body)
        )
        jsonb_params = self._serialize_company_jsonb_params(
            body=body,
            websites_payload=websites_payload,
            social_pages_payload=social_pages_payload,
            validated_company_custom_fields=validated_company_custom_fields,
        )

        contact_id, contact_data, contact_addresses, set_primary, created_contact_password = (
            await self._prepare_optional_company_contact_association(
                body=body,
                contact_phones_payload=contact_phones_payload,
                contact_social_pages_payload=contact_social_pages_payload,
            )
        )

        addresses_rows = self._company_addresses_rows(body=body)

        created = await self.companies_repo.create_company_with_optional_contact_link(
            organization_id=org_id,
            company_data={
                "status": ClientStatus.ACTIVE.value,
                "name": body.name.strip(),
                "industry": body.industry,
                "profile_photo_url": body.profile_photo_url,
                "portal_access": body.portal_access,
                "tags": body.tags,
                "websites": jsonb_params["websites"],
                "billing_preferences": jsonb_params["billing_preferences"],
                "social_pages": jsonb_params["social_pages"],
                "target_market_segments": body.target_market_segments,
                "current_tech_stack": body.current_tech_stack,
                "preferred_communication_channels": body.preferred_communication_channels,
                "industry_specific_terminologies": body.industry_specific_terminologies,
                "description": body.description,
                "custom_fields": jsonb_params["custom_fields"],
                "additional_data": jsonb_params["additional_data"],
            },
            addresses=addresses_rows,
            contact_id=str(contact_id) if contact_id else None,
            contact_data=contact_data,
            contact_addresses=contact_addresses,
            set_primary=set_primary,
        )
        company_id = str(created["company_id"])
        company = created["company"]

        created_contact_row, created_contact_id = self._extract_created_contact(created=created)
        self._validate_contact_link_outcome(
            requested_contact_id=contact_id,
            created_contact_row=created_contact_row,
            created=created,
        )
        created_contact_id = self._normalize_created_contact_id_for_response(
            requested_contact_id=contact_id,
            created_contact_row=created_contact_row,
            created_contact_id=created_contact_id,
        )

        enrichment_targets = self._build_create_company_enrichment_targets(
            body=body,
            company_id=company_id,
            organization_id=org_id,
            websites_payload=websites_payload,
            social_pages_payload=social_pages_payload,
            created_contact_row=created_contact_row,
            created_contact_id=created_contact_id,
        )
        created_entities = self._build_created_entities(
            company_id=company_id,
            created_contact_id=created_contact_id,
            created_new_contact=contact_data is not None,
        )
        self._maybe_send_portal_welcome_email(
            portal_access=bool(body.portal_access),
            created_contact_row=created_contact_row,
            created_contact_password=created_contact_password,
        )

        return {
            "company_id": company_id,
            "old_data": None,
            "new_data": company,
            "enrichment_targets": enrichment_targets,
            "created_entities": created_entities,
        }

    async def _validate_custom_fields_for_create(
        self,
        *,
        custom_fields: list[dict[str, Any]] | None,
        entity_type: EntityType,
    ) -> list[dict[str, Any]]:
        if not custom_fields:
            return []
        cfs = CustomFieldService(db_connection=self.db_connection, user_context=self.user_context)
        return await cfs.validate_for_create(custom_fields, entity_type)

    def _build_create_company_list_payloads(
        self, *, body: CreateCompanyRequest
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        create_contact_for_payloads = (
            body.contact.contact if (body.contact is not None and body.contact.contact is not None) else None
        )
        list_payloads = self._build_list_payloads(
            inputs={
                "company_websites": [w.model_dump(mode="json", exclude_none=True) for w in body.websites],
                "company_social_pages": [p.model_dump(mode="json", exclude_none=True) for p in body.social_pages],
                "contact_phones": (
                    [p.model_dump(mode="json", exclude_none=True) for p in create_contact_for_payloads.phones]
                    if create_contact_for_payloads is not None
                    else []
                ),
                "contact_social_pages": (
                    [p.model_dump(mode="json", exclude_none=True) for p in create_contact_for_payloads.social_pages]
                    if create_contact_for_payloads is not None
                    else []
                ),
            }
        )
        return (
            list_payloads["company_websites"],
            list_payloads["company_social_pages"],
            list_payloads["contact_phones"],
            list_payloads["contact_social_pages"],
        )

    def _serialize_company_jsonb_params(
        self,
        *,
        body: CreateCompanyRequest,
        websites_payload: list[dict[str, Any]],
        social_pages_payload: list[dict[str, Any]],
        validated_company_custom_fields: list[dict[str, Any]],
    ) -> dict[str, Any]:
        jsonb_inputs: dict[str, Any] = {
            "websites": websites_payload,
            "billing_preferences": body.billing_preferences.model_dump(mode="json")
            if body.billing_preferences
            else None,
            "social_pages": social_pages_payload,
            "custom_fields": validated_company_custom_fields,
            "additional_data": body.additional_data,
        }
        return {
            field_name: serialize_jsonb_param(field_name, value, COMPANY_JSONB_COLUMNS)
            for field_name, value in jsonb_inputs.items()
        }

    async def _prepare_optional_company_contact_association(
        self,
        *,
        body: CreateCompanyRequest,
        contact_phones_payload: list[dict[str, Any]],
        contact_social_pages_payload: list[dict[str, Any]],
    ) -> tuple[str | None, dict[str, Any] | None, list[dict[str, Any]] | None, bool, str | None]:
        created_contact_password: str | None = None

        contact_id: str | None = None
        contact_data: dict[str, Any] | None = None
        contact_addresses: list[dict[str, Any]] | None = None
        set_primary = False

        if body.contact is None:
            return contact_id, contact_data, contact_addresses, set_primary, created_contact_password

        set_primary = bool(body.contact.is_primary)
        contact_id = body.contact.contact_id

        if body.contact.contact is None:
            return contact_id, contact_data, contact_addresses, set_primary, created_contact_password

        create_contact = body.contact.contact
        validated_contact_custom_fields = await self._validate_custom_fields_for_create(
            custom_fields=create_contact.custom_fields,
            entity_type=EntityType.CONTACT,
        )

        email_norm, user_id, isometrik_user_id, created_contact_password = await self._maybe_provision_portal_identity(
            portal_access=bool(body.portal_access),
            create_contact=create_contact,
        )

        contact_data = {
            "user_id": user_id,
            "isometrik_user_id": isometrik_user_id,
            "status": ClientStatus.ACTIVE.value,
            "prefix": create_contact.prefix,
            "first_name": create_contact.first_name,
            "middle_name": create_contact.middle_name,
            "last_name": create_contact.last_name,
            "title": create_contact.title,
            "date_of_birth": create_contact.date_of_birth,
            "profile_photo_url": create_contact.profile_photo_url,
            "email": email_norm,
            "phones": contact_phones_payload,
            "tags": create_contact.tags,
            "custom_fields": validated_contact_custom_fields,
            "additional_data": create_contact.additional_data,
            "social_pages": contact_social_pages_payload,
        }
        contact_addresses = (
            [a.model_dump(exclude_none=True) for a in create_contact.addresses] if create_contact.addresses else []
        )
        return contact_id, contact_data, contact_addresses, set_primary, created_contact_password

    def _company_addresses_rows(self, *, body: CreateCompanyRequest) -> list[dict[str, Any]]:
        return [a.model_dump(exclude_none=True) for a in body.addresses] if body.addresses else []

    def _extract_created_contact(
        self,
        *,
        created: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str | None]:
        if created.get("contact") is None:
            return None, None

        raw_contact = created.get("contact")
        created_contact_id = created.get("contact_id")

        parsed_contact: Any = raw_contact
        if isinstance(raw_contact, str):
            try:
                parsed_contact = parse_json_field(raw_contact)
            except Exception:
                parsed_contact = None

        if isinstance(parsed_contact, dict):
            return parsed_contact, str(created_contact_id) if created_contact_id else None
        return None, str(created_contact_id) if created_contact_id else None

    def _validate_contact_link_outcome(
        self,
        *,
        requested_contact_id: str | None,
        created_contact_row: dict[str, Any] | None,
        created: dict[str, Any],
    ) -> None:
        if not requested_contact_id:
            return
        if created_contact_row is None and not bool(created.get("contact_found")):
            raise NotFoundException(
                message_key="clients.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

    def _normalize_created_contact_id_for_response(
        self,
        *,
        requested_contact_id: str | None,
        created_contact_row: dict[str, Any] | None,
        created_contact_id: str | None,
    ) -> str | None:
        if not requested_contact_id:
            return created_contact_id
        return created_contact_id if created_contact_row is not None else None

    def _build_create_company_enrichment_targets(
        self,
        *,
        body: CreateCompanyRequest,
        company_id: str,
        organization_id: str,
        websites_payload: list[dict[str, Any]],
        social_pages_payload: list[dict[str, Any]],
        created_contact_row: dict[str, Any] | None,
        created_contact_id: str | None,
    ) -> list[dict[str, Any]]:
        enrichment_targets: list[dict[str, Any]] = []
        addresses_payload = [{"country": a.country} for a in (body.addresses or []) if a.country]
        enrichment_targets.append(
            {
                "entity_table": "companies",
                "client_id": company_id,
                "organization_id": organization_id,
                "client_type": "company",
                "payload_data": {
                    "name": body.name.strip(),
                    "industry": body.industry,
                    "email": None,
                    "websites": websites_payload,
                    "social_pages": social_pages_payload,
                    "addresses": addresses_payload,
                },
            }
        )

        if created_contact_row is None:
            return enrichment_targets

        primary_phone = None
        phones = created_contact_row.get("phones") or []
        if isinstance(phones, list) and phones:
            primary_phone = next(
                (p for p in phones if isinstance(p, dict) and p.get("is_primary") is True),
                phones[0],
            )
        person_payload: dict[str, Any] = {
            "first_name": created_contact_row.get("first_name") or "",
            "middle_name": created_contact_row.get("middle_name") or "",
            "last_name": created_contact_row.get("last_name") or "",
            "email": created_contact_row.get("email"),
            "company": body.name.strip(),
            "addresses": [],
        }
        if isinstance(primary_phone, dict):
            person_payload["phone_isd_code"] = primary_phone.get("phone_isd_code")
            person_payload["phone_number"] = primary_phone.get("phone_number")

        contact_entity_id = created_contact_row.get("id") or created_contact_id
        if contact_entity_id:
            enrichment_targets.append(
                {
                    "entity_table": "contacts",
                    "client_id": str(contact_entity_id),
                    "organization_id": organization_id,
                    "client_type": "person",
                    "payload_data": person_payload,
                }
            )
        return enrichment_targets

    def _build_created_entities(
        self,
        *,
        company_id: str,
        created_contact_id: str | None,
        created_new_contact: bool,
    ) -> list[dict[str, str]]:
        """Build a list of created entities for the company and contact."""
        created_entities: list[dict[str, str]] = [
            {"entity_table": "companies", "entity_id": str(company_id), "action": "create"}
        ]
        if created_new_contact and created_contact_id:
            created_entities.append(
                {
                    "entity_table": "contacts",
                    "entity_id": str(created_contact_id),
                    "action": "create_contact",
                }
            )
        return created_entities

    def _maybe_send_portal_welcome_email(
        self,
        *,
        portal_access: bool,
        created_contact_row: dict[str, Any] | None,
        created_contact_password: str | None,
    ) -> None:
        """Send a welcome email to the contact if portal access is enabled."""
        if not portal_access or created_contact_row is None:
            return
        email_to_send = created_contact_row.get("email")
        if not email_to_send:
            return
        try:
            send_client_creation_email(
                email=str(email_to_send),
                organization_name=str(shared_settings.company_name or ""),
                password=created_contact_password,
            )
        except Exception as exc:
            logger.error("Failed to send contact creation email: %s", str(exc))

    def _contacts_service(self) -> ContactsServiceV2:
        """Reuse the existing portal-identity provisioning logic from ContactsServiceV2."""
        return ContactsServiceV2(
            db_connection=self.db_connection,
            user_context=self.user_context,
            supabase_client=self.supabase_client,
        )

    async def _maybe_provision_portal_identity(
        self,
        *,
        portal_access: bool,
        create_contact: CreateContactRequest,
    ) -> tuple[str | None, str | None, str | None, str | None]:
        """Normalize email and provision identity only when portal access is enabled."""
        email_norm = (create_contact.email or "").strip() or None
        user_id, isometrik_user_id, created_password = await self._contacts_service()._provision_identity_if_needed(
            email=email_norm,
            portal_access=portal_access,
            first_name=create_contact.first_name,
            last_name=create_contact.last_name,
            prefix=create_contact.prefix,
        )
        return email_norm, user_id, isometrik_user_id, created_password

    def _build_list_payloads(
        self, *, inputs: dict[str, list[dict[str, Any]]]
    ) -> dict[str, list[dict[str, Any]]]:
        """Convert list inputs into JSON-ready payloads with stable ids."""
        return {k: self._ensure_list_item_ids(v) for k, v in inputs.items()}

    async def _create_contact_for_company_association(
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

        contact_list_payloads = self._build_list_payloads(
            inputs={
                "phones": [
                    p.model_dump(mode="json", exclude_none=True) for p in create_contact.phones
                ],
                "social_pages": [
                    p.model_dump(mode="json", exclude_none=True)
                    for p in create_contact.social_pages
                ],
            }
        )
        phones_payload = contact_list_payloads["phones"]
        social_pages_payload = contact_list_payloads["social_pages"]

        email_norm, user_id, isometrik_user_id, _created_password = (
            await self._maybe_provision_portal_identity(
                portal_access=bool(create_contact.portal_access),
                create_contact=create_contact,
            )
        )

        rows = await self.contacts_repo.create_contacts(
            [
                {
                    "organization_id": org_id,
                    "status": ClientStatus.ACTIVE.value,
                    "user_id": user_id,
                    "isometrik_user_id": isometrik_user_id,
                    "prefix": create_contact.prefix,
                    "first_name": create_contact.first_name,
                    "middle_name": create_contact.middle_name,
                    "last_name": create_contact.last_name,
                    "title": create_contact.title,
                    "date_of_birth": create_contact.date_of_birth,
                    "profile_photo_url": create_contact.profile_photo_url,
                    "email": email_norm,
                    "phones": phones_payload,
                    "tags": create_contact.tags,
                    "custom_fields": validated_custom_fields,
                    "additional_data": create_contact.additional_data,
                    "social_pages": social_pages_payload,
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

    async def soft_delete_company(self, *, company_id: str) -> dict[str, Any]:
        """Soft-delete a company (sets status='deleted') and return old/new snapshots."""
        org_id = self.user_context.organization_id
        current = await self.companies_repo.get_company_for_update(
            company_id=company_id,
            organization_id=org_id,
        )
        if not current:
            raise NotFoundException(
                message_key="clients.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        updated = await self.companies_repo.soft_delete_company(
            company_id=company_id,
            organization_id=org_id,
        )
        return {"old_data": current, "new_data": updated}

    async def update_company(self, *, company_id: str, body: UpdateCompanyRequest) -> dict[str, Any]:
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
            await self._apply_jsonb_list_changes(
                body.websites,
                current=current,
                payload=update_data,
                field_name="websites",
                not_found_message_key="clients.errors.website_not_found",
            )

        if body.social_pages is not None:
            await self._apply_jsonb_list_changes(
                body.social_pages,
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

        updated_row: dict[str, Any] | None = None
        if update_data:
            updated_row = await self.companies_repo.update_company(
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
        return {
            "ok": True,
            "old_data": current,
            "new_data": updated_row or current,
        }

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

    async def _apply_jsonb_list_changes(
        self,
        update_obj: Any,
        *,
        current: dict[str, Any],
        payload: dict[str, Any],
        field_name: str,
        not_found_message_key: str,
    ) -> None:
        """Apply JSONB list operations: add, update, and/or remove.

        Mirrors `ContactsServiceV2._apply_jsonb_list_changes` so JSON list fields behave
        identically across contacts and companies.
        """
        current_list = parse_json_field(current.get(field_name)) or []
        if not isinstance(current_list, list):
            current_list = []
        updated = current_list.copy()

        # Remove operations
        if hasattr(update_obj, "remove") and update_obj.remove:
            updated = [item for item in updated if str(item.get("id")) not in update_obj.remove]

        # Update operations
        if hasattr(update_obj, "update") and update_obj.update:
            for item in update_obj.update:
                data = item.model_dump(exclude_none=True, exclude={"id"})
                found = False
                for i, existing_item in enumerate(updated):
                    if str(existing_item.get("id")) == item.id:
                        updated[i] = {**existing_item, **data}
                        found = True
                        break
                if not found:
                    raise NotFoundException(
                        message_key=not_found_message_key,
                        custom_code=CustomStatusCode.NOT_FOUND,
                    )

        # Add operations (always generate a fresh UUID, same as contacts)
        if hasattr(update_obj, "add") and update_obj.add:
            for item in update_obj.add:
                new_item = item.model_dump(exclude_none=True)
                new_item["id"] = str(uuid.uuid4())
                updated.append(new_item)

        payload[field_name] = updated
        current[field_name] = updated

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
        if body.contact:
            contact_id, _created_row = await self._create_contact_for_company_association(
                create_contact=body.contact
            )
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
        filters = [f"organization_id:={org_id}"]
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
            params.update(COMPANY_EMAIL_SEARCH_PARAMS)
        elif sum(c.isdigit() for c in q) >= 5:
            params.update(COMPANY_PHONE_SEARCH_PARAMS)
        else:
            params.update(COMPANY_SEARCH_PARAMS)

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

