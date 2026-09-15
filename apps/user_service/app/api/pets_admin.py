"""Admin project-scoped pets API (ADR 0016 Phase 2)."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.pets import (
    AdminCreatePetRequest,
    AdminPetApiResponse,
    AdminPetListApiResponse,
    AdminPetListQuery,
    PetCatalogApiResponse,
    PetSummaryApiResponse,
    RemovePetRequest,
    UpdatePetRequest,
)
from apps.user_service.app.services.pets_service import PetsService
from apps.user_service.app.utils.audit_context import set_audit_context
from apps.user_service.app.utils.common_utils import (
    ensure_staff_project_access,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    RESIDENT_MANAGEMENT_EDIT,
    RESIDENT_MANAGEMENT_VIEW,
)
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/projects", tags=["Pets (Admin)"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden (insufficient permissions)."},
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


SUMMARY_SUCCESS_RESPONSES = _ok_response(
    PetSummaryApiResponse,
    "Active and total pet counts for the project registry header.",
)
LIST_SUCCESS_RESPONSES = _ok_response(
    AdminPetListApiResponse,
    "Paginated pets for the project registry.",
)
DETAIL_SUCCESS_RESPONSES = _ok_response(
    AdminPetApiResponse,
    "Pet profile detail for admin edit.",
)
CREATED_SUCCESS_RESPONSES = _ok_response(
    AdminPetApiResponse,
    "Pet profile created.",
    status_code=http_status.HTTP_201_CREATED,
)
UPDATED_SUCCESS_RESPONSES = _ok_response(
    AdminPetApiResponse,
    "Pet profile updated.",
)
REMOVED_SUCCESS_RESPONSES = _ok_response(
    AdminPetApiResponse,
    "Pet profile removed.",
)
CATALOG_SUCCESS_RESPONSES = _ok_response(
    PetCatalogApiResponse,
    "Pet type and breed catalog for admin forms.",
)


@handle_api_exceptions("get project pets summary")
@router.get(
    "/{project_id}/pets/summary",
    status_code=http_status.HTTP_200_OK,
    summary="Pet registry summary for a project",
    response_model=None,
    responses=SUMMARY_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_project_pets_summary(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return active and total pet counts for the admin registry header."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=RESIDENT_MANAGEMENT_VIEW,
        request=request,
    )
    service = PetsService(db_connection=db_connection, user_context=user_context)
    data = await service.get_project_summary(project_id=project_id)
    return success_response(
        request=request,
        message_key="pets.success.summary_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("list project pets")
@router.get(
    "/{project_id}/pets",
    status_code=http_status.HTTP_200_OK,
    summary="List pets for a project",
    response_model=None,
    responses=LIST_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def list_project_pets(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    query: AdminPetListQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return paginated pets for the project registry with filters."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=RESIDENT_MANAGEMENT_VIEW,
        request=request,
    )
    service = PetsService(db_connection=db_connection, user_context=user_context)
    items, total = await service.list_pets_for_project(project_id=project_id, query=query)
    return list_response(
        request=request,
        items=items,
        total=total,
        page=query.page,
        page_size=query.page_size,
        message_key="pets.success.list_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("get project pet catalog")
@router.get(
    "/{project_id}/pets/catalog",
    status_code=http_status.HTTP_200_OK,
    summary="Get pet type and breed catalog",
    response_model=None,
    responses=CATALOG_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_project_pet_catalog(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
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
    """Return static pet type and breed options for admin forms."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=RESIDENT_MANAGEMENT_VIEW,
        request=request,
    )
    service = PetsService(db_connection=db_connection, user_context=user_context)
    data = await service.get_catalog(pet_type_id=pet_type_id, search=search)
    return success_response(
        request=request,
        message_key="pets.success.catalog_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("get project pet detail")
@router.get(
    "/{project_id}/pets/{pet_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get pet detail for a project",
    response_model=None,
    responses=DETAIL_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_project_pet_detail(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    pet_id: str = Path(..., description="Pet identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return one pet profile in the project, including removed rows."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=RESIDENT_MANAGEMENT_VIEW,
        request=request,
    )
    service = PetsService(db_connection=db_connection, user_context=user_context)
    data = await service.get_pet_detail_admin(project_id=project_id, pet_id=pet_id)
    return success_response(
        request=request,
        message_key="pets.success.detail_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("create project pet")
@router.post(
    "/{project_id}/pets",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a pet profile for a unit",
    response_model=None,
    responses=CREATED_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="pets",
    category="PETS",
)
async def create_project_pet(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: AdminCreatePetRequest = Body(...),
):
    """Create a pet profile linked to a unit in the project."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=RESIDENT_MANAGEMENT_EDIT,
        request=request,
    )
    service = PetsService(db_connection=db_connection, user_context=user_context)
    data = await service.create_pet_admin(project_id=project_id, body=body)
    set_audit_context(
        request,
        user_context,
        table="pets",
        requested_id=str(data.get("id")),
        description=f"Admin created pet for unit: {body.unit_id}",
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


@handle_api_exceptions("update project pet")
@router.patch(
    "/{project_id}/pets/{pet_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Update a pet profile",
    response_model=None,
    responses=UPDATED_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="pets",
    category="PETS",
)
async def update_project_pet(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    pet_id: str = Path(..., description="Pet identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: UpdatePetRequest = Body(...),
):
    """Patch an active pet profile in the project."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=RESIDENT_MANAGEMENT_EDIT,
        request=request,
    )
    service = PetsService(db_connection=db_connection, user_context=user_context)
    data = await service.update_pet_admin(project_id=project_id, pet_id=pet_id, body=body)
    set_audit_context(
        request,
        user_context,
        table="pets",
        requested_id=pet_id,
        description=f"Admin updated pet: {pet_id}",
        risk_level="low",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="pets.success.updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("remove project pet")
@router.post(
    "/{project_id}/pets/{pet_id}/remove",
    status_code=http_status.HTTP_200_OK,
    summary="Remove a pet profile",
    response_model=None,
    responses=REMOVED_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="pets",
    category="PETS",
)
async def remove_project_pet(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    pet_id: str = Path(..., description="Pet identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: RemovePetRequest = Body(...),
):
    """Soft-remove a pet profile with reason."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=RESIDENT_MANAGEMENT_EDIT,
        request=request,
    )
    service = PetsService(db_connection=db_connection, user_context=user_context)
    data = await service.remove_pet_admin(project_id=project_id, pet_id=pet_id, body=body)
    set_audit_context(
        request,
        user_context,
        table="pets",
        requested_id=pet_id,
        description=f"Admin removed pet: {pet_id}",
        risk_level="low",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="pets.success.removed",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )
