"""Contact onboarding API."""

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status
from supabase import AsyncClient

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.dependencies.supabase import supabase_anon, supabase_service
from apps.user_service.app.schemas.contact_onboarding import (
    AcceptHouseholdInvitationRequest,
    AddHouseholdMemberApiResponse,
    ClaimPropertiesApiResponse,
    ClaimPropertiesRequest,
    ClaimPropertiesResponse,
    CompleteOnboardingApiResponse,
    CompleteOnboardingRequest,
    CompleteProfileRequest,
    ConfirmPropertiesRequest,
    ContactOnboardingMessageApiResponse,
    ContactProfileApiResponse,
    ContactPropertiesListApiResponse,
    ContactPropertyProjectGroupResponse,
    CreateHouseholdMemberRequest,
    CreateVehicleRequest,
    DeclineHouseholdInvitationRequest,
    HouseholdInvitationAcceptApiResponse,
    HouseholdInvitationDeclineApiResponse,
    HouseholdInvitationSentApiResponse,
    HouseholdInvitationValidateApiResponse,
    HouseholdMemberApiResponse,
    HouseholdMemberListApiResponse,
    OnboardingReviewApiResponse,
    OnboardingStatusApiResponse,
    RemoveHouseholdMemberApiResponse,
    ResubmitVehicleRequest,
    SetDefaultUnitApiResponse,
    SetDefaultUnitRequest,
    UpdateHouseholdMemberRequest,
    UpdateVehicleRequest,
    ValidateHouseholdInvitationRequest,
    VehicleCatalogApiResponse,
    VehicleCatalogResponse,
    VehicleDetailApiResponse,
    VehicleListApiResponse,
    VehicleResponse,
)
from apps.user_service.app.schemas.enums import VehicleType
from apps.user_service.app.services.contact_onboarding_service import (
    ContactOnboardingService,
)
from apps.user_service.app.services.contact_units_service import ContactUnitsService
from apps.user_service.app.services.household_invitation_service import (
    HouseholdInvitationService,
)
from apps.user_service.app.services.vehicle_catalog_service import VehicleCatalogService
from apps.user_service.app.services.vehicles_service import VehiclesService
from apps.user_service.app.utils.audit_context import set_audit_context
from apps.user_service.app.utils.common_utils import (
    UserContext,
    extract_onboarding_contact_context,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.session_context_cache import (
    extract_session_id_from_access_token,
    warm_session_context_after_auth,
)
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/contact-onboarding", tags=["Contact Onboarding"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden."},
    404: {"description": "Not found."},
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


