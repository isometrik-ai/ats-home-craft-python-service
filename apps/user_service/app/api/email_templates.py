"""Email Templates API Module."""

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.email_templates import (
    CreateEmailTemplateRequest,
    RenderEmailTemplateRequest,
    UpdateEmailTemplateRequest,
)
from apps.user_service.app.schemas.enums import EmailTemplateStatus, EmailTemplateType
from apps.user_service.app.services.email_template_service import EmailTemplateService
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    EMAIL_TEMPLATES_MANAGEMENT_CREATE,
    EMAIL_TEMPLATES_MANAGEMENT_DELETE,
    EMAIL_TEMPLATES_MANAGEMENT_EDIT,
    EMAIL_TEMPLATES_MANAGEMENT_VIEW,
)
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/email-templates", tags=["Email Templates"])


@handle_api_exceptions("create email template")
@router.post(
    "",
    status_code=http_status.HTTP_201_CREATED,
    description="Create a new email template (TRIGGER or LAYOUT)",
    summary="Create email template",
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="confidential",
    compliance_tags=["soc2_audit", "audit_required"],
    table_name="email_templates",
    category="EMAIL_TEMPLATE",
)
async def create_email_template(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateEmailTemplateRequest = Body(...),
):
    """Create an email template for the authenticated organization."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=EMAIL_TEMPLATES_MANAGEMENT_CREATE,
    )

    request.state.audit_table = "email_templates"
    request.state.audit_description = f"Created email template: {body.name}"
    request.state.audit_risk_level = "medium"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    service = EmailTemplateService(user_context=user_context, db_connection=db_connection)
    created = await service.create_email_template(body)
    request.state.audit_requested_id = str(created.get("id", "")) if created else ""
    request.state.raw_audit_new_data = created

    return success_response(
        request=request,
        message_key="email_templates.success.template_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("list email templates")
@router.get(
    "",
    status_code=http_status.HTTP_200_OK,
    description="List email templates for the authenticated organization",
    summary="List email templates",
)
@limiter.limit("100/minute")
async def list_email_templates(
    request: Request,
    template_type: EmailTemplateType | None = Query(None, description="Filter by template type"),
    status: EmailTemplateStatus | None = Query(None, description="Filter by status"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List email template summaries."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=EMAIL_TEMPLATES_MANAGEMENT_VIEW,
    )

    service = EmailTemplateService(user_context=user_context, db_connection=db_connection)
    items, total = await service.list_email_templates(
        template_type=template_type,
        status=status.value if status else None,
    )

    if not items:
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
        items=items,
        total=total,
        message_key="email_templates.success.templates_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("get email template")
@router.get(
    "/{template_id}",
    status_code=http_status.HTTP_200_OK,
    description="Get email template details by id",
    summary="Get email template",
)
@limiter.limit("100/minute")
async def get_email_template(
    request: Request,
    template_id: str = Path(..., description="Email template ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get full email template detail including variables."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=EMAIL_TEMPLATES_MANAGEMENT_VIEW,
    )

    service = EmailTemplateService(user_context=user_context, db_connection=db_connection)
    data = await service.get_email_template(template_id)

    return success_response(
        request=request,
        message_key="email_templates.success.template_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data,
    )


@handle_api_exceptions("render email template")
@router.post(
    "/{template_id}/render",
    status_code=http_status.HTTP_200_OK,
    description="Render template HTML with runtime variable values (does not send email)",
    summary="Render email template",
)
@limiter.limit("100/minute")
async def render_email_template(
    request: Request,
    template_id: str = Path(..., description="Email template ID (usually a TRIGGER)"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    body: RenderEmailTemplateRequest = Body(...),
):
    """Produce final HTML by merging layout + body and substituting {{.variable}} placeholders."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=EMAIL_TEMPLATES_MANAGEMENT_VIEW,
    )

    service = EmailTemplateService(user_context=user_context, db_connection=db_connection)
    data = await service.render_email_template(template_id, body)

    return success_response(
        request=request,
        message_key="email_templates.success.template_rendered",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data,
    )


@handle_api_exceptions("update email template")
@router.patch(
    "/{template_id}",
    status_code=http_status.HTTP_200_OK,
    description="Update an email template (partial)",
    summary="Update email template",
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=["soc2_audit", "audit_required"],
    table_name="email_templates",
    category="EMAIL_TEMPLATE",
)
async def update_email_template(
    request: Request,
    template_id: str = Path(..., description="Email template ID"),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: UpdateEmailTemplateRequest = Body(...),
):
    """Update an email template for the authenticated organization."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=EMAIL_TEMPLATES_MANAGEMENT_EDIT,
    )

    request.state.audit_table = "email_templates"
    request.state.audit_requested_id = template_id
    request.state.audit_description = f"Updated email template: {template_id}"
    request.state.audit_risk_level = "medium"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    service = EmailTemplateService(user_context=user_context, db_connection=db_connection)
    previous, updated = await service.update_email_template(template_id, body)
    request.state.raw_audit_old_data = previous
    request.state.raw_audit_new_data = updated

    return success_response(
        request=request,
        message_key="email_templates.success.template_updated",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("delete email template")
@router.delete(
    "/{template_id}",
    status_code=http_status.HTTP_200_OK,
    description="Delete an email template",
    summary="Delete email template",
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="confidential",
    compliance_tags=["soc2_audit", "audit_required"],
    table_name="email_templates",
    category="EMAIL_TEMPLATE",
)
async def delete_email_template(
    request: Request,
    template_id: str = Path(..., description="Email template ID"),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete an email template (default layout cannot be deleted)."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=EMAIL_TEMPLATES_MANAGEMENT_DELETE,
    )

    request.state.audit_table = "email_templates"
    request.state.audit_requested_id = template_id
    request.state.audit_description = f"Deleted email template: {template_id}"
    request.state.audit_risk_level = "high"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    service = EmailTemplateService(user_context=user_context, db_connection=db_connection)
    deleted = await service.delete_email_template(template_id)
    request.state.raw_audit_old_data = deleted

    return success_response(
        request=request,
        message_key="email_templates.success.template_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )
