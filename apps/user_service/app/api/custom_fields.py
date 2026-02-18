"""Custom Fields Management API Module

This module provides CRUD operations for custom field management.
All endpoints include proper authentication, validation, and database operations.
"""

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.custom_fields import (
    CreateCustomFieldRequest,
)
from apps.user_service.app.schemas.enums import EntityType
from apps.user_service.app.services.custom_field_service import CustomFieldService
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    CUSTOM_FIELDS_MANAGEMENT_CREATE,
    CUSTOM_FIELDS_MANAGEMENT_VIEW,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/custom-fields", tags=["Custom Fields Management"])

logger = get_logger("custom-fields-api")


@handle_api_exceptions("create custom field")
@router.post(
    "",
    status_code=http_status.HTTP_201_CREATED,
    description="Create a new custom field definition",
    summary="Create a new custom field",
    responses={
        http_status.HTTP_201_CREATED: {"description": "Custom field created successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_409_CONFLICT: {"description": "Field key already exists"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
    },
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="confidential",
    compliance_tags=[
        "soc2_audit",
        "audit_required",
    ],
    table_name="custom_fields",
    category="CUSTOM_FIELD",
)
async def create_custom_field(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateCustomFieldRequest = Body(...),
):
    """Create a new custom field definition.

    Supports creating:
    - Top-level fields (with entity_type)
    - Object parent fields with sub-fields in bulk
    (with entity_type, field_type='object', sub_fields array)

    When creating an object type field with sub_fields, the parent field is created
    first, then all sub-fields are bulk created in a single transaction.

    Returns 201 Created on success.
    """
    # Set audit context
    request.state.audit_table = "custom_fields"
    audit_desc = f"Created custom field: {body.field_name}"
    if body.sub_fields:
        audit_desc += f" with {len(body.sub_fields)} sub-fields"
    request.state.audit_description = audit_desc
    request.state.audit_risk_level = "medium"

    # Check permissions and get user context
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CUSTOM_FIELDS_MANAGEMENT_CREATE,
    )

    # Create service and delegate to service
    custom_field_service = CustomFieldService(
        user_context=user_context,
        db_connection=db_connection,
    )
    await custom_field_service.create_custom_field(body)

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    return success_response(
        request=request,
        message_key="custom_fields.success.field_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("list custom fields")
@router.get(
    "",
    status_code=http_status.HTTP_200_OK,
    description="List all custom fields for an entity type",
    summary="List custom fields",
    responses={
        http_status.HTTP_200_OK: {"description": "Custom fields retrieved successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
    },
)
@limiter.limit("100/minute")
async def list_custom_fields(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    entity_type: EntityType = Query(..., description="Entity type"),
):
    """List all custom fields for an organization.

    Returns top-level fields with their sub-fields nested.
    """
    # Check permissions and get user context
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CUSTOM_FIELDS_MANAGEMENT_VIEW,
    )

    # Create service and delegate to service
    custom_field_service = CustomFieldService(
        user_context=user_context,
        db_connection=db_connection,
    )
    fields, total = await custom_field_service.get_custom_fields_list(entity_type)

    if not fields:
        return list_response(
            request=request,
            items=[],
            total=0,
            message_key="success.no_data",
            custom_code=CustomStatusCode.NO_CONTENT,
            status_code=http_status.HTTP_200_OK,
        )

    return list_response(
        request=request,
        items=[field.model_dump() for field in fields],
        total=total,
        message_key="custom_fields.success.fields_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("get custom field by id")
@router.get(
    "/{field_id}",
    status_code=http_status.HTTP_200_OK,
    description="Get a custom field by ID with sub-fields",
    summary="Get custom field by ID",
    responses={
        http_status.HTTP_200_OK: {"description": "Custom field retrieved successfully"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Custom field not found"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
    },
)
@limiter.limit("100/minute")
async def get_custom_field_by_id(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    field_id: str = Path(..., description="Custom field ID"),
):
    """Get a single custom field by ID with nested sub-fields."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CUSTOM_FIELDS_MANAGEMENT_VIEW,
    )
    custom_field_service = CustomFieldService(
        user_context=user_context,
        db_connection=db_connection,
    )
    field = await custom_field_service.get_custom_field_by_id(field_id)
    return success_response(
        request=request,
        message_key="success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=field.model_dump(),
    )
