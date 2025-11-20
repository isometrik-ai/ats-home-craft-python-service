"""Presigned URL schemas for S3/R2 upload operations."""

from pydantic import BaseModel, Field, ConfigDict


class PresignedUrlRequest(BaseModel):
    """Request model for generating presigned URL."""

    file_name: str = Field(..., description="Name of the file to upload")
    path: str = Field(..., description="Path prefix for the file (e.g., 'user-id' or 'org-id/user-id')")
    bucket: str = Field(..., description="Bucket name")
    content_type: str = Field(..., description="Content type of the file (e.g., 'image/jpeg', 'application/pdf')")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "file_name": "document.pdf",
                "path": "user-123",
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
                "url": "https://<ACCOUNT_ID>.r2.cloudflarestorage.com/your-bucket-name/user-123/document.pdf?X-Amz-Algorithm=...",
                "fileName": "document.pdf",
                "bucket": "my-bucket",
            }
        }
    )

