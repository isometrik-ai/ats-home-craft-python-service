"""JWT Authentication middleware and utilities for FastAPI applications.

This module provides JWT-based authentication middleware and utilities
for FastAPI applications using Supabase as the authentication provider.
It includes:

- JWTAuthMiddleware: Middleware for JWT token validation and user
authentication
- verify_jwt: Dependency for ensuring JWT authentication in route handlers
- check_user_access: Utility function for role-based access control

The module integrates with Supabase for user authentication and
permission management, using environment variables for configuration.
"""

import asyncpg
from fastapi import Depends, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from supabase import AsyncClient, AuthError

from apps.user_service.app.db.repositories import (
    SessionRepository,
)
from apps.user_service.app.dependencies.db import db_conn
from libs.shared_db.supabase_db.client import get_supabase_client
from libs.shared_utils.http_exceptions import (
    InternalServerErrorException,
    UnauthorizedException,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger(__name__)


def extract_user_data(
    user: dict,
) -> tuple[str | None, str | None, str | None]:
    """Extract user data from JWT token."""
    if not user:
        return None, None, None

    user_id = user.get("sub")
    user_email = user.get("email")
    session_id = user.get("session_id")

    return user_id, user_email, session_id


def setup_audit_context(
    request: Request,
    user_id: str,
    user_email: str,
    organization_id: str,
    session_id: str,
) -> None:
    """Setup default audit context for request."""
    request.state.audit_risk_level = "high"
    request.state.audit_description = "Authentication or authorization failure"
    request.state.audit_user_context = {
        "user_id": user_id,
        "user_email": user_email,
        "user_role": "unknown",
        "organization_id": organization_id,
        "session_id": session_id,
    }


async def get_claims_from_token(
    token: str,
    supabase_client: AsyncClient | None = None,
) -> dict:
    """Get JWT claims from token using Supabase auth.

    This is a common method for extracting claims from JWT tokens.
    It handles all error cases consistently across the codebase.

    Args:
        token (str): The JWT token to decode
        supabase_client (AsyncClient | None): Optional Supabase client.
            If None, will get client from get_supabase_client()

    Returns:
        dict: The claims payload from the token

    Raises:
        UnauthorizedException: If token is invalid, expired, or authentication fails
    """
    try:
        if supabase_client is None:
            supabase_client = await get_supabase_client()

        claims_response = await supabase_client.auth.get_claims(jwt=token)

        if not claims_response or not claims_response.get("claims"):
            raise UnauthorizedException(
                message_key="errors.invalid_token",
                custom_code=CustomStatusCode.UNAUTHORIZED,
            )

        return claims_response["claims"]
    except AuthError as e:
        logger.error("Supabase auth error during token validation: %s", str(e))
        error_message = str(e).lower()
        status = getattr(e, "status", None)

        # Check for expired tokens first (by error message or status code)
        # AuthInvalidJwtError with "expired" message should be treated as expired
        if "expired" in error_message or status == 401:
            raise UnauthorizedException(
                message_key="errors.token_expired",
                custom_code=CustomStatusCode.UNAUTHORIZED,
            ) from e

        # Handle invalid JWT errors (status 400) - but not expired ones
        if status == 400:
            raise UnauthorizedException(
                message_key="errors.invalid_token",
                custom_code=CustomStatusCode.UNAUTHORIZED,
            ) from e

        # Fallback for other auth errors
        raise UnauthorizedException(
            message_key="errors.authentication_failed",
            custom_code=CustomStatusCode.UNAUTHORIZED,
        ) from e
    except Exception as e:
        logger.error("JWT validation error: %s", str(e))
        raise UnauthorizedException(
            message_key="errors.authentication_failed",
            custom_code=CustomStatusCode.UNAUTHORIZED,
        ) from e


async def check_user_access_async(
    permission_code: list[str],
    user_id: str,
    organization_id: str | None,
    db_connection: asyncpg.Connection,
) -> bool:
    """Check if a user has the specified role permission using asyncpg.

    This function provides a truly async alternative for permission checking
    that doesn't block the event loop.

    Args:
        permission_code (List[str]): List of permission codes to check
        (e.g., ["settings.roles.manage", "business.dashboard.view", etc])
        user_id (str): The ID of the user to check permissions for
        organization_id (str): The ID of the Organization to check permissions for
        db_connection (asyncpg.Connection): Database connection to use

    Returns:
        bool: True if user has permission, False otherwise

    Note:
        This function uses asyncpg to check permissions,
        providing true non-blocking database operations.
    """

    try:
        # Handle NULL or empty input
        if user_id is None or organization_id is None or permission_code is None:
            return False

        if len(permission_code) == 0:
            return False

        # Get all permission codes that the user has for this organization
        # Query: organization_members -> role_permissions -> permissions where status='active'
        query = """
            SELECT ARRAY_AGG(DISTINCT p.code) as user_permissions
            FROM organization_members om
            INNER JOIN role_permissions rp ON om.role_id = rp.role_id
            INNER JOIN permissions p ON rp.permission_id = p.id
            WHERE om.user_id = $1
                AND om.organization_id = $2
                AND om.status = 'active'
        """
        row = await db_connection.fetchrow(query, user_id, organization_id)

        # If user has no permissions, return FALSE
        user_permissions = row["user_permissions"] if row and row.get("user_permissions") else None
        if user_permissions is None:
            return False

        # Check if all required permissions are in user's permissions
        # Using PostgreSQL <@ operator (contained in)
        check_query = "SELECT ($1::text[]) <@ ($2::text[]) as has_all_permissions"
        result = await db_connection.fetchrow(check_query, permission_code, user_permissions)

        return result["has_all_permissions"] if result else False

    except HTTPException as error:
        raise error
    except Exception as error:
        logger.error("Failed to check permission: %s", str(error))
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
            params={"error": str(error)},
        ) from error


