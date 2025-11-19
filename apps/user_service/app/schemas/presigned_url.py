"""Presigned URL schemas for S3/R2 upload operations."""

from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class PresignedUrlRequest(BaseModel):
    """Request model for generating presigned URL."""

    fileName: str = Field(..., description="Name of the file to upload")
    bucket: Optional[str] = Field(
        default=None, description="Bucket name (uses default from env if not provided)"
    )
    content_type: Optional[str] = Field(
        default=None, description="Content type of the file (e.g., 'image/jpeg', 'application/pdf')"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "fileName": "document.pdf",
                "bucket": "my-bucket",
                "content_type": "application/pdf",
            }
        }
    )


class PresignedUrlResponse(BaseModel):
    """Response model for presigned URL generation."""

    url: str = Field(..., description="Presigned URL for file upload")
    fileName: str = Field(..., description="Name of the file")
    bucket: str = Field(..., description="Bucket name used for upload")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "url": "https://<ACCOUNT_ID>.r2.cloudflarestorage.com/your-bucket-name/document.pdf?X-Amz-Algorithm=...",
                "fileName": "document.pdf",
                "bucket": "my-bucket",
            }
        }
    )

