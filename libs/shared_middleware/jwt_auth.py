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

import jwt
from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

from libs.shared_config.app_settings import shared_settings
from libs.shared_db.supabase_db.client import get_supabase_client
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    InternalServerErrorException,
    UnauthorizedException,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger(__name__)


def extract_user_data(
    user: dict,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Extract user data from JWT token."""
    if not user:
        return None, None, None, None

    user_id = user.get("sub")
    user_metadata = user.get("user_metadata", {})
    organization_id = user_metadata.get("organization_id")
    user_email = user.get("email")
    session_id = user.get("session_id")

    return user_id, organization_id, user_email, session_id


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


async def check_user_access_async(permission_code: list[str], user_id, organisation_id):
    """Check if a user has the specified role permission using Supabase SDK.

    This function provides a truly async alternative for permission checking
    that doesn't block the event loop.

    Args:
        permission_code (List[str]): List of permission codes to check
        (e.g., ["settings.roles.manage", "business.dashboard.view", etc])
        user_id (str): The ID of the user to check permissions for
        organisation_id (str): The ID of the Organisation to check permissions for

    Returns:
        bool: True if user has permission, False otherwise

    Note:
        This function uses Supabase SDK to check permissions,
        providing true non-blocking database operations.
    """

    try:
        # Get global Supabase client
        supabase = await get_supabase_client()

        if not organisation_id:
            raise BadRequestException(
                message_key="organisations.errors.user_not_a_member_of_any_organization",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        # Use Supabase RPC function for permission checking
        rpc_result = supabase.rpc(
            "check_permission",
            {
                "user_id": user_id,
                "organization_id": organisation_id,
                "permission_code": permission_code,
            },
        )
        response = await rpc_result.execute()

        return response.data if response.data is not None else False

    except HTTPException as error:
        raise error
    except Exception as error:
        logger.error("Failed to check permission: %s", str(error))
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
            params={"error": str(error)},
        ) from error


def get_user_from_auth(request: Request) -> dict:
    """Validate user from JWT, check org membership and role.

    Sets audit context in request.state.
    Ensures audit context is populated even during authentication/authorization failures.
    """
    user = getattr(request.state, "user", None)

    # Extract user data from JWT token
    user_id, organization_id, user_email, session_id = extract_user_data(user)

    # Setup audit context
    setup_audit_context(request, user_id, user_email, organization_id, session_id)

    # Validate basic authentication
    if not user:
        raise UnauthorizedException(
            message_key="errors.unauthorized",
            custom_code=CustomStatusCode.UNAUTHORIZED,
            params={"user_id": user_id},
            headers={"WWW-Authenticate": "Bearer"},
        )

    # ✅ User is valid, update audit context and success markers
    # request.state.audit_user_context["user_role"] = role_name
    request.state.audit_risk_level = "low"
    request.state.audit_description = "Successfully authenticated and authorized user"

    return user


def get_user_from_token(token: str) -> dict:
    """Get user from token.

    Args:
        token (str): The JWT token to decode

    Returns:
        dict: The user data from the token
    """
    try:
        payload = jwt.decode(
            token,
            shared_settings.supabase.jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
        return payload
    except jwt.ExpiredSignatureError as exc:
        logger.error("JWT token expired: %s", str(exc))
        raise UnauthorizedException(
            message_key="errors.token_expired",
            custom_code=CustomStatusCode.UNAUTHORIZED,
        ) from exc
    except jwt.InvalidTokenError as exc:
        logger.error("Invalid JWT token: %s", str(exc))
        raise UnauthorizedException(
            message_key="errors.invalid_token",
            custom_code=CustomStatusCode.UNAUTHORIZED,
        ) from exc


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
            payload = jwt.decode(
                token,
                shared_settings.supabase.jwt_secret,
                algorithms=["HS256"],
                audience="authenticated",
            )
            request.state.user = payload
            request.state.access_token = token
        except jwt.ExpiredSignatureError as e:
            logger.error("JWT token expired: %s", str(e))
            raise UnauthorizedException(
                message_key="errors.token_expired",
                custom_code=CustomStatusCode.UNAUTHORIZED,
            ) from e
        except jwt.InvalidTokenError as e:
            logger.error("Invalid JWT token: %s", str(e))
            raise UnauthorizedException(
                message_key="errors.invalid_token",
                custom_code=CustomStatusCode.UNAUTHORIZED,
            ) from e
        except Exception as e:
            logger.error("JWT validation error: %s", str(e))
            raise UnauthorizedException(
                message_key="errors.authentication_failed",
                custom_code=CustomStatusCode.UNAUTHORIZED,
            ) from e

        return await call_next(request)
