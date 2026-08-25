"""Admin daily help API (project-scoped)."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request, Response
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.daily_help import (
    AddDailyHelpDocumentRequest,
    AdminLinkDailyHelpUnitRequest,
    CreateDailyHelpApiResponse,
    CreateDailyHelpCategoryRequest,
    CreateDailyHelpRequest,
    DailyHelpAttendanceApiResponse,
    DailyHelpAvailabilityApiResponse,
    DailyHelpCategoryApiResponse,
    DailyHelpCategoryListApiResponse,
    DailyHelpDetailApiResponse,
    DailyHelpDocumentApiResponse,
    DailyHelpExportQuery,
    DailyHelpGatePasscodeApiResponse,
    DailyHelpHouseholdLinkApiResponse,
    DailyHelpHouseholdLinkListApiResponse,
    DailyHelpListApiResponse,
    DailyHelpListQuery,
    DailyHelpMessageApiResponse,
    DailyHelpSubmissionListQuery,
    DailyHelpSummaryApiResponse,
    RejectDailyHelpRequest,
    RemoveDailyHelpHouseholdLinkRequest,
    ReplaceDailyHelpAvailabilityRequest,
    UpdateDailyHelpCategoryRequest,
    UpdateDailyHelpRequest,
)
from apps.user_service.app.services.daily_help_service import DailyHelpService
from apps.user_service.app.utils.audit_context import set_audit_context
from apps.user_service.app.utils.common_utils import (
    ensure_daily_help_reviewer_access,
    ensure_security_project_member_access,
    ensure_staff_project_access,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    PROJECTS_MANAGEMENT_EDIT,
    PROJECTS_MANAGEMENT_VIEW,
    VISITOR_MANAGEMENT_VERIFY,
)
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/projects", tags=["Daily Help"])

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
        status_code: {"model": model, "description": description},
    }


def _created_response(model: type, description: str) -> dict[int | str, dict]:
    """Build OpenAPI responses for HTTP 201 success."""
    return _ok_response(model, description, status_code=http_status.HTTP_201_CREATED)


SUMMARY_SUCCESS_RESPONSES = _ok_response(
    DailyHelpSummaryApiResponse,
    "Dashboard summary card counts for the project daily help registry.",
)
LIST_SUCCESS_RESPONSES = _ok_response(
    DailyHelpListApiResponse,
    "Paginated daily help profiles for admin review.",
)
EXPORT_SUCCESS_RESPONSES: dict[int | str, dict] = {
    **COMMON_ERROR_RESPONSES,
    http_status.HTTP_200_OK: {
        "content": {"text/csv": {}},
        "description": "CSV export of filtered daily help profiles.",
    },
}
CATEGORY_LIST_SUCCESS_RESPONSES = _ok_response(
    DailyHelpCategoryListApiResponse,
    "Project-scoped daily help categories.",
)
CATEGORY_CREATED_RESPONSES = _created_response(
    DailyHelpCategoryApiResponse,
    "Newly created daily help category.",
)
CATEGORY_UPDATED_RESPONSES = _ok_response(
    DailyHelpCategoryApiResponse,
    "Updated daily help category.",
)
PROFILE_CREATED_RESPONSES = _created_response(
    CreateDailyHelpApiResponse,
    "Newly created daily help profile with linked gate pass.",
)
DETAIL_SUCCESS_RESPONSES = _ok_response(
    DailyHelpDetailApiResponse,
    "Full daily help profile detail with documents, events, and links.",
)
MESSAGE_SUCCESS_RESPONSES = _ok_response(
    DailyHelpMessageApiResponse,
    "Profile status mutation result.",
)
DOCUMENT_CREATED_RESPONSES = _created_response(
    DailyHelpDocumentApiResponse,
    "Document added to the daily help profile.",
)
DOCUMENT_DELETED_RESPONSES = _ok_response(
    DailyHelpDocumentApiResponse,
    "Document removed from the daily help profile.",
)
AVAILABILITY_SUCCESS_RESPONSES = _ok_response(
    DailyHelpAvailabilityApiResponse,
    "Replaced availability slots on the profile.",
)
ATTENDANCE_SUCCESS_RESPONSES = _ok_response(
    DailyHelpAttendanceApiResponse,
    "Monthly attendance calendar with gate check-ins, resident absences, and events.",
)
HOUSEHOLD_LINK_LIST_SUCCESS_RESPONSES = _ok_response(
    DailyHelpHouseholdLinkListApiResponse,
    "Active household unit links for the daily help profile.",
)
HOUSEHOLD_LINK_CREATED_RESPONSES = _created_response(
    DailyHelpHouseholdLinkApiResponse,
    "Daily help profile linked to the unit.",
)
HOUSEHOLD_LINK_REMOVED_RESPONSES = _ok_response(
    DailyHelpHouseholdLinkApiResponse,
    "Daily help profile unlinked from the unit.",
)
GATE_PASSCODE_REGENERATED_RESPONSES = _ok_response(
    DailyHelpGatePasscodeApiResponse,
    "New gate pass verification code for the daily help profile.",
)


@handle_api_exceptions("get project daily help summary")
@router.get(
    "/{project_id}/daily-help/summary",
    status_code=http_status.HTTP_200_OK,
    summary="Daily help dashboard summary for a project",
    response_model=None,
    responses=SUMMARY_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_project_daily_help_summary(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return summary card counts for the admin daily help dashboard."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.get_summary(project_id=project_id)
    return success_response(
        request=request,
        message_key="daily_help.success.summary_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("list project daily help profiles")
@router.get(
    "/{project_id}/daily-help",
    status_code=http_status.HTTP_200_OK,
    summary="List daily help profiles for a project",
    response_model=None,
    responses=LIST_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def list_project_daily_help_profiles(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    query: DailyHelpListQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return paginated daily help profiles for admin review."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    items, total = await service.list_profiles(project_id=project_id, query=query)
    return list_response(
        request=request,
        items=[item.model_dump() for item in items],
        total=total,
        page=query.page,
        page_size=query.page_size,
        message_key="daily_help.success.list_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("export project daily help profiles")
@router.get(
    "/{project_id}/daily-help/export",
    status_code=http_status.HTTP_200_OK,
    summary="Export daily help profiles as CSV",
    response_model=None,
    responses=EXPORT_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
async def export_project_daily_help_profiles(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    query: DailyHelpExportQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Export filtered daily help profiles as CSV."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    csv_text = await service.export_csv(project_id=project_id, query=query)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="daily-help-{project_id}.csv"'},
    )


@handle_api_exceptions("list project daily help categories")
@router.get(
    "/{project_id}/daily-help/categories",
    status_code=http_status.HTTP_200_OK,
    summary="List daily help categories for a project",
    response_model=None,
    responses=CATEGORY_LIST_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def list_project_daily_help_categories(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return project-scoped daily help categories."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    items = await service.list_categories(project_id=project_id)
    return success_response(
        request=request,
        message_key="daily_help.success.categories_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=[item.model_dump() for item in items],
    )


@handle_api_exceptions("create project daily help category")
@router.post(
    "/{project_id}/daily-help/categories",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a daily help category",
    response_model=None,
    responses=CATEGORY_CREATED_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="general",
    compliance_tags=["audit_required"],
    table_name="daily_help_categories",
    category="DAILY_HELP",
)
async def create_project_daily_help_category(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    body: CreateDailyHelpCategoryRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Create a project-scoped daily help category."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.create_category(project_id=project_id, body=body)
    set_audit_context(
        request,
        user_context,
        table="daily_help_categories",
        requested_id=data.id,
        description=f"Created daily help category: {data.id}",
        risk_level="low",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="daily_help.success.category_created",
        custom_code=CustomStatusCode.CREATED,
        data=data.model_dump(),
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("update project daily help category")
@router.patch(
    "/{project_id}/daily-help/categories/{category_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Update a daily help category",
    response_model=None,
    responses=CATEGORY_UPDATED_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="general",
    compliance_tags=["audit_required"],
    table_name="daily_help_categories",
    category="DAILY_HELP",
)
async def update_project_daily_help_category(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    category_id: str = Path(...),
    body: UpdateDailyHelpCategoryRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Patch a project daily help category."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.update_category(
        project_id=project_id,
        category_id=category_id,
        body=body,
    )
    set_audit_context(
        request,
        user_context,
        table="daily_help_categories",
        requested_id=category_id,
        description=f"Updated daily help category: {category_id}",
        risk_level="low",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="daily_help.success.category_updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("create project daily help profile")
@router.post(
    "/{project_id}/daily-help",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a daily help profile",
    response_model=None,
    responses=PROFILE_CREATED_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="daily_help_profiles",
    category="DAILY_HELP",
)
async def create_project_daily_help_profile(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    body: CreateDailyHelpRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Create a daily help profile with documents and linked gate pass."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.create_profile(project_id=project_id, body=body)
    set_audit_context(
        request,
        user_context,
        table="daily_help_profiles",
        requested_id=data.id,
        description=f"Created daily help profile: {data.id}",
        risk_level="medium",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="daily_help.success.created",
        custom_code=CustomStatusCode.CREATED,
        data=data.model_dump(),
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("submit project daily help profile")
@router.post(
    "/{project_id}/daily-help/submissions",
    status_code=http_status.HTTP_201_CREATED,
    summary="Security submits a daily help profile for admin review",
    response_model=None,
    responses=PROFILE_CREATED_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="daily_help_profiles",
    category="DAILY_HELP",
)
async def submit_project_daily_help_profile(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    body: CreateDailyHelpRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Security registers a helper pending admin approval (no gate pass issued)."""
    user_context = await ensure_security_project_member_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=VISITOR_MANAGEMENT_VERIFY,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.submit_profile(project_id=project_id, body=body)
    set_audit_context(
        request,
        user_context,
        table="daily_help_profiles",
        requested_id=data.id,
        description=f"Submitted daily help profile for review: {data.id}",
        risk_level="medium",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="daily_help.success.submitted",
        custom_code=CustomStatusCode.CREATED,
        data=data.model_dump(),
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("list security daily help submissions")
@router.get(
    "/{project_id}/daily-help/submissions",
    status_code=http_status.HTTP_200_OK,
    summary="List daily help profiles submitted by the current security user",
    response_model=None,
    responses=LIST_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def list_security_daily_help_submissions(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    query: DailyHelpSubmissionListQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return paginated submissions created by the caller (pending, rejected, or approved)."""
    user_context = await ensure_security_project_member_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=VISITOR_MANAGEMENT_VERIFY,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    items, total = await service.list_my_submissions(project_id=project_id, query=query)
    return list_response(
        request=request,
        items=[item.model_dump() for item in items],
        total=total,
        page=query.page,
        page_size=query.page_size,
        message_key="daily_help.success.submissions_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("get project daily help profile")
@router.get(
    "/{project_id}/daily-help/{profile_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get daily help profile detail",
    response_model=None,
    responses=DETAIL_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_project_daily_help_profile(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    profile_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return one daily help profile with documents, events, and links."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.get_detail(project_id=project_id, profile_id=profile_id)
    return success_response(
        request=request,
        message_key="daily_help.success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("list project daily help household links")
@router.get(
    "/{project_id}/daily-help/{profile_id}/household-links",
    status_code=http_status.HTTP_200_OK,
    summary="List household unit links for a daily help profile",
    response_model=None,
    responses=HOUSEHOLD_LINK_LIST_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def list_project_daily_help_household_links(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    profile_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return active flats linked to a daily help profile via resident households."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    items = await service.list_household_links(project_id=project_id, profile_id=profile_id)
    return success_response(
        request=request,
        message_key="daily_help.success.household_links_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=[item.model_dump() for item in items],
    )


@handle_api_exceptions("link project daily help profile to unit")
@router.post(
    "/{project_id}/daily-help/{profile_id}/household-links",
    status_code=http_status.HTTP_201_CREATED,
    summary="Link daily help profile to a unit",
    response_model=None,
    responses=HOUSEHOLD_LINK_CREATED_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="general",
    compliance_tags=["audit_required"],
    table_name="daily_help_household_links",
    category="DAILY_HELP",
)
async def link_project_daily_help_to_unit(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    profile_id: str = Path(...),
    body: AdminLinkDailyHelpUnitRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Admin links an active daily help profile to a project unit."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.add_admin_household_link(
        project_id=project_id,
        profile_id=profile_id,
        unit_id=body.unit_id,
    )
    set_audit_context(
        request,
        user_context,
        table="daily_help_household_links",
        requested_id=data.id,
        description=f"Linked daily help profile {profile_id} to unit {body.unit_id}",
        risk_level="low",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="daily_help.success.admin_household_linked",
        custom_code=CustomStatusCode.CREATED,
        data=data.model_dump(),
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("unlink project daily help profile from unit")
@router.delete(
    "/{project_id}/daily-help/{profile_id}/household-links/{link_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Unlink daily help profile from a unit",
    response_model=None,
    responses=HOUSEHOLD_LINK_REMOVED_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="general",
    compliance_tags=["audit_required"],
    table_name="daily_help_household_links",
    category="DAILY_HELP",
)
async def unlink_project_daily_help_from_unit(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    profile_id: str = Path(...),
    link_id: str = Path(...),
    body: RemoveDailyHelpHouseholdLinkRequest | None = Body(None),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Admin removes a daily help profile link from a unit."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.remove_admin_household_link(
        project_id=project_id,
        profile_id=profile_id,
        link_id=link_id,
        reason=body.reason if body else None,
    )
    set_audit_context(
        request,
        user_context,
        table="daily_help_household_links",
        requested_id=link_id,
        description=f"Unlinked daily help profile {profile_id} from unit {data.unit_id}",
        risk_level="low",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="daily_help.success.admin_household_unlinked",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("regenerate daily help gate passcode")
@router.post(
    "/{project_id}/daily-help/{profile_id}/regenerate-passcode",
    status_code=http_status.HTTP_200_OK,
    summary="Regenerate gate pass verification code",
    response_model=None,
    responses=GATE_PASSCODE_REGENERATED_RESPONSES,
)
@limiter.limit("20/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="general",
    compliance_tags=["audit_required"],
    table_name="daily_help_profiles",
    category="DAILY_HELP",
)
async def regenerate_daily_help_gate_passcode(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    profile_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Admin issues a new gate verification code and syncs the linked pass."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.regenerate_gate_passcode(
        project_id=project_id,
        profile_id=profile_id,
    )
    set_audit_context(
        request,
        user_context,
        table="daily_help_profiles",
        requested_id=profile_id,
        description=f"Regenerated gate passcode for daily help profile {profile_id}",
        risk_level="medium",
        new_data={"linked_pass_id": data.linked_pass_id},
    )
    return success_response(
        request=request,
        message_key="daily_help.success.gate_passcode_regenerated",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("update project daily help profile")
@router.patch(
    "/{project_id}/daily-help/{profile_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Update daily help profile details",
    response_model=None,
    responses=DETAIL_SUCCESS_RESPONSES,
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="daily_help_profiles",
    category="DAILY_HELP",
)
async def update_project_daily_help_profile(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    profile_id: str = Path(...),
    body: UpdateDailyHelpRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Patch identity and contact fields on a daily help profile."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.update_profile(
        project_id=project_id,
        profile_id=profile_id,
        body=body,
    )
    set_audit_context(
        request,
        user_context,
        table="daily_help_profiles",
        requested_id=profile_id,
        description=f"Updated daily help profile: {profile_id}",
        risk_level="medium",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="daily_help.success.updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("get security daily help submission")
@router.get(
    "/{project_id}/daily-help/{profile_id}/submission",
    status_code=http_status.HTTP_200_OK,
    summary="Get a daily help profile submitted by the current security user",
    response_model=None,
    responses=DETAIL_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_security_daily_help_submission(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    profile_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return one submission with documents, review fields, and audit events."""
    user_context = await ensure_security_project_member_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=VISITOR_MANAGEMENT_VERIFY,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.get_my_submission(project_id=project_id, profile_id=profile_id)
    return success_response(
        request=request,
        message_key="daily_help.success.submission_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("resubmit project daily help profile")
@router.patch(
    "/{project_id}/daily-help/{profile_id}/submission",
    status_code=http_status.HTTP_200_OK,
    summary="Security resubmits a rejected daily help profile",
    response_model=None,
    responses=DETAIL_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="daily_help_profiles",
    category="DAILY_HELP",
)
async def resubmit_project_daily_help_profile(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    profile_id: str = Path(...),
    body: CreateDailyHelpRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Security edits and resubmits a rejected profile for admin review."""
    user_context = await ensure_security_project_member_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=VISITOR_MANAGEMENT_VERIFY,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.resubmit_profile(
        project_id=project_id,
        profile_id=profile_id,
        body=body,
    )
    set_audit_context(
        request,
        user_context,
        table="daily_help_profiles",
        requested_id=profile_id,
        description=f"Resubmitted daily help profile: {profile_id}",
        risk_level="medium",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="daily_help.success.resubmitted",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("approve project daily help profile")
@router.post(
    "/{project_id}/daily-help/{profile_id}/approve",
    status_code=http_status.HTTP_200_OK,
    summary="Approve a security-submitted daily help profile",
    response_model=None,
    responses=DETAIL_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="daily_help_profiles",
    category="DAILY_HELP",
)
async def approve_project_daily_help_profile(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    profile_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Approve a pending profile and issue its gate pass."""
    user_context = await ensure_daily_help_reviewer_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.approve_profile(project_id=project_id, profile_id=profile_id)
    set_audit_context(
        request,
        user_context,
        table="daily_help_profiles",
        requested_id=profile_id,
        description=f"Approved daily help profile: {profile_id}",
        risk_level="medium",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="daily_help.success.approved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("reject project daily help profile")
@router.post(
    "/{project_id}/daily-help/{profile_id}/reject",
    status_code=http_status.HTTP_200_OK,
    summary="Reject a security-submitted daily help profile",
    response_model=None,
    responses=DETAIL_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="daily_help_profiles",
    category="DAILY_HELP",
)
async def reject_project_daily_help_profile(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    profile_id: str = Path(...),
    body: RejectDailyHelpRequest = Body(default_factory=RejectDailyHelpRequest),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Reject a pending security submission."""
    user_context = await ensure_daily_help_reviewer_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.reject_profile(
        project_id=project_id,
        profile_id=profile_id,
        body=body,
    )
    set_audit_context(
        request,
        user_context,
        table="daily_help_profiles",
        requested_id=profile_id,
        description=f"Rejected daily help profile: {profile_id}",
        risk_level="medium",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="daily_help.success.rejected",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("deactivate project daily help profile")
@router.post(
    "/{project_id}/daily-help/{profile_id}/deactivate",
    status_code=http_status.HTTP_200_OK,
    summary="Deactivate a daily help profile",
    response_model=None,
    responses=MESSAGE_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="daily_help_profiles",
    category="DAILY_HELP",
)
async def deactivate_project_daily_help_profile(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    profile_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Mark a daily help profile inactive and cancel its gate pass."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.deactivate_profile(project_id=project_id, profile_id=profile_id)
    set_audit_context(
        request,
        user_context,
        table="daily_help_profiles",
        requested_id=profile_id,
        description=f"Deactivated daily help profile: {profile_id}",
        risk_level="medium",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="daily_help.success.deactivated",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("reactivate project daily help profile")
@router.post(
    "/{project_id}/daily-help/{profile_id}/reactivate",
    status_code=http_status.HTTP_200_OK,
    summary="Reactivate an inactive daily help profile",
    response_model=None,
    responses=MESSAGE_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="daily_help_profiles",
    category="DAILY_HELP",
)
async def reactivate_project_daily_help_profile(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    profile_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Mark an inactive daily help profile active and re-issue its gate pass when needed."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.reactivate_profile(project_id=project_id, profile_id=profile_id)
    set_audit_context(
        request,
        user_context,
        table="daily_help_profiles",
        requested_id=profile_id,
        description=f"Reactivated daily help profile: {profile_id}",
        risk_level="medium",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="daily_help.success.reactivated",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("delete project daily help profile")
@router.post(
    "/{project_id}/daily-help/{profile_id}/delete",
    status_code=http_status.HTTP_200_OK,
    summary="Soft-delete a daily help profile",
    response_model=None,
    responses=MESSAGE_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="daily_help_profiles",
    category="DAILY_HELP",
)
async def delete_project_daily_help_profile(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    profile_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Soft-delete a daily help profile and cancel its gate pass."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.delete_profile(project_id=project_id, profile_id=profile_id)
    set_audit_context(
        request,
        user_context,
        table="daily_help_profiles",
        requested_id=profile_id,
        description=f"Deleted daily help profile: {profile_id}",
        risk_level="high",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="daily_help.success.deleted",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("restore project daily help profile")
@router.post(
    "/{project_id}/daily-help/{profile_id}/restore",
    status_code=http_status.HTTP_200_OK,
    summary="Restore a deleted daily help profile",
    response_model=None,
    responses=MESSAGE_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="daily_help_profiles",
    category="DAILY_HELP",
)
async def restore_project_daily_help_profile(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    profile_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Restore a soft-deleted daily help profile and re-issue pass when needed."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.restore_profile(project_id=project_id, profile_id=profile_id)
    set_audit_context(
        request,
        user_context,
        table="daily_help_profiles",
        requested_id=profile_id,
        description=f"Restored daily help profile: {profile_id}",
        risk_level="medium",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="daily_help.success.restored",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("add daily help document")
@router.post(
    "/{project_id}/daily-help/{profile_id}/documents",
    status_code=http_status.HTTP_201_CREATED,
    summary="Add a document to a daily help profile",
    response_model=None,
    responses=DOCUMENT_CREATED_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="daily_help_documents",
    category="DAILY_HELP",
)
async def add_daily_help_document(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    profile_id: str = Path(...),
    body: AddDailyHelpDocumentRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Upload one document slot on an existing daily help profile."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.add_document(
        project_id=project_id,
        profile_id=profile_id,
        body=body,
    )
    set_audit_context(
        request,
        user_context,
        table="daily_help_documents",
        requested_id=data.id,
        description=f"Added daily help document: {data.id}",
        risk_level="medium",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="daily_help.success.document_added",
        custom_code=CustomStatusCode.CREATED,
        data=data.model_dump(),
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("delete daily help document")
@router.delete(
    "/{project_id}/daily-help/{profile_id}/documents/{document_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Remove a document from a daily help profile",
    response_model=None,
    responses=DOCUMENT_DELETED_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="daily_help_documents",
    category="DAILY_HELP",
)
async def delete_daily_help_document(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    profile_id: str = Path(...),
    document_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete one document from a daily help profile."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.delete_document(
        project_id=project_id,
        profile_id=profile_id,
        document_id=document_id,
    )
    set_audit_context(
        request,
        user_context,
        table="daily_help_documents",
        requested_id=document_id,
        description=f"Deleted daily help document: {document_id}",
        risk_level="medium",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="daily_help.success.document_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("replace daily help availability slots")
@router.put(
    "/{project_id}/daily-help/{profile_id}/availability",
    status_code=http_status.HTTP_200_OK,
    summary="Replace availability slots on a daily help profile",
    response_model=None,
    responses=AVAILABILITY_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="general",
    compliance_tags=["audit_required"],
    table_name="daily_help_availability_slots",
    category="DAILY_HELP",
)
async def replace_daily_help_availability(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    profile_id: str = Path(...),
    body: ReplaceDailyHelpAvailabilityRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Replace all free-time windows on a daily help profile."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    items = await service.replace_availability_slots(
        project_id=project_id,
        profile_id=profile_id,
        body=body,
    )
    set_audit_context(
        request,
        user_context,
        table="daily_help_availability_slots",
        requested_id=profile_id,
        description=f"Replaced availability slots for daily help: {profile_id}",
        risk_level="low",
        new_data={"slot_count": len(items)},
    )
    return success_response(
        request=request,
        message_key="daily_help.success.availability_updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=[item.model_dump() for item in items],
    )


@handle_api_exceptions("get daily help attendance")
@router.get(
    "/{project_id}/daily-help/{profile_id}/attendance",
    status_code=http_status.HTTP_200_OK,
    summary="Get check-in attendance for a daily help profile",
    response_model=None,
    responses=ATTENDANCE_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_daily_help_attendance(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    profile_id: str = Path(...),
    unit_id: str | None = Query(
        None,
        description=(
            "Optional household unit filter. When omitted, absences from all linked units are merged."
        ),
    ),
    year: int | None = Query(
        None,
        ge=2000,
        le=2100,
        description="Calendar year (defaults to current year in Asia/Kolkata).",
    ),
    month: int | None = Query(
        None,
        ge=1,
        le=12,
        description="Calendar month 1-12 (defaults to current month in Asia/Kolkata).",
    ),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return monthly attendance calendar including resident-reported absences."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.get_attendance(
        project_id=project_id,
        profile_id=profile_id,
        unit_id=unit_id,
        year=year,
        month=month,
    )
    return success_response(
        request=request,
        message_key="daily_help.success.attendance_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )
