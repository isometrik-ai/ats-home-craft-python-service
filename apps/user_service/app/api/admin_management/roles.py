"""
Roles Management API Module

This module provides CRUD operations for role management.
All endpoints include proper authentication, validation, and database operations.

"""
from datetime import datetime
import uuid

from fastapi import APIRouter, HTTPException, status, Depends, Request
from pydantic import BaseModel

# Logger import
from apps.user_service.app.dependencies.logger import get_logger

# Utility imports
from apps.user_service.app.dependencies.common_utils import (
    extract_user_context,
    require_permission,
    format_iso_datetime,
    safe_json_loads,
    validate_uuid_format,
    validate_uuid_list,
)
from apps.user_service.app.dependencies.roles_utils import (
    validate_role_type,
    build_roles_filter_query,
    build_roles_count_query,
    build_role_filter_message,
    validate_permissions_exist,
    check_role_name_unique,
    assign_permissions_to_role,
    check_role_exists,
    remove_all_permissions_from_role,
    check_role_usage,
    check_roles_manage_permission,
    check_roles_manage_multiple_permission,
)

# Schema imports
from apps.user_service.app.schemas.admin_access_management import (
    RolesResponse,
    RoleItem,
    RoleQueryParams,
    RoleDetailResponse,
    RoleDetailItem,
    PermissionItem,
    CreateRoleRequest,
    CreateRoleResponse,
    UpdateRoleRequest,
    UpdateRoleResponse,
    DeleteRoleResponse,
)

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
    audit_api_call,
)  # adjust path as needed

# Local imports
from libs.shared_db.postgres_db.db import get_async_db_conn
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import ROLE_SELECT_FIELDS, PERMISSION_SELECT_FIELDS



# Authentication description for API documentation
AUTH_DESCRIPTION = "Bearer token required for authentication"

# Initialize logger for roles module
logger = get_logger("roles-api")
logger.info("Roles API module loaded")

# Create router for roles endpoints
router = APIRouter(prefix="/roles", tags=["Roles Management"])

class RoleResponse(BaseModel):
    """Response model for role operations"""

    message: str
    status: str = "success"

    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {"message": self.message, "status": self.status}


