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

# pylint: disable=import-error

import os  # Standard library import first
import jwt

from fastapi import Request, HTTPException, status, responses, Depends
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.status import HTTP_401_UNAUTHORIZED
from supabase import Client
from typing import List

from psycopg2.extras import RealDictCursor

from libs.shared_db.supabase_db.db import get_supabase_client
from libs.shared_db.postgres_db.db import get_async_db_conn
from libs.shared_models import ALLOWED_USER_STATUSES, is_allowed_user_status

SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")


def check_user_access(permission_code, user_id, organisation_id):
    """Check if a user has the specified role permission using Supabase RPC.

    Args:
        permission_code (str): role's permission code
        (e.g., "USERS_READ", "ROLES_READ", etc)
        user_id (str): The ID of the user to check permissions for
        organisation_id (str): The ID of the Organisation to check permissions for

    Returns:
        bool: The response data from Supabase RPC call containing
        permission check result (True if user has permission, False otherwise)

    Note:
        This function calls the Supabase RPC function
        'check_permission' to verify if the user has the
        specified permission in the database.
    """

    supabase: Client = get_supabase_client()

    # Call the check_permission function using Supabase client
    # Note: The function signature is check_permission(user_id, organization_id, permission_code)
    response = supabase.rpc(
        "check_permission",
        {
            "user_id": user_id,
            "organization_id": organisation_id,
            "permission_code": permission_code,
        },
    ).execute()

    return response.data


async def check_user_access_async(
    permission_code: List[str], user_id, organisation_id, db_conn
):
    """Check if a user has the specified role permission using async SQL query.

    This function provides a truly async alternative for permission checking
    that doesn't block the event loop.

    Args:
        permission_code (str): role's permission code
        (e.g., "settings.roles.manage", "business.dashboard.view", etc)
        user_id (str): The ID of the user to check permissions for
        organisation_id (str): The ID of the Organisation to check permissions for
        db_conn: AsyncPG database connection (async)

    Returns:
        bool: True if user has permission, False otherwise

    Note:
        This function uses async SQL query to check permissions,
        providing true non-blocking database operations.
    """

    try:
        # Async SQL query matching the RPC function logic
        permission_query = """
            SELECT EXISTS (
                SELECT 1
                FROM public.organization_members om
                JOIN public.roles r ON om.role_id = r.id
                JOIN public.role_permissions rp ON r.id = rp.role_id
                JOIN public.permissions p ON rp.permission_id = p.id
                WHERE om.user_id = $1
                  AND om.organization_id = $2
                  AND r.organization_id = $3
                  AND p.code = ANY($4::text[])
            ) AS has_permission;
        """

        # Execute async query
        result = await db_conn.fetchrow(
            permission_query, user_id, organisation_id, organisation_id, permission_code
        )

        return result["has_permission"] if result else False

    except Exception as error:
        print(f"Async permission check error: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check permmission",
        ) from error


def check_user_access_direct(permission_code, user_id, organisation_id, db_conn):
    """Check if a user has the specified role permission using direct SQL query.

    This function provides a direct SQL alternative to the RPC approach for
    performance comparison and reduced network overhead.

    Args:
        permission_code (str): role's permission code
        (e.g., "settings.roles.manage", "business.dashboard.view", etc)
        user_id (str): The ID of the user to check permissions for
        organisation_id (str): The ID of the Organisation to check permissions for
        db_conn: PostgreSQL database connection

    Returns:
        bool: True if user has permission, False otherwise

    Note:
        This function uses direct SQL query to check permissions,
        avoiding the network overhead of RPC calls.
    """

    try:

        with db_conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # Direct SQL query matching the RPC function logic
            permission_query = """
                SELECT EXISTS (
                    SELECT 1
                    FROM public.organization_members om
                    JOIN public.roles r ON om.role_id = r.id
                    JOIN public.role_permissions rp ON r.id = rp.role_id
                    JOIN public.permissions p ON rp.permission_id = p.id
                    WHERE om.user_id = %s
                      AND om.organization_id = %s
                      AND r.organization_id = %s
                      AND p.code = %s
                ) as has_permission;
            """

            cursor.execute(
                permission_query,
                (user_id, organisation_id, organisation_id, permission_code),
            )
            result = cursor.fetchone()

            return result["has_permission"] if result else False

    except Exception as error:
        print(f"Direct permission check error: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check permmission",
        ) from error


