"""Service for client business logic

This service handles all business logic related to clients, including
validation, formatting, and orchestration of client operations.
"""

# pylint: disable=too-many-lines

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import asyncpg
import httpx
from asyncpg import UniqueViolationError
from supabase import AsyncClient

from apps.user_service.app.config.app_settings import app_settings, shared_settings
from apps.user_service.app.db.repositories import (
    ClientRepository,
    LeadRepository,
    OrganizationRepository,
    UserEventRepository,
    UserRepository,
)
from apps.user_service.app.schemas.clients import (
    PORTAL_ACCESS_DEFAULT,
    ClientAddressResponse,
    ClientDetailsResponse,
    ClientListResponse,
    CompanyContact,
    CreateClientFromUserRequest,
    CreateClientRequest,
    LeadInfo,
    LeadManagementUpdate,
    PrimaryContactInfo,
    PrimaryContactUpdate,
    UpdateClientRequest,
)
from apps.user_service.app.schemas.common import (
    AddressesUpdate,
    AddressInput,
    AddressUpdateItem,
    BillingPreferences,
    EducationalHistoryItem,
    KeyPerson,
    LinkedPageItem,
    Phone,
    PhonesUpdate,
    Product,
    SocialPage,
    Website,
    WorkHistoryItem,
)
from apps.user_service.app.schemas.enums import (
    ClientStatus,
    ClientType,
    EntityType,
    IsometrikRole,
    UserEventStatus,
)
from apps.user_service.app.schemas.typesense import TypesenseClientDocument
from apps.user_service.app.search.client_typesense_schema import (
    CLIENT_COLLECTION_SCHEMA,
    EMAIL_SEARCH_PARAMS,
    PHONE_SEARCH_PARAMS,
    SEARCH_PARAMS,
    build_document_from_schema,
)
from apps.user_service.app.services.client_enrichment_service import (
    ClientEnrichmentService,
)
from apps.user_service.app.services.custom_field_service import CustomFieldService
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
    generate_random_password,
    parse_json_field,
    safe_json_loads,
    serialize_pydantic_models,
    validate_uuid_format,
)
from apps.user_service.app.utils.email_utils import send_client_creation_email
from apps.user_service.app.utils.user_utils import build_full_name
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

logger = get_logger("client_service")


def should_mark_primary_on_create(
    client_type: ClientType,
    has_company_link: bool,
) -> bool:
    """Single source of truth for when a client_user is primary.

    Current behavior:
    - For a person without a company link, mark as primary.
    - For a person linked to a company, do not mark as primary.
    - For other types (e.g. company), mark as primary.
    """
    if client_type == ClientType.PERSON:
        return not has_company_link
    return True


@dataclass
class CreateClientResult:
    """Result of client creation: persisted records and items to run enrichment on."""

    records: list[dict[str, Any]]
    enrichment_items: list[dict[str, Any]]
    primary_record_id: str
    lead_id: str | None = None


