"""
Presigned URL API Module

This module provides API endpoints for generating presigned URLs
for Cloudflare R2 (S3-compatible) file uploads.

Author: AI Assistant
Date: 2024-12-19
"""

# Standard library imports
import os
import sys

# Third-party imports
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, status, Request, Depends

# Internal utility imports
from apps.user_service.app.dependencies.common_utils import handle_api_exceptions

# Logger import
from apps.user_service.app.dependencies.logger import get_logger

# Schema imports
from apps.user_service.app.schemas.presigned_url import (
    PresignedUrlRequest,
    PresignedUrlResponse,
)

# Shared library imports
from libs.shared_middleware.jwt_auth import get_user_from_auth

# Modify sys.path to support monorepo imports
base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, base_path)

monorepo_root = os.path.abspath(os.path.join(base_path, "../../.."))
sys.path.insert(0, monorepo_root)

# Create router for presigned URL endpoints
router = APIRouter(prefix="/upload", tags=["Upload"])

# Initialize logger
logger = get_logger(__name__)

# Environment variables
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY")
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_BUCKET = os.getenv("R2_BUCKET")  # Default bucket name


def get_r2_client():
    """Create and return an S3-compatible client for Cloudflare R2."""
    if not R2_ACCESS_KEY or not R2_SECRET_KEY or not R2_ACCOUNT_ID:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="R2 credentials not configured. Please set R2_ACCESS_KEY, R2_SECRET_KEY, and R2_ACCOUNT_ID environment variables.",
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
    status_code=status.HTTP_200_OK,
    summary="Generate Presigned URL for File Upload",
    description="Generate a presigned URL for uploading files to Cloudflare R2 bucket.",
)
@handle_api_exceptions("generate presigned URL")
async def get_presigned_url(
    request: Request,
    file_name: str,
    path: str,
    bucket: str,
    content_type: str,
    current_user: dict = Depends(get_user_from_auth),
):
    """
    Generate a presigned URL for file upload to Cloudflare R2.

    Args:
        request: FastAPI request object
        file_name: Name of the file to upload
        path: Path prefix for the file (e.g., "user-id" or "org-id/user-id")
        bucket: Bucket name
        content_type: Content type of the file (e.g., "image/jpeg", "application/pdf")
        current_user: Authenticated user (from JWT token)

    Returns:
        PresignedUrlResponse: Contains the presigned URL, file_name, and bucket

    Raises:
        HTTPException: If R2 credentials are not configured or URL generation fails
    """
    try:
        # Validate required parameters
        if not file_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="file_name is required",
            )

        if not path:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="path is required",
            )

        if not bucket:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="bucket is required",
            )

        if not content_type:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="content_type is required",
            )

        # Get R2 client
        s3_client = get_r2_client()

        # Build file key using path
        # Remove leading/trailing slashes from path and join with file_name
        path_clean = path.strip("/")
        file_key = f"{path_clean}/{file_name}" if path_clean else file_name

        # Prepare parameters for presigned URL
        params = {
            "Bucket": bucket,
            "Key": file_key,
            "ContentType": content_type,
        }

        # Generate presigned URL (expires in 5 minutes)
        try:
            presigned_url = s3_client.generate_presigned_url(
                "put_object",
                Params=params,
                ExpiresIn=60 * 5,  # 5 minutes
            )
        except ClientError as e:
            logger.error(f"Error generating presigned URL: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to generate presigned URL: {str(e)}",
            ) from e

        logger.info(
            f"Presigned URL generated for user {current_user.get('sub')}, "
            f"file: {file_name}, path: {file_key}, bucket: {bucket}"
        )

        return PresignedUrlResponse(
            url=presigned_url,
            fileName=file_name,
            bucket=bucket,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error generating presigned URL: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred: {str(e)}",
        ) from e