async def get_user_from_auth(
    request: Request, db_conn=Depends(get_async_db_conn)
) -> dict:
    """
    Validates user from JWT, checks org membership and role,
    and sets audit context in request.state.
    Ensures audit context is populated even during authentication/authorization failures.
    """

    user = getattr(request.state, "user", None)
    print(user)

    user_id = None
    organization_id = None
    user_email = None
    session_id = None

    # Default values for audit log
    # request.state.audit_table = "organization_members"
    request.state.audit_risk_level = "high"  # auth failures are high risk
    request.state.audit_description = "Authentication or authorization failure"

    if user:
        user_id = user.get("sub")
        user_metadata = user.get("user_metadata", {})
        organization_id = user_metadata.get("organization_id")
        user_email = user.get("email")
        session_id = user.get("session_id")

    request.state.audit_user_context = {
        "user_id": user_id,
        "user_email": user_email,
        "user_role": "unknown",
        "organization_id": organization_id,
        "session_id": session_id,
    }

    if not user:
        request.state.audit_description = (
            "User not authenticated (missing token or invalid token)"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user_id or not organization_id:
        request.state.audit_description = "JWT token missing user_id or organization_id"
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing user or organization ID",
        )

#     if not session_id:
#         request.state.audit_description = "JWT token missing session_id"
#         raise HTTPException(
#             status_code=status.HTTP_401_UNAUTHORIZED,
#             detail="Invalid token: missing session ID",
#         )

#    # Check session status first
#     session_row = await db_conn.fetchrow(
#         """
#         SELECT session_status, logout_timestamp
#         FROM public.user_sessions
#         WHERE id = $1 AND user_id = $2 AND organization_id = $3
#         """,
#         session_id,
#         user_id,
#         organization_id,
#     )

#     if not session_row:
#         request.state.audit_description = "Session not found in database"
#         raise HTTPException(
#             status_code=status.HTTP_401_UNAUTHORIZED,
#             detail="Invalid session: session not found",
#         )

