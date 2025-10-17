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


import os  # Standard library import first
from typing import List, Tuple, Optional

import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request, HTTPException, status, responses

from libs.shared_db.supabase_db.db import get_supabase_client

SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")

# Centralized error handlers
def raise_auth_error(request: Request, description: str, detail: str) -> None:
    """Raise 401 Unauthorized error with audit context."""
    request.state.audit_description = description
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def raise_forbidden_error(request: Request, description: str, detail: str) -> None:
    """Raise 403 Forbidden error with audit context."""
    request.state.audit_description = description
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=detail,
    )


def raise_internal_error(request: Request, description: str, detail: str) -> None:
    """Raise 500 Internal Server Error with audit context."""
    request.state.audit_description = description
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=detail,
    )


# Helper functions for user validation
def extract_user_data(
    user: dict
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
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


async def check_user_access_async(
    permission_code: List[str], user_id, organisation_id
):
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

        print(permission_code,user_id,organisation_id,sep='\n\n')

        if not organisation_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User is not a member of any organization",
            )

        # Use Supabase RPC function for permission checking
        rpc_result = supabase.rpc(
            "check_permission",
            {
                "user_id": user_id,
                "organization_id": organisation_id,
                "permission_code": permission_code,
            }
        )
        response = await rpc_result.execute()
        print("\n\nresponse",response)

        return response.data if response.data is not None else False

    except HTTPException as error:
        raise error
    except Exception as error:
        print(f"Async permission check error: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check permission",
        ) from error




def get_user_from_auth(
    request: Request
) -> dict:
    """
    Validates user from JWT, checks org membership and role,
    and sets audit context in request.state.
    Ensures audit context is populated even during authentication/authorization failures.
    """
    user = getattr(request.state, "user", None)
    print(user)

    # Extract user data from JWT token
    user_id, organization_id, user_email, session_id = extract_user_data(user)

    # Setup audit context
    setup_audit_context(request, user_id, user_email, organization_id, session_id)

    # Validate basic authentication
    if not user:
        raise_auth_error(
            request,
            "User not authenticated (missing token or invalid token)",
            "Not authenticated",
        )

    # ✅ User is valid, update audit context and success markers
    # request.state.audit_user_context["user_role"] = role_name
    request.state.audit_risk_level = "low"
    request.state.audit_description = "Successfully authenticated and authorized user"
    print("get_user_from_auth")

    return user


def get_user_from_token(token: str) -> dict:
    """
    Get user from token

    Args:
        token (str): The JWT token to decode

    Returns:
        dict: The user data from the token
    """
    try:
        payload = jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired"
        )
    except jwt.InvalidTokenError as exception:
        print("JWT DECODE ERROR:", str(exception))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )


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
            call_next: The next middleware or route handler in the chain

        Returns:
            Response: Either the next middleware's response or an error response
            if token validation fails

        Raises:
            No exceptions are raised directly, but various JWT validation errors
            are caught and converted to appropriate HTTP responses
        """
        auth_header = request.headers.get("Authorization")

        if not auth_header or not auth_header.startswith("Bearer "):
            return await call_next(request)

        token = auth_header.split(" ")[1]
        print("working fine")
        try:
            payload = jwt.decode(
                token,
                SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                audience="authenticated",
            )
            request.state.user = payload
            request.state.access_token = token
            print(payload)
        except jwt.ExpiredSignatureError:
            return responses.JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": "Token expired"}
            )
        except jwt.InvalidTokenError as exception:
            print("JWT DECODE ERROR:", str(exception))
            return responses.JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": "Invalid token"}
            )

        return await call_next(request)
