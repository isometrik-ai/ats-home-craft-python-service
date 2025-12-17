"""Teams Management API Module.

This module provides CRUD operations for team management.
All endpoints include proper authentication, validation, and database operations.
"""

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.business.team_service import TeamService
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.dependencies.logger import get_logger

# Schema imports
from apps.user_service.app.schemas.teams import (
    CreateTeamRequest,
    TeamDetailResponse,
    TeamsListResponse,
    UpdateTeamRequest,
)
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    handle_api_exceptions,
    validate_uuid_format,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth

# Permission imports
from libs.shared_utils.common_query import (
    TEAMS_MANAGEMENT_CREATE,
    TEAMS_MANAGEMENT_DELETE,
    TEAMS_MANAGEMENT_EDIT,
    TEAMS_MANAGEMENT_VIEW,
)
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

# Create router for teams endpoints
router = APIRouter(prefix="/teams", tags=["Teams Management"])

# Initialize logger
logger = get_logger("teams-api")


@handle_api_exceptions("create team")
@router.post(
    "",
    description="Create a new team",
    summary="Create a new team",
    response_model=None,
    status_code=http_status.HTTP_201_CREATED,
    responses={
        http_status.HTTP_201_CREATED: {"model": None, "description": "Team created successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Invalid request body or data"},
        http_status.HTTP_403_FORBIDDEN: {
            "description": "User does not have permission to create team"
        },
        http_status.HTTP_409_CONFLICT: {"description": "Team name already exists"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Rate limit exceeded"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("20/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",
        "pii",
        "soc2_audit",
        "audit_required",
    ],
    table_name="teams",
    category="TEAM",
)
async def create_team_endpoint(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateTeamRequest = Body(...),
):
    """Create a new team.

    This endpoint creates a team with the following validations:
    - Checks user permissions (requires teams_management.create)
    - Validates team name uniqueness within organization
    - Validates all member user IDs belong to the organization
    - Creates team record and team_members records

    Args:
        request: FastAPI request object
        db_connection: postgresql database connection
        current_user: Decoded JWT token containing user information
        body: Team creation data

    Raises:
        HTTPException: 400 for invalid data
        HTTPException: 403 for insufficient permissions
        HTTPException: 409 for duplicate team name
    """
    # Set audit context for team creation
    request.state.audit_table = "teams"
    request.state.audit_description = f"Created new team: {body.name}"
    request.state.audit_risk_level = "high"

    # Check permissions and get user context (includes organization_id)
    user_context = await check_permissions(
        current_user,
        TEAMS_MANAGEMENT_CREATE,
    )

    # Set audit user context
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    # Create service with user context and delegate to service
    team_service = TeamService(db_connection=db_connection, user_context=user_context)
    await team_service.create_team(body)

    return success_response(
        request=request,
        message_key="teams.success.team_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("list teams")
@router.get(
    "",
    description="List all teams with pagination",
    summary="Get paginated list of teams",
    response_model=TeamsListResponse,
    status_code=http_status.HTTP_200_OK,
    responses={
        http_status.HTTP_200_OK: {
            "model": TeamDetailResponse,
            "description": "Team details retrieved successfully",
        },
        http_status.HTTP_400_BAD_REQUEST: {"description": "Invalid team ID format"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_403_FORBIDDEN: {
            "description": "User does not have permission to view this team"
        },
        http_status.HTTP_404_NOT_FOUND: {"description": "Team not found"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Rate limit exceeded"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
)
@limiter.limit("100/minute")
async def list_teams_endpoint(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    page: int = Query(1, ge=1, description="Page number for pagination"),
    page_size: int = Query(50, ge=1, le=100, description="Number of teams per page"),
    search: str | None = Query(None, description="Search term for team name"),
):
    """Retrieve paginated list of teams for the user's organization.

    This endpoint supports optional filtering by team name and pagination.
    Requires teams_management.view permission.

    Args:
        request: FastAPI request object
        db_connection: postgresql database connection
        current_user: Decoded JWT token containing user information
        page: Page number for pagination (1-indexed, minimum 1)
        page_size: Number of teams per page (minimum 1, maximum 100)
        search: Optional search term for team name filtering

    Returns:
        TeamsListResponse: List of teams and total filtered count

    Raises:
        HTTPException: 403 for insufficient permissions
    """
    # Validate permissions and get organization context
    user_context = await check_permissions(
        current_user,
        TEAMS_MANAGEMENT_VIEW,
    )

    # Create service with user context and delegate to service
    team_service = TeamService(db_connection=db_connection, user_context=user_context)
    result: TeamsListResponse = await team_service.list_teams(
        page=page, page_size=page_size, search=search
    )

    if not result.data:
        return success_response(
            request=request,
            message_key="success.no_data",
            custom_code=CustomStatusCode.NO_CONTENT,
            status_code=http_status.HTTP_204_NO_CONTENT,
        )

    return list_response(
        request=request,
        items=result.data,
        total=result.total_count,
        page=page,
        page_size=page_size,
        message_key="teams.success.teams_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("get team detail")
@router.get(
    "/{team_id}",
    description="Get detailed information for a specific team",
    summary="Retrieve team details including members",
    response_model=TeamDetailResponse,
    status_code=http_status.HTTP_200_OK,
    responses={
        http_status.HTTP_200_OK: {
            "model": TeamDetailResponse,
            "description": "Team details retrieved successfully",
        },
        http_status.HTTP_400_BAD_REQUEST: {"description": "Invalid team ID format"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_403_FORBIDDEN: {
            "description": "User does not have permission to view this team"
        },
        http_status.HTTP_404_NOT_FOUND: {"description": "Team not found"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Rate limit exceeded"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
)
@limiter.limit("100/minute")
async def get_team_detail_endpoint(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    team_id: str = Path(..., description="ID of the team to retrieve"),
):
    """Retrieve detailed information of a specific team.

    This endpoint retrieves team details including all members.
    Requires teams_management.view permission.

    Args:
        request: FastAPI request object
        db_connection: postgresql database connection
        current_user: Decoded JWT token containing user information
        team_id: UUID of the team to retrieve

    Returns:
        TeamDetailResponse: Detailed team info including members

    Raises:
        HTTPException: 400 for invalid team ID format
        HTTPException: 403 for insufficient permissions
        HTTPException: 404 if team not found
    """
    # Validate team ID
    validate_uuid_format(team_id, "team ID")

    # Validate permissions
    user_context = await check_permissions(
        current_user,
        TEAMS_MANAGEMENT_VIEW,
    )

    # Create service with user context and delegate to service
    team_service = TeamService(db_connection=db_connection, user_context=user_context)
    result: TeamDetailResponse = await team_service.get_team_detail(team_id)
    return success_response(
        request=request,
        message_key="teams.success.team_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=result.data,
    )


@handle_api_exceptions("delete team")
@router.delete(
    "/{team_id}",
    description="Delete a team",
    summary="Delete a team (soft delete)",
    response_model=None,
    status_code=http_status.HTTP_200_OK,
    responses={
        http_status.HTTP_200_OK: {"description": "Team deleted successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Invalid team ID format"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_403_FORBIDDEN: {
            "description": "User does not have permission to delete this team"
        },
        http_status.HTTP_404_NOT_FOUND: {"description": "Team not found"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Rate limit exceeded"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
)
@limiter.limit("20/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",
        "pii",
        "soc2_audit",
        "audit_required",
    ],
    table_name="teams",
    category="TEAM",
)
async def delete_team(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    team_id: str = Path(..., description="ID of the team to delete"),
):
    """Soft delete a team and hard delete its members.

    This endpoint performs a soft delete on the team (sets deleted_at timestamp)
    and hard deletes all team member associations.
    Requires teams_management.delete permission.

    Args:
        request: FastAPI request object
        db_connection: PostgreSQL database connection
        current_user: Decoded JWT token containing user information
        team_id: UUID of the team to delete

    Returns:
        None

    Raises:
        HTTPException: 400 for invalid team ID format
        HTTPException: 403 for insufficient permissions
        HTTPException: 404 if team not found
    """

    # Validate UUID format
    validate_uuid_format(team_id, "team ID")

    # Permission checks
    user_context = await check_permissions(
        current_user,
        TEAMS_MANAGEMENT_DELETE,
    )

    # Set audit metadata
    request.state.audit_table = "teams"
    request.state.audit_description = f"Soft delete team: {team_id}"
    request.state.audit_risk_level = "high"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    # Create service with user context and delegate to service
    team_service = TeamService(db_connection=db_connection, user_context=user_context)
    await team_service.delete_team(team_id)
    return success_response(
        request=request,
        message_key="teams.success.team_deleted",
        custom_code=CustomStatusCode.DELETED,
        status_code=http_status.HTTP_200_OK,  # Using HTTP_200_OK alias for consistency
    )


@handle_api_exceptions("update team")
@router.put(
    "/{team_id}",
    description="Update team information and members",
    summary="Update a team",
    response_model=None,
    status_code=http_status.HTTP_200_OK,
    responses={
        http_status.HTTP_200_OK: {"model": None, "description": "Team updated successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Invalid team ID format or invalid data"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_403_FORBIDDEN: {
            "description": "User does not have permission to update this team"
        },
        http_status.HTTP_404_NOT_FOUND: {"description": "Team not found"},
        http_status.HTTP_409_CONFLICT: {"description": "Team name already exists"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Rate limit exceeded"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
)
@limiter.limit("20/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",
        "pii",
        "soc2_audit",
        "audit_required",
    ],
    table_name="teams",
    category="TEAM",
)
async def update_team_endpoint(
    team_id: str,
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: UpdateTeamRequest = Body(...),
):
    """Update a team with validation and member changes.

    This endpoint updates team name, description, and/or members.
    All validations and update logic are delegated to TeamService.
    Requires teams_management.edit permission.

    Args:
        team_id: UUID of the team to update
        request: FastAPI request object
        db_connection: postgresql database connection
        current_user: Decoded JWT token containing user information
        body: Update request with optional fields (name, description, member_ids)

    Raises:
        HTTPException: 400 for invalid team ID format or invalid data
        HTTPException: 403 for insufficient permissions
        HTTPException: 404 if team not found
        HTTPException: 409 for duplicate team name
    """
    # Check permissions and get user context
    user_context = await check_permissions(
        current_user,
        TEAMS_MANAGEMENT_EDIT,
    )
    # Set audit metadata
    request.state.audit_table = "teams"
    request.state.audit_description = f"Update team: {team_id}"
    request.state.audit_risk_level = "high"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    # Create service with user context and delegate to service
    team_service = TeamService(db_connection=db_connection, user_context=user_context)
    await team_service.update_team(team_id, body)

    return success_response(
        request=request,
        message_key="teams.success.team_updated",
        custom_code=CustomStatusCode.UPDATED,
        status_code=http_status.HTTP_200_OK,
    )
