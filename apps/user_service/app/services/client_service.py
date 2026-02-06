"""Service for client business logic

This service handles all business logic related to clients, including
validation, formatting, and orchestration of client operations.
"""

import json
import uuid
from typing import Any

import asyncpg
from supabase import AsyncClient

from apps.user_service.app.db.repositories import (
    ClientRepository,
    OrganizationRepository,
    UserEventRepository,
    UserRepository,
)
from apps.user_service.app.schemas.clients import (
    AddressUpdate,
    BillingPreferences,
    ClientAddressResponse,
    ClientDetailsResponse,
    ClientListResponse,
    CreateClientFromUserRequest,
    CreateClientRequest,
    LeadInfo,
    LeadManagementUpdate,
    PrimaryContactInfo,
    UpdateClientRequest,
    Website,
    WebsiteUpdate,
)
from apps.user_service.app.schemas.enums import (
    ClientType,
    IsometrikRole,
    UserEventStatus,
)
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
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
)
from libs.shared_utils.isometrik_service import (
    create_isometrik_user,
    get_isometrik_data_from_settings,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("client_service")


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
        client_record = await self.client_repository.create_client(
            {
                "organization_id": organization_id,
                "client_type": ClientType.PERSON.value,
            }
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

        # Validate email uniqueness
        email_exists = await self.client_repository.check_email_exists(
            email=request_data.email, organization_id=organization_id
        )
        if email_exists:
            raise ConflictException(
                message_key="clients.errors.email_already_exists",
                custom_code=CustomStatusCode.CONFLICT,
            )
        # Validate phone uniqueness
        user_repository = UserRepository(db_connection=self.db_connection)
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

        return organization

    async def _create_auth_and_isometrik_user(
        self, request_data: CreateClientRequest, organization: dict[str, Any], organization_id: str
    ) -> tuple[str, str]:
        """Create Supabase auth user and Isometrik user.

        Args:
            request_data: Request data
            organization: Organization data
            organization_id: Organization ID

        Returns:
            tuple: (user_id, isometrik_user_id)

        Raises:
            ServiceUnavailableException: If auth or Isometrik creation fails
        """
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

        # Create Supabase auth user
        auth_user = await create_user(
            sb_client=self.supabase_client,
            email=request_data.email,
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

        return user_id, isometrik_response["userId"]

    def _prepare_client_data(
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
            client_data["websites"] = json.dumps(self._ensure_website_ids(serialized_websites))
        if request_data.billing_preferences:
            serialized_billing = serialize_pydantic_models(request_data.billing_preferences)
            client_data["billing_preferences"] = json.dumps(serialized_billing)
        if request_data.custom_fields:
            serialized_custom_fields = serialize_pydantic_models(request_data.custom_fields)
            client_data["custom_fields"] = json.dumps(serialized_custom_fields)

        return client_data

    def _prepare_client_user_data(
        self,
        request_data: CreateClientRequest,
        client_id: str,
        organization_id: str,
        user_id: str,
        isometrik_user_id: str,
    ) -> dict[str, Any]:
        """Prepare client_user data dictionary.

        Args:
            request_data: Request data
            client_id: Client ID
            organization_id: Organization ID
            user_id: User ID
            isometrik_user_id: Isometrik user ID

        Returns:
            dict: Client user data
        """
        client_user_data = {
            "client_id": client_id,
            "organization_id": organization_id,
            "user_id": user_id,
            "isometrik_user_id": isometrik_user_id,
            "is_primary_contact": True,
            "first_name": request_data.first_name,
            "last_name": request_data.last_name,
        }

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

    async def create_client(self, request_data: CreateClientRequest) -> None:
        """Create a new client with complete onboarding flow.

        Orchestrates the full client creation process: validates organization existence
        and data uniqueness (email, phone, company name), provisions authentication
        and Isometrik users, creates client and client_user records, and optionally
        creates lead and address records. Sends a welcome email upon successful creation.

        Args:
            request_data: Request data containing client information

        Raises:
            NotFoundException: If organization not found
            ConflictException: If email/phone/name already exists
            ServiceUnavailableException: If auth or Isometrik creation fails
            ValidationException: If validation fails
        """
        organization_id = self.user_context.organization_id

        # Validate and get organization
        organization = await self._validate_client_creation(request_data, organization_id)

        # Create auth and Isometrik users
        user_id, isometrik_user_id = await self._create_auth_and_isometrik_user(
            request_data, organization, organization_id
        )

        # Create client record
        client_data = self._prepare_client_data(request_data, organization_id)
        client_record = await self.client_repository.create_client(client_data)

        # Create client_user record
        client_user_data = self._prepare_client_user_data(
            request_data, client_record["id"], organization_id, user_id, isometrik_user_id
        )
        await self.client_repository.create_client_user(client_user_data)

        # Create optional records (lead, address)
        await self._create_optional_records(request_data, client_record["id"])

        # Send creation email
        if request_data.portal_access:
            try:
                send_client_creation_email(
                    email=request_data.email, organization_name=organization["name"]
                )
            except Exception as e:
                logger.error("Failed to send client creation email: %s", str(e))

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
                primary_contact=primary_contact,
                company_type=client.get("client_type"),
                status=client.get("status"),
                matters=[],
                created_at=format_iso_datetime(client.get("created_at")) or "",
                updated_at=format_iso_datetime(client.get("updated_at")) or "",
                outstanding=None,
                tags=client.get("tags") or [],
            )
            transformed_clients.append(client_response.model_dump())

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
            first_name=client.get("first_name"),
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

        custom_fields = parse_json_field(client.get("custom_fields")) or {}

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
            status=client.get("status"),
            industry=client.get("industry"),
            profile_photo_url=client.get("profile_photo_url"),
            tags=client.get("tags") or [],
            primary_contact=primary_contact,
            websites=websites,
            billing_preferences=billing_preferences,
            custom_fields=custom_fields,
            addresses=formatted_addresses,
            lead=lead_info,
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

        update_data = self._build_client_update_payload(body, current)

        if body.addresses is not None:
            current_addresses = await self.client_repository.get_client_addresses(client_id)
            await self._apply_addresses_final(client_id, current_addresses, body.addresses)

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
            )
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

    def _merge_custom_fields_into_payload(
        self,
        body: UpdateClientRequest,
        current: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        """Merge body.custom_fields with current and set on payload."""
        if body.custom_fields is None:
            return
        existing = parse_json_field(current.get("custom_fields"))
        merged = dict(existing)
        for key, value in body.custom_fields.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        payload["custom_fields"] = merged

    def _build_client_update_payload(
        self, body: UpdateClientRequest, current: dict[str, Any]
    ) -> dict[str, Any]:
        """Build the client row update dict from body and current row (merge where needed)."""
        payload: dict[str, Any] = {}
        self._apply_simple_client_update_fields(body, payload)
        if body.websites is not None:
            payload["websites"] = self._normalize_websites_final(body.websites)
        self._merge_billing_preferences_into_payload(body, current, payload)
        self._merge_custom_fields_into_payload(body, current, payload)
        return payload

    def _ensure_website_ids(self, websites: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return a copy of websites with an id set on each item (generated if missing)."""
        result: list[dict[str, Any]] = []
        for website in websites:
            item = dict(website)
            if not item.get("id"):
                item["id"] = str(uuid.uuid4())
            result.append(item)
        return result

    def _normalize_websites_final(self, final: list[WebsiteUpdate]) -> list[dict[str, Any]]:
        """Normalize final website list from UI: ensure each item has an id."""
        result: list[dict[str, Any]] = []
        for item in final:
            data = item.model_dump(exclude_none=True)
            if not data.get("id"):
                data["id"] = str(uuid.uuid4())
            result.append(data)
        return result

    async def _apply_lead_update(self, client_id: str, lead: LeadManagementUpdate) -> None:
        """Apply lead update by lead_id; only provided fields are sent to repository."""
        lead_data = lead.model_dump(exclude={"lead_id"}, exclude_none=True)
        if not lead_data:
            return
        await self.client_repository.update_lead(lead.lead_id, client_id, lead_data)

    def _diff_addresses_final(
        self,
        current: list[dict[str, Any]],
        final: list[AddressUpdate],
    ) -> tuple[list[str], list[AddressUpdate], list[AddressUpdate]]:
        """Compare current addresses with final state; return (to_remove_ids, to_update, to_add)."""
        current_ids = {str(a["id"]) for a in current if a.get("id")}
        final_with_id = [a for a in final if a.id]
        final_ids = {str(a.id) for a in final_with_id}
        to_remove = list(current_ids - final_ids)
        to_update = [a for a in final_with_id if str(a.id) in current_ids]
        to_add = [a for a in final if not a.id or str(a.id) not in current_ids]
        return to_remove, to_update, to_add

    async def _apply_addresses_final(
        self,
        client_id: str,
        current: list[dict[str, Any]],
        final: list[AddressUpdate],
    ) -> None:
        """Apply final address state: remove missing, update existing, add new."""
        to_remove, to_update, to_add = self._diff_addresses_final(current, final)
        if to_remove:
            await self.client_repository.delete_addresses_by_ids(client_id, to_remove)
        for item in to_update:
            payload = item.model_dump(exclude_none=True)
            addr_id = payload.pop("id", None)
            if addr_id and payload:
                await self.client_repository.update_address(addr_id, client_id, payload)
        if to_add:
            rows = [self._address_add_row(client_id, a) for a in to_add]
            await self.client_repository.bulk_create_addresses(rows)

    def _address_add_row(self, client_id: str, address: AddressUpdate) -> dict[str, Any]:
        """Build address row for bulk_create_addresses (no id; matches create flow)."""
        row: dict[str, Any] = {"client_id": client_id}
        data = address.model_dump(exclude_none=True, exclude={"id"})
        row.update(data)
        return row
