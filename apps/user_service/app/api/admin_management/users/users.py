"""
Users Management API Module
This module provides CRUD operations for user management.
All endpoints include proper authentication, validation, and database operations.
"""

from datetime import datetime

from fastapi import APIRouter, status, Depends, Request

# Logger import
from apps.user_service.app.dependencies.logger import get_logger

from apps.user_service.app.schemas.admin_access_management import UserQueryParams
from apps.user_service.app.app_instance import limiter

from apps.user_service.app.dependencies.common_utils import (
    validate_pagination_params,
    check_permissions,
)

# Schema imports
from apps.user_service.app.schemas.users import (
    UserListResponse,
)

# Local imports
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import SETTINGS_USERS_MANAGE

# Database operations imports
from libs.shared_db.postgres_db.user_service_operations.user_operations import (
    get_users_details_list,
    get_users_total_count,
    transform_users
)

# Create router for users endpoints
router = APIRouter(prefix="/users", tags=["Users Management"])

# Authentication description for API documentation
AUTH_DESCRIPTION = "Bearer token required for authentication"

# Initialize logger for users module
logger = get_logger("users-api")


@router.get("/list", response_model=UserListResponse, status_code=status.HTTP_200_OK)
@limiter.limit("20/minute")
# @audit_api_call(
#     action_type="READ",
#     data_classification="confidential",
#     compliance_tags=[
#         "gdpr",  # Accessing user list data involves personal information
#         "pii",  # User list contains personally identifiable information
#         "audit_required",  # User list access must be logged for compliance and security audits
#     ],
#     table_name="organization_members",
#     category="USER_LIST",
# )
async def get_users_list(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    query_params: UserQueryParams = Depends()
):
    """
    List all users in the current organization (async, paginated, sequential)
    """
    # # Generate request ID for tracking
    # request_id = str(uuid.uuid4())

    # Set audit context for user list access
    request.state.audit_table = "organization_members"
    request.state.audit_description = (
        f"Admin accessed user list with search: '{query_params.search or 'none'}'"
    )
    request.state.audit_risk_level = "medium"

    # Validate pagination params and calculate offset
    page, page_size, offset = validate_pagination_params(
        query_params.page, query_params.page_size
    )

    # Permission check
    user_context = await check_permissions(current_user, SETTINGS_USERS_MANAGE,
        action_description="access user list")

    # Get users list using database operations
    users_data = await get_users_details_list(
        organization_id=user_context.organization_id,
        search=query_params.search,
        limit=page_size,
        offset=offset
    )

    # Get total count using database operations
    total_count = await get_users_total_count(
        organization_id=user_context.organization_id,
        search=query_params.search
    )

    users = await transform_users(users_data, user_context.organization_id)

    # Set audit data for user list access
    request.state.raw_audit_new_data = {
        "organization_id": str(user_context.organization_id),
        "accessed_by_user_id": user_context.user_id,
        "accessed_by_email": user_context.email,
        "search_term": query_params.search or "none",
        "page": page,
        "page_size": page_size,
        "total_users_retrieved": len(users),
        "total_count": total_count,
        "access_timestamp": datetime.now().isoformat(),
        "user_ids_accessed": [user.user_id for user in users] if users else [],
    }

    return UserListResponse(
        message="Users retrieved successfully",
        data=users,
        total_count=total_count,
        page=page,
        page_size=page_size,
    )
