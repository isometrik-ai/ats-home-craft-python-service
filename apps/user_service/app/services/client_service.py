"""Service for client business logic

This service handles all business logic related to clients, including
validation, formatting, and orchestration of client operations.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Any

import asyncpg
import httpx
from supabase import AsyncClient

from apps.user_service.app.config.app_settings import app_settings, shared_settings
from apps.user_service.app.db.repositories import (
    ClientRepository,
    OrganizationRepository,
    UserEventRepository,
    UserRepository,
)
from apps.user_service.app.schemas.clients import (
    AddressesUpdate,
    BillingPreferences,
    ClientAddressResponse,
    ClientDetailsResponse,
    ClientListResponse,
    CreateClientFromUserRequest,
    CreateClientRequest,
    EducationalHistoryItem,
    KeyPerson,
    LeadInfo,
    LeadManagementUpdate,
    LinkedPageItem,
    PrimaryContactInfo,
    Product,
    SocialPage,
    UpdateClientRequest,
    Website,
    WorkHistoryItem,
)
from apps.user_service.app.schemas.enums import (
    ClientType,
    EntityType,
    IsometrikRole,
    UserEventStatus,
)
from apps.user_service.app.services.custom_field_service import CustomFieldService
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
    generate_random_password,
    parse_json_field,
    safe_json_loads,
    serialize_pydantic_models,
)
from apps.user_service.app.utils.email_utils import send_client_creation_email
from apps.user_service.app.utils.user_utils import build_full_name
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

logger = get_logger("client_service")


@dataclass
class CreateClientResult:
    """Result of client creation: persisted records and items to run enrichment on."""

    records: list[dict[str, Any]]
    enrichment_items: list[dict[str, Any]]


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
        self.supabase_client = supabase_client

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

        # Check if client_user already exists
        client_user_exists = await self.client_repository.check_client_user_exists(
            user_id=user_id, organization_id=organization_id
        )
        if client_user_exists:
            raise ConflictException(
                message_key="clients.errors.user_already_a_client",
                custom_code=CustomStatusCode.CONFLICT,
            )

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
            user_id=user_id,
            email=user_details.get("email"),
            isometrik_credentials=isometrik_credentials,
            organization_id=organization_id,
            role=IsometrikRole.CLIENT.value,
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
            user_id=user_id, status=UserEventStatus.COMPLETED
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

        # Validate email uniqueness (check across all auth.users)
        user_repository = UserRepository(db_connection=self.db_connection)
        existing_user = await user_repository.get_auth_user_by_email(request_data.email)
        if existing_user:
            raise ConflictException(
                message_key="clients.errors.email_already_exists",
                custom_code=CustomStatusCode.CONFLICT,
            )
        # Validate phone uniqueness (check across all auth.users)
        phone_exists = await user_repository.phone_exists_for_other_user(
            phone=f"{request_data.phone_isd_code}{request_data.phone_number}",
        )
        if phone_exists:
            raise ConflictException(
                message_key="clients.errors.phone_number_already_exists",
                custom_code=CustomStatusCode.CONFLICT,
            )

        # Validate name uniqueness (use same name as stored in DB)
        name_to_check = None
        if request_data.client_type == ClientType.PERSON:
            name_to_check = build_full_name(request_data.first_name, request_data.last_name).strip()
        elif request_data.client_type == ClientType.COMPANY:
            name_to_check = (request_data.name or "").strip()

        name_exists = await self.client_repository.check_client_name_exists(
            name=name_to_check,
            organization_id=organization_id,
        )
        if name_exists:
            raise ConflictException(
                message_key="clients.errors.client_name_already_exists",
                custom_code=CustomStatusCode.CONFLICT,
            )

        # When creating a person with a company name (and not linking to existing company),
        # validate that the company name does not already exist (we will create a new company).
        if request_data.client_type == ClientType.PERSON and not request_data.client_company_id:
            company_name_for_person = (request_data.company or request_data.name or "").strip()
            if company_name_for_person:
                company_name_exists = await self.client_repository.check_client_name_exists(
                    name=company_name_for_person,
                    organization_id=organization_id,
                )
                if company_name_exists:
                    raise ConflictException(
                        message_key="clients.errors.client_name_already_exists",
                        custom_code=CustomStatusCode.CONFLICT,
                    )

        return organization

    async def _create_auth_and_isometrik_user(
        self, request_data: CreateClientRequest, organization: dict[str, Any], organization_id: str
    ) -> tuple[str, str, str]:
        """Create Supabase auth user and Isometrik user.

        Args:
            request_data: Request data
            organization: Organization data
            organization_id: Organization ID

        Returns:
            tuple: (user_id, isometrik_user_id, password)

        Raises:
            ServiceUnavailableException: If auth or Isometrik creation fails
        """
        # Generate a random password for the client user
        password = generate_random_password()

        # Build phone number for auth
        phone = f"{request_data.phone_isd_code}{request_data.phone_number}"

        # Build user metadata same as signup/invite accept flow
        user_metadata: dict[str, Any] = {
            "phone_number": request_data.phone_number,
            "phone_isd_code": request_data.phone_isd_code,
            "timezone": "UTC",
            "first_name": request_data.first_name,
            "last_name": request_data.last_name,
        }

        # Add person-specific fields if client type is PERSON
        if request_data.client_type == ClientType.PERSON:
            if request_data.prefix:
                user_metadata["salutation"] = request_data.prefix

        # Create Supabase auth user with generated password
        auth_user = await create_user(
            sb_client=self.supabase_client,
            email=request_data.email,
            password=password,
            phone=phone,
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
            user_id=user_id,
            email=request_data.email,
            isometrik_credentials=isometrik_credentials,
            organization_id=organization_id,
            role=IsometrikRole.CLIENT.value,
            first_name=isometrik_first_name,
            last_name=isometrik_last_name,
        )
        if not isometrik_response or not isometrik_response.get("userId"):
            raise ServiceUnavailableException(
                message_key="clients.errors.isometrik_user_creation_failed",
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
            )

        return user_id, isometrik_response["userId"], password

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
        if request_data.client_type == ClientType.PERSON:
            client_name = build_full_name(request_data.first_name, request_data.last_name)
        else:
            client_name = request_data.name or ""
        client_data = {
            "organization_id": organization_id,
            "client_type": request_data.client_type.value,
            "name": client_name,
        }

        if request_data.industry:
            client_data["industry"] = request_data.industry
        if request_data.profile_photo_url:
            client_data["profile_photo_url"] = request_data.profile_photo_url
        if request_data.tags:
            client_data["tags"] = request_data.tags

        # Add portal_access field (defaults to False if not provided)
        client_data["portal_access"] = request_data.portal_access

        # Serialize JSONB fields for asyncpg (business logic in service layer)
        if request_data.websites:
            serialized_websites = serialize_pydantic_models(request_data.websites)
            client_data["websites"] = json.dumps(self._ensure_list_item_ids(serialized_websites))
        if request_data.billing_preferences:
            serialized_billing = serialize_pydantic_models(request_data.billing_preferences)
            client_data["billing_preferences"] = json.dumps(serialized_billing)
        if request_data.custom_fields:
            # Validate and format custom fields against definitions
            entity_type = (
                EntityType.COMPANY
                if request_data.client_type == ClientType.COMPANY
                else EntityType.CONTACT
            )
            custom_field_service = CustomFieldService(
                db_connection=self.db_connection,
                user_context=self.user_context,
            )
            validated_custom_fields = await custom_field_service.validate_and_format_custom_fields(
                request_data.custom_fields, entity_type
            )
            client_data["custom_fields"] = json.dumps(validated_custom_fields)

        if request_data.additional_data:
            client_data["additional_data"] = json.dumps(request_data.additional_data)
        if request_data.social_pages:
            serialized = [p.model_dump() for p in request_data.social_pages]
            client_data["social_pages"] = json.dumps(self._ensure_list_item_ids(serialized))

        return client_data

    async def _build_create_client_payloads(
        self, request_data: CreateClientRequest, organization_id: str
    ) -> list[dict[str, Any]]:
        """Build the ordered list of client payloads for create_client
        based on client type and linking rules."""
        if request_data.client_type == ClientType.COMPANY:
            return [
                await self._prepare_client_data(request_data, organization_id),
                self._prepare_primary_contact_person_client_data(request_data, organization_id),
            ]
        if request_data.client_type == ClientType.PERSON:
            company_name = (request_data.company or "").strip()
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
            and (request_data.company or "").strip()
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
        client_user_data = {
            "client_id": client_id,
            "organization_id": organization_id,
            "user_id": user_id,
            "isometrik_user_id": isometrik_user_id,
            # When linked to a company (client_company_id set),
            # this client_user should not be primary.
            "is_primary_contact": client_company_id is None,
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

        return client_user_data

    async def _create_optional_records(
        self,
        request_data: CreateClientRequest,
        client_id: str,
    ) -> None:
        """Create lead and address records if provided.

        Args:
            request_data: Request data
            client_id: Client ID
        """
        # Create lead record if enabled
        if request_data.lead_management and request_data.lead_management.enabled:
            lead_data = {
                "client_id": client_id,
                "lead_status": request_data.lead_management.lead_status.value
                if request_data.lead_management.lead_status
                else None,
                "intake_stage": request_data.lead_management.intake_stage.value
                if request_data.lead_management.intake_stage
                else None,
                "lead_source": request_data.lead_management.lead_source,
                "referral_source": request_data.lead_management.referral_source,
                "lead_score": request_data.lead_management.lead_score,
            }
            await self.client_repository.create_lead(lead_data)

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
        """
        organization_id = self.user_context.organization_id

        organization = await self._validate_client_creation(request_data, organization_id)
        user_id, isometrik_user_id, password = await self._create_auth_and_isometrik_user(
            request_data, organization, organization_id
        )

        payloads = await self._build_create_client_payloads(request_data, organization_id)
        records = await self.client_repository.create_client(payloads)
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
        try:
            await self.client_repository.create_client_user(client_user_data)
        except asyncpg.UniqueViolationError as exc:
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

        await self._create_optional_records(request_data, primary_record["id"])

        if request_data.portal_access:
            try:
                send_client_creation_email(
                    email=request_data.email,
                    organization_name=organization["name"],
                    password=password,
                )
            except Exception as e:
                logger.error("Failed to send client creation email: %s", str(e))
        enrichment_items = self._get_enrichment_items_for_created_clients(records, organization_id)
        return CreateClientResult(records=records, enrichment_items=enrichment_items)

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
            primary_contact = {
                "first_name": client.get("first_name"),
                "last_name": client.get("last_name"),
                "title": client.get("title"),
                "email": client.get("email"),
                "phone_isd_code": client.get("phone_isd_code"),
                "phone": client.get("phone"),
            }

            client_response = ClientListResponse(
                id=str(client.get("id")),
                name=client.get("name") or "",
                company_name=client.get("company_name"),
                primary_contact=primary_contact,
                company_type=client.get("client_type"),
                status=client.get("status"),
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

        # Build primary contact info
        primary_contact = PrimaryContactInfo(
            salutation=client.get("prefix"),
            first_name=client.get("first_name"),
            middle_name=client.get("middle_name"),
            last_name=client.get("last_name"),
            title=client.get("title"),
            email=client.get("email"),
            phone_isd_code=client.get("phone_isd_code"),
            phone=client.get("phone"),
        )

        # Parse JSONB fields
        websites_data = safe_json_loads(client.get("websites"), [])
        websites = [Website(**website) for website in websites_data] if websites_data else []

        billing_preferences_data = parse_json_field(client.get("billing_preferences"))
        billing_preferences = None
        if billing_preferences_data:
            billing_preferences = BillingPreferences(**billing_preferences_data)

        # Format custom fields for response
        custom_fields = parse_json_field(client.get("custom_fields")) or {}

        additional_data = parse_json_field(client.get("additional_data")) or {}
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

        # Format lead information if exists
        lead_info = None
        if client.get("lead_id"):
            lead_info = LeadInfo(
                id=str(client.get("lead_id")),
                lead_status=client.get("lead_status"),
                intake_stage=client.get("intake_stage"),
                lead_source=client.get("lead_source"),
                referral_source=client.get("referral_source"),
                lead_score=client.get("lead_score"),
                converted_at=format_iso_datetime(client.get("converted_at")),
                notes=client.get("lead_notes"),
                created_at=format_iso_datetime(client.get("lead_created_at")) or "",
                updated_at=format_iso_datetime(client.get("lead_updated_at")) or "",
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
        return ClientDetailsResponse(
            id=str(client.get("id")),
            organization_id=str(client.get("organization_id")),
            client_type=client.get("client_type"),
            name=client.get("name") or "",
            company_name=client.get("company_name"),
            status=client.get("status"),
            industry=client.get("industry"),
            image_url=client.get("contact_profile_photo_url") or client.get("profile_photo_url"),
            tags=client.get("tags") or [],
            primary_contact=primary_contact,
            websites=websites,
            billing_preferences=billing_preferences,
            custom_fields=custom_fields,
            addresses=formatted_addresses,
            lead=lead_info,
            additional_data=additional_data,
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
        await self.client_repository.delete_leads(client_id)
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
            dict | None: Result with old_data for audit when update is applied, None when no-op

        Raises:
            NotFoundException: If client not found
            ConflictException: If client name already exists
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

        old_data = self._format_client_for_audit(current)

        # Validate client name when updating (same as create: non-empty and unique)
        if body.client_name is not None:
            name_to_check = body.client_name.lower().strip()
            name_exists = await self.client_repository.check_client_name_exists(
                name=name_to_check,
                organization_id=organization_id,
                exclude_client_id=client_id,
            )
            if name_exists:
                raise ConflictException(
                    message_key="clients.errors.client_name_already_exists",
                    custom_code=CustomStatusCode.CONFLICT,
                )

        update_data = await self._build_client_update_payload(body, current)

        await self._apply_batch_jsonb_list_changes(
            body=body,
            client_id=client_id,
            organization_id=organization_id,
            current=current,
        )

        if body.lead_management is not None:
            await self._apply_lead_update(client_id, body.lead_management)

        if update_data:
            await self.client_repository.update_client(client_id, organization_id, update_data)

        return {"old_data": old_data}

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
                "client_name",
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
            ("client_name", "name"),
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
        if body.custom_fields is None:
            return
        existing = parse_json_field(current.get("custom_fields")) or {}
        merged = dict(existing)

        # Determine entity type from client type
        client_type = current.get("client_type", "")
        entity_type = (
            EntityType.COMPANY if client_type == ClientType.COMPANY.value else EntityType.CONTACT
        )

        # Validate and format new/updated custom fields
        custom_field_service = CustomFieldService(
            db_connection=self.db_connection,
            user_context=self.user_context,
        )

        # Merge: remove fields set to None, update/add others
        fields_to_validate: dict[str, Any] = {}
        for key, value in body.custom_fields.items():
            if value is None:
                merged.pop(key, None)
            else:
                fields_to_validate[key] = value

        # Validate only the fields being updated/added
        if fields_to_validate:
            validated_fields = await custom_field_service.validate_and_format_custom_fields(
                fields_to_validate, entity_type
            )
            merged.update(validated_fields)

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

    async def _apply_lead_update(self, client_id: str, lead: LeadManagementUpdate) -> None:
        """Apply lead update by lead_id; only provided fields are sent to repository."""
        lead_data = lead.model_dump(exclude={"lead_id"}, exclude_none=True)
        if not lead_data:
            return
        await self.client_repository.update_lead(lead.lead_id, client_id, lead_data)

    async def _apply_jsonb_list_changes(
        self,
        client_id: str,
        organization_id: str,
        update_obj: Any,
        current: dict[str, Any],
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
            client_id: Client ID
            organization_id: Organization ID
            update_obj: Update object with optional add, update, remove attributes
            current: Current client data dict
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

        # Update the JSONB field
        await self.client_repository.update_client(
            client_id, organization_id, {field_name: json.dumps(updated)}
        )

    async def _apply_batch_jsonb_list_changes(
        self,
        body: UpdateClientRequest,
        client_id: str,
        organization_id: str,
        current: dict[str, Any],
    ) -> None:
        """Apply batch JSONB list changes."""
        # Apply batch list operations (addresses, websites, social_pages)
        if body.addresses is not None:
            await self._apply_addresses_changes(client_id, body.addresses)

        if body.websites is not None:
            await self._apply_jsonb_list_changes(
                client_id,
                organization_id,
                body.websites,
                current,
                "websites",
                "clients.errors.website_not_found",
            )

        if body.social_pages is not None:
            await self._apply_jsonb_list_changes(
                client_id,
                organization_id,
                body.social_pages,
                current,
                "social_pages",
                "clients.errors.social_page_not_found",
            )

        if body.work_history is not None:
            await self._apply_jsonb_list_changes(
                client_id,
                organization_id,
                body.work_history,
                current,
                "work_history",
                "clients.errors.work_history_item_not_found",
            )

        if body.educational_history is not None:
            await self._apply_jsonb_list_changes(
                client_id,
                organization_id,
                body.educational_history,
                current,
                "educational_history",
                "clients.errors.educational_history_item_not_found",
            )

        if body.linked_pages is not None:
            await self._apply_jsonb_list_changes(
                client_id,
                organization_id,
                body.linked_pages,
                current,
                "linked_pages",
                "clients.errors.linked_page_not_found",
            )

        if body.products is not None:
            await self._apply_jsonb_list_changes(
                client_id,
                organization_id,
                body.products,
                current,
                "products",
                "clients.errors.product_not_found",
            )

        if body.key_people is not None:
            await self._apply_jsonb_list_changes(
                client_id,
                organization_id,
                body.key_people,
                current,
                "key_people",
                "clients.errors.key_person_not_found",
            )

    async def _apply_addresses_changes(
        self,
        client_id: str,
        addresses_update: AddressesUpdate,
    ) -> None:
        """Apply batch address operations: add, update, and/or remove."""
        # Remove operations
        if addresses_update.remove:
            await self.client_repository.delete_addresses_by_ids(client_id, addresses_update.remove)

        # Update operations
        if addresses_update.update:
            for update_item in addresses_update.update:
                payload = update_item.model_dump(exclude_none=True, exclude={"id"})
                if payload:
                    await self.client_repository.update_address(update_item.id, client_id, payload)

        # Add operations
        if addresses_update.add:
            rows = []
            for add_item in addresses_update.add:
                row: dict[str, Any] = {"client_id": client_id}
                row.update(add_item.model_dump(exclude_none=True))
                rows.append(row)
            await self.client_repository.bulk_create_addresses(rows)

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
