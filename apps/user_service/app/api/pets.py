"""Resident household pets API (ADR 0016)."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.pets import (
    CreatePetRequest,
    PetApiResponse,
    PetCatalogApiResponse,
    PetDetailApiResponse,
    PetListApiResponse,
    RemovePetRequest,
    UpdatePetRequest,
)
from apps.user_service.app.services.pets_service import PetsService
from apps.user_service.app.utils.audit_context import set_audit_context
from apps.user_service.app.utils.common_utils import (
    extract_onboarding_contact_context,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/pets", tags=["Pets (Resident)"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden."},
    404: {"description": "Not found."},
    409: {"description": "Conflict."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}


def _ok_response(
    model: type,
    description: str,
    *,
    status_code: int = http_status.HTTP_200_OK,
) -> dict[int | str, dict]:
    """Build OpenAPI responses for a successful JSON envelope."""
    return {
        **COMMON_ERROR_RESPONSES,
        status_code: {
            "description": description,
            "content": {"application/json": {"schema": model.model_json_schema()}},
        },
    }


@handle_api_exceptions("get pet catalog")
@router.get(
    "/catalog",
    status_code=http_status.HTTP_200_OK,
    summary="Get pet type and breed catalog",
    response_model=None,
    responses=_ok_response(PetCatalogApiResponse, "Pet catalog retrieved."),
)
@limiter.limit("100/minute")
async def get_pet_catalog(
    request: Request,
    pet_type_id: str | None = Query(
        default=None,
        description="Filter to one pet type id from the catalog (e.g. dog).",
    ),
    search: str | None = Query(
        default=None,
        description="Case-insensitive search on type or breed names.",
    ),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return static pet type and breed options."""
    user_context, _ = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = PetsService(db_connection=db_connection, user_context=user_context)
    data = await service.get_catalog(pet_type_id=pet_type_id, search=search)
    return success_response(
        request=request,
        message_key="pets.success.catalog_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("list pets")
@router.get(
    "",
    status_code=http_status.HTTP_200_OK,
    summary="List pets on a unit",
    response_model=None,
    responses=_ok_response(PetListApiResponse, "Pet list retrieved."),
)
@limiter.limit("100/minute")
async def list_pets(
    request: Request,
    unit_id: str = Query(..., description="Unit identifier (UUID string)."),
    page: int = Query(default=1, ge=1, description="Page number (1-based)."),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List active pets for a unit the caller occupies."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = PetsService(db_connection=db_connection, user_context=user_context)
    items, total = await service.list_pets(
        contact_id=str(contact["id"]),
        unit_id=unit_id,
        page=page,
        page_size=page_size,
    )
    return list_response(
        request=request,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        message_key="pets.success.list_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("get pet detail")
@router.get(
    "/{pet_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get pet detail",
    response_model=None,
    responses=_ok_response(PetDetailApiResponse, "Pet detail retrieved."),
)
@limiter.limit("100/minute")
async def get_pet_detail(
    request: Request,
    pet_id: str = Path(..., description="Pet identifier (UUID string)."),
    unit_id: str = Query(..., description="Unit identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return one pet profile with unit, creator, and household members."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = PetsService(db_connection=db_connection, user_context=user_context)
    data = await service.get_pet_detail(
        contact_id=str(contact["id"]),
        pet_id=pet_id,
        unit_id=unit_id,
    )
    return success_response(
        request=request,
        message_key="pets.success.detail_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("create pet")
@router.post(
    "",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a pet profile",
    response_model=None,
    responses=_ok_response(
        PetApiResponse,
        "Pet profile created.",
        status_code=http_status.HTTP_201_CREATED,
    ),
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="pets",
    category="PETS",
)
async def create_pet(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreatePetRequest = Body(...),
):
    """Create a household pet for a unit."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = PetsService(db_connection=db_connection, user_context=user_context)
    data = await service.create_pet(contact_id=str(contact["id"]), body=body)
    set_audit_context(
        request,
        user_context,
        table="pets",
        requested_id=str(data.get("id")),
        description=f"Created pet for unit: {body.unit_id}",
        risk_level="low",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="pets.success.created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data,
    )


@handle_api_exceptions("update pet")
@router.patch(
    "/{pet_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Update a pet profile",
    response_model=None,
    responses=_ok_response(PetApiResponse, "Pet profile updated."),
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="pets",
    category="PETS",
)
async def update_pet(
    request: Request,
    pet_id: str = Path(..., description="Pet identifier (UUID string)."),
    unit_id: str = Query(..., description="Unit identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: UpdatePetRequest = Body(...),
):
    """Patch an active pet profile."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = PetsService(db_connection=db_connection, user_context=user_context)
    data = await service.update_pet(
        contact_id=str(contact["id"]),
        pet_id=pet_id,
        unit_id=unit_id,
        body=body,
    )
    set_audit_context(
        request,
        user_context,
        table="pets",
        requested_id=pet_id,
        description=f"Updated pet: {pet_id}",
        risk_level="low",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="pets.success.updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("remove pet")
@router.post(
    "/{pet_id}/remove",
    status_code=http_status.HTTP_200_OK,
    summary="Remove a pet profile",
    response_model=None,
    responses=_ok_response(PetApiResponse, "Pet profile removed."),
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="pets",
    category="PETS",
)
async def remove_pet(
    request: Request,
    pet_id: str = Path(..., description="Pet identifier (UUID string)."),
    unit_id: str = Query(..., description="Unit identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: RemovePetRequest = Body(...),
):
    """Soft-remove a pet profile with reason."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = PetsService(db_connection=db_connection, user_context=user_context)
    data = await service.remove_pet(
        contact_id=str(contact["id"]),
        pet_id=pet_id,
        unit_id=unit_id,
        body=body,
    )
    set_audit_context(
        request,
        user_context,
        table="pets",
        requested_id=pet_id,
        description=f"Removed pet: {pet_id}",
        risk_level="low",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="pets.success.removed",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )
