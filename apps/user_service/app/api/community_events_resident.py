"""Resident community events API."""

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.community_events import (
    CreateEventBookingRequest,
    ResidentEventListQuery,
    VerifyBookingRequest,
)
from apps.user_service.app.services.community_event_booking_service import (
    CommunityEventBookingService,
)
from apps.user_service.app.services.community_events_resident_service import (
    CommunityEventsResidentService,
)
from apps.user_service.app.utils.common_utils import (
    extract_onboarding_contact_context,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/community-events", tags=["Community Events (Resident)"])


@handle_api_exceptions("list resident community events")
@router.get(
    "",
    status_code=http_status.HTTP_200_OK,
    summary="List community events for resident",
)
@limiter.limit("100/minute")
async def list_resident_community_events(
    request: Request,
    query: ResidentEventListQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Upcoming/past events for resident."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = CommunityEventsResidentService(
        db_connection=db_connection,
        user_context=user_context,
    )
    items, total = await service.list_events(
        contact_id=str(contact["id"]),
        query=query,
    )
    return list_response(
        request=request,
        items=[item.model_dump(mode="json") for item in items],
        total=total,
        page=query.page,
        page_size=query.page_size,
        message_key="community_events.success.list_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("list my community event bookings")
@router.get(
    "/my-bookings",
    status_code=http_status.HTTP_200_OK,
    summary="All my event bookings",
)
@limiter.limit("100/minute")
async def list_my_community_event_bookings(
    request: Request,
    project_id: str = Query(...),
    unit_id: str = Query(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """All active bookings."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = CommunityEventsResidentService(
        db_connection=db_connection,
        user_context=user_context,
    )
    items = await service.list_my_bookings(
        contact_id=str(contact["id"]),
        unit_id=unit_id,
        project_id=project_id,
    )
    return success_response(
        request=request,
        message_key="community_events.success.bookings_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=[item.model_dump(mode="json") for item in items],
    )


@handle_api_exceptions("my community event bookings summary")
@router.get(
    "/my-bookings/summary",
    status_code=http_status.HTTP_200_OK,
    summary="My bookings badge summary",
)
@limiter.limit("100/minute")
async def get_my_community_event_bookings_summary(
    request: Request,
    project_id: str = Query(...),
    unit_id: str = Query(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Ticket badge count."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = CommunityEventsResidentService(
        db_connection=db_connection,
        user_context=user_context,
    )
    data = await service.get_my_booking_summary(
        contact_id=str(contact["id"]),
        unit_id=unit_id,
        project_id=project_id,
    )
    return success_response(
        request=request,
        message_key="community_events.success.summary_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("verify community event booking at gate")
@router.post(
    "/verify-booking",
    status_code=http_status.HTTP_200_OK,
    summary="Verify booking QR at gate",
)
@limiter.limit("120/minute")
async def verify_community_event_booking(
    request: Request,
    body: VerifyBookingRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Security gate QR verification."""
    user_context, _contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    booking_service = CommunityEventBookingService(
        db_connection=db_connection,
        user_context=user_context,
    )
    data = await booking_service.verify_booking_at_gate(
        gate_qr_token=body.gate_qr_token,
    )
    return success_response(
        request=request,
        message_key="community_events.success.booking_verified",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(mode="json"),
    )


@handle_api_exceptions("cancel community event booking")
@router.post(
    "/bookings/{booking_id}/cancel",
    status_code=http_status.HTTP_200_OK,
    summary="Cancel my booking",
)
@limiter.limit("30/minute")
async def cancel_community_event_booking(
    request: Request,
    booking_id: str = Path(...),
    unit_id: str = Query(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Resident cancel booking."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = CommunityEventsResidentService(
        db_connection=db_connection,
        user_context=user_context,
    )
    await service.cancel_booking(
        contact_id=str(contact["id"]),
        unit_id=unit_id,
        booking_id=booking_id,
    )
    return success_response(
        request=request,
        message_key="community_events.success.booking_cancelled",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("get resident community event detail")
@router.get(
    "/{event_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Community event detail for resident",
)
@limiter.limit("100/minute")
async def get_resident_community_event(
    request: Request,
    event_id: str = Path(...),
    unit_id: str = Query(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Event detail."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = CommunityEventsResidentService(
        db_connection=db_connection,
        user_context=user_context,
    )
    data = await service.get_event_detail(
        contact_id=str(contact["id"]),
        unit_id=unit_id,
        event_id=event_id,
    )
    return success_response(
        request=request,
        message_key="community_events.success.detail_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(mode="json"),
    )


@handle_api_exceptions("book community event tickets")
@router.post(
    "/{event_id}/bookings",
    status_code=http_status.HTTP_201_CREATED,
    summary="Book event tickets",
)
@limiter.limit("30/minute")
async def book_community_event(
    request: Request,
    event_id: str = Path(...),
    unit_id: str = Query(...),
    body: CreateEventBookingRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Create booking."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = CommunityEventsResidentService(
        db_connection=db_connection,
        user_context=user_context,
    )
    data = await service.book_event(
        contact_id=str(contact["id"]),
        unit_id=unit_id,
        event_id=event_id,
        body=body,
    )
    return success_response(
        request=request,
        message_key="community_events.success.booking_created",
        custom_code=CustomStatusCode.CREATED,
        data=data.model_dump(mode="json"),
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("get my booking for event")
@router.get(
    "/{event_id}/my-booking",
    status_code=http_status.HTTP_200_OK,
    summary="My booking for an event",
)
@limiter.limit("100/minute")
async def get_my_community_event_booking(
    request: Request,
    event_id: str = Path(...),
    unit_id: str = Query(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """View tickets for one event."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = CommunityEventsResidentService(
        db_connection=db_connection,
        user_context=user_context,
    )
    data = await service.get_my_booking_for_event(
        contact_id=str(contact["id"]),
        unit_id=unit_id,
        event_id=event_id,
    )
    return success_response(
        request=request,
        message_key="community_events.success.booking_retrieved",
        custom_code=CustomStatusCode.SUCCESS if data else CustomStatusCode.NO_CONTENT,
        data=data.model_dump(mode="json") if data else None,
    )
