"""Presigned URL API for invoice and work-order file uploads."""

import boto3
from botocore.config import Config
from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.schemas.presigned_url import PresignedUrlResponse
from apps.user_service.app.utils.common_utils import (
    ensure_staff_project_access,
    handle_api_exceptions,
)
from apps.work_order_service.app.api._helpers import ok_response
from apps.work_order_service.app.app_instance import limiter
from apps.work_order_service.app.config.app_settings import app_settings
from apps.work_order_service.app.dependencies.db import db_conn
from apps.work_order_service.app.schemas.openapi import PresignedUrlApiResponse
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import WORK_ORDER_MANAGEMENT_EDIT
from libs.shared_utils.http_exceptions import InternalServerErrorException
from libs.shared_utils.response_factory import success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/projects", tags=["Work Order — Upload"])

_R2 = app_settings.shared_settings.cloudflare_r2


def _get_r2_client():
    """Get r2 client."""
    if not _R2.access_key or not _R2.secret_key or not _R2.account_id:
        raise InternalServerErrorException(
            message_key="presigned_url.errors.r2_credentials_not_configured",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        )
    endpoint = f"https://{_R2.account_id}.r2.cloudflarestorage.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=_R2.access_key,
        aws_secret_access_key=_R2.secret_key,
        config=Config(signature_version="v4"),
        region_name="auto",
    )


@handle_api_exceptions("generate presigned URL")
@router.get(
    "/{project_id}/upload/presigned-url",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=ok_response(PresignedUrlApiResponse, "Presigned upload URL."),
)
@limiter.limit("100/minute")
async def get_presigned_url(
    request: Request,
    project_id: str = Path(...),
    file_name: str = Query(...),
    path: str = Query(..., description="Path prefix, e.g. org-id/project-id/invoices"),
    bucket: str = Query(...),
    content_type: str = Query(...),
    db_connection=Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get presigned url."""
    await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_EDIT,
        request=request,
    )
    s3_client = _get_r2_client()
    path_clean = path.strip("/")
    file_key = f"{path_clean}/{file_name}" if path_clean else file_name
    presigned_url = s3_client.generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket, "Key": file_key, "ContentType": content_type},
        ExpiresIn=60 * 5,
    )
    return success_response(
        request=request,
        message_key="presigned_url.success.presigned_url_generated",
        custom_code=CustomStatusCode.SUCCESS,
        data=PresignedUrlResponse(url=presigned_url, fileName=file_name, bucket=bucket),
    )
