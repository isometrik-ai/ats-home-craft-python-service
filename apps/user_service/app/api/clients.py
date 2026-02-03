"""Clients Management API Module
This module provides CRUD operations for client management.
All endpoints include proper authentication, validation, and database operations.
"""

import asyncpg
from fastapi import APIRouter, Body, Depends, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.db import db_uow
from apps.user_service.app.schemas.clients import CreateClientFromUserRequest
from apps.user_service.app.services.client_service import ClientService
from apps.user_service.app.utils.common_utils import handle_api_exceptions
from libs.shared_utils.logger import get_logger
from libs.shared_utils.response_factory import success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/clients", tags=["Clients Management"])

logger = get_logger("clients-api")


@handle_api_exceptions("create client from user")
@router.post(
    "/from-auth",
    status_code=http_status.HTTP_201_CREATED,
    description="Create a client from user ID",
    summary="Create a client from user ID",
    responses={
        http_status.HTTP_201_CREATED: {"description": "Client created successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_404_NOT_FOUND: {"description": "User or organization not found"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_409_CONFLICT: {"description": "User is already a client"},
    },
)
@limiter.limit("100/minute")
async def create_client_from_user(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    body: CreateClientFromUserRequest = Body(...),
):
    """Create a client and client_user from user ID.

    This endpoint creates:
    1. A client record with client_type='person' and mandatory fields
    2. A client_user record linking the user to the client
    3. Sends a creation email to the user

    Args:
        request: FastAPI request object
        db_connection: Database connection
        body: Request body containing user_id and organization_id

    Returns:
        Response with status code 201 and no body
    """
    # Create service and delegate all business logic to service
    client_service = ClientService(
        db_connection=db_connection,
    )
    await client_service.create_client_from_user(body)

    return success_response(
        request=request,
        message_key="clients.success.client_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
    )
