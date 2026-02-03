"""Service for client business logic

This service handles all business logic related to clients, including
validation, formatting, and orchestration of client operations.
"""

import asyncpg

from apps.user_service.app.db.repositories import OrganizationRepository, UserRepository
from apps.user_service.app.db.repositories.client_repository import ClientRepository
from apps.user_service.app.schemas.clients import CreateClientFromUserRequest
from apps.user_service.app.schemas.enums import ClientType
from apps.user_service.app.utils.email_utils import send_client_creation_email
from libs.shared_utils.http_exceptions import NotFoundException
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
        1. Validate user exists in auth.users
        2. Validate organization exists
        3. Create client record
        4. Create client_user record
        5. Send creation email

        Args:
            request_data: Request data containing user_id and organization_id

        Raises:
            NotFoundException: If user or organization not found
            BadRequestException: If validation fails
        """
        user_id = request_data.user_id
        organization_id = request_data.organization_id

        user_repository = UserRepository(db_connection=self.db_connection)

        exists, email = await user_repository.get_user_email_by_id(user_id)
        if not exists:
            raise NotFoundException(
                message_key="errors.user_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
                params={"user_id": user_id},
            )

        organization_repository = OrganizationRepository(db_connection=self.db_connection)
        # Validate organization exists
        organization = await organization_repository.get_organization_by_id(organization_id)
        if not organization:
            raise NotFoundException(
                message_key="errors.organization_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
                params={"organization_id": organization_id},
            )
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
            }
        )

        # Send creation email
        try:
            if email:
                send_client_creation_email(
                    email=email,
                    organization_name=organization.get("name", "Organization"),
                )
            else:
                logger.info("Cannot send client creation email: no email address")
        except Exception as e:
            logger.error("Failed to send client creation email: %s", str(e))
            # Don't fail the request if email fails