async def get_user_from_auth(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
) -> dict:
    """Validate user from JWT, get organization_id from session.

    Sets audit context in request.state.
    Ensures audit context is populated even during authentication/authorization failures.

    Raises:
        UnauthorizedException: If user is not authenticated
    """
    user = getattr(request.state, "user", None)

    # Extract user data from JWT token
    user_id, user_email, session_id = extract_user_data(user)

    # Setup audit context (before validation to ensure audit trail)
    organization_id = None
    session_repo = SessionRepository(db_connection=db_connection)
    organization_id = await session_repo.get_session_organization_id(session_id)

    setup_audit_context(request, user_id, user_email, organization_id, session_id)

    # Validate basic authentication
    if not user:
        raise UnauthorizedException(
            message_key="errors.unauthorized",
            custom_code=CustomStatusCode.UNAUTHORIZED,
            params={"user_id": user_id},
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.audit_risk_level = "low"
    request.state.audit_description = "Successfully authenticated and authorized user"

    return user


async def get_user_from_token(token: str) -> dict:
    """Get user from token.

    Args:
        token (str): The JWT token to decode

    Returns:
        dict: The user data from the token
    """
    return await get_claims_from_token(token)


class JWTAuthMiddleware(BaseHTTPMiddleware):
    """Middleware for JWT token validation and user authentication.

    This middleware validates JWT tokens and stores the decoded user
    information in the request state. It ensures that only authenticated
    users can access protected routes.

    Attributes:
        app (FastAPI): The FastAPI application instance
        supabase (Client): The Supabase client instance
    """

    async def dispatch(self, request: Request, call_next):
        """Process incoming requests to validate JWT tokens
        and authenticate users.

        This method is called for each request and performs the following:
        1. Extracts the JWT token from the Authorization header
        2. Validates the token using the Supabase JWT secret
        3. Decodes and stores the user information in request.state
        4. Handles various JWT validation errors with appropriate responses

        Args:
            request (Request): The incoming FastAPI request object
            call_next (Callable): The next middleware or route handler in the chain

        Returns:
            Response: Either the next middleware's response or an error response
            if token validation fails

        Raises:
            HTTPException: When JWT validation fails (expired or invalid token)
        """
        # Skip OPTIONS requests (CORS preflight) - they don't need authentication
        # CORS middleware will handle these requests and add appropriate headers
        if request.method == "OPTIONS":
            return await call_next(request)

        auth_header = request.headers.get("Authorization")

        if not auth_header or not auth_header.startswith("Bearer "):
            return await call_next(request)

        token = auth_header.split(" ")[1]
        try:
            payload = await get_claims_from_token(token)
            request.state.user = payload
            request.state.access_token = token
        except Exception as e:
            logger.error("JWT validation error: %s", str(e))
            raise UnauthorizedException(
                message_key="errors.authentication_failed",
                custom_code=CustomStatusCode.UNAUTHORIZED,
            ) from e

        return await call_next(request)
