# pylint: disable=all

"""
Test cases for presigned URL API endpoints.
Tests the GET /upload/presigned-url endpoint for generating presigned URLs for Cloudflare R2 uploads.
"""

import pytest
import uuid
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI, HTTPException

# Handle botocore imports - it's a dependency of boto3
try:
    from botocore.exceptions import ClientError
    from botocore.config import Config
except ImportError:
    # Create mock classes if botocore is not available
    class ClientError(Exception):
        """Mock ClientError for testing when botocore is not available."""
        def __init__(self, error_response, operation_name):
            self.response = error_response
            self.operation_name = operation_name
            super().__init__(f"{operation_name}: {error_response}")
    
    class Config:
        """Mock Config for testing when botocore is not available."""
        def __init__(self, **kwargs):
            self.signature_version = kwargs.get("signature_version", "v4")

from apps.user_service.app.api.presigned_url import router as presigned_url_router
from libs.shared_middleware.jwt_auth import get_user_from_auth


@pytest.fixture
def app():
    """Create FastAPI app with presigned URL router for testing."""
    app = FastAPI()
    app.include_router(presigned_url_router)

    # Mock authentication dependency
    def mock_get_user_from_auth():
        return {
            "sub": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "organization_id": str(uuid.uuid4()),
            "email": "test@example.com",
        }

    app.dependency_overrides[get_user_from_auth] = mock_get_user_from_auth
    return app


@pytest.fixture
def client(app):
    """Test client for presigned URL endpoints."""
    return TestClient(app)


@pytest.fixture
def mock_r2_credentials():
    """Mock R2 environment variables."""
    with patch("apps.user_service.app.api.presigned_url.R2_ACCESS_KEY", "test-access-key"), \
         patch("apps.user_service.app.api.presigned_url.R2_SECRET_KEY", "test-secret-key"), \
         patch("apps.user_service.app.api.presigned_url.R2_ACCOUNT_ID", "test-account-id"), \
         patch("apps.user_service.app.api.presigned_url.R2_BUCKET", "test-bucket"):
        yield


@pytest.fixture
def mock_s3_client():
    """Mock boto3 S3 client."""
    mock_client = MagicMock()
    mock_client.generate_presigned_url.return_value = (
        "https://test-account-id.r2.cloudflarestorage.com/test-bucket/test-file.pdf?"
        "X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=test-access-key"
    )
    return mock_client


# ============================================================================
# SUCCESSFUL SCENARIOS
# ============================================================================


