"""Service for client business logic

This service handles all business logic related to clients, including
validation, formatting, and orchestration of client operations.
"""

import asyncpg

from apps.user_service.app.db.repositories import OrganizationRepository, UserRepository
from apps.user_service.app.db.repositories.client_repository import ClientRepository
from apps.user_service.app.schemas.clients import CreateClientFromUserRequest
from apps.user_service.app.schemas.enums import ClientType
from apps.user_service.app.utils.common_utils import parse_json_field
from apps.user_service.app.utils.email_utils import send_client_creation_email
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
    ) -> None:
        """Initialize ClientService with user context and database connection.

        Args:
            db_connection: database connection for postgresql
        """
        self.db_connection = db_connection
        self.client_repository = ClientRepository(db_connection=db_connection)

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
            ConflictException: If user is already a client
        """
        user_id = request_data.user_id
        organization_id = request_data.organization_id

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
            role="client",
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

        # Send creation email
        try:
            if user_details.get("email"):
                send_client_creation_email(
                    email=user_details.get("email"),
                    organization_name=organization["name"],
                )
        except Exception as e:
            logger.error("Failed to send client creation email: %s", str(e))