class ClientService:
    """Service for client business logic.

    Handles all business logic related to clients, including validation,
    formatting, and orchestration of client operations.
    """

    def __init__(
        self,
        db_connection: asyncpg.Connection,
        user_context: UserContext | None = None,
        supabase_client: AsyncClient | None = None,
    ) -> None:
        """Initialize ClientService with user context and database connection.

        Args:
            db_connection: database connection for postgresql
            supabase_client: Supabase client for auth operations
        """
        self.user_context = user_context
        self.db_connection = db_connection
        self.client_repository = ClientRepository(db_connection=db_connection)
        self.lead_repository = LeadRepository(db_connection=db_connection)
        self.supabase_client = supabase_client
        self._typesense_service: TypesenseService | None = None

    @staticmethod
    def _lead_score_for_typesense(value: Any) -> int | None:
        """Convert string lead score to int for Typesense only.

        Lead score is string in app flow; Typesense expects int32.
        Invalid values are ignored (None) so indexing stays best-effort.
        """
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            return int(cleaned)
        except ValueError:
            return None

    @staticmethod
    async def index_clients_in_typesense_background(
        client_refs: Iterable[tuple[str, str]],
    ) -> None:
        """Index the given client refs into Typesense using a pool connection.

        For use from background tasks (e.g. webhooks, post-create) where no
        request-scoped DB connection is available. Acquires its own connection.
        """
        refs = list(client_refs)
        if not refs:
            return
        pool = await get_pool()
        async with AcquireConnection(pool) as conn:
            service = ClientService(db_connection=conn)
            await service._index_clients_in_typesense(refs)

    @staticmethod
    async def delete_clients_from_typesense_background(
        client_ids: Iterable[str],
    ) -> None:
        """Best-effort deletion of client documents from Typesense.

        Intended for FastAPI BackgroundTasks. Does not require a DB connection;
        deletes are performed via the Typesense HTTP API.
        """
        ids = [str(cid) for cid in client_ids if cid]
        if not ids:
            return
        typesense_service = TypesenseService.from_settings(
            collection_name=app_settings.shared_settings.typesense.clients_collection_name
        )
        for client_id in ids:
            try:
                await typesense_service.delete_document(client_id)
            except Exception:
                logger.exception(
                    "typesense_delete_document_failed",
                    extra={"client_id": client_id},
                )

    @staticmethod
    async def trigger_enrichment_background(
        client_id: str,
        organization_id: str,
    ) -> None:
        """Trigger enrichment using a pool connection.

        Intended for FastAPI BackgroundTasks, where request-scoped connections are not safe
        to reuse after the response is sent.
        """
        pool = await get_pool()
        async with AcquireConnection(pool) as conn:
            service = ClientService(db_connection=conn)
            try:
                await service.trigger_enrichment(
                    client_id=client_id,
                    organization_id=organization_id,
                    conn=conn,
                )
            except Exception:
                logger.exception(
                    "Failed to trigger client enrichment in background",
                    extra={
                        "client_id": client_id,
                        "organization_id": organization_id,
                    },
                )
                raise

    async def _build_typesense_document_for_index(
        self,
        client_id: str,
        organization_id: str,
    ) -> dict[str, Any] | None:
        """Build a full Typesense document for a client using the ADR-compliant schema."""
        details = await self.client_repository.get_client_details_with_primary_contact(
            client_id=client_id,
            organization_id=organization_id,
        )
        if not details:
            return None

        addresses = await self.client_repository.get_client_addresses(client_id)

        work_history = parse_json_field(details.get("work_history")) or []
        educational_history = parse_json_field(details.get("educational_history")) or []
        key_people = parse_json_field(details.get("key_people")) or []
        products = parse_json_field(details.get("products")) or []
        raw_custom_fields = parse_json_field(details.get("custom_fields"))
        root_cells = raw_custom_fields if isinstance(raw_custom_fields, list) else []

        phones_raw = details.get("phones")
        phones = parse_json_field(phones_raw)
        if not isinstance(phones, list):
            phones = []

        custom_field_keys: list[str] = []
        custom_field_values: list[str] = []
        if root_cells:
            entity_type_cf = (
                EntityType.COMPANY
                if details.get("client_type") == ClientType.COMPANY.value
                else EntityType.CONTACT
            )
            cfs = CustomFieldService(
                db_connection=self.db_connection,
                user_context=self.user_context,
            )
            definitions_cf, _ = await cfs.get_custom_fields_list(
                entity_type_cf,
                organization_id=str(details["organization_id"]),
            )
            id_to_def_ts = {str(d.id): d for d in definitions_cf}
            custom_field_keys, custom_field_values = (
                CustomFieldService.field_cells_typesense_facets(root_cells, id_to_def_ts)
            )

        created_at_dt = details.get("created_at")
        updated_at_dt = details.get("updated_at")

        primary_contact_full_name = " ".join(
            part
            for part in (
                details.get("prefix"),
                details.get("first_name"),
                details.get("middle_name"),
                details.get("last_name"),
            )
            if part
        )

        address_cities = [a.get("city", "") for a in addresses if a.get("city")]
        address_states = [a.get("state", "") for a in addresses if a.get("state")]
        address_countries = [a.get("country", "") for a in addresses if a.get("country")]
        address_postal_codes = [a.get("postal_code", "") for a in addresses if a.get("postal_code")]

        document: dict[str, Any] = {
            "id": str(details["id"]),
            "organization_id": str(details["organization_id"]),
            "client_type": details.get("client_type"),
            "status": details.get("status"),
            "name": details.get("name") or "",
            "company_name": details.get("company_name") or "",
            "primary_contact_first_name": details.get("first_name") or "",
            "primary_contact_last_name": details.get("last_name") or "",
            "primary_contact_full_name": primary_contact_full_name,
            "primary_contact_title": details.get("title") or "",
            "email": (details.get("email") or "").lower(),
            "phone_numbers": [
                p.get("phone_number")
                for p in (phones or [])
                if isinstance(p, dict) and p.get("phone_number")
            ],
            "tags": details.get("tags") or [],
            "industry": details.get("industry") or None,
            "description": details.get("description") or "",
            "target_market_segments": details.get("target_market_segments") or [],
            "current_tech_stack": details.get("current_tech_stack") or [],
            "industry_specific_terminologies": details.get("industry_specific_terminologies") or [],
            "preferred_communication_channels": details.get("preferred_communication_channels")
            or [],
            "key_people_names": [
                kp.get("name", "")
                for kp in (key_people or [])
                if isinstance(kp, dict) and kp.get("name")
            ],
            "product_names": [
                p.get("name", "") for p in (products or []) if isinstance(p, dict) and p.get("name")
            ],
            "skills": details.get("skills") or [],
            "work_history_companies": [
                j.get("company", "")
                for j in (work_history or [])
                if isinstance(j, dict) and j.get("company")
            ],
            "work_history_titles": [
                j.get("job_title", "")
                for j in (work_history or [])
                if isinstance(j, dict) and j.get("job_title")
            ],
            "educational_institutions": [
                e.get("university", "")
                for e in (educational_history or [])
                if isinstance(e, dict) and e.get("university")
            ],
            "address_cities": address_cities,
            "address_states": address_states,
            "address_countries": address_countries,
            "address_postal_codes": address_postal_codes,
            "lead_status": details.get("lead_status") or "",
            "lead_score": self._lead_score_for_typesense(details.get("lead_score")),
            "intake_stage": details.get("intake_stage") or "",
            "custom_field_values": custom_field_values,
            "custom_field_keys": custom_field_keys,
            "enrichment_done": bool(details.get("enrichment_done")),
            "created_at": int(created_at_dt.timestamp()) if created_at_dt else 0,
            "updated_at": int(updated_at_dt.timestamp()) if updated_at_dt else 0,
            "company_id": str(details["company_id"]) if details.get("company_id") else "",
            "profile_photo_url": details.get("profile_photo_url") or "",
        }

        # Typesense search facets are defined as `string[]` — keep the indexed
        # values clean by removing accidental duplicates.
        self._dedupe_string_list_fields(document)

        typesense_document = TypesenseClientDocument.model_validate(document).model_dump(
            exclude_none=True
        )
        return build_document_from_schema(
            schema=CLIENT_COLLECTION_SCHEMA,
            raw_document=typesense_document,
        )

    async def _index_clients_in_typesense(
        self,
        client_refs: Iterable[tuple[str, str]],
    ) -> None:
        """Best-effort indexing of clients into Typesense using the full schema.

        This method is designed to be called from a background context. It builds
        full denormalized documents for the provided client references and then
        performs a single bulk upsert into Typesense for efficiency.
        """
        refs = list(client_refs)
        if not refs:
            return

        documents: list[dict[str, Any]] = []
        for client_id, organization_id in refs:
            document = await self._build_typesense_document_for_index(
                client_id=client_id,
                organization_id=organization_id,
            )
            if document:
                documents.append(document)

        if not documents:
            return

        await self.typesense_service.upsert_documents_bulk(documents)

    @property
    def typesense_service(self) -> TypesenseService:
        """Lazily initialized Typesense service for client indexing."""
        if self._typesense_service is None:
            self._typesense_service = TypesenseService.from_settings(
                collection_name=app_settings.shared_settings.typesense.clients_collection_name,
            )
        return self._typesense_service

    async def create_client_from_user(self, request_data: CreateClientFromUserRequest) -> None:
        """Create a client and client_user from user ID.

        Flow:
        1. Check if user is already a client of the organization and
            raise a conflict exception if yes.
        2. Validate user exists in auth.users
        3. Validate organization exists
        4. Create Isometrik user
        5. Create client record
        6. Create client_user record
        7. Send creation email

        Args:
            request_data: Request data containing user_id and organization_id

        Raises:
            NotFoundException: If user or organization not found
            ServiceUnavailableException: If Isometrik user creation fails
            ConflictException: If user is already a client or user event is not pending
        """
        user_id = request_data.user_id
        organization_id = request_data.organization_id

        # Only process if user_event for this user is pending
        user_event_repository = UserEventRepository(db_connection=self.db_connection)
        user_event_details = await user_event_repository.get_user_event_by_user_id(
            user_id, ["status"]
        )
        is_valid = (
            user_event_details and user_event_details.get("status") == UserEventStatus.PENDING.value
        )

        if not is_valid:
            raise ConflictException(
                message_key="clients.errors.user_event_not_available",
                custom_code=CustomStatusCode.CONFLICT,
            )

        user_repository = UserRepository(db_connection=self.db_connection)

        # Get user details including email and raw_user_meta_data for first_name/last_name
        user_details = await user_repository.get_user_details_by_id(user_id, ["email"])
        if not user_details:
            raise NotFoundException(
                message_key="users.errors.user_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
                params={"user_id": user_id},
            )

        organization_repository = OrganizationRepository(db_connection=self.db_connection)
        # Validate organization exists
        organization = await organization_repository.get_organization_by_id(organization_id)
        if not organization:
            raise NotFoundException(
                message_key="organizations.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
                params={"organization_id": organization_id},
            )

        org_settings = parse_json_field(organization.get("settings"))
        isometrik_credentials = get_isometrik_data_from_settings(org_settings)

        # Create Isometrik user
        isometrik_response = await create_isometrik_user(
            user={
                "user_id": user_id,
                "email": user_details.get("email"),
                "organization_id": organization_id,
                "role": IsometrikRole.CLIENT.value,
            },
            isometrik_credentials=isometrik_credentials,
        )
        if not isometrik_response or not isometrik_response.get("userId"):
            raise ServiceUnavailableException(
                message_key="clients.errors.isometrik_user_creation_failed",
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
            )

        isometrik_user_id = isometrik_response["userId"]
        # Create client record
        [client_record] = await self.client_repository.create_client(
            [
                {
                    "organization_id": organization_id,
                    "client_type": ClientType.PERSON.value,
                }
            ]
        )
        # Create client_user record
        await self.client_repository.create_client_user(
            {
                "client_id": client_record["id"],
                "organization_id": organization_id,
                "user_id": user_id,
                "isometrik_user_id": isometrik_user_id,
            }
        )

        # Sync user with external social service (best-effort, non-blocking)
        await self._sync_user_with_social_service(
            user_id=user_id,
            organization_id=organization_id,
            email=user_details.get("email"),
            isometrik_user_id=isometrik_user_id,
        )

        # Mark user_event as completed
        await user_event_repository.update_status_by_user_id(
            user_id=user_id,
            status=UserEventStatus.COMPLETED,
        )

        # Send creation email
        try:
            if user_details.get("email"):
                send_client_creation_email(
                    email=user_details.get("email"),
                    organization_name=organization["name"],
                )
        except Exception as e:
            logger.error("Failed to send client creation email: %s", str(e))

    async def _validate_client_creation(
        self, request_data: CreateClientRequest, organization_id: str
    ) -> dict[str, Any]:
        """Validate organization exists and check for conflicts.

        Args:
            request_data: Request data
            organization_id: Organization ID

        Returns:
            dict: Organization data

        Raises:
            NotFoundException: If organization not found
            ConflictException: If email/name already exists
        """
        organization_repository = OrganizationRepository(db_connection=self.db_connection)
        organization = await organization_repository.get_organization_by_id(organization_id)
        if not organization:
            raise NotFoundException(
                message_key="organizations.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
                params={"organization_id": organization_id},
            )

        # Validate email uniqueness at organization level (via client + client_user linkage).
        # Company-only create is allowed without a primary contact;
        # in that case email may be absent.
        if request_data.email:
            existing_client_id = await self.client_repository._check_client_email_exists(
                email=request_data.email,
                organization_id=organization_id,
            )
            if existing_client_id:
                raise ConflictException(
                    message_key="clients.errors.email_already_exists",
                    custom_code=CustomStatusCode.CONFLICT,
                    params={"client_id": existing_client_id},
                )

        return organization

    def _should_create_client_user_on_create(self, request_data: CreateClientRequest) -> bool:
        """Return True when create flow should provision a user + client_user row."""
        if request_data.client_type == ClientType.PERSON:
            return True
        if request_data.client_type == ClientType.COMPANY:
            return bool(request_data.email and request_data.first_name and request_data.last_name)
        return False

    async def _provision_primary_contact_user(
        self,
        request_data: CreateClientRequest,
        organization: dict[str, Any],
        organization_id: str,
    ) -> tuple[str, str, str | None]:
        """Provision/reuse auth user and create Isometrik user for primary contact."""
        user_repository = UserRepository(db_connection=self.db_connection)
        existing_user = await user_repository.get_auth_user_by_email(request_data.email)
        return await self._create_auth_and_isometrik_user(
            request_data,
            organization,
            organization_id,
            existing_user=existing_user,
        )

    async def _create_auth_and_isometrik_user(
        self,
        request_data: CreateClientRequest,
        organization: dict[str, Any],
        organization_id: str,
        existing_user: dict[str, Any] | None = None,
    ) -> tuple[str, str, str | None]:
        """Create or reuse Supabase auth user and create Isometrik user.

        Args:
            request_data: Request data
            organization: Organization dataz
            organization_id: Organization ID
            existing_user: Existing user data

        Returns:
            tuple: (user_id, isometrik_user_id, password or None if reused)

        Raises:
            ServiceUnavailableException: If auth or Isometrik creation fails
        """
        # When an auth user already exists for this email, reuse it instead of creating a new one.
        # In that case, we do not generate or return a password.
        if existing_user and existing_user.get("id"):
            user_id = str(existing_user["id"])
            password: str | None = None
        else:
            # Generate a random password for the new client user
            password = generate_random_password()

            # Build user metadata same as signup/invite accept flow (without phone fields)
            user_metadata: dict[str, Any] = {
                "timezone": "UTC",
                "first_name": request_data.first_name,
                "last_name": request_data.last_name,
            }

            # Add person-specific fields if client type is PERSON
            if request_data.client_type == ClientType.PERSON and request_data.prefix:
                user_metadata["salutation"] = request_data.prefix

            # Create Supabase auth user with generated password
            auth_user = await create_user(
                sb_client=self.supabase_client,
                email=request_data.email,
                password=password,
                email_confirm=True,
                user_metadata=user_metadata,
            )
            if not auth_user or not auth_user.get("id"):
                raise ServiceUnavailableException(
                    message_key="clients.errors.auth_user_creation_failed",
                    custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
                )
            user_id = auth_user["id"]

        # Get Isometrik credentials
        org_settings = parse_json_field(organization.get("settings"))
        isometrik_credentials = get_isometrik_data_from_settings(org_settings)

        # Prepare name for Isometrik
        isometrik_first_name = request_data.first_name
        isometrik_last_name = request_data.last_name

        # Create Isometrik user
        isometrik_response = await create_isometrik_user(
            user={
                "user_id": user_id,
                "email": request_data.email,
                "organization_id": organization_id,
                "role": IsometrikRole.CLIENT.value,
                "first_name": isometrik_first_name,
                "last_name": isometrik_last_name,
            },
            isometrik_credentials=isometrik_credentials,
        )
        if not isometrik_response or not isometrik_response.get("userId"):
            raise ServiceUnavailableException(
                message_key="clients.errors.isometrik_user_creation_failed",
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
            )

        return user_id, isometrik_response["userId"], password

    def _get_client_name_for_create(self, request_data: CreateClientRequest) -> str:
        """Return the persisted client name for create payloads."""
        if request_data.client_type == ClientType.PERSON:
            return build_full_name(request_data.first_name, request_data.last_name)
        return request_data.name or ""

    def _build_base_client_data(
        self, request_data: CreateClientRequest, organization_id: str
    ) -> dict[str, Any]:
        """Build the non-serialized parts of the client payload."""
        client_name = self._get_client_name_for_create(request_data)
        client_data: dict[str, Any] = {
            "organization_id": organization_id,
            "client_type": request_data.client_type.value,
            "name": client_name,
            "portal_access": request_data.portal_access,
        }

        if request_data.industry:
            client_data["industry"] = request_data.industry
        if request_data.profile_photo_url:
            client_data["profile_photo_url"] = request_data.profile_photo_url
        if request_data.tags:
            client_data["tags"] = request_data.tags

        return client_data

    def _apply_serialized_jsonb_fields(
        self, client_data: dict[str, Any], request_data: CreateClientRequest
    ) -> None:
        """Serialize JSONB fields to JSON strings for asyncpg."""
        if request_data.websites:
            serialized_websites = serialize_pydantic_models(request_data.websites)
            client_data["websites"] = json.dumps(self._ensure_list_item_ids(serialized_websites))
        if request_data.billing_preferences:
            serialized_billing = serialize_pydantic_models(request_data.billing_preferences)
            client_data["billing_preferences"] = json.dumps(serialized_billing)

    async def _apply_custom_fields_if_needed(
        self, client_data: dict[str, Any], request_data: CreateClientRequest
    ) -> None:
        """Validate and apply custom fields when user context is available."""
        if not (self.user_context and self.user_context.organization_id):
            return

        if request_data.client_type == ClientType.COMPANY:
            entity_type = EntityType.COMPANY
        else:
            entity_type = EntityType.CONTACT

        custom_field_service = CustomFieldService(
            db_connection=self.db_connection,
            user_context=self.user_context,
        )
        validated_custom_fields = await custom_field_service.validate_for_create(
            request_data.custom_fields, entity_type
        )
        client_data["custom_fields"] = json.dumps(validated_custom_fields)

    def _apply_additional_and_social_pages(
        self, client_data: dict[str, Any], request_data: CreateClientRequest
    ) -> None:
        """Apply remaining optional fields requiring JSON serialization."""
        if request_data.additional_data:
            client_data["additional_data"] = json.dumps(request_data.additional_data)
        if request_data.social_pages:
            serialized = [p.model_dump() for p in request_data.social_pages]
            client_data["social_pages"] = json.dumps(self._ensure_list_item_ids(serialized))

    async def _prepare_client_data(
        self,
        request_data: CreateClientRequest,
        organization_id: str,
    ) -> dict[str, Any]:
        """Prepare client data dictionary.

        Args:
            request_data: Request data
            organization_id: Organization ID

        Returns:
            dict: Client data with JSONB fields serialized to JSON strings
        """
        client_data = self._build_base_client_data(request_data, organization_id)
        self._apply_serialized_jsonb_fields(client_data, request_data)
        await self._apply_custom_fields_if_needed(client_data, request_data)
        self._apply_additional_and_social_pages(client_data, request_data)
        return client_data

    async def _build_create_client_payloads(
        self, request_data: CreateClientRequest, organization_id: str
    ) -> list[dict[str, Any]]:
        """Build the ordered list of client payloads for create_client
        based on client type and linking rules."""
        # Note: in the 2-row create flows, the second (linked) payload does NOT go
        # through `_prepare_client_data(...)`, so custom-field required validation
        # is only applied for the payload built by `_prepare_client_data(...)`.
        if request_data.client_type == ClientType.COMPANY:
            # Allow company-only create when primary contact fields are omitted.
            if not self._should_create_client_user_on_create(request_data):
                return [await self._prepare_client_data(request_data, organization_id)]
            return [
                await self._prepare_client_data(request_data, organization_id),
                self._prepare_primary_contact_person_client_data(request_data, organization_id),
            ]
        if request_data.client_type == ClientType.PERSON:
            company_name = (request_data.company_name or "").strip()
            if company_name and not request_data.client_company_id:
                return [
                    self._prepare_company_client_data_for_linked_company(
                        request_data, organization_id, company_name
                    ),
                    await self._prepare_client_data(request_data, organization_id),
                ]
        return [await self._prepare_client_data(request_data, organization_id)]

    def _get_enrichment_items_for_created_clients(
        self, records: list[dict[str, Any]], organization_id: str
    ) -> list[dict[str, Any]]:
        """Build list of enrichment descriptors for created clients.

        When both a person and a company are created (e.g. company + primary contact,
        or person + linked company), returns one item per record so enrichment runs
        for both. Only includes client_type 'person' and 'company'.
        """
        items: list[dict[str, Any]] = []
        for rec in records:
            client_type = rec.get("client_type")
            items.append(
                {
                    "client_id": str(rec["id"]),
                    "organization_id": str(rec.get("organization_id") or organization_id),
                    "client_type": client_type,
                }
            )
        return items

    def _resolve_created_client_records(
        self,
        records: list[dict[str, Any]],
        request_data: CreateClientRequest,
    ) -> tuple[dict[str, Any], str, str | None]:
        """Resolve primary record, client_id for user link, and client_company_id."""
        creation_failed = ServiceUnavailableException(
            message_key="clients.errors.creation_failed",
            custom_code=CustomStatusCode.SERVICE_UNAVAILABLE,
        )
        if not records:
            raise creation_failed

        is_company = request_data.client_type == ClientType.COMPANY
        person_with_new_company = (
            request_data.client_type == ClientType.PERSON
            and (request_data.company_name or "").strip()
            and request_data.client_company_id is None
        )
        person_with_existing_company = (
            request_data.client_type == ClientType.PERSON
            and request_data.client_company_id is not None
        )

        # Company: expect [company, primary_contact_person]
        if is_company:
            if len(records) != 2:
                raise creation_failed
            return records[0], records[1]["id"], records[0]["id"]

        # Person with new linked company (name given, no client_company_id)
        # : expect [company, person]
        if person_with_new_company:
            if len(records) != 2:
                raise creation_failed
            return records[1], records[1]["id"], records[0]["id"]

        # Person with existing company link: expect [person]
        if person_with_existing_company:
            if len(records) != 1:
                raise creation_failed
            primary = records[0]
            return primary, primary["id"], request_data.client_company_id

        # Standalone person (no company association): expect [person]
        if len(records) != 1:
            raise creation_failed
        primary = records[0]
        return primary, primary["id"], None

    def _prepare_company_client_data_for_linked_company(
        self,
        request_data: CreateClientRequest,
        organization_id: str,
        company_name: str,
    ) -> dict[str, Any]:
        """Build minimal client payload for a company created when linking a person to a company.
        Used when creating a person with company/name provided;
        creates the company and links person via client_company_id.
        """
        return {
            "organization_id": organization_id,
            "client_type": ClientType.COMPANY.value,
            "name": company_name,
            "portal_access": request_data.portal_access,
        }

    def _prepare_primary_contact_person_client_data(
        self,
        request_data: CreateClientRequest,
        organization_id: str,
    ) -> dict[str, Any]:
        """Build minimal client payload for the primary-contact person when creating a company.
        Company link is on client_user (client_company_id), not on this client row.
        """
        name = build_full_name(request_data.first_name, request_data.last_name)
        return {
            "organization_id": organization_id,
            "client_type": ClientType.PERSON.value,
            "name": name,
            "portal_access": request_data.portal_access,
        }

    def _prepare_client_user_data(
        self,
        request_data: CreateClientRequest,
        client_id: str,
        organization_id: str,
        user_id: str,
        isometrik_user_id: str,
        client_company_id: str | None = None,
    ) -> dict[str, Any]:
        """Prepare client_user data dictionary.

        Args:
            request_data: Request data
            client_id: Client ID (person client when creating a company)
            organization_id: Organization ID
            user_id: User ID
            isometrik_user_id: Isometrik user ID
            client_company_id: Optional; set when linking this user to a company.
        """
        # For primary-contact decision, rely solely on request_data.client_company_id:
        # - None  => treat as not linked to an existing company (primary allowed)
        # - value => treat as linked to an existing company (primary not allowed)
        has_company_link = request_data.client_company_id is not None
        client_user_data = {
            "client_id": client_id,
            "organization_id": organization_id,
            "user_id": user_id,
            "isometrik_user_id": isometrik_user_id,
            "is_primary_contact": should_mark_primary_on_create(
                request_data.client_type,
                has_company_link,
            ),
            "first_name": request_data.first_name,
            "last_name": request_data.last_name,
        }
        if client_company_id is not None:
            client_user_data["client_company_id"] = client_company_id
        # Add optional fields only if provided
        if request_data.middle_name:
            client_user_data["middle_name"] = request_data.middle_name
        if request_data.title:
            client_user_data["title"] = request_data.title
        if request_data.prefix:
            client_user_data["prefix"] = request_data.prefix
        if request_data.date_of_birth:
            client_user_data["date_of_birth"] = request_data.date_of_birth
        if request_data.profile_photo_url:
            client_user_data["profile_photo_url"] = request_data.profile_photo_url

        # Phones: required via request_data.phones
        phones_list = self._ensure_list_item_ids([p.model_dump() for p in request_data.phones])
        if phones_list:
            client_user_data["phones"] = json.dumps(phones_list)

        return client_user_data

    async def _create_optional_records(
        self,
        request_data: CreateClientRequest,
        client_id: str,
    ) -> str | None:
        """Create lead and address records if provided.

        Args:
            request_data: Request data
            client_id: Client ID
        """
        lead_id: str | None = None
        # Optional lead from client onboarding: CRM links use ``contacts`` / ``companies`` ids only.
        if request_data.lead_management and request_data.lead_management.enabled:
            organization_id = self.user_context.organization_id
            lead = request_data.lead_management
            owner_id = self.user_context.user_id if self.user_context else None
            lead_row = {
                "organization_id": organization_id,
                "name": self._get_client_name_for_create(request_data),
                "stage_id": lead.stage_id or None,
                "lead_source": lead.lead_source,
                "referral_source": lead.referral_source,
                "lead_score": lead.lead_score,
                "notes": [],
                "custom_fields": [],
                "owner_id": owner_id,
            }
            created_lead = await self.lead_repository.create_lead(
                lead_row,
                contacts=[],
                company=None,
            )
            lead_id = (
                str(created_lead.get("id")) if created_lead and created_lead.get("id") else None
            )

        # Create address records if provided
        if request_data.addresses:
            addresses_data = [
                {
                    "client_id": client_id,
                    "address_line1": address.address_line1,
                    "address_line2": address.address_line2,
                    "city": address.city,
                    "state": address.state,
                    "postal_code": address.postal_code,
                    "country": address.country,
                    "address_type": address.address_type.value if address.address_type else None,
                    "is_primary": address.is_primary,
                }
                for address in request_data.addresses
            ]
            await self.client_repository.bulk_create_addresses(addresses_data)
        return lead_id

    async def create_client(self, request_data: CreateClientRequest) -> CreateClientResult:
        """Create a new client with complete onboarding flow.

        Orchestrates the full client creation process: validates organization existence
        and data uniqueness (email, phone, company name), provisions authentication
        and Isometrik users, creates client and client_user records, and optionally
        creates lead and address records. Sends a welcome email upon successful creation.

        Args:
            request_data: Request data containing client information

        Returns:
            CreateClientResult: Created client records and enrichment items (one per
                created client). Enrichment items have keys {client_id, organization_id,
                client_type}. When both person and company are created, enrichment_items
                contains two items so enrichment runs for both.

        Raises:
            NotFoundException: If organization not found
            ConflictException: If email/phone/name already exists
            ServiceUnavailableException: If auth or Isometrik creation fails
            ValidationException: If validation fails
            UniqueViolationError: If primary contact already exists
        """
        organization_id = self.user_context.organization_id

        organization = await self._validate_client_creation(request_data, organization_id)

        should_create_client_user = self._should_create_client_user_on_create(request_data)

        # Provision auth/Isometrik only when we are creating a primary contact user.
        user_id: str | None = None
        isometrik_user_id: str | None = None
        password: str | None = None
        if should_create_client_user:
            user_id, isometrik_user_id, password = await self._provision_primary_contact_user(
                request_data=request_data,
                organization=organization,
                organization_id=organization_id,
            )

        payloads = await self._build_create_client_payloads(request_data, organization_id)
        records = await self.client_repository.create_client(payloads)
        if not records:
            raise ServiceUnavailableException(
                message_key="clients.errors.creation_failed",
                custom_code=CustomStatusCode.SERVICE_UNAVAILABLE,
            )

        primary_record = await self._maybe_create_client_user_for_created_clients(
            request_data=request_data,
            records=records,
            organization_id=organization_id,
            should_create_client_user=should_create_client_user,
            user_id=user_id,
            isometrik_user_id=isometrik_user_id,
        )
        await self._maybe_link_primary_contact_to_new_company(request_data, primary_record)

        lead_id = await self._create_optional_records(request_data, primary_record["id"])

        self._maybe_send_client_creation_email(
            request_data=request_data,
            should_create_client_user=should_create_client_user,
            organization_name=organization["name"],
            password=password,
        )
        enrichment_items = self._get_enrichment_items_for_created_clients(records, organization_id)
        return CreateClientResult(
            records=records,
            enrichment_items=enrichment_items,
            primary_record_id=str(primary_record["id"]),
            lead_id=lead_id,
        )

    async def _maybe_create_client_user_for_created_clients(
        self,
        *,
        request_data: CreateClientRequest,
        records: list[dict[str, Any]],
        organization_id: str,
        should_create_client_user: bool,
        user_id: str | None,
        isometrik_user_id: str | None,
    ) -> dict[str, Any]:
        """Maybe create client user for created clients."""
        if not should_create_client_user:
            return records[0]

        primary_record, client_id_for_user, client_company_id = (
            self._resolve_created_client_records(records, request_data)
        )
        client_user_data = self._prepare_client_user_data(
            request_data,
            client_id_for_user,
            organization_id,
            user_id,
            isometrik_user_id,
            client_company_id=client_company_id,
        )
        await self._create_client_user_with_primary_contact_uniqueness(client_user_data)
        return primary_record

    async def _create_client_user_with_primary_contact_uniqueness(
        self,
        client_user_data: dict[str, Any],
    ) -> None:
        """Create client user with primary contact uniqueness."""
        try:
            await self.client_repository.create_client_user(client_user_data)
        except UniqueViolationError as exc:
            constraint = getattr(exc, "constraint_name", None)
            if constraint in {
                "client_users_one_primary_per_client",
                "client_users_one_primary_contact_per_company",
            }:
                raise ConflictException(
                    message_key="clients.errors.primary_contact_already_exists",
                    custom_code=CustomStatusCode.CONFLICT,
                ) from exc
            raise

    async def _maybe_link_primary_contact_to_new_company(
        self,
        request_data: CreateClientRequest,
        primary_record: dict[str, Any],
    ) -> None:
        """Maybe link primary contact to new company."""
        if request_data.client_type != ClientType.COMPANY or not request_data.primary_contact_id:
            return
        await self.client_repository._update_client_user(
            request_data.primary_contact_id,
            {
                "client_company_id": str(primary_record["id"]),
                "is_primary_contact": True,
            },
        )

    def _maybe_send_client_creation_email(
        self,
        *,
        request_data: CreateClientRequest,
        should_create_client_user: bool,
        organization_name: str,
        password: str | None,
    ) -> None:
        """Maybe send client creation email."""
        if not (should_create_client_user and request_data.portal_access):
            return
        try:
            send_client_creation_email(
                email=request_data.email,
                organization_name=organization_name,
                password=password,
            )
        except Exception as exc:
            logger.error("Failed to send client creation email: %s", str(exc))

    async def get_clients_list(
        self,
        organization_id: str,
        filter_params: dict[str, Any],
    ) -> dict[str, Any]:
        """Get paginated list of clients with filtering.

        Args:
            organization_id: Organization ID
            filter_params: Dictionary containing filter parameters:
                - search: Search term (searches in client name)
                - client_type: Filter by client type
                - status: Filter by status
                - page: Page number
                - page_size: Page size

        Returns:
            dict: Dictionary containing 'clients' list and 'total' count
        """
        page = filter_params.get("page", 1)
        page_size = filter_params.get("page_size", 20)
        offset = (page - 1) * page_size

        # Prepare repository filter params
        repo_filter_params = {
            "search": filter_params.get("search"),
            "client_type": filter_params.get("client_type"),
            "status": filter_params.get("status"),
            "limit": page_size,
            "offset": offset,
        }

        # Get clients from repository
        clients = await self.client_repository.get_clients_list(
            organization_id=organization_id,
            filter_params=repo_filter_params,
        )

        # Prepare count filter params (without pagination)
        count_filter_params = {
            "search": filter_params.get("search"),
            "client_type": filter_params.get("client_type"),
            "status": filter_params.get("status"),
        }

        # Get total count
        total = await self.client_repository.get_clients_count(
            organization_id=organization_id,
            filter_params=count_filter_params,
        )

        # Transform to response model
        transformed_clients = []
        for client in clients:
            phones = self._parse_primary_contact_phones(client.get("phones"))
            primary_contact = {
                "first_name": client.get("first_name"),
                "last_name": client.get("last_name"),
                "title": client.get("title"),
                "email": client.get("email"),
                "phones": phones,
            }

            is_person = client.get("client_type") == ClientType.PERSON.value
            company_id = str(client.get("company_id")) if is_person else None
            company_name = str(client.get("company_name")) if is_person else None

            client_response = ClientListResponse(
                id=str(client.get("id")),
                name=client.get("name") or "",
                company_id=company_id,
                company_name=company_name,
                primary_contact=primary_contact,
                client_type=client.get("client_type"),
                status=client.get("status"),
                industry=client.get("industry"),
                projects=[],
                image_url=client.get("contact_profile_photo_url")
                or client.get("profile_photo_url"),
                created_at=format_iso_datetime(client.get("created_at")) or "",
                updated_at=format_iso_datetime(client.get("updated_at")) or "",
                outstanding=None,
                tags=client.get("tags") or [],
            )
            transformed_clients.append(client_response.model_dump(exclude_none=True))

        return {"clients": transformed_clients, "total": total}

    async def search_clients(
        self,
        *,
        organization_id: str,
        query: str,
        page: int,
        page_size: int,
        client_type: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """Search clients using Typesense with hybrid keyword + vector search.

        All queries are implicitly scoped to the caller's organization via ``organization_id``.
        """
        filters: list[str] = [f"organization_id:={organization_id}"]
        if client_type:
            filters.append(f"client_type:={client_type}")
        if status:
            filters.append(f"status:={status}")
        filter_by = " && ".join(filters)

        # Start from the default full-text params and specialise for email / phone queries.
        query = query.strip()
        params: dict[str, Any] = {
            "q": query,
            "per_page": page_size,
            "page": page,
            "filter_by": filter_by,
            "exclude_fields": "embedding",
        }

        if "@" in query:
            # Strict email lookup (no typos / prefix) for email-shaped queries.
            params.update(EMAIL_SEARCH_PARAMS)
        elif sum(c.isdigit() for c in query) >= 5:
            # Digit-heavy queries treated as phone lookups.
            params.update(PHONE_SEARCH_PARAMS)
        else:
            params.update(SEARCH_PARAMS)

        # Hybrid vector + keyword search when we can build an embedding.
        embedding = await self.typesense_service.embed_query_text(query)
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

        raw = await self.typesense_service.search(params)

        hits: list[dict[str, Any]] = raw.get("hits") or []
        clients: list[dict[str, Any]] = []
        for hit in hits:
            doc = hit.get("document") or {}
            is_person = doc.get("client_type") == ClientType.PERSON.value
            company_id = str(doc["company_id"]) if is_person and doc.get("company_id") else None
            company_name = (
                str(doc["company_name"]) if is_person and doc.get("company_name") else None
            )

            primary_contact = PrimaryContactInfo(
                salutation=None,
                first_name=doc.get("primary_contact_first_name"),
                middle_name=None,
                last_name=doc.get("primary_contact_last_name"),
                title=doc.get("primary_contact_title"),
                email=doc.get("email"),
                phones=[
                    Phone(
                        id=None,
                        phone_number=value,
                        phone_isd_code="",
                        label=None,
                        is_primary=index == 0,
                    )
                    for index, value in enumerate(doc.get("phone_numbers") or [])
                ],
            )

            item = ClientListResponse(
                id=str(doc.get("id")),
                name=doc.get("name") or "",
                company_id=company_id,
                company_name=company_name,
                primary_contact=primary_contact,
                client_type=ClientType(doc.get("client_type")),
                status=ClientStatus(doc.get("status")),
                industry=doc.get("industry"),
                projects=[],
                image_url=doc.get("profile_photo_url") or None,
                created_at="",
                updated_at="",
                outstanding=None,
                tags=doc.get("tags") or [],
            )
            clients.append(item.model_dump(exclude_none=True))

        return {"clients": clients, "total": raw.get("found", 0)}

    async def get_client_details(
        self, client_id: str, organization_id: str
    ) -> ClientDetailsResponse:
        """Get client details with all fields and addresses.

        Args:
            client_id: Client ID
            organization_id: Organization ID

        Returns:
            ClientDetailsResponse: Client details with addresses

        Raises:
            NotFoundException: If client not found
        """
        # Get client details with primary contact
        client = await self.client_repository.get_client_details_with_primary_contact(
            client_id, organization_id
        )
        if not client:
            raise NotFoundException(
                message_key="clients.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Get addresses
        addresses = await self.client_repository.get_client_addresses(client_id)

        # Build primary contact info (phones from client_users.phones)
        phones_list = self._parse_primary_contact_phones(client.get("phones"))
        primary_contact = PrimaryContactInfo(
            salutation=client.get("prefix"),
            first_name=client.get("first_name"),
            middle_name=client.get("middle_name"),
            last_name=client.get("last_name"),
            title=client.get("title"),
            email=client.get("email"),
            phones=phones_list,
        )

        # Build all company contacts when client type is company
        company_contacts: list[CompanyContact] = []
        if client.get("client_type") == ClientType.COMPANY.value:
            raw_contacts = await self.client_repository.get_company_contacts(
                company_client_id=client_id,
                organization_id=organization_id,
            )
            company_contacts = self._map_company_contacts(raw_contacts)
        else:
            company_contacts = []

        # Parse JSONB fields
        websites_data = safe_json_loads(client.get("websites"), [])
        websites = [Website(**website) for website in websites_data] if websites_data else []

        billing_preferences_data = parse_json_field(client.get("billing_preferences"))
        billing_preferences = None
        if billing_preferences_data:
            billing_preferences = BillingPreferences(**billing_preferences_data)

        custom_fields_raw = parse_json_field(client.get("custom_fields"))
        custom_fields: list[Any] = custom_fields_raw if isinstance(custom_fields_raw, list) else []
        if custom_fields:
            entity_type_cf = (
                EntityType.COMPANY
                if client.get("client_type") == ClientType.COMPANY.value
                else EntityType.CONTACT
            )
            cfs = CustomFieldService(
                db_connection=self.db_connection,
                user_context=self.user_context,
            )
            definitions_cf, _ = await cfs.get_custom_fields_list(
                entity_type_cf,
                organization_id=organization_id,
            )
            id_to_def_cf = {str(d.id): d for d in definitions_cf}
            custom_fields = cfs.resolve_fields_for_read(custom_fields, id_to_def_cf)

        additional_data = parse_json_field(client.get("additional_data")) or {}
        sales_intelligence_raw = parse_json_field(client.get("sales_intelligence")) or {}
        sales_intelligence = (
            sales_intelligence_raw if isinstance(sales_intelligence_raw, dict) else {}
        )
        social_pages_data = parse_json_field(client.get("social_pages")) or []
        social_pages = [SocialPage(**p) for p in social_pages_data] if social_pages_data else []

        work_history_data = parse_json_field(client.get("work_history")) or []
        work_history = (
            [WorkHistoryItem(**w) for w in work_history_data]
            if work_history_data and isinstance(work_history_data, list)
            else []
        )
        educational_history_data = parse_json_field(client.get("educational_history")) or []
        educational_history = (
            [EducationalHistoryItem(**e) for e in educational_history_data]
            if educational_history_data and isinstance(educational_history_data, list)
            else []
        )
        skills = parse_json_field(client.get("skills"))
        skills = skills if isinstance(skills, list) else []
        target_market_segments = parse_json_field(client.get("target_market_segments"))
        target_market_segments = (
            target_market_segments if isinstance(target_market_segments, list) else []
        )
        current_tech_stack = parse_json_field(client.get("current_tech_stack"))
        current_tech_stack = current_tech_stack if isinstance(current_tech_stack, list) else []
        preferred_communication_channels = parse_json_field(
            client.get("preferred_communication_channels")
        )
        preferred_communication_channels = (
            preferred_communication_channels
            if isinstance(preferred_communication_channels, list)
            else []
        )
        industry_specific_terminologies = parse_json_field(
            client.get("industry_specific_terminologies")
        )
        industry_specific_terminologies = (
            industry_specific_terminologies
            if isinstance(industry_specific_terminologies, list)
            else []
        )
        linked_pages_data = parse_json_field(client.get("linked_pages")) or []
        linked_pages = (
            [LinkedPageItem(**lp) for lp in linked_pages_data]
            if linked_pages_data and isinstance(linked_pages_data, list)
            else []
        )
        products_data = parse_json_field(client.get("products")) or []
        products = (
            [Product(**p) for p in products_data]
            if products_data and isinstance(products_data, list)
            else []
        )
        key_people_data = parse_json_field(client.get("key_people")) or []
        key_people = (
            [KeyPerson(**kp) for kp in key_people_data]
            if key_people_data and isinstance(key_people_data, list)
            else []
        )

        # Repository returns a list; str covers JSON-encoded values from older paths or edges.
        raw_linked = client.get("linked_leads")
        if isinstance(raw_linked, str):
            raw_linked = safe_json_loads(raw_linked, [])
        lead_rows = [
            row
            for row in (raw_linked if isinstance(raw_linked, list) else [])
            if isinstance(row, dict)
        ]

        leads_info: list[LeadInfo] = []
        for row in lead_rows:
            raw_stage = row.get("stage_id")
            raw_owner = row.get("owner_id")
            leads_info.append(
                LeadInfo(
                    id=str(row["id"]),
                    name=(row.get("name") or "").strip() or "",
                    stage_id=str(raw_stage) if raw_stage is not None else None,
                    stage_name=row.get("stage_name"),
                    deal_type=row.get("deal_type"),
                    priority=row.get("priority"),
                    lead_score=row.get("lead_score"),
                    close_date=row.get("close_date"),
                    amount=row.get("amount"),
                    owner_id=str(raw_owner) if raw_owner is not None else None,
                    owner_name=row.get("owner_name"),
                    lead_source=row.get("lead_source"),
                    referral_source=row.get("referral_source"),
                    created_at=format_iso_datetime(row.get("created_at")),
                    updated_at=format_iso_datetime(row.get("updated_at")),
                )
            )

        # Format addresses
        formatted_addresses = []
        for addr in addresses:
            address_data = parse_json_field(addr.get("address_data"))
            formatted_addresses.append(
                ClientAddressResponse(
                    id=str(addr.get("id")),
                    place_id=addr.get("place_id"),
                    address_line1=addr.get("address_line1"),
                    address_line2=addr.get("address_line2"),
                    city=addr.get("city"),
                    state=addr.get("state"),
                    postal_code=addr.get("postal_code"),
                    country=addr.get("country"),
                    latitude=float(addr.get("latitude"))
                    if addr.get("latitude") is not None
                    else None,
                    longitude=float(addr.get("longitude"))
                    if addr.get("longitude") is not None
                    else None,
                    address_type=addr.get("address_type"),
                    address_data=address_data or {},
                    is_primary=bool(addr.get("is_primary", False)),
                    created_at=format_iso_datetime(addr.get("created_at")) or "",
                    updated_at=format_iso_datetime(addr.get("updated_at")) or "",
                )
            )

        # Build response
        is_person = client.get("client_type") == ClientType.PERSON.value
        company_id = client.get("company_id") if is_person else None
        company_name = client.get("company_name") if is_person else None

        return ClientDetailsResponse(
            id=str(client.get("id")),
            organization_id=str(client.get("organization_id")),
            client_type=client.get("client_type"),
            name=client.get("name") or "",
            company_id=str(company_id),
            company_name=company_name,
            status=client.get("status"),
            portal_access=client.get("portal_access", PORTAL_ACCESS_DEFAULT)
            if client.get("portal_access", PORTAL_ACCESS_DEFAULT) is not None
            else PORTAL_ACCESS_DEFAULT,
            industry=client.get("industry"),
            image_url=client.get("contact_profile_photo_url") or client.get("profile_photo_url"),
            tags=client.get("tags") or [],
            primary_contact=primary_contact,
            company_contacts=company_contacts,
            websites=websites,
            billing_preferences=billing_preferences,
            custom_fields=custom_fields,
            addresses=formatted_addresses,
            leads=leads_info,
            additional_data=additional_data,
            sales_intelligence=sales_intelligence,
            social_pages=social_pages,
            work_history=work_history,
            educational_history=educational_history,
            skills=skills,
            target_market_segments=target_market_segments,
            current_tech_stack=current_tech_stack,
            description=client.get("description"),
            preferred_communication_channels=preferred_communication_channels,
            industry_specific_terminologies=industry_specific_terminologies,
            linked_pages=linked_pages,
            products=products,
            key_people=key_people,
            enrichment_done=bool(client.get("enrichment_done", False)),
            last_enriched_at=format_iso_datetime(client.get("last_enriched_at")),
            created_at=format_iso_datetime(client.get("created_at")) or "",
            updated_at=format_iso_datetime(client.get("updated_at")) or "",
        )

    async def trigger_enrichment(
        self,
        client_id: str,
        organization_id: str,
        conn: asyncpg.Connection | None = None,
    ) -> None:
        """Trigger enrichment for an existing client using current persisted data.

        Rebuilds the minimal enrichment payload from the latest client details and
        calls the existing ClientEnrichmentService.run_client_enrichment helper.

        If conn is provided, it is passed through to the enrichment service and its
        lifecycle is managed by the caller. When conn is None, the service falls back
        to using the ClientService.db_connection (which is typically managed by the
        FastAPI dependency layer).
        """
        # Validate client_id format early to avoid DB layer errors
        validate_uuid_format(client_id, "client_id")

        details = await self.get_client_details(client_id, organization_id)

        # Build payload from current details; only fields used by enrichment builders.
        addresses_payload = [
            {"country": addr.country} for addr in details.addresses if addr.country
        ]

        primary = details.primary_contact
        primary_phone = None
        for phone in primary.phones:
            if phone.is_primary:
                primary_phone = phone
                break
        if primary_phone is None and primary.phones:
            primary_phone = primary.phones[0]

        if details.client_type == ClientType.PERSON:
            payload_data: dict[str, Any] = {
                "first_name": primary.first_name or "",
                "middle_name": primary.middle_name or "",
                "last_name": primary.last_name or "",
                "email": primary.email,
                "company": details.company_name,
                "addresses": addresses_payload,
            }
            if primary_phone:
                payload_data["phone_isd_code"] = primary_phone.phone_isd_code
                payload_data["phone_number"] = primary_phone.phone_number
        else:
            payload_data = {
                "name": details.name,
                "industry": details.industry,
                "email": primary.email,
                "websites": [w.model_dump(mode="json") for w in details.websites],
                "social_pages": [
                    social_page.model_dump(mode="json") for social_page in details.social_pages
                ],
                "addresses": addresses_payload,
            }

        enrichment_service = ClientEnrichmentService.from_settings()
        await enrichment_service.run_client_enrichment(
            client_id=client_id,
            organization_id=organization_id,
            client_type=details.client_type.value,
            payload_data=payload_data,
            conn=conn or self.db_connection,
        )

    async def delete_client(self, client_id: str, organization_id: str) -> None:
        """Soft delete a client.

        Args:
            client_id: Client ID
            organization_id: Organization ID

        Raises:
            NotFoundException: If client not found
        """
        # Soft delete client (existence check is handled in repository)
        await self.client_repository.delete_client(client_id, organization_id)
        await self.client_repository.delete_client_users(client_id)
        await self.lead_repository.delete_leads_by_client_id(client_id)
        await self.client_repository.delete_addresses(client_id)

    async def update_client(
        self, client_id: str, organization_id: str, body: UpdateClientRequest
    ) -> dict | None:
        """Update a client by ID. Only provided fields are applied (PATCH semantics).

        Scalar fields (name, industry, etc.) are set when provided. Nested structures
        use standard delta semantics: websites and addresses support add/update/remove;
        billing_preferences and custom_fields are merged with existing. Client must exist
        when any update is requested.

        Args:
            client_id: Client ID
            organization_id: Organization ID
            body: PATCH body; only non-None fields are applied

        Returns:
            dict | None: Result with old/new data for audit when update is applied, None when no-op

        Raises:
            NotFoundException: If client not found
            ConflictException: If client name already exists
            ValidationException: If client_company_id and is_primary_contact are provided together
        """
        if not self._has_any_update(body):
            return None

        current = await self.client_repository.get_client_for_update(client_id, organization_id)
        if not current:
            raise NotFoundException(
                message_key="clients.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        self._validate_client_type_scope(body, current.get("client_type", ""))
        # Defensive check mirroring UpdateClientRequest validator.
        # Prevents implicit service-level combinations from creating invalid state.
        if body.client_company_id is not None and body.is_primary_contact is not None:
            raise ValidationException(
                message_key="clients.errors.client_company_and_primary_contact_mutually_exclusive",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        old_data = self._format_client_for_audit(current)

        created_company_id: str | None = None
        if current.get("client_type") == ClientType.PERSON.value:
            created_company_id = await self._create_and_link_company_for_person_update(
                organization_id=organization_id,
                body=body,
            )

        update_data = await self._build_client_update_payload(body, current)

        await self._apply_batch_jsonb_list_changes(
            body=body,
            current=current,
            payload=update_data,
        )

        primary_contact_audit = await self._apply_primary_contact_update_if_needed(
            client_id=client_id,
            organization_id=organization_id,
            primary_contact=body.primary_contact,
            is_person=current.get("client_type") == ClientType.PERSON.value,
            profile_photo_url=body.profile_photo_url,
            client_company_id=body.client_company_id or created_company_id,
            is_primary_contact=False
            if body.client_company_id is not None
            else body.is_primary_contact,
        )
        person_name_from_primary_contact = (
            primary_contact_audit.get("person_full_name") if primary_contact_audit else None
        )

        if body.lead_management is not None:
            await self._apply_lead_update(body.lead_management)

        if (
            current.get("client_type") == ClientType.PERSON.value
            and person_name_from_primary_contact
            and person_name_from_primary_contact != (current.get("name") or "")
        ):
            update_data["name"] = person_name_from_primary_contact

        updated_client_row = None
        if update_data:
            updated_client_row = await self.client_repository.update_client(
                client_id, organization_id, update_data
            )

        new_data = self._format_client_for_audit(updated_client_row or current)
        if primary_contact_audit:
            old_data["client_user"] = primary_contact_audit["old_client_user"]
            new_data["client_user"] = primary_contact_audit["new_client_user"]

        return {
            "old_data": old_data,
            "new_data": new_data,
            "created_company_id": created_company_id,
        }

    async def _create_and_link_company_for_person_update(
        self,
        organization_id: str,
        body: UpdateClientRequest,
    ) -> str | None:
        """For person updates, create linked company from primary_contact.company_name.

        This keeps PATCH behavior intact for all other fields while adding the
        requested contact->company linking flow.
        """
        company_name = (body.company_name or "").strip()
        if not company_name:
            return None

        company_rows = await self.client_repository.create_client(
            [
                {
                    "organization_id": organization_id,
                    "client_type": ClientType.COMPANY.value,
                    "name": company_name,
                }
            ]
        )
        if not company_rows:
            return None
        company_id = company_rows[0]["id"]
        return str(company_id)

    async def _apply_primary_contact_update_if_needed(
        self,
        client_id: str,
        organization_id: str,
        primary_contact: PrimaryContactUpdate | None,
        is_person: bool,
        profile_photo_url: str | None,
        client_company_id: str | None = None,
        is_primary_contact: bool | None = None,
    ) -> dict[str, Any] | None:
        """Apply primary_contact updates and return audit snapshots when contact changed."""
        if (
            primary_contact is None
            and profile_photo_url is None
            and client_company_id is None
            and is_primary_contact is None
            and primary_contact is None
        ):
            return None

        primary_contact_row = await self.client_repository._get_primary_contact_for_update(
            client_id, organization_id
        )
        if not primary_contact_row:
            return None

        return await self._apply_primary_contact_updates(
            primary_contact_row=primary_contact_row,
            primary_contact=primary_contact,
            is_person=is_person,
            profile_photo_url=profile_photo_url,
            client_company_id=client_company_id,
            is_primary_contact=is_primary_contact,
            organization_id=organization_id,
        )

    @staticmethod
    def _format_client_for_audit(client_data: dict[str, Any]) -> dict[str, Any]:
        """Format client data for audit logging.

        Extracts and formats client fields into a structure suitable for audit log comparison.

        Args:
            client_data: Raw client data from database

        Returns:
            Dictionary with formatted client data for audit logging
        """
        return {
            "client_id": str(client_data.get("id")),
            "name": client_data.get("name"),
            "industry": client_data.get("industry"),
            "profile_photo_url": client_data.get("profile_photo_url"),
            "portal_access": client_data.get("portal_access"),
            "tags": client_data.get("tags"),
            "websites": parse_json_field(client_data.get("websites")),
            "billing_preferences": parse_json_field(client_data.get("billing_preferences")),
            "custom_fields": parse_json_field(client_data.get("custom_fields")),
            "additional_data": parse_json_field(client_data.get("additional_data")),
            "social_pages": parse_json_field(client_data.get("social_pages")),
            "work_history": parse_json_field(client_data.get("work_history")),
            "educational_history": parse_json_field(client_data.get("educational_history")),
            "skills": parse_json_field(client_data.get("skills")),
            "target_market_segments": parse_json_field(client_data.get("target_market_segments")),
            "current_tech_stack": parse_json_field(client_data.get("current_tech_stack")),
            "description": client_data.get("description"),
            "preferred_communication_channels": parse_json_field(
                client_data.get("preferred_communication_channels")
            ),
            "industry_specific_terminologies": parse_json_field(
                client_data.get("industry_specific_terminologies")
            ),
            "linked_pages": parse_json_field(client_data.get("linked_pages")),
            "products": parse_json_field(client_data.get("products")),
            "key_people": parse_json_field(client_data.get("key_people")),
            "enrichment_done": client_data.get("enrichment_done"),
            "last_enriched_at": client_data.get("last_enriched_at"),
        }

    def _has_any_update(self, body: UpdateClientRequest) -> bool:
        """Return True if body contains at least one field to apply."""
        return any(
            getattr(body, name) is not None
            for name in (
                "company_name",
                "client_company_id",
                "is_primary_contact",
                "industry",
                "profile_photo_url",
                "portal_access",
                "tags",
                "websites",
                "addresses",
                "lead_management",
                "billing_preferences",
                "custom_fields",
                "additional_data",
                "social_pages",
                "enrichment_done",
                "last_enriched_at",
                "work_history",
                "educational_history",
                "skills",
                "target_market_segments",
                "current_tech_stack",
                "description",
                "preferred_communication_channels",
                "industry_specific_terminologies",
                "linked_pages",
                "products",
                "key_people",
                "primary_contact",
            )
        )

    def _validate_client_type_scope(self, body: UpdateClientRequest, client_type: str) -> None:
        """Validate that update fields are scoped to the correct client type.

        Person type: work_history, educational_history, skills only.
        Company type: target_market_segments, current_tech_stack, description,
        preferred_communication_channels, industry_specific_terminologies, linked_pages only.
        """
        person_only = (
            body.work_history is not None
            or body.educational_history is not None
            or body.skills is not None
        )
        company_only = (
            body.target_market_segments is not None
            or body.current_tech_stack is not None
            or body.description is not None
            or body.preferred_communication_channels is not None
            or body.industry_specific_terminologies is not None
            or body.linked_pages is not None
        )
        if client_type == ClientType.PERSON.value and company_only:
            raise ValidationException(
                message_key="clients.errors.company_fields_not_allowed_for_person",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if client_type == ClientType.COMPANY.value and person_only:
            raise ValidationException(
                message_key="clients.errors.person_fields_not_allowed_for_company",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    def _apply_simple_client_update_fields(
        self, body: UpdateClientRequest, payload: dict[str, Any]
    ) -> None:
        """Apply simple (no-merge) client fields from body onto payload."""
        simple_fields = [
            ("company_name", "name"),
            ("industry", "industry"),
            ("profile_photo_url", "profile_photo_url"),
            ("portal_access", "portal_access"),
            ("tags", "tags"),
            ("enrichment_done", "enrichment_done"),
            ("last_enriched_at", "last_enriched_at"),
            ("skills", "skills"),
            ("target_market_segments", "target_market_segments"),
            ("current_tech_stack", "current_tech_stack"),
            ("description", "description"),
            ("preferred_communication_channels", "preferred_communication_channels"),
            ("industry_specific_terminologies", "industry_specific_terminologies"),
        ]
        for body_attr, payload_key in simple_fields:
            value = getattr(body, body_attr, None)
            if value is not None:
                payload[payload_key] = value

    def _merge_billing_preferences_into_payload(
        self,
        body: UpdateClientRequest,
        current: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        """Merge body.billing_preferences with current and set on payload."""
        if body.billing_preferences is None:
            return
        existing = parse_json_field(current.get("billing_preferences"))
        payload["billing_preferences"] = {
            **existing,
            **body.billing_preferences.model_dump(exclude_none=True),
        }

    async def _merge_custom_fields_into_payload(
        self,
        body: UpdateClientRequest,
        current: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        """Merge body.custom_fields with current, validate, and set on payload."""
        if not (self.user_context and self.user_context.organization_id):
            return

        existing = parse_json_field(current.get("custom_fields"))
        merged = existing if isinstance(existing, list) else []

        client_type = current.get("client_type", "")
        entity_type = (
            EntityType.COMPANY if client_type == ClientType.COMPANY.value else EntityType.CONTACT
        )

        custom_field_service = CustomFieldService(
            db_connection=self.db_connection,
            user_context=self.user_context,
        )

        patch = body.custom_fields if body.custom_fields is not None else None
        merged = await custom_field_service.merge_for_update(patch, merged, entity_type)
        if json.dumps(merged, sort_keys=True, default=str) != json.dumps(
            existing if isinstance(existing, list) else [],
            sort_keys=True,
            default=str,
        ):
            payload["custom_fields"] = json.dumps(merged)

    async def _build_client_update_payload(
        self, body: UpdateClientRequest, current: dict[str, Any]
    ) -> dict[str, Any]:
        """Build the client row update dict from body and current row (merge where needed)."""
        payload: dict[str, Any] = {}
        self._apply_simple_client_update_fields(body, payload)
        if body.additional_data is not None:
            payload["additional_data"] = body.additional_data
        self._merge_billing_preferences_into_payload(body, current, payload)
        await self._merge_custom_fields_into_payload(body, current, payload)
        return payload

    @staticmethod
    def _dedupe_string_list_fields(document: dict[str, Any]) -> None:
        """Deduplicate `string[]`-like fields while preserving the first occurrence order.

        Typesense facets/search operate on sets of terms, so duplicates only increase
        index size and noise without improving recall.
        """
        for key, value in document.items():
            if not isinstance(value, list):
                continue
            seen: set[str] = set()
            deduped: list[str] = []
            for item in value:
                if item is None:
                    continue
                # Keep strict `list[str]` shape by stringifying non-str items.
                string_item = item if isinstance(item, str) else str(item)
                if string_item in seen:
                    continue
                seen.add(string_item)
                deduped.append(string_item)
            document[key] = deduped

    @staticmethod
    def _ensure_list_item_ids(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return a copy of list items with an id set on each (generated if missing).
        Use for all JSONB list fields so create, patch, and enrichment behave the same."""
        result: list[dict[str, Any]] = []
        for item in items:
            row = dict(item)
            if not row.get("id"):
                row["id"] = str(uuid.uuid4())
            result.append(row)
        return result

    @staticmethod
    def _parse_primary_contact_phones(raw_phones: Any) -> list[Phone]:
        """Parse phones from DB (JSON string or list) to list[Phone] objects."""
        parsed = parse_json_field(raw_phones)
        if not isinstance(parsed, list):
            parsed = []
        phones_list: list[Phone] = []
        for phone in parsed:
            if not isinstance(phone, dict):
                continue
            try:
                phones_list.append(
                    Phone(
                        id=str(phone["id"]) if phone.get("id") else None,
                        phone_number=phone.get("phone_number") or "",
                        phone_isd_code=phone.get("phone_isd_code") or "",
                        label=phone.get("label"),
                        is_primary=bool(phone.get("is_primary", False)),
                    )
                )
            except (KeyError, TypeError):
                continue
        return phones_list

    @staticmethod
    def _map_company_contacts(
        raw_contacts: Iterable[dict[str, Any]],
    ) -> list[CompanyContact]:
        """Map raw DB contact rows into CompanyContact domain models."""
        return [
            CompanyContact(
                name=build_full_name(r.get("first_name"), r.get("last_name")) or None,
                designation=r.get("title"),
                email=r.get("email"),
                is_primary_contact=bool(r.get("is_primary_contact", False)),
            )
            for r in raw_contacts
        ]

    async def _apply_lead_update(self, lead: LeadManagementUpdate) -> None:
        """Apply lead update by lead_id; only provided fields are sent to repository."""
        lead_data = lead.model_dump(
            exclude={"lead_id"},
            exclude_none=True,
            mode="json",
        )
        lead_data.pop("enabled", None)
        if "notes" in lead_data:
            note_text = lead_data["notes"]
            if isinstance(note_text, str):
                stripped = note_text.strip()
                lead_data["notes"] = [{"title": "Note", "content": stripped}] if stripped else []
        if not lead_data:
            return
        organization_id = self.user_context.organization_id
        await self.lead_repository.update_lead(organization_id, lead.lead_id, lead_data)

    async def _apply_jsonb_list_changes(
        self,
        update_obj: Any,
        current: dict[str, Any],
        payload: dict[str, Any],
        field_name: str,
        not_found_message_key: str,
    ) -> None:
        """Generic helper to apply batch JSONB list operations: add, update, and/or remove.

        This function handles the common pattern of updating JSONB list fields in the client
        record. It supports three operations:
        - remove: Remove items by their IDs
        - update: Update existing items by ID (raises NotFoundException if item not found)
        - add: Add new items with auto-generated UUIDs

        Args:
            update_obj: Update object with optional add, update, remove attributes
            current: Current client data dict
            payload: Final client update payload to populate
            field_name: Name of the JSONB field to update (e.g., "websites", "work_history")
            not_found_message_key: Message key for NotFoundException when update item not found

        Raises:
            NotFoundException: If an update item ID is not found in the current list
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

        # Add operations
        if hasattr(update_obj, "add") and update_obj.add:
            for item in update_obj.add:
                new_item = item.model_dump(exclude_none=True)
                new_item["id"] = str(uuid.uuid4())
                updated.append(new_item)

        # Stage JSONB change for the final single client-row update.
        payload[field_name] = updated

    async def _apply_batch_jsonb_list_changes(
        self,
        body: UpdateClientRequest,
        current: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        """Apply batch JSONB list changes."""
        # Apply batch list operations (addresses, websites, social_pages)
        if body.addresses is not None:
            await self._apply_addresses_changes(str(current["id"]), body.addresses)

        if body.websites is not None:
            await self._apply_jsonb_list_changes(
                body.websites,
                current,
                payload,
                "websites",
                "clients.errors.website_not_found",
            )

        if body.social_pages is not None:
            await self._apply_jsonb_list_changes(
                body.social_pages,
                current,
                payload,
                "social_pages",
                "clients.errors.social_page_not_found",
            )

        if body.work_history is not None:
            await self._apply_jsonb_list_changes(
                body.work_history,
                current,
                payload,
                "work_history",
                "clients.errors.work_history_item_not_found",
            )

        if body.educational_history is not None:
            await self._apply_jsonb_list_changes(
                body.educational_history,
                current,
                payload,
                "educational_history",
                "clients.errors.educational_history_item_not_found",
            )

        if body.linked_pages is not None:
            await self._apply_jsonb_list_changes(
                body.linked_pages,
                current,
                payload,
                "linked_pages",
                "clients.errors.linked_page_not_found",
            )

        if body.products is not None:
            await self._apply_jsonb_list_changes(
                body.products,
                current,
                payload,
                "products",
                "clients.errors.product_not_found",
            )

        if body.key_people is not None:
            await self._apply_jsonb_list_changes(
                body.key_people,
                current,
                payload,
                "key_people",
                "clients.errors.key_person_not_found",
            )

    async def _apply_primary_contact_updates(
        self,
        primary_contact_row: dict[str, Any],
        primary_contact: PrimaryContactUpdate | None,
        is_person: bool,
        profile_photo_url: str | None = None,
        client_company_id: str | None = None,
        is_primary_contact: bool | None = None,
        organization_id: str | None = None,
    ) -> dict[str, Any]:
        """Apply primary contact scalar fields and phones.

        Returns:
            dict[str, Any]: person_full_name and client_user old/new snapshots for audit.
        """
        primary_id = primary_contact_row["id"]

        update_data = (
            self._build_primary_contact_update_data(primary_contact)
            if primary_contact is not None
            else {}
        )
        if client_company_id is not None:
            update_data["client_company_id"] = client_company_id
        if is_primary_contact is False:
            update_data["is_primary_contact"] = False
        if profile_photo_url is not None:
            update_data["profile_photo_url"] = profile_photo_url
        if primary_contact is not None and primary_contact.phones is not None:
            update_data["phones"] = self._build_primary_contact_phones_json(
                existing_phones_json=primary_contact_row.get("phones"),
                phones_update=primary_contact.phones,
            )

        if is_primary_contact is True:
            resolved_company_id = client_company_id or primary_contact_row.get("client_company_id")
            if resolved_company_id and organization_id:
                await self.client_repository.clear_primary_contact_for_company(
                    company_client_id=resolved_company_id,
                    organization_id=organization_id,
                    exclude_client_user_id=primary_id,
                )
            update_data["is_primary_contact"] = True
        old_contact_snapshot = self._format_client_user_for_audit(primary_contact_row)
        updated_contact_row = None
        if update_data:
            updated_contact_row = await self.client_repository._update_client_user(
                primary_id, update_data
            )
        new_contact_snapshot = self._format_client_user_for_audit(
            updated_contact_row or primary_contact_row
        )

        # Person only: return computed full name so caller can fold it into client update_data.
        person_full_name = None
        if primary_contact is not None and self._should_compute_person_full_name(
            is_person=is_person, update=primary_contact
        ):
            person_full_name = self._compute_person_full_name(
                primary_contact_row=primary_contact_row,
                update=primary_contact,
            )

        return {
            "person_full_name": person_full_name,
            "old_client_user": old_contact_snapshot,
            "new_client_user": new_contact_snapshot,
        }

    @staticmethod
    def _format_client_user_for_audit(client_user_data: dict[str, Any]) -> dict[str, Any]:
        """Format primary contact row for client-user audit snapshots."""
        return {
            "client_user_id": (
                str(client_user_data.get("id")) if client_user_data.get("id") else None
            ),
            "first_name": client_user_data.get("first_name"),
            "middle_name": client_user_data.get("middle_name"),
            "last_name": client_user_data.get("last_name"),
            "phones": parse_json_field(client_user_data.get("phones")),
            "client_company_id": (
                str(client_user_data.get("client_company_id"))
                if client_user_data.get("client_company_id")
                else None
            ),
            "is_primary_contact": client_user_data.get("is_primary_contact"),
            "profile_photo_url": client_user_data.get("profile_photo_url"),
        }

    @staticmethod
    def _build_primary_contact_update_data(update: PrimaryContactUpdate) -> dict[str, Any]:
        """Build DB update payload for primary contact scalar fields."""
        update_data: dict[str, Any] = {}
        if update.salutation is not None:
            update_data["prefix"] = update.salutation
        if update.first_name is not None:
            update_data["first_name"] = update.first_name
        if update.middle_name is not None:
            update_data["middle_name"] = update.middle_name
        if update.last_name is not None:
            update_data["last_name"] = update.last_name
        if update.title is not None:
            update_data["title"] = update.title
        return update_data

    @staticmethod
    def _build_primary_contact_phones_json(
        *,
        existing_phones_json: Any,
        phones_update: PhonesUpdate,
    ) -> str:
        """Apply phones add/update/remove operations and return JSON string."""
        # Semantics intentionally match _apply_primary_contact_phones / inlined prior logic.
        current_list = parse_json_field(existing_phones_json) or []
        if not isinstance(current_list, list):
            current_list = []
        updated = current_list.copy()

        if phones_update.remove:
            updated = [p for p in updated if str(p.get("id")) not in phones_update.remove]

        if phones_update.update:
            for item in phones_update.update:
                data = item.model_dump(exclude_none=True, exclude={"id"})
                found = False
                for i, existing in enumerate(updated):
                    if str(existing.get("id")) == item.id:
                        updated[i] = {**existing, **data}
                        found = True
                        break
                if not found:
                    raise NotFoundException(
                        message_key="clients.errors.phone_not_found",
                        custom_code=CustomStatusCode.NOT_FOUND,
                    )

        if phones_update.add:
            for item in phones_update.add:
                new_item = item.model_dump(exclude_none=True)
                new_item["id"] = str(uuid.uuid4())
                updated.append(new_item)

        return json.dumps(updated)

    @staticmethod
    def _should_compute_person_full_name(
        *,
        is_person: bool,
        update: PrimaryContactUpdate,
    ) -> bool:
        """Return True when person name parts were provided in update."""
        return is_person and (
            update.first_name is not None
            or update.middle_name is not None
            or update.last_name is not None
        )

    @staticmethod
    def _compute_person_full_name(
        *,
        primary_contact_row: dict[str, Any],
        update: PrimaryContactUpdate,
    ) -> str | None:
        """Compute full name from updated + existing name parts."""
        first = (
            update.first_name
            if update.first_name is not None
            else (primary_contact_row.get("first_name") or "")
        )
        middle = (
            update.middle_name
            if update.middle_name is not None
            else (primary_contact_row.get("middle_name") or "")
        )
        last = (
            update.last_name
            if update.last_name is not None
            else (primary_contact_row.get("last_name") or "")
        )
        new_name = build_full_name(
            str(first).strip(),
            str(middle).strip(),
            str(last).strip(),
        ).strip()
        return new_name or None

    async def _apply_addresses_changes(
        self,
        client_id: str,
        addresses_update: AddressesUpdate,
    ) -> None:
        """Apply batch address operations: add, update, and/or remove."""
        try:
            await self._delete_removed_addresses(client_id, addresses_update.remove)
            await self._update_existing_addresses(client_id, addresses_update.update)
            await self._add_new_addresses(client_id, addresses_update.add)
        except UniqueViolationError as exc:
            if getattr(exc, "constraint_name", None) == "uq_client_primary_address":
                raise ValidationException(
                    message_key="clients.errors.only_one_primary_address",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                ) from exc
            raise

    async def _delete_removed_addresses(
        self, client_id: str, address_ids_to_remove: list[str] | None
    ) -> None:
        """Delete address rows listed in remove operation."""
        if not address_ids_to_remove:
            return
        await self.client_repository._delete_addresses_by_ids(client_id, address_ids_to_remove)

    async def _update_existing_addresses(
        self,
        client_id: str,
        address_updates: list[AddressUpdateItem] | None,
    ) -> None:
        """Apply partial updates to existing addresses."""
        if not address_updates:
            return

        for update_item in address_updates:
            payload = update_item.model_dump(exclude_none=True, exclude={"id"})
            if not payload:
                continue
            if payload.get("is_primary") is True:
                await self.client_repository.clear_primary_addresses(
                    client_id, exclude_address_id=update_item.id
                )
            await self.client_repository.update_address(update_item.id, client_id, payload)

    async def _add_new_addresses(
        self,
        client_id: str,
        addresses_to_add: list[AddressInput] | None,
    ) -> None:
        """Create new address rows from add operation."""
        if not addresses_to_add:
            return

        if any(item.is_primary is True for item in addresses_to_add):
            await self.client_repository.clear_primary_addresses(client_id)

        rows = self._build_address_rows_for_create(client_id, addresses_to_add)
        await self.client_repository.bulk_create_addresses(rows)

    @staticmethod
    def _build_address_rows_for_create(
        client_id: str, addresses_to_add: list[AddressInput]
    ) -> list[dict[str, Any]]:
        """Build DB rows for bulk address creation."""
        rows: list[dict[str, Any]] = []
        for add_item in addresses_to_add:
            row: dict[str, Any] = {"client_id": client_id}
            row.update(add_item.model_dump(exclude_none=True))
            rows.append(row)
        return rows

    async def _sync_user_with_social_service(
        self,
        user_id: str,
        organization_id: str,
        email: str,
        isometrik_user_id: str,
    ) -> bool:
        """Sync user with social service.

        Args:
            user_id: User ID
            organization_id: Organization ID
            email: User email
            isometrik_user_id: Isometrik user ID

        Returns:
            bool: True if sync is successful, False otherwise
        """
        if app_settings.external_service.social_service_url:
            username = email.split("@")[0] if email else str(user_id)
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        f"{app_settings.external_service.social_service_url}/api/v1/users",
                        json={
                            "id": str(user_id),
                            "username": username,
                            "user_metadata": {
                                "isometrik_user_id": isometrik_user_id,
                            },
                        },
                        headers={
                            "lan": "en",
                            "x-tenant-id": shared_settings.isometrik.client_name,
                            "x-project-id": organization_id,
                            "Content-Type": "application/json",
                        },
                    )
                    if response.status_code in {200, 201}:
                        return True

                    logger.error(
                        "Failed to sync user with social service: %s",
                        response.text,
                        extra={
                            "user_id": str(user_id),
                            "organization_id": str(organization_id),
                            "email": email,
                            "isometrik_user_id": isometrik_user_id,
                        },
                    )
                    return False
            except Exception as e:
                logger.error(
                    "Failed to sync user with social service: %s",
                    str(e),
                    extra={
                        "user_id": str(user_id),
                        "organization_id": str(organization_id),
                        "email": email,
                        "isometrik_user_id": isometrik_user_id,
                    },
                )
        return False