def test_get_presigned_url_success_with_all_params(client, mock_r2_credentials, mock_s3_client):
    """Test successful presigned URL generation with all parameters."""
    with patch("apps.user_service.app.api.presigned_url.boto3.client", return_value=mock_s3_client):
        response = client.get(
            "/upload/presigned-url",
            params={
                "fileName": "test-file.pdf",
                "bucket": "custom-bucket",
                "content_type": "application/pdf",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "url" in data
        assert data["fileName"] == "test-file.pdf"
        assert data["bucket"] == "custom-bucket"
        assert isinstance(data["url"], str)
        assert len(data["url"]) > 0

        # Verify boto3 client was called correctly
        mock_s3_client.generate_presigned_url.assert_called_once()
        call_args = mock_s3_client.generate_presigned_url.call_args
        assert call_args[0][0] == "put_object"
        assert call_args[1]["Params"]["Bucket"] == "custom-bucket"
        assert call_args[1]["Params"]["Key"] == "test-file.pdf"
        assert call_args[1]["Params"]["ContentType"] == "application/pdf"
        assert call_args[1]["ExpiresIn"] == 300  # 5 minutes


def test_get_presigned_url_success_with_default_bucket(client, mock_r2_credentials, mock_s3_client):
    """Test successful presigned URL generation with default bucket from env."""
    with patch("apps.user_service.app.api.presigned_url.boto3.client", return_value=mock_s3_client):
        response = client.get(
            "/upload/presigned-url",
            params={"fileName": "test-file.pdf"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "url" in data
        assert data["fileName"] == "test-file.pdf"
        assert data["bucket"] == "test-bucket"  # From env var

        # Verify boto3 client was called with default bucket
        call_args = mock_s3_client.generate_presigned_url.call_args
        assert call_args[1]["Params"]["Bucket"] == "test-bucket"
        assert call_args[1]["Params"]["Key"] == "test-file.pdf"
        assert "ContentType" not in call_args[1]["Params"]


def test_get_presigned_url_success_with_content_type(client, mock_r2_credentials, mock_s3_client):
    """Test successful presigned URL generation with content type."""
    with patch("apps.user_service.app.api.presigned_url.boto3.client", return_value=mock_s3_client):
        response = client.get(
            "/upload/presigned-url",
            params={
                "fileName": "image.jpg",
                "content_type": "image/jpeg",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["fileName"] == "image.jpg"
        assert data["bucket"] == "test-bucket"

        # Verify content type was passed
        call_args = mock_s3_client.generate_presigned_url.call_args
        assert call_args[1]["Params"]["ContentType"] == "image/jpeg"


def test_get_presigned_url_success_with_custom_bucket(client, mock_r2_credentials, mock_s3_client):
    """Test successful presigned URL generation with custom bucket parameter."""
    with patch("apps.user_service.app.api.presigned_url.boto3.client", return_value=mock_s3_client):
        response = client.get(
            "/upload/presigned-url",
            params={
                "fileName": "document.docx",
                "bucket": "my-custom-bucket",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["bucket"] == "my-custom-bucket"

        # Verify custom bucket was used
        call_args = mock_s3_client.generate_presigned_url.call_args
        assert call_args[1]["Params"]["Bucket"] == "my-custom-bucket"


# ============================================================================
# ERROR SCENARIOS - VALIDATION ERRORS
# ============================================================================


def test_get_presigned_url_missing_file_name(client, mock_r2_credentials):
    """Test presigned URL generation fails when fileName is missing."""
    response = client.get("/upload/presigned-url")

    assert response.status_code == 422  # FastAPI validation error
    data = response.json()
    assert "detail" in data


def test_get_presigned_url_empty_file_name(client, mock_r2_credentials):
    """Test presigned URL generation fails when fileName is empty."""
    response = client.get("/upload/presigned-url", params={"fileName": ""})

    assert response.status_code == 400
    data = response.json()
    assert "fileName is required" in data["detail"]


def test_get_presigned_url_missing_bucket_no_env(client, mock_s3_client):
    """Test presigned URL generation fails when bucket is missing and R2_BUCKET not set."""
    with patch("apps.user_service.app.api.presigned_url.R2_ACCESS_KEY", "test-access-key"), \
         patch("apps.user_service.app.api.presigned_url.R2_SECRET_KEY", "test-secret-key"), \
         patch("apps.user_service.app.api.presigned_url.R2_ACCOUNT_ID", "test-account-id"), \
         patch("apps.user_service.app.api.presigned_url.R2_BUCKET", None), \
         patch("apps.user_service.app.api.presigned_url.boto3.client", return_value=mock_s3_client):
        response = client.get("/upload/presigned-url", params={"fileName": "test.pdf"})

        assert response.status_code == 400
        data = response.json()
        assert "Bucket name is required" in data["detail"]


# ============================================================================
# ERROR SCENARIOS - CONFIGURATION ERRORS
# ============================================================================


def test_get_presigned_url_missing_r2_access_key(client):
    """Test presigned URL generation fails when R2_ACCESS_KEY is missing."""
    with patch("apps.user_service.app.api.presigned_url.R2_ACCESS_KEY", None), \
         patch("apps.user_service.app.api.presigned_url.R2_SECRET_KEY", "test-secret-key"), \
         patch("apps.user_service.app.api.presigned_url.R2_ACCOUNT_ID", "test-account-id"), \
         patch("apps.user_service.app.api.presigned_url.R2_BUCKET", "test-bucket"):
        response = client.get("/upload/presigned-url", params={"fileName": "test.pdf"})

        assert response.status_code == 500
        data = response.json()
        assert "R2 credentials not configured" in data["detail"]


def test_get_presigned_url_missing_r2_secret_key(client):
    """Test presigned URL generation fails when R2_SECRET_KEY is missing."""
    with patch("apps.user_service.app.api.presigned_url.R2_ACCESS_KEY", "test-access-key"), \
         patch("apps.user_service.app.api.presigned_url.R2_SECRET_KEY", None), \
         patch("apps.user_service.app.api.presigned_url.R2_ACCOUNT_ID", "test-account-id"), \
         patch("apps.user_service.app.api.presigned_url.R2_BUCKET", "test-bucket"):
        response = client.get("/upload/presigned-url", params={"fileName": "test.pdf"})

        assert response.status_code == 500
        data = response.json()
        assert "R2 credentials not configured" in data["detail"]


def test_get_presigned_url_missing_r2_account_id(client):
    """Test presigned URL generation fails when R2_ACCOUNT_ID is missing."""
    with patch("apps.user_service.app.api.presigned_url.R2_ACCESS_KEY", "test-access-key"), \
         patch("apps.user_service.app.api.presigned_url.R2_SECRET_KEY", "test-secret-key"), \
         patch("apps.user_service.app.api.presigned_url.R2_ACCOUNT_ID", None), \
         patch("apps.user_service.app.api.presigned_url.R2_BUCKET", "test-bucket"):
        response = client.get("/upload/presigned-url", params={"fileName": "test.pdf"})

        assert response.status_code == 500
        data = response.json()
        assert "R2 credentials not configured" in data["detail"]


# ============================================================================
# ERROR SCENARIOS - BOTO3 CLIENT ERRORS
# ============================================================================


def test_get_presigned_url_boto3_client_error(client, mock_r2_credentials, mock_s3_client):
    """Test presigned URL generation fails when boto3 raises ClientError."""
    # Mock ClientError from boto3
    error_response = {"Error": {"Code": "AccessDenied", "Message": "Access denied"}}
    mock_s3_client.generate_presigned_url.side_effect = ClientError(
        error_response, "generate_presigned_url"
    )

    with patch("apps.user_service.app.api.presigned_url.boto3.client", return_value=mock_s3_client):
        response = client.get("/upload/presigned-url", params={"fileName": "test.pdf"})

        assert response.status_code == 500
        data = response.json()
        assert "Failed to generate presigned URL" in data["detail"]


def test_get_presigned_url_boto3_unexpected_error(client, mock_r2_credentials, mock_s3_client):
    """Test presigned URL generation handles unexpected boto3 errors."""
    # Mock unexpected exception
    mock_s3_client.generate_presigned_url.side_effect = Exception("Unexpected error")

    with patch("apps.user_service.app.api.presigned_url.boto3.client", return_value=mock_s3_client):
        response = client.get("/upload/presigned-url", params={"fileName": "test.pdf"})

        assert response.status_code == 500
        data = response.json()
        assert "An unexpected error occurred" in data["detail"]


# ============================================================================
# EDGE CASES
# ============================================================================


def test_get_presigned_url_special_characters_in_filename(client, mock_r2_credentials, mock_s3_client):
    """Test presigned URL generation with special characters in filename."""
    with patch("apps.user_service.app.api.presigned_url.boto3.client", return_value=mock_s3_client):
        response = client.get(
            "/upload/presigned-url",
            params={"fileName": "test file (1).pdf"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["fileName"] == "test file (1).pdf"

        # Verify filename was passed as-is to boto3
        call_args = mock_s3_client.generate_presigned_url.call_args
        assert call_args[1]["Params"]["Key"] == "test file (1).pdf"


def test_get_presigned_url_long_filename(client, mock_r2_credentials, mock_s3_client):
    """Test presigned URL generation with long filename."""
    long_filename = "a" * 200 + ".pdf"
    with patch("apps.user_service.app.api.presigned_url.boto3.client", return_value=mock_s3_client):
        response = client.get(
            "/upload/presigned-url",
            params={"fileName": long_filename},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["fileName"] == long_filename


def test_get_presigned_url_various_content_types(client, mock_r2_credentials, mock_s3_client):
    """Test presigned URL generation with various content types."""
    content_types = [
        "image/png",
        "image/jpeg",
        "application/json",
        "text/plain",
        "video/mp4",
    ]

    with patch("apps.user_service.app.api.presigned_url.boto3.client", return_value=mock_s3_client):
        for content_type in content_types:
            response = client.get(
                "/upload/presigned-url",
                params={
                    "fileName": f"test.{content_type.split('/')[1]}",
                    "content_type": content_type,
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert data["fileName"] == f"test.{content_type.split('/')[1]}"

            # Verify content type was passed correctly
            call_args = mock_s3_client.generate_presigned_url.call_args
            assert call_args[1]["Params"]["ContentType"] == content_type


# ============================================================================
# BOTO3 CLIENT CONFIGURATION TESTS
# ============================================================================


def test_get_r2_client_configuration():
    """Test that R2 client is configured correctly."""
    from apps.user_service.app.api.presigned_url import get_r2_client
    # Config is imported at the top of the file

    with patch("apps.user_service.app.api.presigned_url.R2_ACCESS_KEY", "test-access-key"), \
         patch("apps.user_service.app.api.presigned_url.R2_SECRET_KEY", "test-secret-key"), \
         patch("apps.user_service.app.api.presigned_url.R2_ACCOUNT_ID", "test-account-id"), \
         patch("apps.user_service.app.api.presigned_url.R2_BUCKET", "test-bucket"), \
         patch("boto3.client") as mock_boto3_client:
        mock_client_instance = MagicMock()
        mock_boto3_client.return_value = mock_client_instance

        client = get_r2_client()

        # Verify boto3.client was called with correct parameters
        mock_boto3_client.assert_called_once()
        call_kwargs = mock_boto3_client.call_args[1]

        assert call_kwargs["endpoint_url"] == "https://test-account-id.r2.cloudflarestorage.com"
        assert call_kwargs["aws_access_key_id"] == "test-access-key"
        assert call_kwargs["aws_secret_access_key"] == "test-secret-key"
        assert call_kwargs["region_name"] == "auto"
        assert isinstance(call_kwargs["config"], Config)
        assert call_kwargs["config"].signature_version == "v4"


def test_get_r2_client_missing_credentials():
    """Test that get_r2_client raises HTTPException when credentials are missing."""
    from apps.user_service.app.api.presigned_url import get_r2_client

    with patch("apps.user_service.app.api.presigned_url.R2_ACCESS_KEY", None), \
         patch("apps.user_service.app.api.presigned_url.R2_SECRET_KEY", None), \
         patch("apps.user_service.app.api.presigned_url.R2_ACCOUNT_ID", None):
        with pytest.raises(HTTPException) as exc_info:
            get_r2_client()

        assert exc_info.value.status_code == 500
        assert "R2 credentials not configured" in exc_info.value.detail

