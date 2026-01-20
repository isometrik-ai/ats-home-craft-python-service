"""Session Management Service Module

This service provides centralized session management operations for:
- Updating session organization context when users join organizations
- Extracting session IDs from authentication tokens
- Multi-organization membership support with session-level org context

Note: Session creation is handled automatically by database triggers when
auth.sessions are created. This service only handles updates to existing sessions.
"""

from typing import Any

import asyncpg
from supabase import AsyncClient

from apps.user_service.app.db.repositories import SessionRepository
from libs.shared_middleware.jwt_auth import get_claims_from_token
from libs.shared_utils.http_exceptions import (
    ValidationException,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("session_management_service")


class SessionManagementService:
    """Service for centralized session management operations.

    Handles session organization context updates and session ID extraction.
    Sessions are created automatically by database triggers; this service
    only handles updates to existing session entries.
    """

    def __init__(self, db_connection: asyncpg.Connection):
        """Initialize SessionManagementService.

        Args:
            db_connection: Active asyncpg connection (potentially in transaction)
        """
        self.db_connection = db_connection
        self.session_repository = SessionRepository(db_connection=db_connection)

    async def _extract_session_id(self, session: Any, supabase_client: AsyncClient) -> str:
        """Extract session_id from Supabase session or JWT claims.

        Args:
            session: Supabase session object with access_token
            supabase_client: Supabase client for JWT validation

        Returns:
            Session ID extracted from JWT

        Raises:
            ValidationException: If session is invalid, access_token is missing,
                supabase_client is not provided, or session_id cannot be extracted
        """
        if not session or not hasattr(session, "access_token"):
            raise ValidationException(
                message_key="sessions.errors.invalid_session_data",
                custom_code=CustomStatusCode.INVALID_DATA,
            )

        access_token = session.access_token
        if not access_token:
            raise ValidationException(
                message_key="sessions.errors.invalid_session_data",
                custom_code=CustomStatusCode.INVALID_DATA,
            )

        # Extract session_id from JWT claims
        claims = await get_claims_from_token(access_token, supabase_client)
        session_id = claims.get("session_id") or claims.get("jti")
        if not session_id:
            raise ValidationException(
                message_key="sessions.errors.invalid_session_data",
                custom_code=CustomStatusCode.INVALID_DATA,
            )

        return str(session_id)

    async def update_session_organization_context(
        self, session_id: str, user_id: str, organization_id: str
    ) -> None:
        """Update session organization context when user joins an organization.

        Note: Organization membership validation should be done by the caller
        before calling this method, as it provides flexibility for different flows.

        Args:
            session_id: Session ID to update
            user_id: User ID (for validation)
            organization_id: Organization ID to set as active context

        Raises:
            ValidationException: If session_id or organization_id is invalid
        """
        if not session_id or not organization_id:
            raise ValidationException(
                message_key="sessions.errors.invalid_session_data",
                custom_code=CustomStatusCode.INVALID_DATA,
            )

        await self.session_repository.update_session_organization_context(
            session_id=session_id,
            user_id=user_id,
            organization_id=organization_id,
        )
