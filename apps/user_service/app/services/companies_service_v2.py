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
from datetime import datetime, timezone
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
from apps.user_service.app.db.repositories.companies_repository import (
    COMPANY_JSONB_COLUMNS,
)
from apps.user_service.app.schemas.companies_v2 import (
    CompanyContactsUpdate,
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
from apps.user_service.app.services.client_enrichment_service import (
    ClientEnrichmentService,
)
from apps.user_service.app.services.contacts_service_v2 import ContactsServiceV2
from apps.user_service.app.services.custom_field_service import CustomFieldService
from apps.user_service.app.services.event_service import EventService
from apps.user_service.app.services.typesense_index_service_v2 import (
    index_companies_background,
    index_contacts_background,
)
from apps.user_service.app.utils.common_utils import (
    UserContext,
    coerce_json_list,
    format_iso_datetime,
    parse_json_field,
    serialize_jsonb_param,
)
from apps.user_service.app.utils.email_utils import send_client_creation_email
from libs.shared_utils.http_exceptions import (
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode
from libs.shared_utils.typesense_service import TypesenseService

logger = get_logger("companies_service_v2")

_COMPANY_DETAIL_UUID_KEYS = (
    "id",
    "organization_id",
    "primary_contact_id",
    "enrichment_request_id",
)
_COMPANY_DETAIL_JSON_LIST_KEYS = (
    "tags",
    "websites",
    "social_pages",
    "custom_fields",
    "target_market_segments",
    "current_tech_stack",
    "preferred_communication_channels",
    "industry_specific_terminologies",
    "contacts",
    "addresses",
)


def _stringify_company_detail_uuids(details: dict[str, Any]) -> None:
    """Normalize UUID columns on a company detail dict to strings for JSON responses."""
    for field_name in _COMPANY_DETAIL_UUID_KEYS:
        raw_value = details.get(field_name)
        if raw_value is not None and not isinstance(raw_value, str):
            details[field_name] = str(raw_value)


def _coerce_company_detail_json_lists(details: dict[str, Any]) -> None:
    """Coerce JSON-backed list fields on company details to Python lists."""
    for field_name in _COMPANY_DETAIL_JSON_LIST_KEYS:
        details[field_name] = coerce_json_list(details.get(field_name))


def _normalize_company_billing_preferences(details: dict[str, Any]) -> None:
    """Parse billing preferences when asyncpg returns JSONB as a string."""
    billing_prefs = details.get("billing_preferences")
    if isinstance(billing_prefs, str):
        parsed_billing = parse_json_field(billing_prefs)
        details["billing_preferences"] = parsed_billing if isinstance(parsed_billing, dict) else {}
    elif billing_prefs is None:
        details["billing_preferences"] = None


def _normalize_company_additional_data(details: dict[str, Any]) -> None:
    """Ensure ``additional_data`` is a dict after optional string JSON parsing."""
    extra_data = details.get("additional_data")
    if isinstance(extra_data, str):
        details["additional_data"] = parse_json_field(extra_data) or {}
    elif extra_data is None:
        details["additional_data"] = {}


def _normalize_company_detail_contacts(details: dict[str, Any]) -> None:
    """Normalize nested contact rows embedded in company detail payloads."""
    for contact in details.get("contacts") or []:
        if not isinstance(contact, dict):
            continue
        contact_identifier = contact.get("id")
        if contact_identifier is not None and not isinstance(contact_identifier, str):
            contact["id"] = str(contact_identifier)
        contact["phones"] = coerce_json_list(contact.get("phones"))


def _normalize_company_detail_timestamps(details: dict[str, Any]) -> None:
    """Format timestamp columns on company details as ISO strings."""
    details["created_at"] = format_iso_datetime(details.get("created_at")) or ""
    details["updated_at"] = format_iso_datetime(details.get("updated_at")) or ""
    details["last_enriched_at"] = format_iso_datetime(details.get("last_enriched_at"))


class CompaniesServiceV2:
    """Business logic for v2 companies."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
        supabase_client: AsyncClient | None = None,
    ) -> None:
        """Initialize the service with DB access and the authenticated user context."""
        self.db_connection = db_connection
        self.user_context = user_context
        self.supabase_client = supabase_client
        self.companies_repo = CompaniesRepository(db_connection)
        self.contacts_repo = ContactsRepository(db_connection)
        self.cc_repo = ContactCompaniesRepository(db_connection)
        self._typesense: TypesenseService | None = None

    @property
    def typesense(self) -> TypesenseService:
        """Lazily construct the Typesense client for the companies collection."""
        if self._typesense is None:
            self._typesense = TypesenseService.from_settings(
                collection_name=app_settings.shared_settings.typesense.companies_collection_name
            )
        return self._typesense

    @staticmethod
    def _schedule_company_event_tasks(
        *,
        background_tasks: BackgroundTasks,
        update_event: dict[str, Any] | None,
        related_lifecycle_events: list[tuple[dict[str, Any], str]] | None,
        event_key: str,
        event_topics: list[Any],
    ) -> None:
        """Schedule lifecycle event publishes."""
        if update_event is not None:
            background_tasks.add_task(
                EventService.publish_event_background,
                event=update_event,
                key=event_key,
                topics=event_topics,
            )

        for related_event, related_event_key in related_lifecycle_events or ():
            background_tasks.add_task(
                EventService.publish_event_background,
                event=related_event,
                key=related_event_key,
                topics=event_topics,
            )

    @staticmethod
    def _schedule_company_and_contact_index_tasks(
        *,
        background_tasks: BackgroundTasks,
        company_id: str,
        organization_id: str,
        body: UpdateCompanyRequest,
        update_result: dict[str, Any] | None,
    ) -> None:
        """Schedule Typesense indexing for company and affected contacts."""
        background_tasks.add_task(
            index_companies_background,
            [(company_id, organization_id)],
        )

        if body.contacts_update is None:
            return

        meta = (update_result or {}).get("contacts_delta") or {}
        affected = meta.get("affected_contact_ids") or []
        if affected:
            background_tasks.add_task(
                index_contacts_background,
                [(str(contact_identifier), organization_id) for contact_identifier in affected],
            )
        created_cid = meta.get("created_contact_id")
        if created_cid:
            background_tasks.add_task(
                ContactsServiceV2.trigger_enrichment_background,
                str(created_cid),
                organization_id,
            )

    @staticmethod
    def _schedule_company_enrichment_task(
        *,
        background_tasks: BackgroundTasks,
        company_id: str,
        organization_id: str,
        body: UpdateCompanyRequest,
    ) -> None:
        """Schedule enrichment for a company if relevant inputs have changed."""
        enrichment_input_fields = (
            "name",
            "industry",
            "websites",
            "social_pages",
            "addresses",
            "description",
        )
        enrichment_inputs_changed = any(
            getattr(body, field_name) is not None for field_name in enrichment_input_fields
        )
        if not enrichment_inputs_changed:
            return

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

    @staticmethod
    def schedule_company_update_background_tasks(
        *,
        background_tasks: BackgroundTasks,
        company_id: str,
        organization_id: str,
        body: UpdateCompanyRequest,
        update_result: dict[str, Any] | None,
        update_event: dict[str, Any] | None,
        event_key: str,
        event_topics: list[Any],
        related_lifecycle_events: list[tuple[dict[str, Any], str]] | None = None,
    ) -> None:
        """Schedule background tasks after a company update (parity with contacts_v2)."""
        CompaniesServiceV2._schedule_company_event_tasks(
            background_tasks=background_tasks,
            update_event=update_event,
            related_lifecycle_events=related_lifecycle_events,
            event_key=event_key,
            event_topics=event_topics,
        )
        CompaniesServiceV2._schedule_company_and_contact_index_tasks(
            background_tasks=background_tasks,
            company_id=company_id,
            organization_id=organization_id,
            body=body,
            update_result=update_result,
        )
        CompaniesServiceV2._schedule_company_enrichment_task(
            background_tasks=background_tasks,
            company_id=company_id,
            organization_id=organization_id,
            body=body,
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

        (
            websites_payload,
            social_pages_payload,
            contact_phones_payload,
            contact_social_pages_payload,
        ) = self._build_create_company_list_payloads(body=body)
        jsonb_params = self._serialize_company_jsonb_params(
            body=body,
            websites_payload=websites_payload,
            social_pages_payload=social_pages_payload,
            validated_company_custom_fields=validated_company_custom_fields,
        )

        (
            contact_id,
            contact_data,
            contact_addresses,
            set_primary,
            created_contact_password,
        ) = await self._prepare_optional_company_contact_association(
            body=body,
            contact_phones_payload=contact_phones_payload,
            contact_social_pages_payload=contact_social_pages_payload,
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
        """Validate custom fields for the given entity type before insert."""
        if not custom_fields:
            return []
        custom_field_service = CustomFieldService(
            db_connection=self.db_connection,
            user_context=self.user_context,
        )
        return await custom_field_service.validate_for_create(custom_fields, entity_type)

    def _build_create_company_list_payloads(
        self, *, body: CreateCompanyRequest
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
    ]:
        """Build list payloads for company creation."""
        create_contact_for_payloads = (
            body.contact.contact
            if (body.contact is not None and body.contact.contact is not None)
            else None
        )
        list_payloads = self._build_list_payloads(
            inputs={
                "company_websites": [
                    website.model_dump(mode="json", exclude_none=True) for website in body.websites
                ],
                "company_social_pages": [
                    page.model_dump(mode="json", exclude_none=True) for page in body.social_pages
                ],
                "contact_phones": (
                    [
                        phone.model_dump(mode="json", exclude_none=True)
                        for phone in create_contact_for_payloads.phones
                    ]
                    if create_contact_for_payloads is not None
                    else []
                ),
                "contact_social_pages": (
                    [
                        page.model_dump(mode="json", exclude_none=True)
                        for page in create_contact_for_payloads.social_pages
                    ]
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
        """Serialize company JSONB parameters."""
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
    ) -> tuple[
        str | None,
        dict[str, Any] | None,
        list[dict[str, Any]] | None,
        bool,
        str | None,
    ]:
        """Prepare optional company contact association."""
        created_contact_password: str | None = None

        contact_id: str | None = None
        contact_data: dict[str, Any] | None = None
        contact_addresses: list[dict[str, Any]] | None = None
        set_primary = False

        if body.contact is None:
            return (
                contact_id,
                contact_data,
                contact_addresses,
                set_primary,
                created_contact_password,
            )

        set_primary = bool(body.contact.is_primary)
        contact_id = body.contact.contact_id

        if body.contact.contact is None:
            return (
                contact_id,
                contact_data,
                contact_addresses,
                set_primary,
                created_contact_password,
            )

        create_contact = body.contact.contact
        validated_contact_custom_fields = await self._validate_custom_fields_for_create(
            custom_fields=create_contact.custom_fields,
            entity_type=EntityType.CONTACT,
        )

        (
            email_norm,
            user_id,
            isometrik_user_id,
            created_contact_password,
        ) = await self._maybe_provision_portal_identity(
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
            [address.model_dump(exclude_none=True) for address in create_contact.addresses]
            if create_contact.addresses
            else []
        )
        return contact_id, contact_data, contact_addresses, set_primary, created_contact_password

    def _company_addresses_rows(self, *, body: CreateCompanyRequest) -> list[dict[str, Any]]:
        """Build company addresses rows."""
        return [a.model_dump(exclude_none=True) for a in body.addresses] if body.addresses else []

    def _extract_created_contact(
        self,
        *,
        created: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Extract created contact from creation result."""
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
        """Raise NotFoundException if contact link outcome is not found."""
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
        """Normalize created contact id for response."""
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
        """Build enrichment targets for company creation."""
        enrichment_targets: list[dict[str, Any]] = []
        addresses_payload = [
            {"country": address_input.country}
            for address_input in (body.addresses or [])
            if address_input.country
        ]
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
                (
                    phone_entry
                    for phone_entry in phones
                    if isinstance(phone_entry, dict) and phone_entry.get("is_primary") is True
                ),
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
        except Exception as send_error:
            logger.error("Failed to send contact creation email: %s", str(send_error))

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
        contacts_service = self._contacts_service()
        (
            user_id,
            isometrik_user_id,
            created_password,
        ) = await contacts_service._provision_identity_if_needed(
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
        return {
            list_key: self._ensure_list_item_ids(list_items)
            for list_key, list_items in inputs.items()
        }

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
            custom_field_service = CustomFieldService(
                db_connection=self.db_connection,
                user_context=self.user_context,
            )
            validated_custom_fields = await custom_field_service.validate_for_create(
                create_contact.custom_fields,
                EntityType.CONTACT,
            )
        else:
            validated_custom_fields = []

        contact_list_payloads = self._build_list_payloads(
            inputs={
                "phones": [
                    phone.model_dump(mode="json", exclude_none=True)
                    for phone in create_contact.phones
                ],
                "social_pages": [
                    page.model_dump(mode="json", exclude_none=True)
                    for page in create_contact.social_pages
                ],
            }
        )
        phones_payload = contact_list_payloads["phones"]
        social_pages_payload = contact_list_payloads["social_pages"]

        (
            email_norm,
            user_id,
            isometrik_user_id,
            _,
        ) = await self._maybe_provision_portal_identity(
            portal_access=bool(create_contact.portal_access),
            create_contact=create_contact,
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
            address_rows = [
                {"contact_id": contact_id, **addr.model_dump(exclude_none=True)}
                for addr in create_contact.addresses
            ]
            await self.contacts_repo.create_contact_addresses(address_rows)
        return contact_id, dict(contact_row)

    async def get_company_details(self, *, company_id: str) -> dict[str, Any]:
        """Return company details with member contacts (list shape) and addresses."""
        org_id = self.user_context.organization_id
        details = await self.companies_repo.get_company_details(
            company_id=company_id,
            organization_id=org_id,
        )
        if not details:
            raise NotFoundException(
                message_key="clients.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        _stringify_company_detail_uuids(details)
        _coerce_company_detail_json_lists(details)
        _normalize_company_billing_preferences(details)
        _normalize_company_additional_data(details)
        _normalize_company_detail_contacts(details)
        _normalize_company_detail_timestamps(details)
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
        for list_row in rows:
            list_row["created_at"] = format_iso_datetime(list_row.get("created_at")) or ""
            list_row["updated_at"] = format_iso_datetime(list_row.get("updated_at")) or ""
            raw_contacts = list_row.get("contacts")
            if isinstance(raw_contacts, str):
                list_row["contacts"] = parse_json_field(raw_contacts) or []
            elif raw_contacts is None:
                list_row["contacts"] = []
            elif not isinstance(raw_contacts, list):
                list_row["contacts"] = []
        return {"items": rows, "total": total}

    async def soft_delete_company(self, *, company_id: str) -> dict[str, Any]:
        """Soft-delete a company (sets status='deleted') via the same DB update path as PATCH."""
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
        updated = await self.companies_repo.update_company(
            company_id=company_id,
            organization_id=org_id,
            update_data={"status": ClientStatus.DELETED.value},
        )
        if not updated:
            raise NotFoundException(
                message_key="clients.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return {"ok": True, "old_data": current, "new_data": updated}

    async def update_company(
        self,
        *,
        company_id: str,
        body: UpdateCompanyRequest,
    ) -> dict[str, Any]:
        """Patch a company (scalar fields + JSONB lists + addresses table).

        Notes:
        - `websites` and `social_pages` use delta semantics (add/update/remove) matching v1.
        - `billing_preferences` is merged (PATCH semantics).
        - `custom_fields` uses the same merge/validation logic as v1.
        - `addresses` are stored in `company_addresses` table, updated via delta ops.
        """
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

        update_data = await self._build_company_update_data(current=current, body=body)
        updated_row = await self._persist_company_update_if_needed(
            company_id=company_id,
            organization_id=org_id,
            update_data=update_data,
        )
        await self._apply_company_side_effect_deltas(company_id=company_id, body=body)
        contacts_delta = await self._maybe_apply_contacts_update_delta(
            company_id=company_id, body=body
        )
        return self._build_company_update_response(
            current=current,
            updated_row=updated_row,
            contacts_delta=contacts_delta,
        )

    async def _build_company_update_data(
        self,
        *,
        current: dict[str, Any],
        body: UpdateCompanyRequest,
    ) -> dict[str, Any]:
        """Build a dictionary of update data for a company."""
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
        for body_attr, column_name in scalar_fields:
            value = getattr(body, body_attr, None)
            if value is not None:
                update_data[column_name] = value

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
            custom_field_service = CustomFieldService(
                db_connection=self.db_connection,
                user_context=self.user_context,
            )
            # merge_for_update expects existing roots list
            existing_cf = parse_json_field(current.get("custom_fields"))
            merged = existing_cf if isinstance(existing_cf, list) else []
            merged = await custom_field_service.merge_for_update(
                body.custom_fields, merged, EntityType.COMPANY
            )
            update_data["custom_fields"] = merged

        return update_data

    async def _persist_company_update_if_needed(
        self,
        *,
        company_id: str,
        organization_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Update a company if there are changes to persist."""
        if not update_data:
            return None
        return await self.companies_repo.update_company(
            company_id=company_id,
            organization_id=organization_id,
            update_data=update_data,
        )

    async def _apply_company_side_effect_deltas(
        self,
        *,
        company_id: str,
        body: UpdateCompanyRequest,
    ) -> None:
        """Apply side effect deltas for a company update."""
        # Addresses delta: parity with v1 can follow once delta semantics are fixed upstream.
        if body.addresses is not None:
            await self._apply_company_addresses_delta(
                company_id=company_id,
                addresses=body.addresses,
            )

    async def _maybe_apply_contacts_update_delta(
        self,
        *,
        company_id: str,
        body: UpdateCompanyRequest,
    ) -> dict[str, Any] | None:
        """Apply contacts update delta if present."""
        if body.contacts_update is None:
            return None
        return await self.apply_contacts_update_delta(
            company_id=company_id,
            delta=body.contacts_update,
        )

    @staticmethod
    def _build_company_update_response(
        *,
        current: dict[str, Any],
        updated_row: dict[str, Any] | None,
        contacts_delta: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Build a response for a company update."""
        update_response: dict[str, Any] = {
            "ok": True,
            "old_data": current,
            "new_data": updated_row or current,
        }
        if contacts_delta is not None:
            update_response["contacts_delta"] = contacts_delta
        return update_response

    async def _apply_company_addresses_delta(self, *, company_id: str, addresses: Any) -> None:
        """Apply AddressesUpdate to `company_addresses` table."""
        if addresses is None:
            return
        # remove
        if addresses.remove:
            await self.companies_repo.delete_company_addresses(
                company_id=company_id,
                address_ids=addresses.remove,
            )
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
                [
                    {"company_id": company_id, **addr.model_dump(exclude_none=True)}
                    for addr in addresses.add
                ]
            )

    @staticmethod
    def _ensure_list_item_ids(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return a copy of list items with an id set on each (generated if missing)."""
        result: list[dict[str, Any]] = []
        for item in items:
            item_copy = dict(item)
            if not item_copy.get("id"):
                item_copy["id"] = str(uuid.uuid4())
            result.append(item_copy)
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
                for list_index, existing_item in enumerate(updated):
                    if str(existing_item.get("id")) == item.id:
                        updated[list_index] = {**existing_item, **data}
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

    @staticmethod
    def _merge_primary_contact_id(
        current: str | None,
        candidate: str,
    ) -> str:
        """Enforce a single primary contact id when merging association updates."""
        if current is None or current == candidate:
            return candidate
        raise ValidationException(
            message_key="clients.errors.bad_request",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    @staticmethod
    def _parse_company_contacts_update_delta(
        delta: CompanyContactsUpdate,
    ) -> tuple[list[str], list[str], list[str], str | None]:
        """Split payload into remove/add/unset-primary lists and a single primary id (if any)."""
        remove_ids = list(dict.fromkeys(delta.remove_associations or []))
        add_contact_ids: list[str] = []
        unset_primary_ids: list[str] = []
        primary_wants: list[str] = []

        for item in delta.add_associations or []:
            add_contact_ids.append(item.contact_id)
            if item.is_primary:
                primary_wants.append(item.contact_id)

        for item in delta.update_associations or []:
            if item.is_primary:
                primary_wants.append(item.contact_id)
            else:
                unset_primary_ids.append(item.contact_id)

        unset_primary_ids = list(dict.fromkeys(unset_primary_ids))
        unique_primary = list(dict.fromkeys(primary_wants))
        if len(unique_primary) > 1:
            raise ValidationException(
                message_key="clients.errors.bad_request",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        set_primary = unique_primary[0] if unique_primary else None
        return remove_ids, add_contact_ids, unset_primary_ids, set_primary

    async def apply_contacts_update_delta(
        self,
        *,
        company_id: str,
        delta: CompanyContactsUpdate,
    ) -> dict[str, Any]:
        """Apply batch contact association changes (add/remove/create/primary) for a company."""
        org_id = self.user_context.organization_id
        remove_ids, add_contact_ids, unset_primary_ids, set_primary_contact_id = (
            self._parse_company_contacts_update_delta(delta)
        )
        create_block = delta.create_and_associate

        validate_ids = (
            set(remove_ids)
            | {add_item.contact_id for add_item in (delta.add_associations or [])}
            | {update_item.contact_id for update_item in (delta.update_associations or [])}
            | set(unset_primary_ids)
        )
        if validate_ids:
            found = await self.contacts_repo.filter_contact_ids_in_organization(
                organization_id=org_id,
                contact_ids=list(validate_ids),
            )
            missing = validate_ids - found
            if missing:
                raise NotFoundException(
                    message_key="clients.errors.not_found",
                    custom_code=CustomStatusCode.NOT_FOUND,
                )

        created_contact_id: str | None = None
        if create_block is not None:
            created_contact_id, _ = await self._create_contact_for_company_association(
                create_contact=create_block.contact,
            )
            add_contact_ids.append(created_contact_id)
            if create_block.is_primary:
                set_primary_contact_id = self._merge_primary_contact_id(
                    set_primary_contact_id,
                    created_contact_id,
                )

        if set_primary_contact_id:
            add_contact_ids.append(set_primary_contact_id)

        add_contact_ids = list(dict.fromkeys(add_contact_ids))

        await self.cc_repo.apply_contacts_update_delta(
            organization_id=org_id,
            company_id=company_id,
            remove_contact_ids=remove_ids,
            add_contact_ids=add_contact_ids,
            set_primary_contact_id=set_primary_contact_id,
            unset_primary_contact_ids=unset_primary_ids,
        )

        affected: set[str] = set(remove_ids)
        affected.update(add_item.contact_id for add_item in (delta.add_associations or []))
        affected.update(update_item.contact_id for update_item in (delta.update_associations or []))
        affected.update(unset_primary_ids)
        if created_contact_id:
            affected.add(created_contact_id)
        if set_primary_contact_id:
            affected.add(set_primary_contact_id)

        return {
            "ok": True,
            "affected_contact_ids": list(affected),
            "created_contact_id": created_contact_id,
        }

    @staticmethod
    def typesense_hits_to_company_summary_rows(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Map raw Typesense hits to the same row shape as ``list_companies`` (before Pydantic)."""
        rows: list[dict[str, Any]] = []
        for hit in hits:
            hit_document = hit.get("document") if isinstance(hit, dict) else None
            if not isinstance(hit_document, dict):
                continue
            company_identifier = hit_document.get("id")
            if not company_identifier:
                continue

            created_at = hit_document.get("created_at")
            updated_at = hit_document.get("updated_at")
            created_at_iso = (
                datetime.fromtimestamp(int(created_at), tz=timezone.utc).isoformat()
                if isinstance(created_at, int) and created_at > 0
                else ""
            )
            updated_at_iso = (
                datetime.fromtimestamp(int(updated_at), tz=timezone.utc).isoformat()
                if isinstance(updated_at, int) and updated_at > 0
                else ""
            )

            contacts_out: list[dict[str, Any]] = []
            for contact in hit_document.get("contacts") or []:
                if not isinstance(contact, dict):
                    continue
                contact_id = (contact.get("id") or "").strip()
                if not contact_id:
                    continue
                phones = contact.get("phones_display") or contact.get("phones") or []
                if not isinstance(phones, list):
                    phones = []
                contacts_out.append(
                    {
                        "id": contact_id,
                        "first_name": contact.get("first_name"),
                        "last_name": contact.get("last_name"),
                        "title": contact.get("title"),
                        "email": contact.get("email"),
                        "phones": phones,
                        "is_primary": bool(contact.get("is_primary")),
                    }
                )

            rows.append(
                {
                    "id": str(company_identifier),
                    "organization_id": str(hit_document.get("organization_id") or ""),
                    "status": hit_document.get("status"),
                    "name": hit_document.get("name") or "",
                    "industry": hit_document.get("industry"),
                    "profile_photo_url": hit_document.get("profile_photo_url") or None,
                    "contacts": contacts_out,
                    "created_at": created_at_iso,
                    "updated_at": updated_at_iso,
                }
            )
        return rows

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

        query_text = query.strip()
        params: dict[str, Any] = {
            "q": query_text,
            "per_page": page_size,
            "page": page,
            "filter_by": filter_by,
            "exclude_fields": "embedding",
        }
        if "@" in query_text:
            params.update(COMPANY_EMAIL_SEARCH_PARAMS)
        elif sum(char.isdigit() for char in query_text) >= 5:
            params.update(COMPANY_PHONE_SEARCH_PARAMS)
        else:
            params.update(COMPANY_SEARCH_PARAMS)

        embedding = await self.typesense.embed_query_text(query_text)
        if embedding is not None:
            vector = ",".join(map(str, embedding))
            distance_threshold = getattr(
                shared_settings.typesense,
                "vector_distance_threshold",
                None,
            )
            if distance_threshold is not None and float(distance_threshold) > 0:
                params["vector_query"] = (
                    f"embedding:([{vector}], alpha:0.7, distance_threshold:{distance_threshold})"
                )
            else:
                params["vector_query"] = f"embedding:([{vector}], alpha:0.7)"

        search_response = await self.typesense.search(params)
        hits = search_response.get("hits") or []
        rows = self.typesense_hits_to_company_summary_rows(hits)
        return {"items": rows, "total": search_response.get("found", 0)}