#     if session_row["session_status"] != "active" or session_row["logout_timestamp"] is not None:
#         request.state.audit_description = f"Session is {session_row['session_status']} or logged out"
#         raise HTTPException(
#             status_code=status.HTTP_401_UNAUTHORIZED,
#             detail="Session expired or logged out. Please login again.",
#         )

    # row = await db_conn.fetchrow(
    #     """
    #     SELECT m.status, r.name AS role_name
    #     FROM public.organization_members m
    #     LEFT JOIN public.roles r ON r.id = m.role_id
    #     WHERE m.user_id = $1 AND m.organization_id = $2
    #     """,
    #     user_id,
    #     organization_id,
    # )
    print("user_id")
    print(user_id)
    print(organization_id)
    print("new")
    # Determine strict user type from JWT metadata
    user_type = user_metadata.get("type")
    if not user_type:
        request.state.audit_description = "JWT token missing user type"
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing user type",
        )

    # Branch by user type and validate membership/status
    role_name = "unknown"
    if user_type == "organization_member":
        row = await db_conn.fetchrow(
            """
            SELECT m.status, m.email AS db_email, r.name AS role_name
            FROM public.organization_members m
            LEFT JOIN public.roles r ON r.id = m.role_id
            WHERE m.user_id = $1 AND m.organization_id = $2
            """,
            user_id,
            organization_id,
        )

        if not row:
            request.state.audit_description = "User is not a member of the organization"
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is not a member of this organization.",
            )

        if not is_allowed_user_status(row["status"]):
            request.state.audit_description = f"Membership status is '{row['status']}'"
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your account is suspended or inactive.",
            )

        db_email = row["db_email"]
        if (
            db_email
            and user_email
            and db_email.strip().lower() != user_email.strip().lower()
        ):
            request.state.audit_user_context["user_role"] = row["role_name"] or "unknown"
            request.state.audit_description = "JWT email does not match member record"
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Email mismatch between token and organization membership.",
            )

        role_name = row["role_name"] or "unknown"

    elif user_type == "client":
        row = await db_conn.fetchrow(
            """
            SELECT email AS db_email
            FROM public.client_members
            WHERE id = $1 AND organization_id = $2
            """,
            user_id,
            organization_id,
        )
        if not row:
            request.state.audit_description = "Client user not found in organization"
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is not a client member of this organization.",
            )
        db_email = row["db_email"]
        if (
            db_email
            and user_email
            and db_email.strip().lower() != user_email.strip().lower()
        ):
            request.state.audit_user_context["user_role"] = "client"
            request.state.audit_description = "JWT email does not match client member record"
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Email mismatch between token and client membership.",
            )
        role_name = "client"

    elif user_type == "candidate":
        row = await db_conn.fetchrow(
            """
            SELECT email AS db_email, is_active
            FROM public.candidates
            WHERE candidate_id = $1 AND organization_id = $2
            """,
            user_id,
            organization_id,
        )
        if not row:
            request.state.audit_description = "Candidate user not found in organization"
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is not a candidate of this organization.",
            )
        if not row["is_active"]:
            request.state.audit_description = "Candidate account is inactive"
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your candidate account is inactive.",
            )
        db_email = row["db_email"]
        if (
            db_email
            and user_email
            and db_email.strip().lower() != user_email.strip().lower()
        ):
            request.state.audit_user_context["user_role"] = "candidate"
            request.state.audit_description = "JWT email does not match candidate record"
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Email mismatch between token and candidate profile.",
            )
        role_name = "candidate"

    else:
        request.state.audit_description = f"Unsupported user type: {user_type}"
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: unsupported user type",
        )

    # ✅ User is valid, update audit context and success markers
    request.state.audit_user_context["user_role"] = role_name
    request.state.audit_risk_level = "low"
    request.state.audit_description = "Successfully authenticated and authorized user"
    print("get_user_from_auth")

    return user


# def get_user_from_auth(request: Request) -> dict:
#     """
#     Dependency that ensures `JWTAuthMiddleware` has already decoded a token
#     and stored it in request.state.user.
#     """
#     user = getattr(request.state, "user", None)
#     if not user:
#         # either no Authorization header or invalid/expired token
#         raise HTTPException(
#             status_code=status.HTTP_401_UNAUTHORIZED,
#             detail="Not authenticated",
#             headers={"WWW-Authenticate": "Bearer"},
#         )


#     return user


class JWTAuthMiddleware(BaseHTTPMiddleware):
    """Middleware for JWT token validation and user authentication.

    This middleware validates JWT tokens and stores the decoded user
    information in the request state. It ensures that only authenticated
    users can access protected routes.

    Attributes:
        app (FastAPI): The FastAPI application instance
        supabase (Client): The Supabase client instance
    """

    # pylint: disable=R0903

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
            print(payload)
        except jwt.ExpiredSignatureError:
            return responses.JSONResponse(
                status_code=HTTP_401_UNAUTHORIZED, content={"detail": "Token expired"}
            )
        except jwt.InvalidTokenError as exception:
            print("JWT DECODE ERROR:", str(exception))
            return responses.JSONResponse(
                status_code=HTTP_401_UNAUTHORIZED, content={"detail": "Invalid token"}
            )

        return await call_next(request)