# @handle_api_exceptions("get roles")
@router.get("", response_model=RolesResponse, status_code=status.HTTP_200_OK)
@limiter.limit("100/minute")
# @audit_api_call(
#     action_type="READ",
#     data_classification="general",
#     compliance_tags=["access_control", "soc2_audit"],
#     table_name="roles",
#     category="ROLE",
# )
# pylint: disable=unused-argument  # Required by @limiter.limit
async def get_roles(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    db_conn=Depends(get_async_db_conn),
    query_params: RoleQueryParams = Depends(),
):
    """
    Get all roles for the current organization (Optimized & Truly Async)

    This endpoint retrieves all roles for the authenticated user's organization.
    Uses truly async database operations for best performance and scalability.


    Args:
        request (Request): The FastAPI request object
        current_user (dict): Decoded JWT token containing user information
        db_conn: AsyncPG database connection (truly async)
        query_params (RoleQueryParams): Query parameters object containing
            search, pagination, and filter options

    Returns:
        RolesResponse: List of roles with id, name, description, is_default,
            and created_at

    Raises:
        HTTPException: 400 for invalid parameters
        HTTPException: 403 for insufficient permissions
        HTTPException: 500 for database errors
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())
    logger.info(
        "GET /roles request started - Request ID: %s, "
        "User ID: %s, "
        "Organization ID: %s, "
        "Search: %s, Role Type: %s, "
        "Skip: %s, Limit: %s",
        request_id,current_user.get('user_id'),current_user.get('organization_id'),
        query_params.search,query_params.role_type,query_params.skip,query_params.limit
    )
    # Validate role_type parameter
    if query_params.role_type:
        validate_role_type(query_params.role_type)
        logger.debug(
            (
                "Role type validation passed: %s - Request ID: %s",
                query_params.role_type,request_id
            )
        )

    # Extract and validate user context from JWT token
    user_context = await check_roles_manage_multiple_permission(current_user, db_conn)
    logger.debug(
        ("User permissions validated for "
        "organization: %s - "
        "Request ID: %s",user_context.organization_id,request_id)
    )

    # Build query using utility function
    roles_query, query_params_list = build_roles_filter_query(
        organization_id=user_context.organization_id,
        search=query_params.search,
        # role_type=query_params.role_type,
        limit=query_params.limit,
        offset=query_params.skip,
        params=query_params,
    )

    # Execute roles query
    roles_data = await db_conn.fetch(roles_query, *query_params_list)
    logger.debug(
        ("Retrieved %s roles from database - Request ID: %s",len(roles_data),request_id)
    )

    # Build and execute count query
    count_query, count_params = build_roles_count_query(
        organization_id=user_context.organization_id,
        search=query_params.search,
        # role_type=query_params.role_type,
        params=query_params,
    )

    count_result = await db_conn.fetchrow(count_query, *count_params)
    total_count = count_result["total_count"] if count_result else 0
    logger.debug(("Total roles count: %s - Request ID: %s",total_count,request_id))

    # Format roles data using utility functions
    roles = [
        RoleItem(
            id=str(role["id"]),
            name=role["name"],
            description=role["description"],
            is_default=role["is_default"],
            created_at=format_iso_datetime(role["created_at"]) or "",
            user_count=role["user_count"],
            permission_count=role["permission_count"],
            permission_categories=safe_json_loads(role["permission_categories"], {}),
        )
        for role in roles_data
    ]

    # Build response message using utility function
    message = build_role_filter_message(
        search=query_params.search,
        role_type=query_params.role_type,
        skip=query_params.skip,
        limit=query_params.limit,
    )

    logger.info(
        "GET /roles request completed successfully - Request ID: %s, "
        "Roles Count: %s, Total Count: %s, "
        "Status Code: %s",
        request_id,len(roles),total_count,status.HTTP_200_OK
    )

    return RolesResponse(
        status_code=status.HTTP_200_OK,
        message=message,
        roles=roles,
        total_count=total_count,
    )


# @handle_api_exceptions("get role by ID")
@router.get(
    "/{role_id}", response_model=RoleDetailResponse, status_code=status.HTTP_200_OK
)
@limiter.limit("100/minute")
# @audit_api_call(
#     action_type="READ",
#     data_classification="confidential",
#     compliance_tags=[
#         "gdpr",
#         "pii",
#         "audit_required",
#     ],
#     table_name="roles",
#     category="ROLE",
# )
async def get_role_by_id(
    role_id: str,
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    db_conn=Depends(get_async_db_conn),
):
    """
    Get role by ID with all associated permissions (Optimized & Truly Async)

    Args:
        role_id (str): UUID of the role to retrieve
        request (Request): The FastAPI request object
        current_user (dict): Decoded JWT token containing user information
        db_conn: AsyncPG database connection (truly async)

    Returns:
        RoleDetailResponse: Detailed role information with associated permissions

    """
    request_id = str(uuid.uuid4())
    logger.info(
        "GET /roles/%s request started - Request ID: %s, "
        "Role ID: %s, User ID: %s, "
        "Organization ID: %s",
        role_id,request_id,role_id,current_user.get('user_id'),current_user.get('organization_id')
    )
    # Validate role_id format using utility function
    request.state.audit_requested_id = role_id
    validate_uuid_format(role_id, "role ID")
    logger.debug(
        "Role ID format validation passed: %s - Request ID: %s",role_id,request_id
    )

    # Extract and validate user context from JWT token
    user_context = await check_roles_manage_permission(current_user, db_conn)
    logger.debug(
        "User permissions validated for organization: %s",user_context.organization_id,
        extra={"request_id": request_id},
    )

    # Fetch role details using async SQL
    role_query = f"""
                    SELECT
                        {ROLE_SELECT_FIELDS}
                    FROM public.roles r
                    WHERE r.id = $1 AND r.organization_id = $2;
                """

    role_data = await db_conn.fetchrow(
        role_query, role_id, user_context.organization_id
    )

    # Check if role exists in user's organization
    if not role_data:
        logger.warning(
            "Role not found or access denied - Request ID: %s, "
            "Role ID: %s, Organization ID: %s, "
            "Status Code: %s",
            request_id,role_id,user_context.organization_id,status.HTTP_404_NOT_FOUND
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found or access denied",
        )

    # Fetch permissions for this role using async SQL

    permissions_query = f"""
        SELECT DISTINCT
            {PERMISSION_SELECT_FIELDS}
        FROM public.role_permissions rp
        INNER JOIN public.permissions p ON rp.permission_id = p.id
        WHERE rp.role_id = $1
            AND rp.organization_id = $2
        ORDER BY p.category NULLS LAST, p.name ASC;
    """

    permissions_data = await db_conn.fetch(
        permissions_query, role_id, user_context.organization_id
    )
    logger.debug(
        "Retrieved %s permissions for role - Request ID: %s",len(permissions_data),request_id
    )

    # Format permissions data using utility functions
    permissions = [
        PermissionItem(
            id=str(permission["id"]),
            name=permission["name"],
            code=permission["code"],
            category=permission["category"],
            description=permission["description"],
            created_at=format_iso_datetime(permission["created_at"]) or "",
        )
        for permission in permissions_data
    ]

    # Format role data using utility functions
    role_detail = RoleDetailItem(
        id=str(role_data["id"]),
        name=role_data["name"],
        description=role_data["description"],
        is_default=role_data["is_default"],
        created_at=format_iso_datetime(role_data["created_at"]) or "",
        updated_at=format_iso_datetime(role_data["updated_at"]) or "",
        permissions=permissions,
    )

    logger.info(
        "GET /roles/%s request completed successfully - Request ID: %s, "
        "Role ID: %s, Role Name: %s, "
        "Permissions Count: %s, Status Code: %s",
        role_id,request_id,role_id,role_data['name'],len(permissions),status.HTTP_200_OK
    )

    return RoleDetailResponse(
        status_code=status.HTTP_200_OK,
        message=(
            f"Role details retrieved successfully "
            f"(found {len(permissions)} permissions)"
        ),
        role=role_detail,
    )


# @handle_api_exceptions("create role")
@router.post("", response_model=CreateRoleResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="confidential",
    compliance_tags=[
        "access_control",  # Role creation affects access control and security policies.
        "soc2_audit",  # Role management is critical for SOC2 compliance and access governance.
        "audit_required",  # Role creation must be logged for compliance and security audits.
    ],
    table_name="roles",
    category="ROLE",
)
async def create_role(
    role_data: CreateRoleRequest,
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    db_conn=Depends(get_async_db_conn),
):
    """
    Create a new role with associated permissions (Optimized & Truly Async

    Args:
        role_data (CreateRoleRequest): Role creation data including name, type,
            description, and permission IDs
        request (Request): The FastAPI request object
        current_user (dict): Decoded JWT token containing user information
        db_conn: AsyncPG database connection (truly async)

    Returns:
        CreateRoleResponse: Created role information with associated permissions

    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())
    logger.info(
        "POST /roles request started - Request ID: %s, "
        "User ID: %s, "
        "Organization ID: %s, "
        "Role Name: %s, Role Type: %s, "
        "Permissions Count: %s",
        request_id,
        current_user.get('user_id'),
        current_user.get('organization_id'),
        role_data.name,
        role_data.role_type,
        len(role_data.permission_ids) if role_data.permission_ids else 0
    )
    # Validate role type using utility function
    # Set audit context for role creation
    request.state.audit_table = "roles"
    request.state.audit_description = (
        f"Created new role: {role_data.name} (type: {role_data.role_type})"
    )
    request.state.audit_risk_level = "medium"

    validate_role_type(role_data.role_type)
    logger.debug(
        ("Role type validation passed: %s - Request ID: %s",role_data.role_type,request_id)
    )

    # Validate permission IDs format using utility function
    if role_data.permission_ids:
        validate_uuid_list(role_data.permission_ids, "permission ID")
        logger.debug(
            "Permission IDs validation passed: %s \n permissions - Request ID: %s",
            len(role_data.permission_ids),request_id
        )

    # Extract and validate user context from JWT token
    user_context = extract_user_context(current_user)

    # Check permission using utility function
    await require_permission(
        permission_code="settings.roles.manage",
        user_context=user_context,
        db_conn=db_conn,
        action_description="create roles",
    )
    logger.debug(
        "User permissions validated for role creation - Request ID: %s",request_id
    )

    # Validate that all permission IDs exist in the organization
    await validate_permissions_exist(
        permission_ids=role_data.permission_ids,
        organization_id=user_context.organization_id,
        db_conn=db_conn,
    )
    logger.debug("Permission existence validation passed - Request ID: %s",request_id)

    # Check if role name is unique using utility function
    await check_role_name_unique(
        name=role_data.name,
        organization_id=user_context.organization_id,
        db_conn=db_conn,
    )
    logger.debug(
        "Role name uniqueness validation passed: %s - Request ID: %s",
        role_data.name,request_id
    )

    # Convert role_type to is_default boolean
    is_default = role_data.role_type == "system"

    # Create the role using async transaction
    async with db_conn.transaction():
        # Create the role
        create_role_query = """
            INSERT INTO public.roles (name, description, organization_id,
                                     is_default, created_at, updated_at)
            VALUES ($1, $2, $3, $4, NOW(), NOW())
            RETURNING id, name, description, is_default, created_at, updated_at;
        """

        created_role = await db_conn.fetchrow(
            create_role_query,
            role_data.name,
            role_data.description,
            user_context.organization_id,
            is_default,
        )

        role_id = created_role["id"]
        logger.debug("Role created with ID: %s - Request ID: %s",role_id,request_id)

        # Set audit context with new role data
        request.state.raw_audit_new_data = {
            "role_id": str(role_id),
            "role_name": role_data.name,
            "role_type": role_data.role_type,
            "description": role_data.description,
            "permission_ids": role_data.permission_ids,
            "organization_id": user_context.organization_id,
            "created_at": (
                created_role["created_at"].isoformat()
                if created_role["created_at"]
                else None
            ),
        }

        # Assign permissions to the role using utility function
        await assign_permissions_to_role(
            role_id=str(role_id),
            organization_id=user_context.organization_id,
            permission_ids=role_data.permission_ids,
            db_conn=db_conn,
        )
        logger.debug(
            "Permissions assigned to role: %s \npermissions - Request ID: %s",
            len(role_data.permission_ids) if role_data.permission_ids else 0,request_id
        )

    logger.info(
        "POST /roles request completed successfully - Request ID: %s, "
        "Role ID: %s, Role Name: %s, "
        "Status Code: %s",
        request_id,str(role_id),role_data.name,status.HTTP_201_CREATED)
    logger.info(
        "Permissions Count: %s, ",
        (len(role_data.permission_ids) if role_data.permission_ids else 0)
    )

    return CreateRoleResponse(
        status_code=status.HTTP_201_CREATED,
        message="Role created successfully",
    )


