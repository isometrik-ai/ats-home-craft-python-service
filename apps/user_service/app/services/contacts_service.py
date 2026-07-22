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

# pylint: disable=too-many-lines
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from email.utils import parseaddr
from typing import Any

import asyncpg
from asyncpg import UniqueViolationError
from fastapi import BackgroundTasks
from publicsuffix2 import get_sld
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
from apps.user_service.app.db.repositories.contacts_repository import (
    CONTACT_JSONB_COLUMNS,
)
from apps.user_service.app.db.repositories.organization_repository import (
    OrganizationRepository,
)
from apps.user_service.app.db.repositories.user_repository import UserRepository
from apps.user_service.app.schemas.common import NoteItem, Phone
from apps.user_service.app.schemas.contacts import (
    ContactCompanyUpdate,
    ContactSummaryResponse,
    CreateContactRequest,
    UpdateContactRequest,
)
from apps.user_service.app.schemas.enums import (
    ClientStatus,
    CompanyEventType,
    ContactEventType,
    ContactStatus,
    EntityType,
    IsometrikRole,
    KafkaTopics,
)
from apps.user_service.app.schemas.leads import (
    CreateLeadCompany,
    CreateLeadRequest,
    LeadContactCreate,
)
from apps.user_service.app.search.contact_typesense_schema import (
    CONTACT_EMAIL_SEARCH_PARAMS,
    CONTACT_PHONE_SEARCH_PARAMS,
    CONTACT_SEARCH_PARAMS,
)
from apps.user_service.app.services.client_enrichment_service import (
    ClientEnrichmentService,
    client_enrichment_enabled,
)
from apps.user_service.app.services.custom_field_service import CustomFieldService
from apps.user_service.app.services.event_service import EventService
from apps.user_service.app.services.lead_service import LeadService
from apps.user_service.app.services.typesense_index_service import (
    index_companies_background,
    index_contacts_background,
)
from apps.user_service.app.utils.common_utils import (
    UserContext,
    coerce_json_list,
    format_iso_datetime,
    generate_random_password,
    normalize_nested_addresses_for_audit,
    parse_json_any,
    parse_json_field,
    serialize_jsonb_param,
)
from apps.user_service.app.utils.email_utils import send_client_creation_email
from libs.shared_db.drivers.asyncpg_client import AcquireConnection, get_pool
from libs.shared_db.supabase_db.auth_repository import (
    create_user,
    get_user_by_id,
    update_phone,
)
from libs.shared_utils.custom_field_filtering import normalize_dropdown_filters_payload
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ServiceUnavailableException,
    ValidationException,
)
from libs.shared_utils.isometrik_service import (
    create_isometrik_user,
    get_isometrik_data_from_settings,
    login_to_isometrik,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode
from libs.shared_utils.typesense_service import TypesenseService

logger = get_logger("contacts_service")


def _serialize_jsonb_list(items: list[Any] | None) -> list[dict[str, Any]]:
    """Serialize pydantic models or dicts for JSONB list columns."""
    out: list[dict[str, Any]] = []
    for item in items or []:
        if hasattr(item, "model_dump"):
            out.append(item.model_dump(exclude_none=True))
        elif isinstance(item, dict):
            out.append(item)
    return out


def _normalize_phone_item(phone: Any) -> dict[str, Any]:
    """Normalize phone item."""
    if isinstance(phone, Phone):
        return phone.model_dump()
    if isinstance(phone, dict):
        return phone
    return {}


def _get_primary_phone_identity(phones: list[Any] | None) -> tuple[str, str] | None:
    """Return (phone_isd_code, phone_number) for the primary phone, if any."""
    for phone in phones or []:
        item = _normalize_phone_item(phone)
        if item.get("is_primary"):
            return (
                str(item.get("phone_isd_code") or ""),
                str(item.get("phone_number") or ""),
            )
    return None


def _primary_phone_changed(old_phones: Any, new_phones: list[Any]) -> bool:
    """True when the primary phone assignment or number changed."""
    old_primary = _get_primary_phone_identity(parse_json_any(old_phones, default=[]))
    new_primary = _get_primary_phone_identity(new_phones)
    return old_primary != new_primary


def _contact_phone_sync_info(
    *,
    current: dict[str, Any],
    phones: list[Phone],
) -> tuple[bool, Phone | None]:
    """Return whether auth phone should sync and the new primary phone."""
    sync_auth_phone = bool(current.get("user_id")) and _primary_phone_changed(
        current.get("phones"), phones
    )
    primary_phone = next(phone for phone in phones if phone.is_primary) if sync_auth_phone else None
    return sync_auth_phone, primary_phone


class ContactsService:
    """Business logic for contacts."""

    # pylint: disable=too-many-public-methods

    CLIENT_KAFKA_TOPICS: list[KafkaTopics] = [KafkaTopics.CRM_EVENTS]

    # Basic consumer/free email providers that should *not* be inferred as companies.
    # Keep this list focused; it is only used for the "no company selected/provided" fallback.
    _CONSUMER_EMAIL_DOMAINS: frozenset[str] = frozenset(
        {
            "gmail.com",
            "googlemail.com",
            "yahoo.com",
            "yahoo.co.in",
            "yahoo.co.uk",
            "hotmail.com",
            "outlook.com",
            "live.com",
            "msn.com",
            "icloud.com",
            "me.com",
            "mac.com",
            "aol.com",
            "proton.me",
            "protonmail.com",
            "yandex.com",
            "yandex.ru",
            "zoho.com",
            "zohomail.com",
            "mail.com",
            "gmx.com",
        }
    )

    @staticmethod
    def _extract_email_domain(email: str | None) -> str | None:
        """Extract domain portion from an email address.

        Accepts plain emails and "Name <email@domain>" formats.
        Returns a lowercase domain, or None.
        """
        raw = (email or "").strip()
        if not raw:
            return None
        _, addr = parseaddr(raw)
        addr = (addr or raw).strip()
        if "@" not in addr:
            return None
        domain = addr.split("@", 1)[1].strip().lower().strip(".")
        if not domain or "." not in domain:
            return None
        # Strip common trailing punctuation (copy/paste artifacts)
        domain = domain.rstrip(">,);")
        return domain or None

    @classmethod
    def _infer_company_name_from_email(cls, email: str | None) -> str | None:
        """Infer company name from email domain when the domain isn't a mail provider.

        Example: "rohit@appscrip.co" -> "appscrip"
        """
        email_domain = cls._extract_email_domain(email)
        if not email_domain:
            return None

        # Guard: consumer/free providers are not treated as companies.
        if email_domain in cls._CONSUMER_EMAIL_DOMAINS:
            return None

        # Some providers use subdomains; treat any subdomain under a known provider as consumer.
        if any(email_domain.endswith("." + provider) for provider in cls._CONSUMER_EMAIL_DOMAINS):
            return None

        registrable = (get_sld(email_domain) or "").strip().lower().strip(".")
        if not registrable or "." not in registrable:
            return None

        # company token is the left-most label of the registrable domain
        sld = registrable.split(".", 1)[0]
        company = (sld or "").strip().lower()
        if not company:
            return None
        # Keep only basic safe chars; company names in DB are free-form
        company = "".join(ch for ch in company if ch.isalnum() or ch in ("-", "_"))
        return company or None

    async def _apply_inferred_company_assoc_on_create(
        self,
        *,
        organization_id: str,
        email_norm: str,
        company_id: str | None,
        company_data: dict[str, Any] | None,
        company_addresses: list[dict[str, Any]] | None,
        make_primary: bool,
    ) -> tuple[str | None, dict[str, Any] | None, list[dict[str, Any]] | None, bool]:
        """Apply inferred company association for contact create when no company was provided.

        Rules:
        - Only runs when caller has no `company_id` and no `company_data`
        - Infers company name from email domain (non-consumer providers only)
        - Links existing company by case-insensitive name match, else creates a minimal company
        - Never adds company addresses for inferred companies
        """
        if company_id is not None or company_data:
            return company_id, company_data, company_addresses, make_primary

        inferred_name = self._infer_company_name_from_email(email_norm)
        if not inferred_name:
            return company_id, company_data, company_addresses, make_primary

        existing_by_name = await self.companies_repo.get_company_ids_by_names(
            organization_id=organization_id,
            names=[inferred_name],
        )
        existing_company_id = existing_by_name.get(inferred_name)
        if existing_company_id:
            return str(existing_company_id), None, None, False

        inferred_company_data: dict[str, Any] = {
            "status": ClientStatus.ACTIVE.value,
            "name": inferred_name,
            "industry": None,
            "profile_photo_url": None,
            "portal_access": False,
            "email": None,
            "phones": [],
            "tags": [],
            "websites": [],
            "billing_preferences": {},
            "social_pages": [],
            "target_market_segments": [],
            "current_tech_stack": [],
            "preferred_communication_channels": [],
            "industry_specific_terminologies": [],
            "description": None,
            "custom_fields": [],
            "additional_data": {},
        }
        return None, inferred_company_data, [], False

    @staticmethod
    def _created_entity_lifecycle_type_and_module(entity: dict) -> tuple[str, str]:
        """Map a ``created_entities`` row to Kafka event type and payload module."""
        if (entity.get("entity_table") or "").strip().lower() == "companies":
            return CompanyEventType.CREATED.value, "companies"
        return ContactEventType.CREATED.value, "contacts"

    @staticmethod
    async def create_lifecycle_events_for_created_entities(
        *,
        event_service: EventService,
        created_entities: list[dict] | None,
        organization_id: str,
        actor_user_id: str | None,
    ) -> list[tuple[dict, str]]:
        """Create lifecycle events for created entities."""
        created_events: list[tuple[dict, str]] = []
        for entity in created_entities or []:
            entity_id = entity.get("entity_id")
            if not entity_id:
                continue
            event_type, module = ContactsService._created_entity_lifecycle_type_and_module(entity)
            lifecycle_event = await event_service.create_lifecycle_event(
                event_type=event_type,
                aggregate_id=str(entity_id),
                organization_id=organization_id,
                actor_user_id=actor_user_id,
                payload={"module": module, "action": entity.get("action") or "create"},
                topics=ContactsService.CLIENT_KAFKA_TOPICS,
            )
            if lifecycle_event is not None:
                created_events.append((lifecycle_event, str(entity_id)))
        return created_events

    @staticmethod
    def schedule_lifecycle_event_publishes(
        *,
        background_tasks: BackgroundTasks,
        created_events: list[tuple[dict, str]],
    ) -> None:
        """Schedule lifecycle event publishes."""
        for lifecycle_event, event_publish_key in created_events:
            background_tasks.add_task(
                EventService.publish_event_background,
                event=lifecycle_event,
                key=event_publish_key,
                topics=ContactsService.CLIENT_KAFKA_TOPICS,
            )

    @staticmethod
    def schedule_typesense_indexing_for_created_entities(
        *,
        background_tasks: BackgroundTasks,
        created_entities: list[dict] | None,
        organization_id: str,
    ) -> None:
        """Schedule Typesense indexing for created entities."""
        for entity in created_entities or []:
            entity_identifier = entity.get("entity_id")
            if not entity_identifier:
                continue
            if entity.get("entity_table") == "contacts" and entity.get("action") == "create":
                background_tasks.add_task(
                    index_contacts_background,
                    [(str(entity_identifier), organization_id)],
                )
            elif (
                entity.get("entity_table") == "companies"
                and entity.get("action") == "create_company"
            ):
                background_tasks.add_task(
                    index_companies_background,
                    [(str(entity_identifier), organization_id)],
                )

    @staticmethod
    def schedule_enrichment(
        *,
        background_tasks: BackgroundTasks,
        enrichment_targets: list[dict] | None,
    ) -> None:
        """Schedule enrichment for created entities."""
        if not client_enrichment_enabled():
            return

        enrichment_service = ClientEnrichmentService.from_settings()
        for item in enrichment_targets or []:
            background_tasks.add_task(
                enrichment_service.run_client_enrichment,
                client_id=item["client_id"],
                organization_id=item["organization_id"],
                client_type=item["client_type"],
                payload_data=item.get("payload_data") or {},
                entity_table=item.get("entity_table") or "clients",
                skip_company_logo=bool(item.get("skip_company_logo")),
            )

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

    @staticmethod
    def _normalize_full_phone(phone_isd_code: str, phone_number: str) -> str:
        """Combine ISD + number and strip formatting characters for E.164 storage."""
        combined = f"{phone_isd_code or ''}{phone_number or ''}".strip()
        digits = re.sub(r"\D", "", combined)
        return digits if digits else ""

    @staticmethod
    def _isometrik_user_id_from_response(response: dict[str, Any] | None) -> str | None:
        """Extract Isometrik user id from create/login API payloads."""
        if not response:
            return None
        for key in ("userId", "user_id", "id"):
            value = response.get(key)
            if value:
                return str(value)
        return None

    async def _create_or_reuse_isometrik_user(
        self,
        *,
        contact_id: str,
        isometrik_payload: dict[str, Any],
        isometrik_credentials: dict[str, Any],
        existing_isometrik_user_id: str | None = None,
    ) -> str:
        """Create an Isometrik chat user or reuse one that already exists."""
        if existing_isometrik_user_id:
            return existing_isometrik_user_id

        try:
            isometrik_response = await create_isometrik_user(
                user=isometrik_payload,
                isometrik_credentials=isometrik_credentials,
            )
        except ConflictException:
            login_response = await login_to_isometrik(
                user_id=contact_id,
                isometrik_credentials=isometrik_credentials,
            )
            isometrik_user_id = self._isometrik_user_id_from_response(login_response)
            if not isometrik_user_id:
                raise ServiceUnavailableException(
                    message_key="contacts.errors.isometrik_user_creation_failed",
                    custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
                ) from None
            return isometrik_user_id

        isometrik_user_id = self._isometrik_user_id_from_response(isometrik_response)
        if not isometrik_user_id:
            raise ServiceUnavailableException(
                message_key="contacts.errors.isometrik_user_creation_failed",
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
            )
        return isometrik_user_id

    async def _provision_contact_auth_identity(
        self,
        *,
        contact_id: str,
        first_name: str | None,
        last_name: str | None,
        prefix: str | None,
        phone: str | None = None,
        email: str | None = None,
        password: str | None = None,
        existing_isometrik_user_id: str | None = None,
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

        email_norm = email.strip().lower() if email else None
        phone_norm = phone or None
        if not email_norm and not phone_norm:
            raise ValidationException(
                message_key="auth.errors.email_or_phone_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        org_id = self.user_context.organization_id
        organization = await self.org_repo.get_organization_by_id(org_id)
        if not organization:
            raise NotFoundException(
                message_key="organizations.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
                params={"organization_id": org_id},
            )

        user_repo = UserRepository(db_connection=self.db_connection)
        auth_matches = await user_repo.get_auth_users_by_phone_or_email(
            phone=phone_norm,
            email=email_norm,
        )
        matched_user_ids = {
            str(match["id"]) for match in auth_matches if match.get("id") is not None
        }
        if len(matched_user_ids) > 1:
            raise ConflictException(
                message_key="contacts.errors.primary_email_phone_auth_mismatch",
                custom_code=CustomStatusCode.CONFLICT,
            )

        created_password: str | None = None
        if len(matched_user_ids) == 1:
            user_id = next(iter(matched_user_ids))
        else:
            auth_password = password or generate_random_password()
            created_password = auth_password
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
                phone=phone_norm,
                password=auth_password,
                user_metadata=user_metadata,
                email_confirm=True,
            )
            if not auth_user or not auth_user.get("id"):
                raise ServiceUnavailableException(
                    message_key="contacts.errors.auth_user_creation_failed",
                    custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
                )
            user_id = str(auth_user["id"])

        org_settings = parse_json_field(organization.get("settings"))
        isometrik_credentials = get_isometrik_data_from_settings(org_settings)
        isometrik_payload: dict[str, Any] = {
            "user_id": contact_id,
            "organization_id": org_id,
            "role": IsometrikRole.CLIENT.value,
            "first_name": first_name,
            "last_name": last_name,
        }
        if email_norm:
            isometrik_payload["email"] = email_norm
        isometrik_user_id = await self._create_or_reuse_isometrik_user(
            contact_id=contact_id,
            isometrik_payload=isometrik_payload,
            isometrik_credentials=isometrik_credentials,
            existing_isometrik_user_id=existing_isometrik_user_id,
        )
        return user_id, isometrik_user_id, created_password

    async def _sync_contact_auth_phone(self, *, user_id: str, phone: Phone) -> None:
        """Update linked Supabase auth user when the contact primary phone changes."""
        if not self.supabase_client:
            raise ServiceUnavailableException(
                message_key="contacts.errors.auth_user_creation_failed",
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
            )

        full_phone = self._normalize_full_phone(phone.phone_isd_code, phone.phone_number)
        user_repo = UserRepository(db_connection=self.db_connection)
        existing_user = await user_repo.get_auth_user_by_phone(full_phone)
        if existing_user and str(existing_user["id"]) != user_id:
            raise ConflictException(
                message_key="clients.errors.phone_number_already_exists",
                custom_code=CustomStatusCode.CONFLICT,
            )

        auth_user = await get_user_by_id(self.supabase_client, user_id)
        if not auth_user:
            raise ServiceUnavailableException(
                message_key="contacts.errors.auth_user_creation_failed",
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
            )

        updated = await update_phone(
            self.supabase_client,
            user_id,
            auth_user.get("user_metadata") or {},
            phone.phone_number,
            phone.phone_isd_code,
        )
        if not updated:
            raise ServiceUnavailableException(
                message_key="contacts.errors.auth_user_creation_failed",
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
            )

    async def _prepare_optional_contact_company_association(
        self,
        *,
        body: CreateContactRequest,
    ) -> tuple[str | None, dict[str, Any] | None, list[dict[str, Any]] | None, bool]:
        """Extract company link/create inputs from the request.

        Mirrors CompaniesService behavior for nested contact create:
        - Link existing by `company_id`, OR
        - Create a company inline via full `CreateCompanyRequest` payload (association scope).

        Returns:
            (company_id, company_data, company_addresses, make_primary)
        """
        if not body.company_association:
            return None, None, None, False

        if body.company_association.add_association is not None:
            make_primary = bool(body.company_association.add_association.is_primary)
            company_id = (body.company_association.add_association.company_id or "").strip() or None
            return company_id, None, None, make_primary

        create_block = body.company_association.create_and_associate
        create_company = create_block.company if create_block is not None else None
        if create_company is None:
            raise ValidationException(
                message_key="contacts.errors.invalid_company_association",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        make_primary = bool(create_block.is_primary) if create_block is not None else False

        # Ignore nested lead/contact association blocks in the inline company payload.
        # Membership is handled at the contact operation layer.
        company_phones_payload = self._ensure_list_item_ids(
            [
                phone.model_dump(mode="json", exclude_none=True)
                for phone in (create_company.phones or [])
            ]
        )
        company_websites_payload = self._ensure_list_item_ids(
            [w.model_dump(mode="json", exclude_none=True) for w in (create_company.websites or [])]
        )
        company_social_pages_payload = self._ensure_list_item_ids(
            [
                p.model_dump(mode="json", exclude_none=True)
                for p in (create_company.social_pages or [])
            ]
        )

        validated_company_custom_fields: list[dict[str, Any]] = []
        if self.user_context and self.user_context.organization_id:
            custom_field_service = CustomFieldService(
                db_connection=self.db_connection,
                user_context=self.user_context,
            )
            validated_company_custom_fields = await custom_field_service.validate_for_create(
                create_company.custom_fields,
                EntityType.COMPANY,
            )

        jsonb_params = {
            "phones": serialize_jsonb_param(
                "phones", company_phones_payload, COMPANY_JSONB_COLUMNS
            ),
            "websites": serialize_jsonb_param(
                "websites", company_websites_payload, COMPANY_JSONB_COLUMNS
            ),
            "billing_preferences": serialize_jsonb_param(
                "billing_preferences",
                (
                    create_company.billing_preferences.model_dump(mode="json")
                    if create_company.billing_preferences
                    else {}
                ),
                COMPANY_JSONB_COLUMNS,
            ),
            "social_pages": serialize_jsonb_param(
                "social_pages", company_social_pages_payload, COMPANY_JSONB_COLUMNS
            ),
            "custom_fields": serialize_jsonb_param(
                "custom_fields", validated_company_custom_fields, COMPANY_JSONB_COLUMNS
            ),
            "additional_data": serialize_jsonb_param(
                "additional_data", (create_company.additional_data or {}), COMPANY_JSONB_COLUMNS
            ),
        }

        company_data: dict[str, Any] = {
            "status": ClientStatus.ACTIVE.value,
            "name": create_company.name.strip(),
            "industry": create_company.industry,
            "profile_photo_url": create_company.profile_photo_url,
            "portal_access": bool(create_company.portal_access),
            "email": (create_company.email or "").strip() or None,
            "phones": jsonb_params["phones"],
            "tags": create_company.tags,
            "websites": jsonb_params["websites"],
            "billing_preferences": jsonb_params["billing_preferences"],
            "social_pages": jsonb_params["social_pages"],
            "target_market_segments": create_company.target_market_segments,
            "current_tech_stack": create_company.current_tech_stack,
            "preferred_communication_channels": create_company.preferred_communication_channels,
            "industry_specific_terminologies": create_company.industry_specific_terminologies,
            "description": create_company.description,
            "custom_fields": jsonb_params["custom_fields"],
            "additional_data": jsonb_params["additional_data"],
        }
        company_addresses = (
            [a.model_dump(exclude_none=True) for a in (create_company.addresses or [])]
            if getattr(create_company, "addresses", None)
            else []
        )
        return None, company_data, company_addresses, make_primary

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

    @staticmethod
    def _phone_match_key(*, phone_number: str, phone_isd_code: str | None = None) -> str:
        """Return a digits-only key used to compare phone numbers."""
        combined = f"{phone_isd_code or ''}{phone_number}"
        return re.sub(r"\D", "", combined)

    async def add_phones_to_contact_if_missing(
        self,
        *,
        contact_id: str,
        phones: list[Phone],
    ) -> bool:
        """Append phones to an existing contact when they are not already present."""
        if not phones:
            return False

        org_id = self.user_context.organization_id
        existing_phones = await self.contacts_repo.get_contact_phones_for_update(
            contact_id=contact_id,
            organization_id=org_id,
        )
        if existing_phones is None:
            raise NotFoundException(
                message_key="contacts.errors.contact_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        existing_keys = {
            self._phone_match_key(
                phone_number=str(phone.get("phone_number") or ""),
                phone_isd_code=phone.get("phone_isd_code"),
            )
            for phone in existing_phones
            if isinstance(phone, dict) and str(phone.get("phone_number") or "").strip()
        }

        has_primary = any(
            isinstance(phone, dict) and phone.get("is_primary") is True for phone in existing_phones
        )

        phones_to_add: list[dict[str, Any]] = []
        for phone in phones:
            phone_number = (phone.phone_number or "").strip()
            if not phone_number:
                continue
            phone_key = self._phone_match_key(
                phone_number=phone_number,
                phone_isd_code=phone.phone_isd_code,
            )
            if phone_key in existing_keys:
                continue
            existing_keys.add(phone_key)
            is_primary = bool(phone.is_primary and not has_primary)
            if is_primary:
                has_primary = True
            new_phone = phone.model_dump(mode="json", exclude_none=True)
            new_phone["id"] = str(uuid.uuid4())
            new_phone["is_primary"] = is_primary
            phones_to_add.append(new_phone)

        if not phones_to_add:
            return False

        merged_phones = [dict(phone) for phone in existing_phones if isinstance(phone, dict)]
        merged_phones.extend(phones_to_add)

        primary_phone_count = sum(
            1 for phone_item in merged_phones if phone_item.get("is_primary") is True
        )
        if primary_phone_count > 1:
            raise ValidationException(
                message_key="contacts.errors.only_one_primary_phone",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        await self.contacts_repo.update_contact(
            contact_id=contact_id,
            organization_id=org_id,
            update_data={
                "phones": serialize_jsonb_param("phones", merged_phones, CONTACT_JSONB_COLUMNS),
            },
        )
        return True

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

    async def _provision_identity(
        self,
        *,
        contact_id: str,
        email: str | None,
        first_name: str | None,
        last_name: str | None,
        prefix: str | None,
        phone: str | None = None,
        password: str | None = None,
    ) -> tuple[str, str, str | None]:
        """Provision (or reuse) auth identity for a contact.

        Returns:
            ``(user_id, isometrik_user_id, password_if_created)``.
        """
        return await self._provision_contact_auth_identity(
            contact_id=contact_id,
            phone=phone,
            email=email,
            first_name=first_name,
            last_name=last_name,
            prefix=prefix,
            password=password,
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
        skip_company_logo: bool = False,
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
                    "skip_company_logo": skip_company_logo,
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

    async def _create_contact_with_company_link_or_conflict(
        self,
        *,
        organization_id: str,
        user_id: str,
        isometrik_user_id: str | None,
        contact_payload: dict[str, Any],
        company_id: str | None,
        company_data: dict[str, Any] | None,
        company_addresses: list[dict[str, Any]] | None,
        make_primary: bool,
    ) -> dict[str, Any]:
        """Create contact + company link with conflict mapping for known uniqueness rules."""
        try:
            return await self.contacts_repo.create_contact_with_optional_company_link(
                organization_id=organization_id,
                contact_data={
                    **contact_payload,
                    "user_id": user_id,
                    "isometrik_user_id": isometrik_user_id,
                },
                company_id=company_id,
                company_data=company_data,
                company_addresses=company_addresses,
                make_primary=make_primary,
            )
        except UniqueViolationError as exc:
            constraint = getattr(exc, "constraint_name", None)
            if constraint == "uq_contacts_user_org":
                raise ConflictException(
                    message_key="contacts.errors.contact_user_already_exists",
                    custom_code=CustomStatusCode.CONFLICT,
                    params={
                        "organization_id": organization_id,
                        "user_id": user_id,
                    },
                ) from exc
            if constraint == "uq_contacts_org_external_contact_id":
                raise ConflictException(
                    message_key="contacts.errors.external_contact_id_already_exists",
                    custom_code=CustomStatusCode.CONFLICT,
                ) from exc
            raise

    async def create_contact(
        self,
        body: CreateContactRequest,
        *,
        provision_auth: bool = True,
    ) -> dict[str, Any]:
        """Create a contact with optional company link (and optional primary designation)."""
        # pylint: disable=too-complex
        if getattr(body, "contact_type", None) is not None:
            return await self._create_property_contact(body, provision_auth=provision_auth)

        org_id = self.user_context.organization_id
        (
            company_id,
            company_data,
            company_addresses,
            make_primary,
        ) = await self._prepare_optional_contact_company_association(body=body)

        # Feature: on contact creation only, when no company is selected/provided,
        # infer a company name from email domain (excluding consumer mail providers).
        email_norm = (body.email or "").strip().lower()
        (
            company_id,
            company_data,
            company_addresses,
            make_primary,
        ) = await self._apply_inferred_company_assoc_on_create(
            organization_id=org_id,
            email_norm=email_norm,
            company_id=company_id,
            company_data=company_data,
            company_addresses=company_addresses,
            make_primary=make_primary,
        )

        created_new_company = bool(company_data)
        company_name = (company_data or {}).get("name") if company_data else None

        # Align with legacy behavior: prevent org-level duplicate emails when email is provided.
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

        contact_id = str(uuid.uuid4())
        user_id, isometrik_user_id, created_password = await self._provision_identity(
            contact_id=contact_id,
            email=email_norm,
            first_name=body.first_name,
            last_name=body.last_name,
            prefix=body.prefix,
        )

        list_payload_inputs: dict[str, list[dict[str, Any]]] = {
            "phones": [phone.model_dump(mode="json", exclude_none=True) for phone in body.phones],
            "social_pages": [
                page.model_dump(mode="json", exclude_none=True) for page in body.social_pages
            ],
            "websites": [
                website.model_dump(mode="json", exclude_none=True) for website in body.websites
            ],
        }
        list_payloads: dict[str, list[dict[str, Any]]] = {}
        for field_name, items in list_payload_inputs.items():
            list_payloads[field_name] = self._ensure_list_item_ids(items)

        phones_payload = list_payloads["phones"]
        social_pages_payload = list_payloads["social_pages"]
        websites_payload = list_payloads["websites"]
        notes_payload = [n.model_dump() for n in (getattr(body, "notes", None) or [])]

        # Persist intake_stage on the contact when provided (for downstream indexing/filters).
        additional_data_payload = dict(body.additional_data or {})
        lead_payload = getattr(body, "lead", None)
        if lead_payload is not None and getattr(lead_payload, "intake_stage", None) is not None:
            intake_stage = (getattr(lead_payload, "intake_stage", None) or "").strip()
            if intake_stage:
                additional_data_payload["intake_stage"] = intake_stage
        if websites_payload:
            additional_data_payload["websites"] = websites_payload

        # Prepare JSONB params once at the service layer
        jsonb_inputs: dict[str, Any] = {
            "phones": phones_payload,
            "notes": notes_payload,
            "custom_fields": validated_custom_fields,
            "additional_data": additional_data_payload,
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
        notes_jsonb = jsonb_params["notes"]
        custom_fields_jsonb = jsonb_params["custom_fields"]
        additional_data_jsonb = jsonb_params["additional_data"]
        social_pages_jsonb = jsonb_params["social_pages"]

        created = await self._create_contact_with_company_link_or_conflict(
            organization_id=org_id,
            user_id=user_id,
            isometrik_user_id=isometrik_user_id,
            contact_payload={
                "id": contact_id,
                "status": ClientStatus.ACTIVE.value,
                "prefix": body.prefix,
                "first_name": body.first_name,
                "middle_name": body.middle_name,
                "last_name": body.last_name,
                "title": body.title,
                "date_of_birth": body.date_of_birth,
                "profile_photo_url": body.profile_photo_url,
                "external_contact_id": self._normalize_external_contact_id(
                    body.external_contact_id
                ),
                "email": email_norm,
                "phones": phones_jsonb,
                "tags": body.tags,
                "notes": notes_jsonb,
                "custom_fields": custom_fields_jsonb,
                "additional_data": additional_data_jsonb,
                "social_pages": social_pages_jsonb,
            },
            company_id=company_id,
            company_data=company_data,
            company_addresses=company_addresses,
            make_primary=make_primary,
        )
        contact_id = created.get("contact_id") or contact_id
        company_id = created.get("company_id")
        contact_row = created.get("contact")
        if not contact_id:
            raise ValidationException(
                message_key="contacts.errors.contact_creation_failed",
                custom_code=CustomStatusCode.SERVICE_UNAVAILABLE,
            )

        await self._create_addresses_if_any(contact_id=contact_id, addresses=body.addresses)

        # If an existing company id was requested but not found, raise a clean NotFound.
        if (
            body.company_association
            and body.company_association.add_association is not None
            and body.company_association.add_association.company_id
            and not company_id
        ):
            raise NotFoundException(
                message_key="contacts.errors.company_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Optional lead creation + association (contact + optional company).
        lead_payload = getattr(body, "lead", None)
        if lead_payload is not None:
            full_name = " ".join(
                [
                    part
                    for part in [
                        (body.first_name or "").strip(),
                        (body.last_name or "").strip(),
                    ]
                    if part
                ]
            ).strip()
            lead_name = full_name or (email_norm or "").strip()
            lead_service = LeadService(
                user_context=self.user_context,
                db_connection=self.db_connection,
            )
            company_tuple = (
                CreateLeadCompany(company_id=str(company_id)) if company_id is not None else None
            )
            contacts_list = [LeadContactCreate(contact_id=str(contact_id))] if contact_id else None
            created_lead = await lead_service.create_lead(
                CreateLeadRequest(
                    name=lead_name,
                    stage_id=lead_payload.stage_id,
                    lead_source=(getattr(lead_payload, "intake_stage", None) or None),
                    lead_score=(getattr(lead_payload, "lead_score", None) or None),
                    company=company_tuple,
                    contacts=contacts_list,
                )
            )
            created_lead_id = (
                str(created_lead["id"])
                if isinstance(created_lead, dict) and created_lead.get("id") is not None
                else None
            )
        else:
            created_lead_id = None

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
        company_photo = (company_data or {}).get("profile_photo_url") if company_data else None
        skip_logo_for_company = isinstance(company_photo, str) and bool(company_photo.strip())
        enrichment_targets = self._build_enrichment_targets(
            organization_id=org_id,
            contact_id=contact_id,
            person_payload=person_payload,
            created_new_company=created_new_company,
            company_id=company_id,
            company_name=company_name,
            skip_company_logo=skip_logo_for_company,
        )
        created_entities = self._build_created_entities(
            contact_id=contact_id,
            created_new_company=created_new_company,
            company_id=company_id,
        )

        return {
            "contact_id": contact_id,
            "company_id": company_id,
            "created_lead_id": created_lead_id,
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

    async def _apply_contact_addresses_delta(
        self,
        *,
        contact_id: str,
        addresses: Any,
        existing_addresses: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        """Apply AddressesUpdate to `contact_addresses` table and return updated address snapshot.

        This avoids an additional DB read by deriving the post-update address list from:
        - the pre-update address list already loaded for audit (`existing_addresses`)
        - the rows returned by UPDATE/INSERT queries executed as part of the delta
        """
        current_list = existing_addresses if isinstance(existing_addresses, list) else []
        result_list: list[dict[str, Any]] = [
            dict(addr) for addr in current_list if isinstance(addr, dict)
        ]

        if addresses is None:
            return result_list

        result_list = await self._apply_contact_addresses_remove(
            contact_id=contact_id,
            addresses=addresses,
            result_list=result_list,
        )
        result_list = await self._apply_contact_addresses_update(
            contact_id=contact_id,
            addresses=addresses,
            result_list=result_list,
        )
        result_list = await self._apply_contact_addresses_add(
            contact_id=contact_id,
            addresses=addresses,
            result_list=result_list,
        )

        # Keep ordering consistent with `get_contact_details`: primary first then created_at.
        result_list.sort(key=self._contact_address_sort_key)
        return result_list

    @staticmethod
    def _contact_address_sort_key(address_row: dict[str, Any]) -> tuple[int, str]:
        """Sort key for address rows (primary first, then created_at ascending)."""
        is_primary = 0 if address_row.get("is_primary") else 1
        created_at = format_iso_datetime(address_row.get("created_at")) or ""
        return (is_primary, created_at)

    async def _apply_contact_addresses_remove(
        self,
        *,
        contact_id: str,
        addresses: Any,
        result_list: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Apply address removals and return updated in-memory snapshot."""
        if not getattr(addresses, "remove", None):
            return result_list
        remove_set = {str(x) for x in (addresses.remove or [])}
        if not remove_set:
            return result_list
        await self.contacts_repo.delete_contact_addresses(
            contact_id=contact_id,
            address_ids=list(remove_set),
        )
        return [row for row in result_list if str(row.get("id")) not in remove_set]

    async def _apply_contact_addresses_update(
        self,
        *,
        contact_id: str,
        addresses: Any,
        result_list: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Apply address updates (RETURNING rows) to the in-memory snapshot."""
        if not getattr(addresses, "update", None):
            return result_list

        for item in addresses.update or []:
            updated_row = await self.contacts_repo.update_contact_address(
                contact_id=contact_id,
                address_id=item.id,
                update_data=item.model_dump(exclude={"id"}, exclude_none=True),
            )
            if not updated_row:
                continue
            updated_id = str(updated_row.get("id"))
            for idx, existing in enumerate(result_list):
                if str(existing.get("id")) == updated_id:
                    result_list[idx] = dict(updated_row)
                    break
            else:
                result_list.append(dict(updated_row))

        return result_list

    async def _apply_contact_addresses_add(
        self,
        *,
        contact_id: str,
        addresses: Any,
        result_list: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Apply address inserts (RETURNING rows) to the in-memory snapshot."""
        if not getattr(addresses, "add", None):
            return result_list

        inserted = await self.contacts_repo.create_contact_addresses(
            [
                {"contact_id": contact_id, **addr.model_dump(exclude_none=True)}
                for addr in addresses.add
            ]
        )
        for row in inserted or []:
            if isinstance(row, dict):
                result_list.append(dict(row))
        return result_list

    @staticmethod
    def _normalize_contact_audit_snapshot(row: dict[str, Any] | None) -> dict[str, Any] | None:
        """Normalize contact audit snapshots to be JSONB-safe and DB-shaped."""
        if row is None:
            return None

        normalized: dict[str, Any] = dict(row)

        ContactsService._normalize_contact_jsonb_columns(normalized)
        ContactsService._normalize_contact_scalar_arrays(normalized)
        ContactsService._normalize_contact_derived_arrays(normalized)
        ContactsService._normalize_contact_ids(normalized)
        ContactsService._normalize_contact_timestamps(normalized)
        normalize_nested_addresses_for_audit(normalized, parent_fk_field="contact_id")

        return normalized

    @staticmethod
    def _normalize_contact_sales_intelligence(details: dict[str, Any]) -> None:
        """Parse sales_intelligence when asyncpg returns JSONB as a string."""
        sales_intel = details.get("sales_intelligence")
        if isinstance(sales_intel, str):
            parsed = parse_json_field(sales_intel)
            details["sales_intelligence"] = parsed if isinstance(parsed, dict) else None
        elif sales_intel is not None and not isinstance(sales_intel, dict):
            details["sales_intelligence"] = None

    @staticmethod
    def _normalize_contact_jsonb_columns(normalized: dict[str, Any]) -> None:
        """Normalize JSONB contact columns to Python objects (dict/list) where possible."""
        for field_name in CONTACT_JSONB_COLUMNS:
            if field_name not in normalized:
                continue

            # Some JSONB columns are objects (dict) while others are arrays (list).
            # Keep types stable for audit diffs and downstream UI rendering.
            if field_name == "additional_data":
                parsed = parse_json_field(normalized.get(field_name))
                normalized[field_name] = parsed if isinstance(parsed, dict) else {}
                continue

            if field_name == "sales_intelligence":
                ContactsService._normalize_contact_sales_intelligence(normalized)
                continue

            if field_name == "communication_preferences":
                raw_value = normalized.get(field_name)
                if isinstance(raw_value, dict):
                    continue
                if isinstance(raw_value, str):
                    parsed = parse_json_field(raw_value)
                    normalized[field_name] = parsed if isinstance(parsed, dict) else {}
                elif raw_value is None:
                    normalized[field_name] = {}
                continue

            normalized[field_name] = coerce_json_list(normalized.get(field_name))

    @staticmethod
    def _normalize_contact_scalar_arrays(normalized: dict[str, Any]) -> None:
        """Normalize scalar array-ish fields (e.g. tags/skills) when present."""
        for field_name in ("tags", "skills"):
            if field_name not in normalized:
                continue
            raw = normalized.get(field_name)
            parsed = parse_json_any(raw, raw)
            if isinstance(parsed, list):
                normalized[field_name] = parsed

    @staticmethod
    def _normalize_contact_derived_arrays(normalized: dict[str, Any]) -> None:
        """Normalize derived arrays returned by details queries (companies/leads/addresses)."""
        for field_name in ("companies", "leads", "addresses"):
            if field_name in normalized:
                normalized[field_name] = coerce_json_list(normalized.get(field_name))

    @staticmethod
    def _normalize_contact_ids(normalized: dict[str, Any]) -> None:
        """Normalize id fields to strings for JSON/audit stability."""
        for id_field in ("id", "organization_id", "user_id", "isometrik_user_id"):
            if id_field in normalized and normalized.get(id_field) is not None:
                normalized[id_field] = str(normalized[id_field])

    @staticmethod
    def _normalize_contact_timestamps(normalized: dict[str, Any]) -> None:
        """Normalize datetime-like fields to ISO strings when present."""
        for dt_field in ("created_at", "updated_at", "date_of_birth", "last_enriched_at"):
            if dt_field in normalized and normalized.get(dt_field) is not None:
                normalized[dt_field] = format_iso_datetime(normalized.get(dt_field))

    @staticmethod
    def _build_contact_scalar_update_data(*, body: UpdateContactRequest) -> dict[str, Any]:
        """Build the scalar update data for the contact."""
        # pylint: disable=too-complex
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
            ("description", "description"),
        )
        for body_attr, column_name in scalar_fields:
            value = getattr(body, body_attr, None)
            if value is not None:
                update_data[column_name] = value.value if hasattr(value, "value") else value

        if body.contact_type is not None:
            update_data["contact_type"] = body.contact_type.value

        if "portal_access" in body.model_fields_set:
            update_data["portal_access"] = body.portal_access

        if body.additional_data is not None:
            update_data["additional_data"] = body.additional_data

        if body.sales_intelligence is not None:
            update_data["sales_intelligence"] = body.sales_intelligence

        if body.gender is not None:
            update_data["gender"] = body.gender.value

        if body.blood_group is not None:
            update_data["blood_group"] = body.blood_group.value

        if body.communication_preferences is not None:
            update_data["communication_preferences"] = body.communication_preferences.model_dump()

        if body.skills is not None:
            update_data["skills"] = body.skills

        if "notes" in getattr(body, "model_fields_set", set()):
            notes_list = body.notes
            update_data["notes"] = (
                [n.model_dump() for n in (notes_list or [])] if notes_list is not None else []
            )

        return update_data

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
        # pylint: disable=too-complex
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

        sync_auth_phone = False
        primary_phone: Phone | None = None
        if body.phones is not None:
            update_data["phones"] = _serialize_jsonb_list(body.phones)
            primary_phone_count = sum(
                1 for phone_item in update_data["phones"] if phone_item.get("is_primary") is True
            )
            if primary_phone_count > 1:
                raise ValidationException(
                    message_key="contacts.errors.only_one_primary_phone",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            sync_auth_phone, primary_phone = _contact_phone_sync_info(
                current=current,
                phones=body.phones,
            )

        if body.emails is not None:
            update_data["emails"] = _serialize_jsonb_list(body.emails)

        jsonb_list_fields = (
            ("social_pages", "contacts.errors.social_page_not_found"),
            ("work_history", "contacts.errors.work_history_item_not_found"),
            ("educational_history", "contacts.errors.educational_history_item_not_found"),
        )
        for field_name, not_found_message_key in jsonb_list_fields:
            value = getattr(body, field_name, None)
            if value is not None:
                await self._apply_jsonb_list_changes(
                    value,
                    current=current,
                    payload=update_data,
                    field_name=field_name,
                    not_found_message_key=not_found_message_key,
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
            if not updated_row:
                raise NotFoundException(
                    message_key="contacts.errors.contact_not_found",
                    custom_code=CustomStatusCode.NOT_FOUND,
                )

        # Derive the post-update snapshot in-memory (no extra DB read).
        new_snapshot: dict[str, Any] = dict(current)
        if isinstance(updated_row, dict):
            new_snapshot.update(updated_row)

        if sync_auth_phone and primary_phone is not None and updated_row is not None:
            await self._sync_contact_auth_phone(
                user_id=str(current["user_id"]),
                phone=primary_phone,
            )

        updated_addresses = await self._apply_contact_addresses_delta(
            contact_id=contact_id,
            addresses=body.addresses,
            existing_addresses=coerce_json_list(current.get("addresses")),
        )
        new_snapshot["addresses"] = updated_addresses

        created_company_id: str | None = None
        companies_delta: dict[str, Any] | None = None
        if body.company_association is not None:
            delta_result = await self.apply_companies_update_delta(
                contact_id=contact_id,
                delta=body.company_association,
            )
            created_company_id = delta_result.get("created_company_id")
            companies_delta = {
                "affected_company_ids": delta_result.get("affected_company_ids") or [],
                "created_company_id": created_company_id,
            }
            # Keep audit snapshots accurate without extra DB reads:
            # the delta application query returns the post-update `companies[]` snapshot.
            new_snapshot["companies"] = coerce_json_list(delta_result.get("companies"))
        update_response: dict[str, Any] = {
            "ok": True,
            "old_data": self._normalize_contact_audit_snapshot(current),
            "new_data": self._normalize_contact_audit_snapshot(new_snapshot),
            "created_company_id": created_company_id,
        }
        if companies_delta is not None:
            update_response["companies_delta"] = companies_delta
        return update_response

    async def apply_companies_update_delta(
        self,
        *,
        contact_id: str,
        delta: ContactCompanyUpdate,
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

        repo_result = await self.cc_repo.apply_companies_update_delta(
            organization_id=org_id,
            contact_id=contact_id,
            remove_company_ids=remove_ids,
            add_company_ids=list(dict.fromkeys(add_company_ids)),
            set_primary_company_ids=list(dict.fromkeys(set_primary_ids)),
            unset_primary_company_ids=list(dict.fromkeys(unset_primary_ids)),
            create_company_name=created_name,
            create_is_primary=created_primary,
        )
        created_company_id = (
            repo_result.get("created_company_id") if isinstance(repo_result, dict) else None
        )
        # Always fetch the final, authoritative snapshot from DB when changes were requested.
        # This avoids returning any stale in-memory/CTE snapshot.
        has_company_changes = bool(
            remove_ids or add_company_ids or set_primary_ids or unset_primary_ids or created_name
        )
        companies_snapshot = (
            await self.cc_repo.get_contact_companies_snapshot(
                organization_id=org_id,
                contact_id=contact_id,
            )
            if has_company_changes
            else (repo_result.get("companies") if isinstance(repo_result, dict) else None)
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
            "companies": companies_snapshot,
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

        if body.company_association is not None:
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
            body.company_association is not None
            and body.company_association.create_and_associate is not None
            and body.company_association.create_and_associate.name
        ):
            created_company_name = (
                body.company_association.create_and_associate.name.strip() or None
            )

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
        return self._normalize_contact_details(details)

    async def get_contact_details_by_phone(self, *, phone_number: str) -> dict[str, Any]:
        """Return the earliest-created contact (by created_at) matching a phone number.

        Response payload is normalized exactly like `get_contact_details()`.
        """
        org_id = self.user_context.organization_id
        details = await self.contacts_repo.get_contact_details_by_phone(
            organization_id=org_id,
            phone_number=phone_number,
        )
        if not details:
            raise NotFoundException(
                message_key="contacts.errors.contact_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return self._normalize_contact_details(details)

    async def get_contact_details_by_email(self, *, email: str) -> dict[str, Any]:
        """Return contact details for the contact matching email (case-insensitive).

        Response payload is normalized exactly like `get_contact_details()`.
        """
        org_id = self.user_context.organization_id
        contact_id = await self.contacts_repo.get_contact_id_by_email(
            organization_id=org_id,
            email=email,
        )
        if not contact_id:
            raise NotFoundException(
                message_key="contacts.errors.contact_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return await self.get_contact_details(contact_id=contact_id)

    @staticmethod
    def _stringify_contact_detail_uuids(details: dict[str, Any]) -> None:
        """Normalize UUID columns on a contact detail dict to strings for JSON responses."""
        for uuid_field_name in ("id", "organization_id", "user_id"):
            field_value = details.get(uuid_field_name)
            if field_value is not None and not isinstance(field_value, str):
                details[uuid_field_name] = str(field_value)

    @staticmethod
    def _coerce_contact_detail_json_lists(details: dict[str, Any]) -> None:
        """Coerce JSON-backed list fields on contact details to Python lists."""
        json_list_fields = (
            "phones",
            "emails",
            "documents",
            "websites",
            "notes",
            "custom_fields",
            "social_pages",
            "work_history",
            "educational_history",
            "leads",
        )
        for field_name in json_list_fields:
            raw_field_value = details.get(field_name)
            if field_name == "notes":
                details[field_name] = ContactsService._normalize_notes_for_detail(raw_field_value)
            elif field_name in ("work_history", "educational_history"):
                details[field_name] = coerce_json_list(raw_field_value)
            elif isinstance(raw_field_value, list):
                continue
            elif isinstance(raw_field_value, str):
                details[field_name] = coerce_json_list(raw_field_value)
            elif raw_field_value is None:
                details[field_name] = []

    @staticmethod
    def _normalize_contact_detail_communication_preferences(details: dict[str, Any]) -> None:
        """Ensure ``communication_preferences`` is a dict after optional string JSON parsing."""
        raw_value = details.get("communication_preferences")
        if isinstance(raw_value, dict):
            return
        if isinstance(raw_value, str):
            parsed = parse_json_field(raw_value)
            details["communication_preferences"] = parsed if isinstance(parsed, dict) else {}
        elif raw_value is None:
            details["communication_preferences"] = {}

    @staticmethod
    def _normalize_contact_detail_scalar_arrays(details: dict[str, Any]) -> None:
        """Normalize scalar array-ish fields (e.g. tags/skills) on contact details."""
        for scalar_array_field in ("tags", "skills"):
            raw_value = details.get(scalar_array_field)
            parsed = parse_json_any(raw_value, raw_value)
            details[scalar_array_field] = parsed if isinstance(parsed, list) else []

    @staticmethod
    def _normalize_contact_detail_additional_data(details: dict[str, Any]) -> None:
        """Ensure ``additional_data`` is a dict after optional string JSON parsing."""
        additional_raw = details.get("additional_data")
        if isinstance(additional_raw, str):
            details["additional_data"] = parse_json_field(additional_raw) or {}
        elif additional_raw is None:
            details["additional_data"] = {}

    @staticmethod
    def _normalize_contact_detail_timestamps(details: dict[str, Any]) -> None:
        """Normalize datetime-like fields on contact details to ISO strings."""
        details["created_at"] = format_iso_datetime(details.get("created_at")) or ""
        details["updated_at"] = format_iso_datetime(details.get("updated_at")) or ""
        details["last_enriched_at"] = format_iso_datetime(details.get("last_enriched_at"))

    def _normalize_contact_details(self, details: dict[str, Any]) -> dict[str, Any]:
        """Normalize DB contact details to the API response shape."""
        self._stringify_contact_detail_uuids(details)
        self._coerce_contact_detail_json_lists(details)
        self._normalize_contact_detail_scalar_arrays(details)
        self._normalize_contact_detail_additional_data(details)
        self._normalize_contact_detail_communication_preferences(details)
        self._normalize_contact_sales_intelligence(details)
        self._normalize_contact_detail_timestamps(details)
        return details

    @staticmethod
    def _normalize_notes_for_detail(raw_notes: Any) -> list[dict[str, str]]:
        """Normalize notes field to a list of strict {title, content} dicts."""
        items = coerce_json_list(raw_notes)
        out: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or "").strip()
            content = (item.get("content") or "").strip()
            if not title or not content:
                continue
            note = NoteItem(title=title, content=content)
            out.append(note.model_dump())
        return out

    @staticmethod
    def _normalize_contact_list_row(list_row: dict[str, Any]) -> None:
        """Normalize and coerce DB list row fields to API response shape."""
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

        tags = list_row.get("tags")
        if tags is None:
            list_row["tags"] = []
        elif not isinstance(tags, list):
            list_row["tags"] = list(tags)

    async def list_contacts(
        self,
        *,
        search: str | None,
        status: str | None,
        contact_type: str | None = None,
        dropdown_filters: Any = None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        """List contacts (DB-backed) with pagination.

        This is the non-Typesense list endpoint; it is optimized for predictable ordering.
        """
        org_id = self.user_context.organization_id
        parsed_filters = normalize_dropdown_filters_payload(dropdown_filters)

        cfs = CustomFieldService(
            db_connection=self.db_connection,
            user_context=self.user_context,
        )
        await cfs.validate_dropdown_filters_for_entity(EntityType.CONTACT, parsed_filters)

        rows, total = await self.contacts_repo.list_contacts(
            organization_id=org_id,
            search=search,
            status=status,
            contact_type=contact_type,
            dropdown_filters=parsed_filters,
            page=page,
            page_size=page_size,
        )
        for list_row in rows:
            self._normalize_contact_list_row(list_row)
        return {"items": rows, "total": total}

    async def get_contact_overview(self, *, status: str | None) -> dict[str, int]:
        """Return overview card counts for the Contacts registry dashboard."""
        org_id = self.user_context.organization_id
        return await self.contacts_repo.get_contact_overview(
            organization_id=org_id,
            status=status,
        )

    @staticmethod
    def _format_contact_display_name(
        *,
        first_name: str | None,
        last_name: str | None,
    ) -> str:
        """Build a display name from first/last name parts."""
        return " ".join(
            part for part in [(first_name or "").strip(), (last_name or "").strip()] if part
        ).strip()

    @staticmethod
    def _normalize_external_contact_id(value: str | None) -> str | None:
        """Strip and normalize optional external contact id."""
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    async def get_contacts_by_ids(self, *, contact_ids: list[str]) -> list[dict[str, Any]]:
        """Return minimal contact info (id, name, email, external_contact_id) for the given ids."""
        unique_ids = list(dict.fromkeys(cid.strip() for cid in contact_ids if (cid or "").strip()))
        if not unique_ids:
            return []

        rows = await self.contacts_repo.get_contacts_by_ids(
            organization_id=self.user_context.organization_id,
            contact_ids=unique_ids,
        )
        return [
            {
                "id": row["id"],
                "name": self._format_contact_display_name(
                    first_name=row.get("first_name"),
                    last_name=row.get("last_name"),
                ),
                "email": row.get("email"),
                "external_contact_id": row.get("external_contact_id"),
            }
            for row in rows
        ]

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
                "tags": hit_document.get("tags") or [],
                "created_at": created_at_iso,
                "updated_at": updated_at_iso,
            }
            items.append(
                ContactSummaryResponse.model_validate(summary_row).model_dump(exclude_none=True)
            )
        return items

    async def _create_property_contact(
        self,
        body: CreateContactRequest,
        *,
        provision_auth: bool = True,
    ) -> dict[str, Any]:
        """Create a property-management contact (onboarding / unit allotment flows)."""
        org_id = self.user_context.organization_id
        if body.contact_type is None:
            raise ValidationException(
                message_key="contacts.errors.contact_type_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        validated_custom_fields = await self._validate_custom_fields_for_create(body.custom_fields)
        contact_id = str(uuid.uuid4())
        user_id: str | None = None
        isometrik_user_id: str | None = None

        primary_phone = next((phone for phone in body.phones if phone.is_primary), None)
        if not primary_phone:
            raise ValidationException(
                message_key="contacts.errors.exactly_one_primary_phone",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        emails = body.emails or []
        primary_email = next(
            (item.email.strip().lower() for item in emails if item.is_primary),
            None,
        )
        full_phone = re.sub(
            r"\D",
            "",
            f"{primary_phone.phone_isd_code}{primary_phone.phone_number}",
        )

        if provision_auth:
            user_id, isometrik_user_id, _ = await self._provision_contact_auth_identity(
                contact_id=contact_id,
                first_name=body.first_name,
                last_name=body.last_name,
                prefix=body.prefix,
                phone=full_phone or None,
                email=primary_email,
            )

        phones_payload = [
            phone.model_dump(exclude_none=True) if hasattr(phone, "model_dump") else dict(phone)
            for phone in body.phones
        ]
        emails_payload = [email.model_dump(exclude_none=True) for email in emails]

        contact_row = {
            "id": contact_id,
            "organization_id": org_id,
            "user_id": user_id,
            "isometrik_user_id": isometrik_user_id,
            "status": ContactStatus.ACTIVE.value,
            "contact_type": body.contact_type.value,
            "portal_access": body.portal_access,
            "prefix": body.prefix,
            "first_name": body.first_name,
            "middle_name": body.middle_name,
            "last_name": body.last_name,
            "title": body.title,
            "date_of_birth": body.date_of_birth,
            "gender": body.gender.value if body.gender else None,
            "blood_group": body.blood_group.value if body.blood_group else None,
            "communication_preferences": body.communication_preferences.model_dump(),
            "profile_photo_url": body.profile_photo_url,
            "phones": phones_payload,
            "emails": emails_payload,
            "tags": body.tags,
            "custom_fields": validated_custom_fields,
            "additional_data": body.additional_data,
            "social_pages": [
                page.model_dump(mode="json", exclude_none=True) for page in body.social_pages
            ],
            "websites": [
                website.model_dump(mode="json", exclude_none=True) for website in body.websites
            ],
            "notes": [note.model_dump() for note in body.notes],
        }

        try:
            inserted = await self.contacts_repo.insert_contact(contact_row)
        except UniqueViolationError as exc:
            if getattr(exc, "constraint_name", None) == "uq_contacts_user_org":
                raise ConflictException(
                    message_key="contacts.errors.contact_user_already_exists",
                    custom_code=CustomStatusCode.CONFLICT,
                ) from exc
            raise

        return {
            "contact_id": contact_id,
            "old_data": None,
            "new_data": inserted,
        }

    async def provision_auth_for_existing_contact(
        self,
        *,
        contact_id: str,
        password: str | None = None,
    ) -> dict[str, Any]:
        """Provision Supabase/Isometrik identity for an existing contact without auth."""
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
        if current.get("user_id"):
            return current

        phones = parse_json_any(current.get("phones"), default=[])
        primary_phone = next((p for p in phones if p.get("is_primary")), None)
        if not primary_phone:
            raise ValidationException(
                message_key="contacts.errors.exactly_one_primary_phone",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        emails = parse_json_any(current.get("emails"), default=[])
        primary_email = next(
            (e.get("email") for e in emails if e.get("is_primary")),
            emails[0].get("email") if emails else None,
        )
        existing_isometrik_user_id = (
            str(current["isometrik_user_id"]) if current.get("isometrik_user_id") else None
        )
        user_id, isometrik_user_id, _ = await self._provision_contact_auth_identity(
            contact_id=contact_id,
            phone=self._normalize_full_phone(
                primary_phone["phone_isd_code"],
                primary_phone["phone_number"],
            ),
            email=(primary_email or "").strip().lower() or None,
            first_name=current.get("first_name"),
            last_name=current.get("last_name"),
            prefix=current.get("prefix"),
            password=password,
            existing_isometrik_user_id=existing_isometrik_user_id,
        )
        update_data: dict[str, Any] = {}
        if user_id and str(current.get("user_id") or "") != user_id:
            update_data["user_id"] = user_id
        if isometrik_user_id and str(current.get("isometrik_user_id") or "") != isometrik_user_id:
            update_data["isometrik_user_id"] = isometrik_user_id
        if not update_data:
            return current

        updated = await self.contacts_repo.update_contact(
            contact_id=contact_id,
            organization_id=org_id,
            update_data=update_data,
        )
        return updated or current
