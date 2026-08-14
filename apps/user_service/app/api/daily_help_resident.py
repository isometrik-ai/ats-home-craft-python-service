"""Resident daily help directory API."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.daily_help import (
    CreateDailyHelpRatingRequest,
    DailyHelpAttendanceApiResponse,
    DailyHelpHouseholdLinkApiResponse,
    DailyHelpOpenToWorkApiResponse,
    DailyHelpRatingApiResponse,
    DailyHelpRatingSummaryApiResponse,
    MarkDailyHelpAttendanceAbsenceApiResponse,
    MarkDailyHelpAttendanceAbsenceRequest,
    RemoveDailyHelpHouseholdLinkRequest,
    ResidentDailyHelpCategoryStatsApiResponse,
    ResidentDailyHelpDetailApiResponse,
    ResidentDailyHelpHouseholdLinkListApiResponse,
    ResidentDailyHelpListApiResponse,
    ResidentDailyHelpListQuery,
    ResidentDailyHelpSearchQuery,
    SetDailyHelpOpenToWorkRequest,
    UpdateDailyHelpRatingRequest,
)
from apps.user_service.app.services.daily_help_service import DailyHelpService
from apps.user_service.app.utils.common_utils import (
    extract_onboarding_contact_context,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/daily-help", tags=["Daily Help (Resident)"])

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
        status_code: {"model": model, "description": description},
    }


def _created_response(model: type, description: str) -> dict[int | str, dict]:
    """Build OpenAPI responses for HTTP 201 success."""
    return _ok_response(model, description, status_code=http_status.HTTP_201_CREATED)


CATEGORIES_SUCCESS_RESPONSES = _ok_response(
    ResidentDailyHelpCategoryStatsApiResponse,
    "Active categories with stats and up to four profile previews each.",
)
LIST_SUCCESS_RESPONSES = _ok_response(
    ResidentDailyHelpListApiResponse,
    "Paginated active daily help profiles for the resident directory.",
)
SEARCH_SUCCESS_RESPONSES = _ok_response(
    ResidentDailyHelpListApiResponse,
    "Search results for active daily help profiles.",
)
HOUSEHOLD_LINKS_LIST_SUCCESS_RESPONSES = _ok_response(
    ResidentDailyHelpHouseholdLinkListApiResponse,
    "Household-linked daily help profiles grouped by category.",
)
DETAIL_SUCCESS_RESPONSES = _ok_response(
    ResidentDailyHelpDetailApiResponse,
    "Daily help profile detail; phone masked unless household-linked to the unit.",
)
HOUSEHOLD_LINK_CREATED_RESPONSES = _created_response(
    DailyHelpHouseholdLinkApiResponse,
    "Daily help profile linked to the resident unit.",
)
HOUSEHOLD_LINK_REMOVED_RESPONSES = _ok_response(
    DailyHelpHouseholdLinkApiResponse,
    "Household link removed from the resident unit.",
)
OPEN_TO_WORK_SUCCESS_RESPONSES = _ok_response(
    DailyHelpOpenToWorkApiResponse,
    "Open-to-work flag updated for the household-linked profile.",
)
RATING_CREATED_RESPONSES = _created_response(
    DailyHelpRatingSummaryApiResponse,
    "Rating recorded; returns updated aggregate summary.",
)
RATING_SUMMARY_SUCCESS_RESPONSES = _ok_response(
    DailyHelpRatingSummaryApiResponse,
    "Aggregated star average and trait counts for the profile.",
)
RATING_MINE_SUCCESS_RESPONSES = _ok_response(
    DailyHelpRatingApiResponse,
    "The resident's own rating for the profile, or null when not yet rated.",
)
RATING_UPDATED_RESPONSES = _ok_response(
    DailyHelpRatingApiResponse,
    "Updated rating for the daily help profile.",
)
ATTENDANCE_SUCCESS_RESPONSES = _ok_response(
    DailyHelpAttendanceApiResponse,
    "Monthly attendance calendar with present/absent days and gate check-in events.",
)
ATTENDANCE_ABSENCE_SUCCESS_RESPONSES = _ok_response(
    MarkDailyHelpAttendanceAbsenceApiResponse,
    "Calendar day marked absent for the household-linked helper.",
)


@handle_api_exceptions("list resident daily help categories")
@router.get(
    "/categories",
    status_code=http_status.HTTP_200_OK,
    summary="Daily help categories with stats for resident home",
    response_model=None,
    responses=CATEGORIES_SUCCESS_RESPONSES,
)
@limiter.limit("60/minute")
async def list_resident_daily_help_categories(
    request: Request,
    unit_id: str = Query(..., description="Resident unit identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return active categories with stats and up to four profile previews each."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    items = await service.list_resident_categories(
        contact_id=str(contact["id"]),
        unit_id=unit_id,
    )
    return success_response(
        request=request,
        message_key="daily_help.success.categories_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=[item.model_dump() for item in items],
    )


@handle_api_exceptions("list resident daily help profiles")
@router.get(
    "",
    status_code=http_status.HTTP_200_OK,
    summary="Browse active daily help profiles",
    response_model=None,
    responses=LIST_SUCCESS_RESPONSES,
)
@limiter.limit("60/minute")
async def list_resident_daily_help_profiles(
    request: Request,
    query: ResidentDailyHelpListQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return paginated active daily help profiles for resident directory."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    items, total = await service.list_resident_profiles(
        contact_id=str(contact["id"]),
        query=query,
    )
    return list_response(
        request=request,
        items=[item.model_dump() for item in items],
        total=total,
        page=query.page,
        page_size=query.page_size,
        message_key="daily_help.success.list_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
    )


@handle_api_exceptions("search resident daily help profiles")
@router.get(
    "/search",
    status_code=http_status.HTTP_200_OK,
    summary="Search daily help by name, mobile, or passcode",
    response_model=None,
    responses=SEARCH_SUCCESS_RESPONSES,
)
@limiter.limit("60/minute")
async def search_resident_daily_help_profiles(
    request: Request,
    query: ResidentDailyHelpSearchQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Search active daily help profiles for resident directory."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    items, total = await service.search_resident_profiles(
        contact_id=str(contact["id"]),
        query=query,
    )
    return list_response(
        request=request,
        items=[item.model_dump() for item in items],
        total=total,
        page=query.page,
        page_size=query.page_size,
        message_key="daily_help.success.search_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
    )


@handle_api_exceptions("list resident daily help household links")
@router.get(
    "/household-links",
    status_code=http_status.HTTP_200_OK,
    summary="List daily help profiles linked to resident unit",
    response_model=None,
    responses=HOUSEHOLD_LINKS_LIST_SUCCESS_RESPONSES,
)
@limiter.limit("60/minute")
async def list_resident_daily_help_household_links(
    request: Request,
    unit_id: str = Query(..., description="Resident unit identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return household-linked daily help profiles grouped by category."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    items = await service.list_resident_household_links(
        contact_id=str(contact["id"]),
        unit_id=unit_id,
    )
    return success_response(
        request=request,
        message_key="daily_help.success.resident_household_links_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        data=[item.model_dump() for item in items],
    )


@handle_api_exceptions("get resident daily help profile")
@router.get(
    "/{profile_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Daily help profile detail for resident",
    response_model=None,
    responses=DETAIL_SUCCESS_RESPONSES,
)
@limiter.limit("60/minute")
async def get_resident_daily_help_profile(
    request: Request,
    profile_id: str = Path(...),
    unit_id: str = Query(..., description="Resident unit identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return one active daily help profile; phone masked unless household-linked."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.get_resident_detail(
        contact_id=str(contact["id"]),
        unit_id=unit_id,
        profile_id=profile_id,
    )
    return success_response(
        request=request,
        message_key="daily_help.success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("set resident daily help open to work")
@router.patch(
    "/{profile_id}/open-to-work",
    status_code=http_status.HTTP_200_OK,
    summary="Toggle open-to-work for a household-linked daily help profile",
    response_model=None,
    responses=OPEN_TO_WORK_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
async def set_resident_daily_help_open_to_work(
    request: Request,
    profile_id: str = Path(...),
    unit_id: str = Query(..., description="Resident unit identifier (UUID string)."),
    body: SetDailyHelpOpenToWorkRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Set whether a household-linked helper is open to work in the resident directory."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.set_resident_open_to_work(
        contact_id=str(contact["id"]),
        unit_id=unit_id,
        profile_id=profile_id,
        body=body,
    )
    return success_response(
        request=request,
        message_key="daily_help.success.open_to_work_updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("add daily help household link")
@router.post(
    "/{profile_id}/household-links",
    status_code=http_status.HTTP_201_CREATED,
    summary="Link daily help profile to resident unit",
    response_model=None,
    responses=HOUSEHOLD_LINK_CREATED_RESPONSES,
)
@limiter.limit("30/minute")
async def add_daily_help_household_link(
    request: Request,
    profile_id: str = Path(...),
    unit_id: str = Query(..., description="Resident unit identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Add a daily help profile to the caller's household for the selected unit."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.add_household_link(
        contact_id=str(contact["id"]),
        unit_id=unit_id,
        profile_id=profile_id,
    )
    return success_response(
        request=request,
        message_key="daily_help.success.household_linked",
        custom_code=CustomStatusCode.CREATED,
        data=data.model_dump(),
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("remove daily help household link")
@router.delete(
    "/{profile_id}/household-links/{link_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Remove daily help profile from household",
    response_model=None,
    responses=HOUSEHOLD_LINK_REMOVED_RESPONSES,
)
@limiter.limit("30/minute")
async def remove_daily_help_household_link(
    request: Request,
    profile_id: str = Path(...),
    link_id: str = Path(...),
    unit_id: str = Query(..., description="Resident unit identifier (UUID string)."),
    body: RemoveDailyHelpHouseholdLinkRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Remove a household link to a daily help profile."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.remove_household_link(
        contact_id=str(contact["id"]),
        unit_id=unit_id,
        profile_id=profile_id,
        link_id=link_id,
        reason=body.reason,
    )
    return success_response(
        request=request,
        message_key="daily_help.success.household_removed",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("create daily help rating")
@router.post(
    "/{profile_id}/ratings",
    status_code=http_status.HTTP_201_CREATED,
    summary="Rate a daily help profile",
    response_model=None,
    responses=RATING_CREATED_RESPONSES,
)
@limiter.limit("30/minute")
async def create_daily_help_rating(
    request: Request,
    profile_id: str = Path(...),
    unit_id: str = Query(..., description="Resident unit identifier (UUID string)."),
    body: CreateDailyHelpRatingRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Submit a resident rating for a daily help profile."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.create_rating(
        contact_id=str(contact["id"]),
        unit_id=unit_id,
        profile_id=profile_id,
        body=body,
    )
    return success_response(
        request=request,
        message_key="daily_help.success.rating_created",
        custom_code=CustomStatusCode.CREATED,
        data=data.model_dump(),
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("get resident daily help rating")
@router.get(
    "/{profile_id}/ratings/mine",
    status_code=http_status.HTTP_200_OK,
    summary="Get the resident's own rating for a daily help profile",
    response_model=None,
    responses=RATING_MINE_SUCCESS_RESPONSES,
)
@limiter.limit("60/minute")
async def get_resident_daily_help_rating(
    request: Request,
    profile_id: str = Path(...),
    unit_id: str = Query(..., description="Resident unit identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return the logged-in resident's rating for this profile, if one exists."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.get_resident_rating(
        contact_id=str(contact["id"]),
        unit_id=unit_id,
        profile_id=profile_id,
    )
    return success_response(
        request=request,
        message_key=(
            "daily_help.success.rating_retrieved"
            if data
            else "daily_help.success.rating_not_submitted"
        ),
        custom_code=CustomStatusCode.SUCCESS if data else CustomStatusCode.NO_CONTENT,
        data=data.model_dump() if data else None,
    )


@handle_api_exceptions("update daily help rating")
@router.put(
    "/{profile_id}/ratings",
    status_code=http_status.HTTP_200_OK,
    summary="Update the resident's rating for a daily help profile",
    response_model=None,
    responses=RATING_UPDATED_RESPONSES,
)
@limiter.limit("30/minute")
async def update_daily_help_rating(
    request: Request,
    profile_id: str = Path(...),
    unit_id: str = Query(..., description="Resident unit identifier (UUID string)."),
    body: UpdateDailyHelpRatingRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Update stars, comment, and traits on an existing resident rating."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.update_rating(
        contact_id=str(contact["id"]),
        unit_id=unit_id,
        profile_id=profile_id,
        body=body,
    )
    return success_response(
        request=request,
        message_key="daily_help.success.rating_updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("get daily help rating summary")
@router.get(
    "/{profile_id}/ratings/summary",
    status_code=http_status.HTTP_200_OK,
    summary="Aggregated rating summary for a daily help profile",
    response_model=None,
    responses=RATING_SUMMARY_SUCCESS_RESPONSES,
)
@limiter.limit("60/minute")
async def get_daily_help_rating_summary(
    request: Request,
    profile_id: str = Path(...),
    unit_id: str = Query(..., description="Resident unit identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return star average and trait counts for a profile."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.get_rating_summary(
        contact_id=str(contact["id"]),
        unit_id=unit_id,
        profile_id=profile_id,
    )
    return success_response(
        request=request,
        message_key="daily_help.success.rating_summary_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("get resident daily help attendance")
@router.get(
    "/{profile_id}/attendance",
    status_code=http_status.HTTP_200_OK,
    summary="Monthly attendance calendar for a daily help profile",
    response_model=None,
    responses=ATTENDANCE_SUCCESS_RESPONSES,
)
@limiter.limit("60/minute")
async def get_resident_daily_help_attendance(
    request: Request,
    profile_id: str = Path(...),
    unit_id: str = Query(..., description="Resident unit identifier (UUID string)."),
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
    """Return day-wise present/absent calendar for the selected month."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.get_attendance(
        contact_id=str(contact["id"]),
        unit_id=unit_id,
        profile_id=profile_id,
        year=year,
        month=month,
    )
    return success_response(
        request=request,
        message_key="daily_help.success.attendance_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("mark resident daily help attendance absence")
@router.post(
    "/{profile_id}/attendance/absence",
    status_code=http_status.HTTP_200_OK,
    summary="Mark a day absent when the helper did not visit",
    response_model=None,
    responses=ATTENDANCE_ABSENCE_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
async def mark_resident_daily_help_attendance_absence(
    request: Request,
    profile_id: str = Path(...),
    unit_id: str = Query(..., description="Resident unit identifier (UUID string)."),
    body: MarkDailyHelpAttendanceAbsenceRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Record that the helper did not visit the resident unit on the given date."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = DailyHelpService(db_connection=db_connection, user_context=user_context)
    data = await service.mark_attendance_absence(
        contact_id=str(contact["id"]),
        unit_id=unit_id,
        profile_id=profile_id,
        attendance_date=body.attendance_date,
    )
    return success_response(
        request=request,
        message_key="daily_help.success.attendance_absence_marked",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )
