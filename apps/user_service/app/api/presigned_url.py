"""Presigned URL API Module
This module provides API endpoints for generating presigned URLs
for Cloudflare R2 (S3-compatible) file uploads."""

import os
import sys

import boto3
from botocore.config import Config
from fastapi import APIRouter, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.logger import get_logger
from apps.user_service.app.schemas.presigned_url import (
    PresignedUrlResponse,
)
from apps.user_service.app.utils.common_utils import handle_api_exceptions
from libs.shared_utils.http_exceptions import InternalServerErrorException
from libs.shared_utils.response_factory import success_response
from libs.shared_utils.status_codes import CustomStatusCode

base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, base_path)

monorepo_root = os.path.abspath(os.path.join(base_path, "../../.."))
sys.path.insert(0, monorepo_root)

router = APIRouter(prefix="/upload", tags=["Upload"])

logger = get_logger(__name__)

R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY")
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_BUCKET = os.getenv("R2_BUCKET")


def get_r2_client():
    """Create and return an S3-compatible client for Cloudflare R2."""
    if not R2_ACCESS_KEY or not R2_SECRET_KEY or not R2_ACCOUNT_ID:
        raise InternalServerErrorException(
            message_key="presigned_url.errors.r2_credentials_not_configured",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        )

    endpoint = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

    # Configure boto3 client for R2
    s3_client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        config=Config(signature_version="v4"),
        region_name="auto",
    )

    return s3_client


@router.get(
    "/presigned-url",
    response_model=PresignedUrlResponse,
    status_code=http_status.HTTP_200_OK,
    summary="Generate Presigned URL for File Upload",
    description="Generate a presigned URL for uploading files to Cloudflare R2 bucket.",
)
@handle_api_exceptions("generate presigned URL")
@limiter.limit("100/minute")
async def get_presigned_url(
    request: Request,
    file_name: str = Query(..., description="Name of the file to upload"),
    path: str = Query(
        ..., description="Path prefix for the file (e.g., 'user-id' or 'org-id/user-id')"
    ),
    bucket: str = Query(..., description="Bucket name"),
    content_type: str = Query(
        ..., description="Content type of the file (e.g., 'image/jpeg', 'application/pdf')"
    ),
):
    """Generate a presigned URL for file upload to Cloudflare R2."""
    s3_client = get_r2_client()

    path_clean = path.strip("/")
    file_key = f"{path_clean}/{file_name}" if path_clean else file_name

    params = {
        "Bucket": bucket,
        "Key": file_key,
        "ContentType": content_type,
    }

    presigned_url = s3_client.generate_presigned_url(
        "put_object",
        Params=params,
        ExpiresIn=60 * 5,
    )

    return success_response(
        request=request,
        message_key="presigned_url.success.presigned_url_generated",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=PresignedUrlResponse(
            url=presigned_url,
            fileName=file_name,
            bucket=bucket,
        ),
    )
