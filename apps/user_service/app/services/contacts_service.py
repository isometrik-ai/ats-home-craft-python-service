"""Contacts service.

Implements the operations defined in ADR `clients_operations.md` against:
- `contacts`
- `companies`
- `contact_companies`
- `contact_addresses`
- `company_addresses`

Design goals:
- Keep DB round-trips low by doing association changes in single transactions.
- Reuse existing custom-field merge/resolve behavior (same CustomFieldService).
- Keep enrichment and Typesense behavior consistent, but targeted to split tables/collections.
"""

from __future__ import annotations

import json
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
from apps.user_service.app.db.repositories.contacts_repository import (
    CONTACT_JSONB_COLUMNS,
)
from apps.user_service.app.db.repositories.organization_repository import (
    OrganizationRepository,
)
from apps.user_service.app.db.repositories.user_repository import UserRepository
from apps.user_service.app.schemas.contacts import (
    ContactCompaniesUpdate,
    ContactSummaryResponse,
    CreateContactRequest,
    UpdateContactRequest,
)
from apps.user_service.app.schemas.enums import ClientStatus, EntityType, IsometrikRole
from apps.user_service.app.search.contact_typesense_schema import (
    CONTACT_EMAIL_SEARCH_PARAMS,
    CONTACT_PHONE_SEARCH_PARAMS,
    CONTACT_SEARCH_PARAMS,
)
from apps.user_service.app.services.client_enrichment_service import (
    ClientEnrichmentService,
)
from apps.user_service.app.services.custom_field_service import CustomFieldService
from apps.user_service.app.services.event_service import EventService
from apps.user_service.app.services.typesense_index_service import (
    index_companies_background,
    index_contacts_background,
)
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
    generate_random_password,
    parse_json_field,
    serialize_jsonb_param,
)
from apps.user_service.app.utils.email_utils import send_client_creation_email
from libs.shared_db.drivers.asyncpg_client import AcquireConnection, get_pool
from libs.shared_db.supabase_db.auth_repository import create_user
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ServiceUnavailableException,
    ValidationException,
)
from libs.shared_utils.isometrik_service import (
    create_isometrik_user,
    get_isometrik_data_from_settings,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode
from libs.shared_utils.typesense_service import TypesenseService

logger = get_logger("contacts_service")


class ContactsService:
    """Business logic for contacts."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
        supabase_client: AsyncClient | None = None,
    ) -> None:
        """Initialize the service with a DB connection and the caller user context."""
        self.db_connection = db_connection
        self.user_context = user_context
        self.supabase_client = supabase_client
        self.contacts_repo = ContactsRepository(db_connection)
        self.companies_repo = CompaniesRepository(db_connection)
        self.cc_repo = ContactCompaniesRepository(db_connection)
        self.org_repo = OrganizationRepository(db_connection)
        self._typesense: TypesenseService | None = None

    @property
    def typesense(self) -> TypesenseService:
        """Lazily create and return the Typesense client for contacts."""
        if self._typesense is None:
            self._typesense = TypesenseService.from_settings(
                collection_name=app_settings.shared_settings.typesense.contacts_collection_name,
            )
        return self._typesense

    async def _provision_contact_auth_identity(
        self,
        *,
        email: str,
        first_name: str | None,
        last_name: str | None,
        prefix: str | None,
    ) -> tuple[str, str, str | None]:
        """Create/reuse Supabase auth user and create Isometrik user for a contact.

        Returns:
            (user_id, isometrik_user_id, password_if_created)
        """
        if not self.supabase_client:
            raise ServiceUnavailableException(
                message_key="contacts.errors.auth_user_creation_failed",
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
            )

        org_id = self.user_context.organization_id
        organization = await self.org_repo.get_organization_by_id(org_id)
        if not organization:
            raise NotFoundException(
                message_key="organizations.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
                params={"organization_id": org_id},
            )

        email_norm = email.strip()
        user_repo = UserRepository(db_connection=self.db_connection)
        existing_user = await user_repo.get_auth_user_by_email(email_norm)
        created_password: str | None = None
        if existing_user and existing_user.get("id"):
            user_id = str(existing_user["id"])
        else:
            password = generate_random_password()
            created_password = password
            # Keep this metadata block identical to ClientService._create_auth_and_isometrik_user
            user_metadata: dict[str, Any] = {
                "timezone": "UTC",
                "first_name": first_name,
                "last_name": last_name,
            }
            if prefix:
                user_metadata["salutation"] = prefix

            auth_user = await create_user(
                sb_client=self.supabase_client,
                email=email_norm,
                password=password,
                email_confirm=True,
                user_metadata=user_metadata,
            )
            if not auth_user or not auth_user.get("id"):
                raise ServiceUnavailableException(
                    message_key="contacts.errors.auth_user_creation_failed",
                    custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
                )
            user_id = str(auth_user["id"])

        org_settings = parse_json_field(organization.get("settings"))
        isometrik_credentials = get_isometrik_data_from_settings(org_settings)
        isometrik_response = await create_isometrik_user(
            user={
                "user_id": user_id,
                "email": email_norm,
                "organization_id": org_id,
                "role": IsometrikRole.CLIENT.value,
                "first_name": first_name,
                "last_name": last_name,
                # Keep this condition identical to ClientService._create_auth_and_isometrik_user
                "user_identifier": str(uuid.uuid4()) if existing_user else None,
            },
            isometrik_credentials=isometrik_credentials,
        )
        if not isometrik_response or not isometrik_response.get("userId"):
            raise ServiceUnavailableException(
                message_key="contacts.errors.isometrik_user_creation_failed",
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
            )
        return user_id, str(isometrik_response["userId"]), created_password

    @staticmethod
    def _parse_company_create_input(
        body: CreateContactRequest,
    ) -> tuple[str | None, str | None, bool, bool]:
        """Extract company create/link inputs from the request.

        Returns:
            (company_id, company_name, make_primary, created_new_company)
        """
        company_id: str | None = None
        company_name: str | None = None
        make_primary = False
        created_new_company = False

        if not body.company:
            return company_id, company_name, make_primary, created_new_company

        company_id = (body.company.company_id or "").strip() or None
        company_name = (body.company.company_name or "").strip() or None
        make_primary = bool(body.company.is_primary)
        created_new_company = bool(company_name) and not company_id
        return company_id, company_name, make_primary, created_new_company

    async def _assert_contact_email_unique(self, *, organization_id: str, email: str) -> None:
        """Raise ConflictException if a contact with this email already exists in the org."""
        existing_contact_id = await self.contacts_repo.get_contact_id_by_email(
            organization_id=organization_id,
            email=email,
        )
        if existing_contact_id:
            raise ConflictException(
                message_key="contacts.errors.email_already_exists",
                custom_code=CustomStatusCode.CONFLICT,
                params={"client_id": existing_contact_id},
            )

    async def _validate_custom_fields_for_create(
        self,
        custom_fields_payload: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        """Validate and normalize custom fields for contact creation (same rules as clients)."""
        if not (self.user_context and self.user_context.organization_id):
            return []

        custom_field_service = CustomFieldService(
            db_connection=self.db_connection,
            user_context=self.user_context,
        )
        return await custom_field_service.validate_for_create(
            custom_fields_payload,
            EntityType.CONTACT,
        )

    async def _provision_identity_if_needed(
        self,
        *,
        email: str | None,
        portal_access: bool,
        first_name: str | None,
        last_name: str | None,
        prefix: str | None,
    ) -> tuple[str | None, str | None, str | None]:
        """Provision auth identity only when an email is provided.

        Returns:
            ``(user_id, isometrik_user_id, password_if_created)`` when created or reused,
            otherwise ``(None, None, None)``.
        """
        if not (portal_access and email):
            return None, None, None
        return await self._provision_contact_auth_identity(
            email=email,
            first_name=first_name,
            last_name=last_name,
            prefix=prefix,
        )

    def _maybe_send_contact_creation_email(
        self,
        *,
        portal_access: bool,
        email: str | None,
        organization_name: str,
        password: str | None,
    ) -> None:
        """Send portal welcome email when portal access and email are both set."""
        if not (portal_access and email):
            return
        try:
            send_client_creation_email(
                email=email,
                organization_name=organization_name,
                password=password,
            )
        except Exception as send_error:
            logger.error("Failed to send contact creation email: %s", str(send_error))

    async def _create_addresses_if_any(
        self,
        *,
        contact_id: str,
        addresses: list[Any] | None,
    ) -> None:
        """Create contact addresses in bulk when provided."""
        if not addresses:
            return
        await self.contacts_repo.create_contact_addresses(
            [
                {"contact_id": contact_id, **address_input.model_dump(exclude_none=True)}
                for address_input in addresses
            ]
        )

    @staticmethod
    def _select_primary_phone(phones: list[Any] | None) -> Any | None:
        """Return the primary phone if present, else the first phone, else None."""
        if not phones:
            return None
        return next(
            (phone_item for phone_item in phones if getattr(phone_item, "is_primary", False)),
            phones[0],
        )

    def _build_person_payload(
        self,
        *,
        body: CreateContactRequest,
        email: str | None,
    ) -> dict[str, Any]:
        """Build the enrichment payload for a person contact (contacts entity)."""
        addresses_payload = [
            {"country": address_input.country}
            for address_input in (body.addresses or [])
            if getattr(address_input, "country", None)
        ]
        primary_phone = self._select_primary_phone(body.phones)

        person_payload: dict[str, Any] = {
            "first_name": body.first_name or "",
            "middle_name": body.middle_name or "",
            "last_name": body.last_name or "",
            "email": email,
            "addresses": addresses_payload,
        }
        if primary_phone is not None:
            person_payload["phone_isd_code"] = getattr(primary_phone, "phone_isd_code", None)
            person_payload["phone_number"] = getattr(primary_phone, "phone_number", None)
        return person_payload

    @staticmethod
    def _build_enrichment_targets(
        *,
        organization_id: str,
        contact_id: str,
        person_payload: dict[str, Any],
        created_new_company: bool,
        company_id: str | None,
        company_name: str | None,
    ) -> list[dict[str, Any]]:
        """Build enrichment targets for background enrichment jobs."""
        enrichment_targets: list[dict[str, Any]] = [
            {
                "entity_table": "contacts",
                "client_id": contact_id,
                "organization_id": organization_id,
                "client_type": "person",
                "payload_data": person_payload,
            }
        ]
        if created_new_company and company_id:
            enrichment_targets.append(
                {
                    "entity_table": "companies",
                    "client_id": str(company_id),
                    "organization_id": organization_id,
                    "client_type": "company",
                    "payload_data": {"name": company_name or ""},
                }
            )
        return enrichment_targets

    @staticmethod
    def _build_created_entities(
        *,
        contact_id: str,
        created_new_company: bool,
        company_id: str | None,
    ) -> list[dict[str, str]]:
        """Build audit metadata for entities created as part of the request."""
        created_entities: list[dict[str, str]] = [
            {"entity_table": "contacts", "entity_id": str(contact_id), "action": "create"}
        ]
        if created_new_company and company_id:
            created_entities.append(
                {
                    "entity_table": "companies",
                    "entity_id": str(company_id),
                    "action": "create_company",
                }
            )
        return created_entities

    async def create_contact(self, body: CreateContactRequest) -> dict[str, Any]:
        """Create a contact with optional company link (and optional primary designation).

        Implements ADR section 1:
        - 1a contact only
        - 1b contact + link to existing company (optional primary)
        - 1c contact + create new company + link (optional primary)

        Returns:
            dict with keys:
            - contact_id: created contact id (str)
            - company_id: linked/created company id when requested (str | None)
        """
        org_id = self.user_context.organization_id
        company_id, company_name, make_primary, created_new_company = (
            self._parse_company_create_input(body)
        )

        # Align with legacy behavior: prevent org-level duplicate emails when email is provided.
        email_norm = (body.email or "").strip() or None
        if email_norm:
            await self._assert_contact_email_unique(organization_id=org_id, email=email_norm)

        # Custom fields: validate/normalize exactly as existing behavior.
        validated_custom_fields = await self._validate_custom_fields_for_create(body.custom_fields)

        organization = await self.org_repo.get_organization_by_id(org_id)
        if not organization:
            raise NotFoundException(
                message_key="organizations.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
                params={"organization_id": org_id},
            )
        org_name = str(organization.get("name") or shared_settings.company_name or "")

        user_id, isometrik_user_id, created_password = await self._provision_identity_if_needed(
            email=email_norm,
            portal_access=bool(body.portal_access),
            first_name=body.first_name,
            last_name=body.last_name,
            prefix=body.prefix,
        )

        list_payload_inputs: dict[str, list[dict[str, Any]]] = {
            "phones": [phone.model_dump(mode="json", exclude_none=True) for phone in body.phones],
            "social_pages": [
                page.model_dump(mode="json", exclude_none=True) for page in body.social_pages
            ],
        }
        list_payloads: dict[str, list[dict[str, Any]]] = {}
        for field_name, items in list_payload_inputs.items():
            list_payloads[field_name] = self._ensure_list_item_ids(items)

        phones_payload = list_payloads["phones"]
        social_pages_payload = list_payloads["social_pages"]

        # Prepare JSONB params once at the service layer
        jsonb_inputs: dict[str, Any] = {
            "phones": phones_payload,
            "custom_fields": validated_custom_fields,
            "additional_data": body.additional_data,
            "social_pages": social_pages_payload,
        }
        jsonb_params: dict[str, Any] = {}
        for field_name, field_value in jsonb_inputs.items():
            jsonb_params[field_name] = serialize_jsonb_param(
                field_name,
                field_value,
                CONTACT_JSONB_COLUMNS,
            )

        phones_jsonb = jsonb_params["phones"]
        custom_fields_jsonb = jsonb_params["custom_fields"]
        additional_data_jsonb = jsonb_params["additional_data"]
        social_pages_jsonb = jsonb_params["social_pages"]

        created = await self.contacts_repo.create_contact_with_optional_company_link(
            organization_id=org_id,
            contact_data={
                "user_id": user_id,
                "isometrik_user_id": isometrik_user_id,
                "status": ClientStatus.ACTIVE.value,
                "prefix": body.prefix,
                "first_name": body.first_name,
                "middle_name": body.middle_name,
                "last_name": body.last_name,
                "title": body.title,
                "date_of_birth": body.date_of_birth,
                "profile_photo_url": body.profile_photo_url,
                "email": email_norm,
                "phones": phones_jsonb,
                "tags": body.tags,
                "custom_fields": custom_fields_jsonb,
                "additional_data": additional_data_jsonb,
                "social_pages": social_pages_jsonb,
            },
            company_id=company_id,
            company_name=company_name,
            make_primary=make_primary,
        )
        contact_id = created.get("contact_id")
        company_id = created.get("company_id")
        contact_row = created.get("contact")
        if not contact_id:
            raise ValidationException(
                message_key="contacts.errors.contact_creation_failed",
                custom_code=CustomStatusCode.SERVICE_UNAVAILABLE,
            )

        await self._create_addresses_if_any(contact_id=contact_id, addresses=body.addresses)

        self._maybe_send_contact_creation_email(
            portal_access=bool(body.portal_access),
            email=email_norm,
            organization_name=org_name,
            password=created_password,
        )

        person_payload = self._build_person_payload(
            body=body,
            email=email_norm,
        )
        enrichment_targets = self._build_enrichment_targets(
            organization_id=org_id,
            contact_id=contact_id,
            person_payload=person_payload,
            created_new_company=created_new_company,
            company_id=company_id,
            company_name=company_name,
        )
        created_entities = self._build_created_entities(
            contact_id=contact_id,
            created_new_company=created_new_company,
            company_id=company_id,
        )

        return {
            "contact_id": contact_id,
            "company_id": company_id,
            "old_data": None,
            "new_data": contact_row,
            "enrichment_targets": enrichment_targets,
            "created_entities": created_entities,
        }

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
        """Generic helper to apply JSONB list operations: add, update, and/or remove.

        This mirrors `ClientService._apply_jsonb_list_changes` behavior so JSON list
        fields behave identically across table models.
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

        # Add operations (always generate a fresh UUID, same as v1)
        if hasattr(update_obj, "add") and update_obj.add:
            for item in update_obj.add:
                new_item = item.model_dump(exclude_none=True)
                new_item["id"] = str(uuid.uuid4())
                updated.append(new_item)

        payload[field_name] = updated
        current[field_name] = updated

    async def _apply_contact_addresses_delta(self, *, contact_id: str, addresses: Any) -> None:
        """Apply AddressesUpdate to `contact_addresses` table."""
        if addresses is None:
            return
        # remove
        if addresses.remove:
            await self.contacts_repo.delete_contact_addresses(
                contact_id=contact_id, address_ids=addresses.remove
            )
        # update
        if addresses.update:
            for item in addresses.update:
                await self.contacts_repo.update_contact_address(
                    contact_id=contact_id,
                    address_id=item.id,
                    update_data=item.model_dump(exclude={"id"}, exclude_none=True),
                )
        # add
        if addresses.add:
            await self.contacts_repo.create_contact_addresses(
                [
                    {
                        "contact_id": contact_id,
                        **address.model_dump(exclude_none=True),
                    }
                    for address in addresses.add
                ]
            )

    @staticmethod
    def _build_contact_scalar_update_data(*, body: UpdateContactRequest) -> dict[str, Any]:
        """Build the scalar update data for the contact."""
        update_data: dict[str, Any] = {}
        scalar_fields = (
            ("status", "status"),
            ("prefix", "prefix"),
            ("first_name", "first_name"),
            ("middle_name", "middle_name"),
            ("last_name", "last_name"),
            ("title", "title"),
            ("date_of_birth", "date_of_birth"),
            ("profile_photo_url", "profile_photo_url"),
            ("tags", "tags"),
        )
        for body_attr, column_name in scalar_fields:
            value = getattr(body, body_attr, None)
            if value is not None:
                update_data[column_name] = value

        if body.additional_data is not None:
            update_data["additional_data"] = body.additional_data

        return update_data

    async def _apply_phones_delta_and_validate(
        self,
        *,
        body: UpdateContactRequest,
        current: dict[str, Any],
        update_data: dict[str, Any],
    ) -> None:
        """Apply PhonesUpdate to `phones` table and validate that there is only one primary phone"""
        if body.phones is None:
            return

        await self._apply_jsonb_list_changes(
            body.phones,
            current=current,
            payload=update_data,
            field_name="phones",
            not_found_message_key="contacts.errors.phone_not_found",
        )
        phones_items = update_data.get("phones") or []
        primary_phone_count = sum(
            1
            for phone_item in phones_items
            if isinstance(phone_item, dict) and phone_item.get("is_primary") is True
        )
        if primary_phone_count > 1:
            raise ValidationException(
                message_key="contacts.errors.only_one_primary_phone",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    async def _apply_social_pages_delta(
        self,
        *,
        body: UpdateContactRequest,
        current: dict[str, Any],
        update_data: dict[str, Any],
    ) -> None:
        """Apply SocialPagesUpdate to `social_pages` table."""
        if body.social_pages is None:
            return

        await self._apply_jsonb_list_changes(
            body.social_pages,
            current=current,
            payload=update_data,
            field_name="social_pages",
            not_found_message_key="contacts.errors.social_page_not_found",
        )

    async def _merge_contact_custom_fields(
        self,
        *,
        body: UpdateContactRequest,
        current: dict[str, Any],
        update_data: dict[str, Any],
    ) -> None:
        """Merge body.custom_fields with current, validate, and set on payload (same as clients)."""
        if not (self.user_context and self.user_context.organization_id):
            return

        existing = parse_json_field(current.get("custom_fields"))
        merged_existing = existing if isinstance(existing, list) else []

        custom_field_service = CustomFieldService(
            db_connection=self.db_connection,
            user_context=self.user_context,
        )
        patch = body.custom_fields if body.custom_fields is not None else None
        merged = await custom_field_service.merge_for_update(
            patch, merged_existing, EntityType.CONTACT
        )
        if json.dumps(merged, sort_keys=True, default=str) != json.dumps(
            merged_existing,
            sort_keys=True,
            default=str,
        ):
            update_data["custom_fields"] = merged

    async def update_contact(
        self,
        *,
        contact_id: str,
        body: UpdateContactRequest,
    ) -> dict[str, Any]:
        """Patch a contact and optionally apply one company association change.

        This consolidates field updates + ADR section 3 association changes into one call
        so the API can expose a single PATCH endpoint.
        """
        org_id = self.user_context.organization_id

        current = await self.contacts_repo.get_contact_for_update(
            contact_id=contact_id,
            organization_id=org_id,
        )
        if not current:
            raise NotFoundException(
                message_key="contacts.errors.contact_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        update_data = self._build_contact_scalar_update_data(body=body)
        await self._apply_phones_delta_and_validate(
            body=body,
            current=current,
            update_data=update_data,
        )
        await self._apply_social_pages_delta(
            body=body,
            current=current,
            update_data=update_data,
        )
        await self._merge_contact_custom_fields(
            body=body,
            current=current,
            update_data=update_data,
        )

        updated_row: dict[str, Any] | None = None
        if update_data:
            updated_row = await self.contacts_repo.update_contact(
                contact_id=contact_id,
                organization_id=org_id,
                update_data=update_data,
            )

        await self._apply_contact_addresses_delta(
            contact_id=contact_id,
            addresses=body.addresses,
        )
        created_company_id: str | None = None
        companies_delta: dict[str, Any] | None = None
        if body.companies_update is not None:
            delta_result = await self.apply_companies_update_delta(
                contact_id=contact_id,
                delta=body.companies_update,
            )
            created_company_id = delta_result.get("created_company_id")
            companies_delta = {
                "affected_company_ids": delta_result.get("affected_company_ids") or [],
                "created_company_id": created_company_id,
            }
        update_response: dict[str, Any] = {
            "ok": True,
            "old_data": current,
            "new_data": updated_row or current,
            "created_company_id": created_company_id,
        }
        if companies_delta is not None:
            update_response["companies_delta"] = companies_delta
        return update_response

    async def apply_companies_update_delta(
        self,
        *,
        contact_id: str,
        delta: ContactCompaniesUpdate,
    ) -> dict[str, Any]:
        """Apply batch company association changes (add/remove/create) for a contact."""
        org_id = self.user_context.organization_id

        # Ensure contact exists and is in org.
        current = await self.contacts_repo.get_contact_for_update(
            contact_id=contact_id,
            organization_id=org_id,
        )
        if not current:
            raise NotFoundException(
                message_key="contacts.errors.contact_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        remove_ids = list(dict.fromkeys(delta.remove_associations or []))
        add_company_ids: list[str] = []
        set_primary_ids: list[str] = []
        unset_primary_ids: list[str] = []
        for item in delta.add_associations or []:
            add_company_ids.append(item.company_id)
            if item.is_primary:
                set_primary_ids.append(item.company_id)

        for item in delta.update_associations or []:
            if item.is_primary:
                set_primary_ids.append(item.company_id)
            else:
                unset_primary_ids.append(item.company_id)

        created_name = (
            delta.create_and_associate.name.strip()
            if delta.create_and_associate is not None
            else None
        )
        created_primary = (
            bool(delta.create_and_associate.is_primary)
            if delta.create_and_associate is not None
            else False
        )

        # Ensure memberships exist for companies where we're setting primary.
        if set_primary_ids:
            add_company_ids.extend(set_primary_ids)

        created_company_id = await self.cc_repo.apply_companies_update_delta(
            organization_id=org_id,
            contact_id=contact_id,
            remove_company_ids=remove_ids,
            add_company_ids=list(dict.fromkeys(add_company_ids)),
            set_primary_company_ids=list(dict.fromkeys(set_primary_ids)),
            unset_primary_company_ids=list(dict.fromkeys(unset_primary_ids)),
            create_company_name=created_name,
            create_is_primary=created_primary,
        )
        affected_company_ids: set[str] = set(remove_ids)
        affected_company_ids.update(
            add_item.company_id for add_item in (delta.add_associations or [])
        )
        affected_company_ids.update(
            update_item.company_id for update_item in (delta.update_associations or [])
        )
        if created_company_id:
            affected_company_ids.add(str(created_company_id))
        return {
            "ok": True,
            "created_company_id": created_company_id,
            "affected_company_ids": list(affected_company_ids),
        }

    async def trigger_enrichment(
        self,
        *,
        contact_id: str,
        organization_id: str,
        conn: asyncpg.Connection | None = None,
    ) -> None:
        """Trigger enrichment for an existing contact using current persisted data.

        Mirrors `ClientService.trigger_enrichment`: rebuilds the minimal enrichment payload
        from latest contact details and calls `ClientEnrichmentService.run_client_enrichment`.
        """
        details = await self.get_contact_details(contact_id=contact_id)
        if str(details.get("organization_id")) != str(organization_id):
            raise NotFoundException(
                message_key="contacts.errors.contact_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        addresses_payload: list[dict[str, Any]] = []
        for addr in details.get("addresses") or []:
            if isinstance(addr, dict) and (addr.get("country") or "").strip():
                addresses_payload.append({"country": (addr.get("country") or "").strip()})

        phones = details.get("phones") or []
        primary_phone: dict[str, Any] | None = None
        if isinstance(phones, list):
            for phone_entry in phones:
                if isinstance(phone_entry, dict) and phone_entry.get("is_primary") is True:
                    primary_phone = phone_entry
                    break
            if primary_phone is None and phones and isinstance(phones[0], dict):
                primary_phone = phones[0]

        payload_data: dict[str, Any] = {
            "first_name": details.get("first_name") or "",
            "middle_name": details.get("middle_name") or "",
            "last_name": details.get("last_name") or "",
            "email": details.get("email"),
            "addresses": addresses_payload,
        }
        if primary_phone:
            payload_data["phone_isd_code"] = primary_phone.get("phone_isd_code")
            payload_data["phone_number"] = primary_phone.get("phone_number")

        enrichment_service = ClientEnrichmentService.from_settings()
        await enrichment_service.run_client_enrichment(
            client_id=str(contact_id),
            organization_id=str(organization_id),
            client_type="person",
            payload_data=payload_data,
            conn=conn or self.db_connection,
            entity_table="contacts",
        )

    @staticmethod
    async def trigger_enrichment_background(contact_id: str, organization_id: str) -> None:
        """Trigger contact enrichment using a pool connection (BackgroundTasks-safe)."""
        pool = await get_pool()
        async with AcquireConnection(pool) as conn:
            # A minimal user_context is sufficient for get_contact_details scoping.
            service = ContactsService(
                db_connection=conn,
                user_context=UserContext(
                    user_id="system",
                    email="system@local",
                    organization_id=str(organization_id),
                ),
            )
            await service.trigger_enrichment(
                contact_id=str(contact_id),
                organization_id=str(organization_id),
                conn=conn,
            )

    @staticmethod
    def schedule_contact_update_background_tasks(
        *,
        background_tasks: BackgroundTasks,
        contact_id: str,
        organization_id: str,
        body: UpdateContactRequest,
        update_result: dict[str, Any] | None,
        update_event: dict[str, Any] | None,
        event_key: str,
        event_topics: list[Any],
        related_lifecycle_events: list[tuple[dict[str, Any], str]] | None = None,
    ) -> None:
        """Schedule background tasks after a contact update.

        This is the single delegation point the API should call after DB commit.
        """
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

        background_tasks.add_task(
            index_contacts_background,
            [(contact_id, organization_id)],
        )

        if body.companies_update is not None:
            meta = (update_result or {}).get("companies_delta") or {}
            affected_companies = meta.get("affected_company_ids") or []
            if affected_companies:
                background_tasks.add_task(
                    index_companies_background,
                    [
                        (str(company_identifier), organization_id)
                        for company_identifier in affected_companies
                    ],
                )

        # Trigger contact enrichment only when enrichment-relevant inputs changed.
        enrichment_input_fields = (
            "first_name",
            "middle_name",
            "last_name",
            "phones",
            "addresses",
        )
        enrichment_inputs_changed = any(
            getattr(body, field_name) is not None for field_name in enrichment_input_fields
        )
        if enrichment_inputs_changed:
            background_tasks.add_task(
                ContactsService.trigger_enrichment_background,
                contact_id,
                organization_id,
            )

        # If the update created a new company, also enrich that company (best-effort).
        created_company_id = (update_result or {}).get("created_company_id")
        created_company_name = None
        if (
            body.companies_update is not None
            and body.companies_update.create_and_associate is not None
            and body.companies_update.create_and_associate.name
        ):
            created_company_name = body.companies_update.create_and_associate.name.strip() or None

        if created_company_id and created_company_name:
            enrichment_service = ClientEnrichmentService.from_settings()
            background_tasks.add_task(
                enrichment_service.run_client_enrichment,
                client_id=str(created_company_id),
                organization_id=str(organization_id),
                client_type="company",
                payload_data={"name": created_company_name},
                entity_table="companies",
            )

    async def get_contact_details(self, *, contact_id: str) -> dict[str, Any]:
        """Return a contact with companies + addresses.

        The repository performs a single query that returns the contact row plus:
        - `companies`: json array with `company_id`, `name`, `industry`, `is_primary`
        - `addresses`: json array of contact addresses (primary first)
        """
        org_id = self.user_context.organization_id

        details = await self.contacts_repo.get_contact_details(
            contact_id=contact_id,
            organization_id=org_id,
        )
        if not details:
            raise NotFoundException(
                message_key="contacts.errors.contact_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        for uuid_field_name in ("id", "organization_id", "user_id"):
            field_value = details.get(uuid_field_name)
            if field_value is not None and not isinstance(field_value, str):
                details[uuid_field_name] = str(field_value)

        json_list_fields = (
            "phones",
            "custom_fields",
            "social_pages",
            "work_history",
            "educational_history",
        )
        for field_name in json_list_fields:
            raw_field_value = details.get(field_name)
            if isinstance(raw_field_value, str):
                details[field_name] = parse_json_field(raw_field_value) or []
            elif raw_field_value is None:
                details[field_name] = []

        additional_raw = details.get("additional_data")
        if isinstance(additional_raw, str):
            details["additional_data"] = parse_json_field(additional_raw) or {}
        elif additional_raw is None:
            details["additional_data"] = {}

        details["created_at"] = format_iso_datetime(details.get("created_at")) or ""
        details["updated_at"] = format_iso_datetime(details.get("updated_at")) or ""
        details["last_enriched_at"] = format_iso_datetime(details.get("last_enriched_at"))
        return details

    async def list_contacts(
        self,
        *,
        search: str | None,
        status: str | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        """List contacts (DB-backed) with pagination.

        This is the non-Typesense list endpoint; it is optimized for predictable ordering.
        """
        org_id = self.user_context.organization_id
        rows, total = await self.contacts_repo.list_contacts(
            organization_id=org_id,
            search=search,
            status=status,
            page=page,
            page_size=page_size,
        )
        for list_row in rows:
            list_row["created_at"] = format_iso_datetime(list_row.get("created_at")) or ""
            list_row["updated_at"] = format_iso_datetime(list_row.get("updated_at")) or ""
            company_names = list_row.get("company_names")
            if isinstance(company_names, str):
                list_row["company_names"] = parse_json_field(company_names) or []
            elif company_names is None:
                list_row["company_names"] = []
            phones = list_row.get("phones")
            if isinstance(phones, str):
                list_row["phones"] = parse_json_field(phones) or []
            elif phones is None:
                list_row["phones"] = []
        return {"items": rows, "total": total}

    async def soft_delete_contact(self, *, contact_id: str) -> dict[str, Any]:
        """Soft-delete a contact (sets status='deleted') and return old/new snapshots."""
        org_id = self.user_context.organization_id
        current = await self.contacts_repo.get_contact_for_update(
            contact_id=contact_id,
            organization_id=org_id,
        )
        if not current:
            raise NotFoundException(
                message_key="contacts.errors.contact_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        updated = await self.contacts_repo.soft_delete_contact(
            contact_id=contact_id, organization_id=org_id
        )
        return {"old_data": current, "new_data": updated}

    async def search_contacts(
        self,
        *,
        query: str,
        page: int,
        page_size: int,
        status: str | None,
    ) -> dict[str, Any]:
        """Search contacts via Typesense (contacts collection).

        Uses the same hybrid keyword + vector strategy as v1.
        """
        org_id = self.user_context.organization_id
        filters = [f"organization_id:={org_id}"]
        if status:
            filters.append(f"status:={status}")
        filter_by = " && ".join(filters)

        query = query.strip()
        params: dict[str, Any] = {
            "q": query,
            "per_page": page_size,
            "page": page,
            "filter_by": filter_by,
            "exclude_fields": "embedding",
        }
        if "@" in query:
            params.update(CONTACT_EMAIL_SEARCH_PARAMS)
        elif sum(c.isdigit() for c in query) >= 5:
            params.update(CONTACT_PHONE_SEARCH_PARAMS)
        else:
            params.update(CONTACT_SEARCH_PARAMS)

        embedding = await self.typesense.embed_query_text(query)
        if embedding is not None:
            vector = ",".join(map(str, embedding))
            distance_threshold = getattr(
                shared_settings.typesense, "vector_distance_threshold", None
            )
            if distance_threshold is not None and float(distance_threshold) > 0:
                params["vector_query"] = (
                    f"embedding:([{vector}], alpha:0.7, distance_threshold:{distance_threshold})"
                )
            else:
                params["vector_query"] = f"embedding:([{vector}], alpha:0.7)"

        search_response = await self.typesense.search(params)
        hits = search_response.get("hits") or []
        return {
            "items": self.typesense_hits_to_contact_summaries(hits),
            "total": search_response.get("found", 0),
        }

    @staticmethod
    def typesense_hits_to_contact_summaries(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert raw Typesense hits into list API summary items."""
        items: list[dict[str, Any]] = []
        for hit in hits:
            hit_document = hit.get("document") if isinstance(hit, dict) else None
            if not isinstance(hit_document, dict):
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

            summary_row = {
                "id": hit_document.get("id"),
                "organization_id": hit_document.get("organization_id"),
                "status": hit_document.get("status"),
                "first_name": hit_document.get("first_name"),
                "last_name": hit_document.get("last_name"),
                "title": hit_document.get("title"),
                "email": hit_document.get("email"),
                "profile_photo_url": hit_document.get("profile_photo_url"),
                "phones": hit_document.get("phones_display") or [],
                "company_names": hit_document.get("company_names") or [],
                "created_at": created_at_iso,
                "updated_at": updated_at_iso,
            }
            items.append(
                ContactSummaryResponse.model_validate(summary_row).model_dump(exclude_none=True)
            )
        return items