STATUS_SUCCESS_RESPONSES = _ok_response(
    OnboardingStatusApiResponse,
    "Onboarding prompts for the authenticated contact.",
)
PROPERTIES_LIST_SUCCESS_RESPONSES = _ok_response(
    ContactPropertiesListApiResponse,
    "Pending and active properties grouped by project.",
)
CLAIM_PROPERTIES_SUCCESS_RESPONSES = _ok_response(
    ClaimPropertiesApiResponse,
    "Confirmed or claimed contact units with default-unit hint.",
)
PROFILE_SUCCESS_RESPONSES = _ok_response(
    ContactProfileApiResponse,
    "Contact profile details.",
)
VEHICLE_CATALOG_SUCCESS_RESPONSES = _ok_response(
    VehicleCatalogApiResponse,
    "Vehicle brand, model, and color picker options.",
)
VEHICLE_LIST_SUCCESS_RESPONSES = _ok_response(
    VehicleListApiResponse,
    "Vehicles registered by the authenticated contact.",
)
VEHICLE_DETAIL_SUCCESS_RESPONSES = _ok_response(
    VehicleDetailApiResponse,
    "Vehicle detail including unit and parking allotment.",
)
VEHICLE_CREATED_SUCCESS_RESPONSES = _created_response(
    VehicleDetailApiResponse,
    "Newly registered vehicle.",
)
VEHICLE_WITHDRAW_SUCCESS_RESPONSES = _ok_response(
    ContactOnboardingMessageApiResponse,
    "Pending vehicle request withdrawn.",
)
HOUSEHOLD_LIST_SUCCESS_RESPONSES = _ok_response(
    HouseholdMemberListApiResponse,
    "Household members linked to the contact's units.",
)
ADD_HOUSEHOLD_MEMBER_SUCCESS_RESPONSES = _created_response(
    AddHouseholdMemberApiResponse,
    "New household member linked to the unit.",
)
HOUSEHOLD_MEMBER_SUCCESS_RESPONSES = _ok_response(
    HouseholdMemberApiResponse,
    "Updated household member.",
)
INVITATION_VALIDATE_SUCCESS_RESPONSES = _ok_response(
    HouseholdInvitationValidateApiResponse,
    "Invitation token validation result.",
)
INVITATION_ACCEPT_SUCCESS_RESPONSES = _ok_response(
    HouseholdInvitationAcceptApiResponse,
    "Invitation accepted; session tokens returned.",
)
INVITATION_DECLINE_SUCCESS_RESPONSES = _ok_response(
    HouseholdInvitationDeclineApiResponse,
    "Invitation declined.",
)
INVITATION_RESEND_SUCCESS_RESPONSES = _ok_response(
    HouseholdInvitationSentApiResponse,
    "Invitation SMS resent.",
)
REMOVE_HOUSEHOLD_MEMBER_SUCCESS_RESPONSES = _ok_response(
    RemoveHouseholdMemberApiResponse,
    "Household member removed.",
)
SET_DEFAULT_UNIT_SUCCESS_RESPONSES = _ok_response(
    SetDefaultUnitApiResponse,
    "Default login unit updated.",
)
REVIEW_SUCCESS_RESPONSES = _ok_response(
    OnboardingReviewApiResponse,
    "Aggregated onboarding review before finalize.",
)
COMPLETE_SUCCESS_RESPONSES = _ok_response(
    CompleteOnboardingApiResponse,
    "Onboarding finalized; selected properties activated.",
)


def _service(
    *,
    db_connection: asyncpg.Connection,
    user_context,
    sb_client: AsyncClient | None,
) -> ContactOnboardingService:
    """Build ContactOnboardingService for the current request."""
    return ContactOnboardingService(
        db_connection=db_connection,
        user_context=user_context,
        supabase_client=sb_client,
    )


