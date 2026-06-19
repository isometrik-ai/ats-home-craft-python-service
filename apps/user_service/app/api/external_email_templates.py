"""External Email Templates API.

These endpoints support two integration auth modes:

- **Isometrik credentials** (`licenseKey` / `appSecret`) — GET details and POST render.
  The decoded ``projectId`` maps to internal ``organization_id``.

- **Ross AI integration API key** (`ROSSAI_API_KEY`) — POST create. The caller supplies
  ``organization_id`` in the request body.

Endpoints:
    GET  /{template_id}         — template details and variable tree
    POST /{template_id}/render  — populate variables and return final HTML
    POST /                      — create a template (Ross AI API key)
"""

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Request
from fastapi import status as http_status

from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.dependencies.external_auth import (
    get_organization_context,
    resolve_external_organization_id,
)
from apps.user_service.app.schemas.email_templates import (
    CreateEmailTemplateRequest,
    RenderEmailTemplateRequest,
)
from apps.user_service.app.schemas.external_email_templates import (
    ExternalCreateEmailTemplateRequest,
    ExternalCreateEmailTemplateResult,
)
from apps.user_service.app.services.email_template_service import EmailTemplateService
from apps.user_service.app.utils.common_utils import (
    UserContext,
    handle_api_exceptions,
)
from libs.shared_middleware.ross_ai_integration_auth import (
    verify_ross_ai_integration_api_key,
)
from libs.shared_utils.response_factory import success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/integrations/email-templates", tags=["Email Templates (External)"])


@handle_api_exceptions("external get email template")
@router.get(
    "/{template_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get email template details (external auth)",
    description=(
        "Return full email template detail including the variable tree and HTML. "
        "Organization is resolved from Isometrik credentials (`licenseKey`/`appSecret`)."
    ),
    responses={
        http_status.HTTP_200_OK: {"description": "Email template retrieved successfully"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
    },
)
async def external_get_email_template(
    request: Request,
    template_id: str = Path(..., description="Email template ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(get_organization_context),
):
    """External get email template endpoint (Isometrik credential auth)."""
    service = EmailTemplateService(
        user_context=UserContext(
            user_id="00000000-0000-0000-0000-000000000000",
            email=request.state.external_actor_email,
            organization_id=organization_id,
        ),
        db_connection=db_connection,
    )
    data = await service.get_email_template(template_id)

    return success_response(
        request=request,
        message_key="email_templates.success.template_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data,
    )


@handle_api_exceptions("external render email template")
@router.post(
    "/{template_id}/render",
    status_code=http_status.HTTP_200_OK,
    summary="Render email template with variable values (external auth)",
    description=(
        "Merge layout + trigger HTML and substitute runtime variable values. "
        "Does not send email. Organization is resolved from Isometrik credentials "
        "(`licenseKey`/`appSecret`)."
    ),
    responses={
        http_status.HTTP_200_OK: {"description": "Email template rendered successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Validation error"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
    },
)
async def external_render_email_template(
    request: Request,
    template_id: str = Path(..., description="Email template ID (usually a TRIGGER)"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(get_organization_context),
    body: RenderEmailTemplateRequest = Body(...),
):
    """External render email template endpoint (Isometrik credential auth)."""
    service = EmailTemplateService(
        user_context=UserContext(
            user_id="00000000-0000-0000-0000-000000000000",
            email=request.state.external_actor_email,
            organization_id=organization_id,
        ),
        db_connection=db_connection,
    )
    data = await service.render_email_template(template_id, body)

    return success_response(
        request=request,
        message_key="email_templates.success.template_rendered",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data,
    )


@handle_api_exceptions("external create email template")
@router.post(
    "",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create an email template (external auth)",
    description=(
        "Create a TRIGGER or LAYOUT email template for the organization specified in "
        "`organization_id`. Authenticate with the Ross AI integration API key "
        "(`ROSSAI_API_KEY`). Templates are created as draft by default unless `status` is "
        "set to `published`."
    ),
    responses={
        http_status.HTTP_201_CREATED: {"description": "Email template created successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_409_CONFLICT: {"description": "Conflict"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
    },
)
@audit_api_call(
    action_type="CREATE",
    data_classification="confidential",
    compliance_tags=["soc2_audit", "audit_required"],
    table_name="email_templates",
    category="EMAIL_TEMPLATE",
)
async def external_create_email_template(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    _: None = Depends(verify_ross_ai_integration_api_key),
    body: ExternalCreateEmailTemplateRequest = Body(...),
):
    """External create email template endpoint (Ross AI API key auth)."""
    organization_id = await resolve_external_organization_id(
        request=request,
        organization_id=body.organization_id,
        db_connection=db_connection,
    )
    actor_email = request.state.external_actor_email
    create_body = CreateEmailTemplateRequest.model_validate(
        body.model_dump(exclude={"organization_id"})
    )

    service = EmailTemplateService(
        user_context=UserContext(
            user_id="00000000-0000-0000-0000-000000000000",
            email=actor_email,
            organization_id=organization_id,
        ),
        db_connection=db_connection,
    )
    created = await service.create_email_template(create_body)

    request.state.audit_table = "email_templates"
    request.state.audit_requested_id = str(created.get("id", "")) if created else ""
    request.state.audit_description = f"Created external email template: {create_body.name}"
    request.state.audit_risk_level = "medium"
    request.state.audit_user_context = {
        "user_id": "00000000-0000-0000-0000-000000000000",
        "user_email": actor_email,
        "organization_id": organization_id,
    }
    request.state.raw_audit_new_data = created

    return success_response(
        request=request,
        message_key="email_templates.success.template_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=ExternalCreateEmailTemplateResult(
            template_id=str(created["id"]),
            name=created["name"],
            template_type=created["template_type"],
            status=created["status"],
        ).model_dump(exclude_none=True, mode="json"),
    )
