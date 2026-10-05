"""Internal ops endpoint for the daily fee invoice run."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.jobs.issue_due_fee_invoices import run_due_fee_invoices
from apps.user_service.app.utils.common_utils import handle_api_exceptions
from libs.shared_utils.response_factory import success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/internal/fee-billing", tags=["Fee billing (Internal)"])


@handle_api_exceptions("issue due fee invoices")
@router.post(
    "/issue-due",
    status_code=http_status.HTTP_200_OK,
    summary="Issue maintenance and club invoices due today (cron)",
)
@limiter.limit("10/minute")
async def issue_due_fee_invoices_internal(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Queue today's invoice run. Billing and mail happen after this response."""
    background_tasks.add_task(run_due_fee_invoices)
    return success_response(
        request=request,
        message_key="fee_billing.success.issue_due_started",
        custom_code=CustomStatusCode.SUCCESS,
        data={"accepted": True},
    )