def _build_update_query(role_data: UpdateRoleRequest, user_context, role_id: str):
    """Build dynamic update query for role fields."""
    update_fields = []
    update_params = []
    param_count = 0

    if role_data.name is not None:
        param_count += 1
        update_fields.append(f"name = ${param_count}")
        update_params.append(role_data.name)

    if role_data.description is not None:
        param_count += 1
        update_fields.append(f"description = ${param_count}")
        update_params.append(role_data.description)

    if role_data.is_default is not None:
        param_count += 1
        update_fields.append(f"is_default = ${param_count}")
        update_params.append(role_data.is_default)

    if update_fields:
        # Always add updated_at when updating other fields
        update_fields.append("updated_at = NOW()")

        # Add WHERE clause parameters
        param_count += 1
        role_id_param = f"${param_count}"
        update_params.append(role_id)

        param_count += 1
        org_id_param = f"${param_count}"
        update_params.append(user_context.organization_id)

        update_role_query = f"""
            UPDATE public.roles
            SET {', '.join(update_fields)}
            WHERE id = {role_id_param} AND organization_id = {org_id_param};
        """

        return update_role_query, update_params

    return "", []


def _build_update_response_message(role_data: UpdateRoleRequest):
    """Build response message based on what was updated."""
    updated_fields = []

    if role_data.name is not None:
        updated_fields.append("name")
    if role_data.description is not None:
        updated_fields.append("description")
    if role_data.is_default is not None:
        updated_fields.append("type")
    if role_data.permission_ids is not None:
        if len(role_data.permission_ids) == 0:
            updated_fields.append("permissions (removed all)")
        else:
            updated_fields.append(
                f"permissions ({len(role_data.permission_ids)} assigned)"
            )

    if not updated_fields:
        return "No changes were made to the role"

    return f"Role updated successfully. Updated: {', '.join(updated_fields)}"


# @handle_api_exceptions("update role")
@router.put(
    "/{role_id}",
    response_model=UpdateRoleResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=[
        "access_control",  # Role updates affect access control and security policies.
        "soc2_audit",  # Role modifications are critical for SOC2 compliance and access governance.
        "audit_required",  # Role updates must be logged for compliance and security audits.
    ],
    table_name="roles",
    category="ROLE",
)
async def update_role(
    role_id: str,
    role_data: UpdateRoleRequest,
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    db_conn=Depends(get_async_db_conn),
):
    """
    Update an existing role's properties and permissions (Optimized & Truly Async)

    Update Behavior:
    - name: If provided, updates the role name (validates uniqueness)
    - description: If provided, updates the role description
    - is_default: If provided, updates the role type (system vs custom)
    - permission_ids: If provided:
        * Array with values: Replaces all existing permissions with these
        * Empty array: Removes all permissions from the role
        * Not provided: Leaves permissions unchanged

    Args:
        role_id (str): UUID of the role to update
        role_data (UpdateRoleRequest): Updated role data (all fields optional)
        request (Request): The FastAPI request object
        current_user (dict): Decoded JWT token containing user information
        db_conn: AsyncPG database connection (truly async)

    Returns:
        UpdateRoleResponse: Success message describing what was updated

    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())
    logger.info(
        "PUT /roles/%s request started - Request ID: %s, "
        "Role ID: %s, User ID: %s, "
        "Organization ID: %s, "
        "Update Fields: name=%s, "
        "description=%s, "
        "is_default=%s, "
        "permissions=%s",
        role_id,request_id,role_id,current_user.get('user_id'),current_user.get('organization_id'),
        role_data.name is not None,role_data.description is not None,
        role_data.is_default is not None,role_data.permission_ids is not None
    )
    request.state.audit_requested_id = role_id

    # Validate role_id format using utility function
    validate_uuid_format(role_id, "role ID")
    logger.debug(
        "Role ID format validation passed: %s - Request ID: %s",role_id,request_id
    )

    # Extract and validate user context from JWT token
    user_context = await check_roles_manage_permission(current_user, db_conn)
    logger.debug(
        "User permissions validated for organization: %s - Request ID: %s",
        user_context.organization_id,request_id
    )

    # Check if role exists in organization using utility function
    existing_role = await check_role_exists(
        role_id, user_context.organization_id, db_conn
    )
    logger.debug("Role existence confirmed: %s - Request ID: %s",existing_role['name'],request_id)

    # Get current permissions for the role
    current_permissions_query = """
        SELECT permission_id
        FROM public.role_permissions
        WHERE role_id = $1 AND organization_id = $2
    """
    current_permissions = await db_conn.fetch(
        current_permissions_query, role_id, user_context.organization_id
    )
    current_permission_ids = [
        str(perm["permission_id"]) for perm in current_permissions
    ]

    # Set audit context for role update
    request.state.audit_table = "roles"
    request.state.audit_requested_id = role_id
    request.state.audit_description = f"Updated role: {existing_role['name']}"
    request.state.audit_risk_level = "medium"

    # Set old values for audit comparison
    request.state.raw_audit_old_data = {
        "role_id": role_id,
        "role_name": existing_role["name"],
        "description": existing_role["description"],
        "is_default": existing_role["is_default"],
        "permission_ids": current_permission_ids,
        "organization_id": user_context.organization_id,
    }

    # Validate permission IDs if provided using utility function
    if role_data.permission_ids is not None and len(role_data.permission_ids) > 0:
        validate_uuid_list(role_data.permission_ids, "permission ID")
        await validate_permissions_exist(
            permission_ids=role_data.permission_ids,
            organization_id=user_context.organization_id,
            db_conn=db_conn,
        )
        logger.debug(
            "Permission IDs validation passed: %s \npermissions - Request ID: %s",
            len(role_data.permission_ids),request_id
        )

    # Check if new name conflicts with existing roles using utility function
    if role_data.name is not None and role_data.name != existing_role["name"]:
        await check_role_name_unique(
            name=role_data.name,
            organization_id=user_context.organization_id,
            db_conn=db_conn,
            exclude_role_id=role_id,
        )
        logger.debug(
            "Role name uniqueness validation passed: %s - Request ID: %s",
            role_data.name,request_id
        )

    # Update the role with dynamic fields
    update_query, update_params = _build_update_query(role_data, user_context, role_id)

    if update_query:
        await db_conn.execute(update_query, *update_params)
        logger.debug("Role fields updated successfully - Request ID: %s",request_id)
    else:
        logger.debug("No role fields to update - Request ID: %s",request_id)

    # Handle permissions update if provided using utility functions
    if role_data.permission_ids is not None:
        logger.debug("Updating permissions for role - Request ID: %s",request_id)
        # Start transaction for permissions update
        async with db_conn.transaction():
            # Remove all existing permissions using utility function
            await remove_all_permissions_from_role(
                role_id=role_id,
                organization_id=user_context.organization_id,
                db_conn=db_conn,
            )
            logger.debug(
                "Removed all existing permissions from role - Request ID: %s",request_id
            )

            # Add new permissions if any are provided using utility function
            if len(role_data.permission_ids) > 0:
                await assign_permissions_to_role(
                    role_id=role_id,
                    organization_id=user_context.organization_id,
                    permission_ids=role_data.permission_ids,
                    db_conn=db_conn,
                )
                logger.debug(
                    "Assigned %s new permissions to role - Request ID: %s",
                    len(role_data.permission_ids),request_id
                )
            else:
                logger.debug("No new permissions to assign - Request ID: %s",request_id)

    # Build response message based on what was updated
    message = _build_update_response_message(role_data)

    # Set new values for audit comparison
    new_role_data = {
        "role_id": role_id,
        "role_name": (
            role_data.name if role_data.name is not None else existing_role["name"]
        ),
        "description": (
            role_data.description
            if role_data.description is not None
            else existing_role["description"]
        ),
        "is_default": (
            role_data.is_default
            if role_data.is_default is not None
            else existing_role["is_default"]
        ),
        "permission_ids": role_data.permission_ids,
        "organization_id": user_context.organization_id,
    }

    request.state.raw_audit_new_data = new_role_data

    logger.info(
        "PUT /roles/%s request completed successfully - Request ID: %s, "
        "Role ID: %s, Role Name: %s, "
        "Update Message: %s, Status Code: %s",
        role_id,request_id,role_id,existing_role['name'],message,status.HTTP_200_OK
    )

    return UpdateRoleResponse(
        status_code=status.HTTP_200_OK,
        message=message,
    )


# @handle_api_exceptions("delete role")
@router.delete(
    "/{role_id}",
    response_model=DeleteRoleResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="confidential",
    compliance_tags=[
        "access_control",  # Role deletion affects access control and security policies.
        "soc2_audit",  # Role deletion is critical for SOC2 compliance and access governance.
        "audit_required",  # Role deletion must be logged for compliance and security audits.
    ],
    table_name="roles",
    category="ROLE",
)
async def delete_role(
    role_id: str,
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    db_conn=Depends(get_async_db_conn),
):
    """
    Delete an existing role (Optimized & Truly Async)

    Args:
        role_id (str): UUID of the role to delete
        request (Request): The FastAPI request object
        current_user (dict): Decoded JWT token containing user information
        db_conn: AsyncPG database connection (truly async)

    Returns:
        DeleteRoleResponse: Success message for role deletion
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())
    logger.info(
        "DELETE /roles/%s request started - Request ID: %s, "
        "Role ID: %s, User ID: %s, "
        "Organization ID: %s",
        role_id,request_id,role_id,current_user.get('user_id'),current_user.get('organization_id')
    )
    request.state.audit_requested_id = role_id

    # Validate role_id format using utility function
    validate_uuid_format(role_id, "role ID")
    logger.debug("Role ID format validation passed: %s - Request ID: %s",role_id,request_id)

    # Extract and validate user context from JWT token
    user_context = await check_roles_manage_permission(current_user, db_conn)
    logger.debug(
        "User permissions validated for organization: %s - Request ID: %s",
        user_context.organization_id,request_id
    )

    # Check if role exists in organization using utility function
    existing_role = await check_role_exists(
        role_id, user_context.organization_id, db_conn
    )
    logger.debug(
        "Role existence confirmed: %s",existing_role['name'],
        extra={"request_id": request_id},
    )

    # Get current permissions for the role
    current_permissions_query = """
        SELECT permission_id
        FROM public.role_permissions
        WHERE role_id = $1 AND organization_id = $2
    """
    current_permissions = await db_conn.fetch(
        current_permissions_query, role_id, user_context.organization_id
    )
    current_permission_ids = [str(perm["permission_id"]) for perm in current_permissions]

    # Set audit context for role deletion
    request.state.audit_table = "roles"
    request.state.audit_requested_id = role_id
    request.state.audit_description = f"Deleted role: {existing_role['name']}"
    request.state.audit_risk_level = "high"

    # Set old values for audit comparison (what was deleted)
    request.state.raw_audit_old_data = {
        "role_id": role_id,
        "role_name": existing_role["name"],
        "description": existing_role["description"],
        "is_default": existing_role["is_default"],
        "permission_ids": current_permission_ids,
        "organization_id": user_context.organization_id,
    }

    # Check if role is in use by organization members using utility function
    member_count = await check_role_usage(role_id, user_context.organization_id, db_conn)
    logger.debug("Role usage check completed: %s ",member_count)
    logger.debug("members using this role - Request ID: %s",request_id)

    if member_count > 0:
        logger.warning("Cannot delete role - it is currently in use - Request ID: %s, ",request_id)
        logger.warning("Role ID: %s, Role Name: %s, ",role_id,existing_role['name'])
        logger.warning("Member Count: %s, Status Code: %s",member_count,status.HTTP_409_CONFLICT)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot delete role. It is currently assigned to "
                f"{member_count} organization member(s)"
            ),
        )

    # Delete the role using async transaction (with cascade handling)
    async with db_conn.transaction():
        # First, remove all role-permission relationships using utility function
        await remove_all_permissions_from_role(
            role_id=role_id,
            organization_id=user_context.organization_id,
            db_conn=db_conn,
        )
        logger.debug(
            "Removed all permissions from role before deletion - Request ID: %s",request_id
        )

        # Then, delete the role itself
        delete_role_query = """
            DELETE FROM public.roles
            WHERE id = $1 AND organization_id = $2;
        """

        result = await db_conn.execute(
            delete_role_query, role_id, user_context.organization_id
        )

        # Check if the role was actually deleted
        if result == "DELETE 0":
            logger.warning(
                "Role not found or already deleted - Request ID: %s, "
                "Role ID: %s, Organization ID: %s, "
                "Status Code: %s",
                request_id,role_id,user_context.organization_id,status.HTTP_404_NOT_FOUND
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Role not found or already deleted",
            )

            # Set new values for audit comparison (empty since role was deleted)
        request.state.raw_audit_new_data = {
            "role_id": role_id,
            "role_name": existing_role["name"],
            "description": "ROLE_DELETED",
            "is_default": existing_role["is_default"],
            "permission_ids": [],
            "organization_id": user_context.organization_id,
            "deletion_timestamp": datetime.utcnow().isoformat(),
        }

    logger.info(
        "DELETE /roles/%s request completed successfully - Request ID: %s, "
        "Role ID: %s, Role Name: %s, "
        "Status Code: %s",
        role_id,request_id,role_id,existing_role['name'],status.HTTP_200_OK
    )

    return DeleteRoleResponse(
        status_code=status.HTTP_200_OK,
        message="Role deleted successfully",
    )
