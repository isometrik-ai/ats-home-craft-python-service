"""Service for session business logic

This service handles all business logic related to user sessions, including
validation, formatting, and orchestration of session operations.
"""

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.session_repository import SessionRepository
from apps.user_service.app.dependencies.logger import get_logger
from apps.user_service.app.schemas.admin_access_management import SessionItem
from apps.user_service.app.schemas.auth import SessionFilter
from apps.user_service.app.utils.common_utils import UserContext, format_iso_datetime
from libs.shared_utils.http_exceptions import BadRequestException
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("session_service")


class SessionService:
    """Service for session business logic.

    Handles all business logic related to sessions, including validation,
    formatting, and orchestration of session operations.
    """

    def __init__(
        self,
        user_context: UserContext,
        db_connection: asyncpg.Connection,
    ) -> None:
        """Initialize SessionService with user context and database connection.

        Args:
            user_context: Authenticated user context
            db_connection: database connection for postgresql
        """
        self.user_context = user_context
        # Initialize repository with database connection
        self.session_repository = SessionRepository(db_connection=db_connection)

    @staticmethod
    def _format_session_item(session_data: dict) -> SessionItem:
        """Format session data into SessionItem.

        Args:
            session_data: Raw session data from database

        Returns:
            SessionItem: Formatted session item
        """
        return SessionItem(
            id=str(session_data["id"]),
            user_id=str(session_data["user_id"]),
            organization_id=str(session_data["organization_id"]),
            ip_address=str(session_data["ip_address"]),
            user_agent=session_data["user_agent"],
            device_fingerprint=session_data["device_fingerprint"],
            risk_score=session_data["risk_score"],
            login_timestamp=(
                session_data["login_timestamp"]
                if isinstance(session_data["login_timestamp"], str)
                else format_iso_datetime(session_data["login_timestamp"]) or ""
            ),
            logout_timestamp=(
                session_data["logout_timestamp"]
                if isinstance(session_data["logout_timestamp"], str)
                else format_iso_datetime(session_data["logout_timestamp"]) or ""
            ),
            session_status=session_data["session_status"],
            login_method=session_data["login_method"],
            accessed_phi=session_data["accessed_phi"],
            phi_access_purpose=session_data["phi_access_purpose"],
        )

    async def get_user_sessions(
        self,
        filters: SessionFilter,
    ) -> dict[str, Any]:
        """Get paginated list of sessions for the current user.

        Args:
            filters: Session filters including search, pagination, and status filters

        Returns:
            dict containing paginated sessions and total count
        """
        result = await self.session_repository.get_sessions_with_count(
            organization_id=self.user_context.organization_id,
            user_id=self.user_context.user_id,
            filters=filters,
        )

        sessions_data = result["data"]
        total_count = result["total_count"]

        # Format sessions data
        sessions = [self._format_session_item(session) for session in sessions_data]

        return {
            "sessions": sessions,
            "total_count": total_count,
        }

    async def get_organization_sessions(
        self,
        filters: SessionFilter,
    ) -> dict[str, Any]:
        """Get paginated list of sessions for all users in the current organization.

        Intended for org-level admins with settings management permission.

        Args:
            filters: Session filters including search, pagination, and status filters

        Returns:
            dict containing paginated sessions and total count

        Raises:
            BadRequestException: If organization_id is not available
        """
        # Require organization_id – org-wide listing doesn't apply to personal accounts
        if not self.user_context.organization_id:
            raise BadRequestException(
                message_key="sessions.errors.bad_request",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        result = await self.session_repository.get_org_sessions_with_count(
            organization_id=self.user_context.organization_id,
            filters=filters,
        )

        sessions_data = result["data"]
        total_count = result["total_count"]

        # Format sessions data
        sessions = [self._format_session_item(session) for session in sessions_data]

        return {
            "sessions": sessions,
            "total_count": total_count,
        }