@handle_api_exceptions("get contact onboarding status")
@router.get(
    "/status",
    status_code=http_status.HTTP_200_OK,
    summary="Get onboarding status",
    response_model=None,
    responses=STATUS_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_onboarding_status(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return onboarding prompts for the authenticated contact."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = _service(
        db_connection=db_connection,
        user_context=user_context,
        sb_client=None,
    )
    roles = contact.get("roles") or []
    role_type = ""
    if isinstance(roles, list) and roles:
        first = roles[0]
        if isinstance(first, dict):
            role_type = str(first.get("role_type") or "")
    data = await service.get_status(
        contact_id=str(contact["id"]),
        contact_type=role_type,
    )
    return success_response(
        request=request,
        message_key="contact_onboarding.success.status_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("list contact properties")
@router.get(
    "/properties",
    status_code=http_status.HTTP_200_OK,
    summary="List claimable properties",
    response_model=None,
    responses=PROPERTIES_LIST_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def list_properties(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List claimable properties for the authenticated contact."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    units_service = ContactUnitsService(
        db_connection=db_connection,
        user_context=user_context,
    )
    items = await units_service.list_my_properties_grouped(contact_id=str(contact["id"]))
    serialized_items = [
        ContactPropertyProjectGroupResponse.model_validate(item).model_dump(exclude_none=True)
        for item in items
    ]
    return list_response(
        request=request,
        items=serialized_items,
        total=len(serialized_items),
        message_key="contact_onboarding.success.properties_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("confirm contact properties")
@router.post(
    "/properties/confirm",
    status_code=http_status.HTTP_200_OK,
    summary="Confirm selected properties",
    response_model=None,
    responses=CLAIM_PROPERTIES_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "audit_required"],
    table_name="contact_units",
    category="CONTACT_ONBOARDING",
)
async def confirm_properties(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: ConfirmPropertiesRequest = Body(...),
):
    """Confirm selected pending properties."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    units_service = ContactUnitsService(
        db_connection=db_connection,
        user_context=user_context,
    )
    data = await units_service.confirm_properties(
        contact_id=str(contact["id"]),
        contact_unit_ids=body.contact_unit_ids,
        default_contact_unit_id=body.default_contact_unit_id,
    )
    set_audit_context(
        request,
        user_context,
        table="contact_units",
        requested_id=str(contact["id"]),
        description=f"Confirmed properties for contact: {contact['id']}",
        risk_level="high",
        new_data=data,
    )
    payload = ClaimPropertiesResponse.model_validate(data).model_dump(exclude_none=True)
    return success_response(
        request=request,
        message_key="contact_onboarding.success.properties_confirmed",
        custom_code=CustomStatusCode.SUCCESS,
        data=payload,
    )


@handle_api_exceptions("claim post-onboarding properties")
@router.post(
    "/properties/claim",
    status_code=http_status.HTTP_200_OK,
    summary="Claim properties after onboarding is complete",
    description=(
        "Accept pending unit allotments when onboarding is already finished. "
        "Sets activated_at on claimed units and returns whether a default login unit is needed."
    ),
    response_model=None,
    responses=CLAIM_PROPERTIES_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "audit_required"],
    table_name="contact_units",
    category="CONTACT_ONBOARDING",
)
async def claim_properties(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: ClaimPropertiesRequest = Body(...),
):
    """Claim pending properties assigned after onboarding completion."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    units_service = ContactUnitsService(
        db_connection=db_connection,
        user_context=user_context,
    )
    data = await units_service.claim_properties(
        contact_id=str(contact["id"]),
        contact_unit_ids=body.contact_unit_ids,
    )
    set_audit_context(
        request,
        user_context,
        table="contact_units",
        requested_id=str(contact["id"]),
        description=f"Claimed properties for contact: {contact['id']}",
        risk_level="high",
        new_data=data,
    )
    payload = ClaimPropertiesResponse.model_validate(data).model_dump(exclude_none=True)
    return success_response(
        request=request,
        message_key="contact_onboarding.success.properties_claimed",
        custom_code=CustomStatusCode.SUCCESS,
        data=payload,
    )


@handle_api_exceptions("get contact profile")
@router.get(
    "/profile",
    status_code=http_status.HTTP_200_OK,
    summary="Get contact profile for onboarding",
    response_model=None,
    responses=PROFILE_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_profile(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return the authenticated contact's profile for the onboarding wizard."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = _service(
        db_connection=db_connection,
        user_context=user_context,
        sb_client=None,
    )
    data = await service.get_profile(contact_id=str(contact["id"]))
    return success_response(
        request=request,
        message_key="contact_onboarding.success.profile_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("complete contact profile")
@router.patch(
    "/profile",
    status_code=http_status.HTTP_200_OK,
    summary="Complete profile step",
    response_model=None,
    responses=PROFILE_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "audit_required"],
    table_name="contacts",
    category="CONTACT_ONBOARDING",
)
async def complete_profile(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    sb_client: AsyncClient = Depends(supabase_service),
    body: CompleteProfileRequest = Body(...),
):
    """Update contact profile and complete the profile step."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = _service(
        db_connection=db_connection,
        user_context=user_context,
        sb_client=sb_client,
    )
    data = await service.complete_profile(contact_id=str(contact["id"]), body=body)
    set_audit_context(
        request,
        user_context,
        table="contacts",
        requested_id=str(contact["id"]),
        description=f"Completed profile for contact: {contact['id']}",
        risk_level="high",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="contact_onboarding.success.profile_updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("get vehicle catalog")
@router.get(
    "/vehicles/options",
    status_code=http_status.HTTP_200_OK,
    summary="Get vehicle brand, model, and color options",
    description=(
        "Returns static picker options from vehicle_catalog.json for the given vehicle_type. "
        "Optional brand_id narrows models to one brand; search filters names."
    ),
    response_model=None,
    responses=VEHICLE_CATALOG_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_vehicle_catalog(
    request: Request,
    vehicle_type: VehicleType = Query(
        ...,
        description="Vehicle type: two_wheeler or four_wheeler.",
    ),
    brand_id: str | None = Query(
        default=None,
        description="Filter to a single brand (e.g. tata).",
    ),
    search: str | None = Query(
        default=None,
        description="Case-insensitive filter on brand, model, and color names.",
    ),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return vehicle catalog options for the Add Vehicle screen."""
    _ = current_user
    data = VehicleCatalogService.get_catalog(
        vehicle_type=vehicle_type.value,
        brand_id=brand_id,
        search=search,
    )
    payload = VehicleCatalogResponse.model_validate(data).model_dump(exclude_none=True)
    return success_response(
        request=request,
        message_key="contact_onboarding.success.vehicle_catalog_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=payload,
    )


@handle_api_exceptions("list contact vehicles")
@router.get(
    "/vehicles",
    status_code=http_status.HTTP_200_OK,
    summary="List vehicles",
    response_model=None,
    responses=VEHICLE_LIST_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def list_vehicles(
    request: Request,
    unit_id: str | None = Query(
        default=None,
        description="Optional unit filter; returns vehicles for that unit only.",
    ),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List vehicles registered by the authenticated contact."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    vehicles_service = VehiclesService(
        db_connection=db_connection,
        user_context=user_context,
    )
    items = await vehicles_service.list_vehicles(
        contact_id=str(contact["id"]),
        unit_id=unit_id,
    )
    return list_response(
        request=request,
        items=items,
        total=len(items),
        message_key="contact_onboarding.success.vehicles_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("create contact vehicle")
@router.post(
    "/vehicles",
    status_code=http_status.HTTP_201_CREATED,
    summary="Register a vehicle",
    response_model=None,
    responses=VEHICLE_CREATED_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="vehicles",
    category="CONTACT_ONBOARDING",
)
async def create_vehicle(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateVehicleRequest = Body(...),
):
    """Register a vehicle for the authenticated contact."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    vehicles_service = VehiclesService(
        db_connection=db_connection,
        user_context=user_context,
    )
    data = await vehicles_service.create_vehicle(contact_id=str(contact["id"]), body=body)
    set_audit_context(
        request,
        user_context,
        table="vehicles",
        requested_id=str(contact["id"]),
        description=f"Created vehicle for contact: {contact['id']}",
        risk_level="medium",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="contact_onboarding.success.vehicle_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data,
    )


@handle_api_exceptions("get contact vehicle detail")
@router.get(
    "/vehicles/{vehicle_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get vehicle detail",
    description=(
        "Returns one vehicle registered by the authenticated contact, including "
        "assigned unit summary and parking slot allotment when approved."
    ),
    response_model=None,
    responses=VEHICLE_DETAIL_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_vehicle_detail(
    request: Request,
    vehicle_id: str = Path(..., description="Vehicle identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get vehicle detail for the authenticated contact."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    vehicles_service = VehiclesService(
        db_connection=db_connection,
        user_context=user_context,
    )
    data = await vehicles_service.get_vehicle_detail(
        contact_id=str(contact["id"]),
        vehicle_id=vehicle_id,
    )
    payload = VehicleResponse.model_validate(data).model_dump(mode="json", exclude_none=True)
    return success_response(
        request=request,
        message_key="contact_onboarding.success.vehicle_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=payload,
    )


@handle_api_exceptions("update contact vehicle")
@router.patch(
    "/vehicles/{vehicle_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Update a vehicle",
    response_model=None,
    responses=VEHICLE_DETAIL_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="vehicles",
    category="CONTACT_ONBOARDING",
)
async def update_vehicle(
    request: Request,
    vehicle_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: UpdateVehicleRequest = Body(...),
):
    """Update a vehicle owned by the authenticated contact."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    vehicles_service = VehiclesService(
        db_connection=db_connection,
        user_context=user_context,
    )
    data = await vehicles_service.update_vehicle(
        contact_id=str(contact["id"]),
        vehicle_id=vehicle_id,
        body=body,
    )
    set_audit_context(
        request,
        user_context,
        table="vehicles",
        requested_id=vehicle_id,
        description=f"Updated vehicle: {vehicle_id}",
        risk_level="medium",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="contact_onboarding.success.vehicle_updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("resubmit contact vehicle request")
@router.post(
    "/vehicles/{vehicle_id}/resubmit",
    status_code=http_status.HTTP_200_OK,
    summary="Resubmit a rejected vehicle request",
    response_model=None,
    responses=VEHICLE_DETAIL_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="vehicles",
    category="CONTACT_ONBOARDING",
)
async def resubmit_vehicle(
    request: Request,
    vehicle_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: ResubmitVehicleRequest = Body(default_factory=ResubmitVehicleRequest),
):
    """Return a rejected vehicle to pending and optionally update its details."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    vehicles_service = VehiclesService(
        db_connection=db_connection,
        user_context=user_context,
    )
    data = await vehicles_service.resubmit_vehicle(
        contact_id=str(contact["id"]),
        vehicle_id=vehicle_id,
        body=body,
    )
    set_audit_context(
        request,
        user_context,
        table="vehicles",
        requested_id=vehicle_id,
        description=f"Resubmitted vehicle request: {vehicle_id}",
        risk_level="medium",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="contact_onboarding.success.vehicle_resubmitted",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("withdraw contact vehicle request")
@router.post(
    "/vehicles/{vehicle_id}/withdraw",
    status_code=http_status.HTTP_200_OK,
    summary="Withdraw a pending vehicle request",
    response_model=None,
    responses=VEHICLE_WITHDRAW_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="vehicles",
    category="CONTACT_ONBOARDING",
)
async def withdraw_vehicle(
    request: Request,
    vehicle_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Withdraw a pending vehicle request (hard-delete before admin approval)."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    set_audit_context(
        request,
        user_context,
        table="vehicles",
        requested_id=vehicle_id,
        description=f"Withdrew vehicle request: {vehicle_id}",
        risk_level="medium",
    )
    vehicles_service = VehiclesService(
        db_connection=db_connection,
        user_context=user_context,
    )
    await vehicles_service.withdraw_vehicle(
        contact_id=str(contact["id"]),
        vehicle_id=vehicle_id,
    )
    return success_response(
        request=request,
        message_key="contact_onboarding.success.vehicle_withdrawn",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("remove contact vehicle")
@router.delete(
    "/vehicles/{vehicle_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Remove an approved vehicle",
    response_model=None,
    responses=VEHICLE_DETAIL_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="vehicles",
    category="CONTACT_ONBOARDING",
)
async def remove_vehicle(
    request: Request,
    vehicle_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Soft-remove an approved vehicle (status removed; parking slot released)."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    vehicles_service = VehiclesService(
        db_connection=db_connection,
        user_context=user_context,
    )
    data = await vehicles_service.remove_vehicle(
        contact_id=str(contact["id"]),
        vehicle_id=vehicle_id,
    )
    set_audit_context(
        request,
        user_context,
        table="vehicles",
        requested_id=vehicle_id,
        description=f"Removed vehicle: {vehicle_id}",
        risk_level="medium",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="contact_onboarding.success.vehicle_removed",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("list household members")
@router.get(
    "/household",
    status_code=http_status.HTTP_200_OK,
    summary="List household members",
    response_model=None,
    responses=HOUSEHOLD_LIST_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def list_household(
    request: Request,
    unit_id: str | None = Query(
        default=None,
        description="Filter household members to one unit.",
    ),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List household members linked to the authenticated contact."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = _service(
        db_connection=db_connection,
        user_context=user_context,
        sb_client=None,
    )
    items = await service.list_household(
        contact_id=str(contact["id"]),
        unit_id=unit_id,
    )
    return list_response(
        request=request,
        items=items,
        total=len(items),
        message_key="contact_onboarding.success.household_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("add household member")
@router.post(
    "/household",
    status_code=http_status.HTTP_201_CREATED,
    summary="Add household member",
    response_model=None,
    responses=ADD_HOUSEHOLD_MEMBER_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "audit_required"],
    table_name="contacts",
    category="CONTACT_ONBOARDING",
)
async def add_household_member(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    sb_client: AsyncClient = Depends(supabase_service),
    body: CreateHouseholdMemberRequest = Body(...),
):
    """Add a family member to a unit owned by the authenticated contact."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = _service(
        db_connection=db_connection,
        user_context=user_context,
        sb_client=sb_client,
    )
    data = await service.add_household_member(
        primary_contact_id=str(contact["id"]),
        body=body,
    )
    set_audit_context(
        request,
        user_context,
        table="contacts",
        requested_id=str(contact["id"]),
        description=f"Added household member for contact: {contact['id']}",
        risk_level="high",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="contact_onboarding.success.household_member_added",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data,
    )


@handle_api_exceptions("update household member")
@router.patch(
    "/household/{contact_unit_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Update household member",
    response_model=None,
    responses=HOUSEHOLD_MEMBER_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "audit_required"],
    table_name="contacts",
    category="CONTACT_ONBOARDING",
)
async def update_household_member(
    request: Request,
    contact_unit_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    sb_client: AsyncClient = Depends(supabase_service),
    body: UpdateHouseholdMemberRequest = Body(...),
):
    """Update a family member on a unit owned by the authenticated contact."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = _service(
        db_connection=db_connection,
        user_context=user_context,
        sb_client=sb_client,
    )
    data = await service.update_household_member(
        primary_contact_id=str(contact["id"]),
        contact_unit_id=contact_unit_id,
        body=body,
    )
    set_audit_context(
        request,
        user_context,
        table="contacts",
        requested_id=contact_unit_id,
        description=f"Updated household member: {contact_unit_id}",
        risk_level="medium",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="contact_onboarding.success.household_member_updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


def _invitation_service(
    *,
    db_connection: asyncpg.Connection,
    sb_client: AsyncClient | None = None,
    sb_anon_client: AsyncClient | None = None,
) -> HouseholdInvitationService:
    """Build HouseholdInvitationService for public invitation endpoints."""
    return HouseholdInvitationService(
        db_connection=db_connection,
        user_context=None,
        supabase_client=sb_client,
        supabase_anon_client=sb_anon_client,
    )


@handle_api_exceptions("validate household invitation")
@router.post(
    "/household/invitations/validate",
    status_code=http_status.HTTP_200_OK,
    summary="Validate household invitation token",
    response_model=None,
    responses=INVITATION_VALIDATE_SUCCESS_RESPONSES,
)
@limiter.limit("60/minute")
async def validate_household_invitation(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    body: ValidateHouseholdInvitationRequest = Body(...),
):
    """Validate an SMS deep-link token before the invitee accepts."""
    service = _invitation_service(db_connection=db_connection)
    data = await service.validate_token(token=body.token)
    return success_response(
        request=request,
        message_key="contact_onboarding.success.invitation_validated",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("accept household invitation")
@router.post(
    "/household/invitations/accept",
    status_code=http_status.HTTP_200_OK,
    summary="Accept household invitation",
    response_model=None,
    responses=INVITATION_ACCEPT_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "audit_required"],
    table_name="household_invitations",
    category="CONTACT_ONBOARDING",
)
async def accept_household_invitation(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    sb_client: AsyncClient = Depends(supabase_service),
    sb_anon_client: AsyncClient = Depends(supabase_anon),
    body: AcceptHouseholdInvitationRequest = Body(...),
):
    """Accept a phone invitation, set password, sign in, and seed onboarding."""
    service = _invitation_service(
        db_connection=db_connection,
        sb_client=sb_client,
        sb_anon_client=sb_anon_client,
    )
    data = await service.accept(token=body.token, password=body.password)

    contact_id = str(data.get("contact_id", ""))
    audit_user_context = UserContext(
        user_id=str(data.get("user", {}).get("id") or contact_id),
        email=data.get("user", {}).get("email") or f"contact-{contact_id}@household-invite",
        organization_id=data.get("organization_id"),
    )
    set_audit_context(
        request,
        audit_user_context,
        table="household_invitations",
        requested_id=contact_id,
        description=f"Accepted household invitation for contact: {contact_id}",
        risk_level="medium",
        new_data=data,
    )

    session_id = extract_session_id_from_access_token(data.get("access_token"))
    if session_id:
        await warm_session_context_after_auth(
            session_id=session_id,
            organization_id=data.get("organization_id"),
        )

    return success_response(
        request=request,
        message_key="contact_onboarding.success.invitation_accepted",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("decline household invitation")
@router.post(
    "/household/invitations/decline",
    status_code=http_status.HTTP_200_OK,
    summary="Decline household invitation",
    response_model=None,
    responses=INVITATION_DECLINE_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "audit_required"],
    table_name="household_invitations",
    category="CONTACT_ONBOARDING",
)
async def decline_household_invitation(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    body: DeclineHouseholdInvitationRequest = Body(...),
):
    """Decline a phone invitation; removes the pending link and orphan contact."""
    service = _invitation_service(db_connection=db_connection)
    data = await service.decline(token=body.token)

    contact_id = str(data.get("contact_id", ""))
    audit_user_context = UserContext(
        user_id=contact_id,
        email=data.get("user", {}).get("email") or f"contact-{contact_id}@household-invite",
        organization_id=data.get("organization_id"),
    )
    set_audit_context(
        request,
        audit_user_context,
        table="household_invitations",
        requested_id=contact_id,
        description=f"Declined household invitation for contact: {contact_id}",
        risk_level="medium",
        new_data=data,
    )

    return success_response(
        request=request,
        message_key="contact_onboarding.success.invitation_declined",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("revoke household invitation")
@router.post(
    "/household/{contact_unit_id}/revoke-invitation",
    status_code=http_status.HTTP_200_OK,
    summary="Revoke a pending household invitation",
    response_model=None,
    responses=HOUSEHOLD_MEMBER_SUCCESS_RESPONSES,
)
@limiter.limit("10/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="household_invitations",
    category="CONTACT_ONBOARDING",
)
async def revoke_household_invitation(
    request: Request,
    contact_unit_id: str = Path(..., description="Household member's contact_unit id"),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Cancel a pending portal invitation; the household member remains on the unit."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = _service(
        db_connection=db_connection,
        user_context=user_context,
        sb_client=None,
    )
    data = await service.revoke_household_invitation(
        primary_contact_id=str(contact["id"]),
        contact_unit_id=contact_unit_id,
    )
    set_audit_context(
        request,
        user_context,
        table="household_invitations",
        requested_id=contact_unit_id,
        description=f"Revoked household invitation: {contact_unit_id}",
        risk_level="medium",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="contact_onboarding.success.invitation_revoked",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("resend household invitation")
@router.post(
    "/household/{contact_unit_id}/resend-invitation",
    status_code=http_status.HTTP_200_OK,
    summary="Resend household invitation SMS",
    response_model=None,
    responses=INVITATION_RESEND_SUCCESS_RESPONSES,
)
@limiter.limit("10/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="household_invitations",
    category="CONTACT_ONBOARDING",
)
async def resend_household_invitation(
    request: Request,
    contact_unit_id: str = Path(..., description="Household member's contact_unit id"),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Resend the SMS invitation for a pending portal-access household member."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = _service(
        db_connection=db_connection,
        user_context=user_context,
        sb_client=None,
    )
    data = await service.resend_household_invitation(
        primary_contact_id=str(contact["id"]),
        contact_unit_id=contact_unit_id,
    )
    set_audit_context(
        request,
        user_context,
        table="household_invitations",
        requested_id=contact_unit_id,
        description=f"Resent household invitation: {contact_unit_id}",
        risk_level="medium",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="contact_onboarding.success.invitation_resent",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("remove household member")
@router.delete(
    "/household/{contact_unit_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Remove household member",
    response_model=None,
    responses=REMOVE_HOUSEHOLD_MEMBER_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "audit_required"],
    table_name="contact_units",
    category="CONTACT_ONBOARDING",
)
async def remove_household_member(
    request: Request,
    contact_unit_id: str = Path(..., description="Household member's contact_unit id"),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Remove a family member linked to a unit owned by the authenticated contact."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = _service(
        db_connection=db_connection,
        user_context=user_context,
        sb_client=None,
    )
    data = await service.remove_household_member(
        primary_contact_id=str(contact["id"]),
        contact_unit_id=contact_unit_id,
    )
    set_audit_context(
        request,
        user_context,
        table="contact_units",
        requested_id=contact_unit_id,
        description=f"Removed household member: {contact_unit_id}",
        risk_level="medium",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="contact_onboarding.success.household_member_removed",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("get onboarding review")
@router.get(
    "/review",
    status_code=http_status.HTTP_200_OK,
    summary="Review onboarding summary",
    response_model=None,
    responses=REVIEW_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_review(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    sb_client: AsyncClient = Depends(supabase_service),
):
    """Return aggregated onboarding review data."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = _service(
        db_connection=db_connection,
        user_context=user_context,
        sb_client=sb_client,
    )
    data = await service.get_review(contact_id=str(contact["id"]))
    return success_response(
        request=request,
        message_key="contact_onboarding.success.review_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("complete contact onboarding")
@router.post(
    "/complete",
    status_code=http_status.HTTP_200_OK,
    summary="Finalize onboarding",
    description=(
        "Finalize onboarding and activate confirmed properties. Optionally pass "
        "`contact_unit_ids` to finish a subset now; other active properties are moved "
        "back to pending and can be claimed later via POST /properties/claim."
    ),
    response_model=None,
    responses=COMPLETE_SUCCESS_RESPONSES,
)
@limiter.limit("10/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "audit_required"],
    table_name="contact_onboarding_steps",
    category="CONTACT_ONBOARDING",
)
async def complete_onboarding(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CompleteOnboardingRequest | None = Body(default=None),
):
    """Finalize contact onboarding and activate assigned units."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = _service(
        db_connection=db_connection,
        user_context=user_context,
        sb_client=None,
    )
    data = await service.complete_onboarding(
        contact_id=str(contact["id"]),
        body=body,
    )
    set_audit_context(
        request,
        user_context,
        table="contact_onboarding_steps",
        requested_id=str(contact["id"]),
        description=f"Completed onboarding for contact: {contact['id']}",
        risk_level="high",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="contact_onboarding.success.onboarding_completed",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("set default login unit")
@router.post(
    "/default-unit",
    status_code=http_status.HTTP_200_OK,
    summary="Choose default unit to login",
    response_model=None,
    responses=SET_DEFAULT_UNIT_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="contact_units",
    category="CONTACT_ONBOARDING",
)
async def set_default_unit(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: SetDefaultUnitRequest = Body(...),
):
    """Set the default login unit for the authenticated contact."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    units_service = ContactUnitsService(
        db_connection=db_connection,
        user_context=user_context,
    )
    data = await units_service.set_default_unit(
        contact_id=str(contact["id"]),
        contact_unit_id=body.contact_unit_id,
    )
    set_audit_context(
        request,
        user_context,
        table="contact_units",
        requested_id=body.contact_unit_id,
        description=f"Set default unit for contact: {contact['id']}",
        risk_level="medium",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="contact_onboarding.success.default_unit_set",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )
