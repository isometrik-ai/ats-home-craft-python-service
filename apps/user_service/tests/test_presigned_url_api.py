"""Test cases for presigned URL API endpoints.

Tests the GET /upload/presigned-url endpoint for generating presigned URLs
for Cloudflare R2 uploads.
"""

import sys
import uuid
from unittest.mock import MagicMock, patch

# Now import other modules
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

# Import the router after mocking boto3
from apps.user_service.app.api.presigned_url import router as presigned_url_router
from libs.shared_middleware.jwt_auth import get_user_from_auth


# Mock boto3 and botocore in sys.modules BEFORE any other imports
# This prevents ImportError when presigned_url module tries to import boto3
class MockClientError(Exception):
    """Mock ClientError for testing."""

    def __init__(self, error_response, operation_name):
        self.response = error_response
        self.operation_name = operation_name
        super().__init__(f"{operation_name}: {error_response}")


class MockConfig:
    """Mock Config for testing."""

    def __init__(self, **kwargs):
        self.signature_version = kwargs.get("signature_version", "v4")


# Create mock boto3 module with proper structure
class MockBoto3Module:
    """Mock boto3 module."""

    @staticmethod
    def client(*_args, **_kwargs):
        """Mock boto3.client function."""
        mock_client = MagicMock()
        mock_client.generate_presigned_url = MagicMock(return_value="https://mock-url.com")
        return mock_client


mock_boto3_module = MockBoto3Module()


# Create mock botocore module with proper structure
class MockBotocoreExceptions:
    """Mock botocore.exceptions module."""

    ClientError = MockClientError


class MockBotocoreConfig:
    """Mock botocore.config module."""

    Config = MockConfig


class MockBotocoreModule:
    """Mock botocore module."""

    def __init__(self):
        self.exceptions = MockBotocoreExceptions()
        self.config = MockBotocoreConfig()


mock_botocore_module = MockBotocoreModule()

# Inject mocks into sys.modules BEFORE any imports that might use them
sys.modules["boto3"] = mock_boto3_module
sys.modules["botocore"] = mock_botocore_module
sys.modules["botocore.exceptions"] = mock_botocore_module.exceptions
sys.modules["botocore.config"] = mock_botocore_module.config


# Use actual classes if available, otherwise use mocks
try:
    from botocore.config import Config
    from botocore.exceptions import ClientError
except ImportError:
    ClientError = MockClientError
    Config = MockConfig


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
    with (
        patch("apps.user_service.app.api.presigned_url.R2_ACCESS_KEY", "test-access-key"),
        patch("apps.user_service.app.api.presigned_url.R2_SECRET_KEY", "test-secret-key"),
        patch("apps.user_service.app.api.presigned_url.R2_ACCOUNT_ID", "test-account-id"),
        patch("apps.user_service.app.api.presigned_url.R2_BUCKET", "test-bucket"),
    ):
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


def test_get_presigned_url_success_with_all_params(client, mock_s3_client):
    """Test successful presigned URL generation with all parameters."""
    with patch(
        "apps.user_service.app.api.presigned_url.boto3.client",
        return_value=mock_s3_client,
    ):
        response = client.get(
            "/upload/presigned-url",
            params={
                "file_name": "test-file.pdf",
                "path": "user-123",
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
        assert call_args[1]["Params"]["Key"] == "user-123/test-file.pdf"
        assert call_args[1]["Params"]["ContentType"] == "application/pdf"
        assert call_args[1]["ExpiresIn"] == 300  # 5 minutes


def test_get_presigned_url_success_with_default_bucket(client, mock_s3_client):
    """Test successful presigned URL generation with all required parameters."""
    with patch(
        "apps.user_service.app.api.presigned_url.boto3.client",
        return_value=mock_s3_client,
    ):
        response = client.get(
            "/upload/presigned-url",
            params={
                "file_name": "test-file.pdf",
                "path": "user-123",
                "bucket": "test-bucket",
                "content_type": "application/pdf",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "url" in data
        assert data["fileName"] == "test-file.pdf"
        assert data["bucket"] == "test-bucket"

        # Verify boto3 client was called with all parameters
        call_args = mock_s3_client.generate_presigned_url.call_args
        assert call_args[1]["Params"]["Bucket"] == "test-bucket"
        assert call_args[1]["Params"]["Key"] == "user-123/test-file.pdf"
        assert call_args[1]["Params"]["ContentType"] == "application/pdf"


def test_get_presigned_url_success_with_content_type(client, mock_s3_client):
    """Test successful presigned URL generation with content type."""
    with patch(
        "apps.user_service.app.api.presigned_url.boto3.client",
        return_value=mock_s3_client,
    ):
        response = client.get(
            "/upload/presigned-url",
            params={
                "file_name": "image.jpg",
                "path": "user-456",
                "bucket": "test-bucket",
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
        assert call_args[1]["Params"]["Key"] == "user-456/image.jpg"


def test_get_presigned_url_success_with_custom_bucket(client, mock_s3_client):
    """Test successful presigned URL generation with custom bucket parameter."""
    with patch(
        "apps.user_service.app.api.presigned_url.boto3.client",
        return_value=mock_s3_client,
    ):
        response = client.get(
            "/upload/presigned-url",
            params={
                "file_name": "document.docx",
                "path": "org-789/user-123",
                "bucket": "my-custom-bucket",
                "content_type": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["bucket"] == "my-custom-bucket"

        # Verify custom bucket was used
        call_args = mock_s3_client.generate_presigned_url.call_args
        assert call_args[1]["Params"]["Bucket"] == "my-custom-bucket"
        assert call_args[1]["Params"]["Key"] == "org-789/user-123/document.docx"


# ============================================================================
# ERROR SCENARIOS - VALIDATION ERRORS
# ============================================================================


def test_get_presigned_url_missing_file_name(client):
    """Test presigned URL generation fails when fileName is missing."""
    response = client.get("/upload/presigned-url")

    assert response.status_code == 422  # FastAPI validation error
    data = response.json()
    assert "detail" in data


def test_get_presigned_url_empty_file_name(client):
    """Test presigned URL generation fails when file_name is empty."""
    response = client.get(
        "/upload/presigned-url",
        params={
            "file_name": "",
            "path": "user-123",
            "bucket": "test-bucket",
            "content_type": "application/pdf",
        },
    )

    assert response.status_code == 400
    data = response.json()
    assert "file_name is required" in data["detail"]


def test_get_presigned_url_missing_bucket_no_env(client, mock_s3_client):
    """Test presigned URL generation fails when bucket is missing."""
    with (
        patch("apps.user_service.app.api.presigned_url.R2_ACCESS_KEY", "test-access-key"),
        patch("apps.user_service.app.api.presigned_url.R2_SECRET_KEY", "test-secret-key"),
        patch("apps.user_service.app.api.presigned_url.R2_ACCOUNT_ID", "test-account-id"),
        patch("apps.user_service.app.api.presigned_url.R2_BUCKET", None),
        patch(
            "apps.user_service.app.api.presigned_url.boto3.client",
            return_value=mock_s3_client,
        ),
    ):
        response = client.get(
            "/upload/presigned-url",
            params={
                "file_name": "test.pdf",
                "path": "user-123",
                "content_type": "application/pdf",
            },
        )

        assert response.status_code == 422  # FastAPI validation error for missing required field


# ============================================================================
# ERROR SCENARIOS - CONFIGURATION ERRORS
# ============================================================================


def test_get_presigned_url_missing_r2_access_key(client):
    """Test presigned URL generation fails when R2_ACCESS_KEY is missing."""
    with (
        patch("apps.user_service.app.api.presigned_url.R2_ACCESS_KEY", None),
        patch("apps.user_service.app.api.presigned_url.R2_SECRET_KEY", "test-secret-key"),
        patch("apps.user_service.app.api.presigned_url.R2_ACCOUNT_ID", "test-account-id"),
        patch("apps.user_service.app.api.presigned_url.R2_BUCKET", "test-bucket"),
    ):
        response = client.get(
            "/upload/presigned-url",
            params={
                "file_name": "test.pdf",
                "path": "user-123",
                "bucket": "test-bucket",
                "content_type": "application/pdf",
            },
        )

        assert response.status_code == 500
        data = response.json()
        assert "R2 credentials not configured" in data["detail"]


def test_get_presigned_url_missing_r2_secret_key(client):
    """Test presigned URL generation fails when R2_SECRET_KEY is missing."""
    with (
        patch("apps.user_service.app.api.presigned_url.R2_ACCESS_KEY", "test-access-key"),
        patch("apps.user_service.app.api.presigned_url.R2_SECRET_KEY", None),
        patch("apps.user_service.app.api.presigned_url.R2_ACCOUNT_ID", "test-account-id"),
        patch("apps.user_service.app.api.presigned_url.R2_BUCKET", "test-bucket"),
    ):
        response = client.get(
            "/upload/presigned-url",
            params={
                "file_name": "test.pdf",
                "path": "user-123",
                "bucket": "test-bucket",
                "content_type": "application/pdf",
            },
        )

        assert response.status_code == 500
        data = response.json()
        assert "R2 credentials not configured" in data["detail"]


def test_get_presigned_url_missing_r2_account_id(client):
    """Test presigned URL generation fails when R2_ACCOUNT_ID is missing."""
    with (
        patch("apps.user_service.app.api.presigned_url.R2_ACCESS_KEY", "test-access-key"),
        patch("apps.user_service.app.api.presigned_url.R2_SECRET_KEY", "test-secret-key"),
        patch("apps.user_service.app.api.presigned_url.R2_ACCOUNT_ID", None),
        patch("apps.user_service.app.api.presigned_url.R2_BUCKET", "test-bucket"),
    ):
        response = client.get(
            "/upload/presigned-url",
            params={
                "file_name": "test.pdf",
                "path": "user-123",
                "bucket": "test-bucket",
                "content_type": "application/pdf",
            },
        )

        assert response.status_code == 500
        data = response.json()
        assert "R2 credentials not configured" in data["detail"]


# ============================================================================
# ERROR SCENARIOS - BOTO3 CLIENT ERRORS
# ============================================================================


def test_get_presigned_url_boto3_client_error(client, mock_s3_client):
    """Test presigned URL generation fails when boto3 raises ClientError."""
    # Mock ClientError from boto3
    error_response = {"Error": {"Code": "AccessDenied", "Message": "Access denied"}}
    mock_s3_client.generate_presigned_url.side_effect = ClientError(
        error_response, "generate_presigned_url"
    )

    with patch(
        "apps.user_service.app.api.presigned_url.boto3.client",
        return_value=mock_s3_client,
    ):
        response = client.get(
            "/upload/presigned-url",
            params={
                "file_name": "test.pdf",
                "path": "user-123",
                "bucket": "test-bucket",
                "content_type": "application/pdf",
            },
        )

        assert response.status_code == 500
        data = response.json()
        assert "Failed to generate presigned URL" in data["detail"]


def test_get_presigned_url_boto3_unexpected_error(client, mock_s3_client):
    """Test presigned URL generation handles unexpected boto3 errors."""
    # Mock unexpected exception
    mock_s3_client.generate_presigned_url.side_effect = Exception("Unexpected error")

    with patch(
        "apps.user_service.app.api.presigned_url.boto3.client",
        return_value=mock_s3_client,
    ):
        response = client.get(
            "/upload/presigned-url",
            params={
                "file_name": "test.pdf",
                "path": "user-123",
                "bucket": "test-bucket",
                "content_type": "application/pdf",
            },
        )

        assert response.status_code == 500
        data = response.json()
        assert "An unexpected error occurred" in data["detail"]


# ============================================================================
# EDGE CASES
# ============================================================================


def test_get_presigned_url_special_chars(client, mock_s3_client):
    """Test presigned URL generation with special characters in filename."""
    with patch(
        "apps.user_service.app.api.presigned_url.boto3.client",
        return_value=mock_s3_client,
    ):
        response = client.get(
            "/upload/presigned-url",
            params={
                "file_name": "test file (1).pdf",
                "path": "user-123",
                "bucket": "test-bucket",
                "content_type": "application/pdf",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["fileName"] == "test file (1).pdf"

        # Verify filename was passed with path to boto3
        call_args = mock_s3_client.generate_presigned_url.call_args
        assert call_args[1]["Params"]["Key"] == "user-123/test file (1).pdf"


def test_get_presigned_url_long_filename(client, mock_s3_client):
    """Test presigned URL generation with long filename."""
    long_filename = "a" * 200 + ".pdf"
    with patch(
        "apps.user_service.app.api.presigned_url.boto3.client",
        return_value=mock_s3_client,
    ):
        response = client.get(
            "/upload/presigned-url",
            params={
                "file_name": long_filename,
                "path": "user-123",
                "bucket": "test-bucket",
                "content_type": "application/pdf",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["fileName"] == long_filename


def test_get_presigned_url_various_content_types(client, mock_s3_client):
    """Test presigned URL generation with various content types."""
    content_types = [
        "image/png",
        "image/jpeg",
        "application/json",
        "text/plain",
        "video/mp4",
    ]

    with patch(
        "apps.user_service.app.api.presigned_url.boto3.client",
        return_value=mock_s3_client,
    ):
        for content_type in content_types:
            response = client.get(
                "/upload/presigned-url",
                params={
                    "file_name": f"test.{content_type.split('/')[1]}",
                    "path": "user-123",
                    "bucket": "test-bucket",
                    "content_type": content_type,
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert data["fileName"] == f"test.{content_type.split('/')[1]}"

            # Verify content type was passed correctly
            call_args = mock_s3_client.generate_presigned_url.call_args
            assert call_args[1]["Params"]["ContentType"] == content_type
            assert call_args[1]["Params"]["Key"] == f"user-123/test.{content_type.split('/')[1]}"


# ============================================================================
# BOTO3 CLIENT CONFIGURATION TESTS
# ============================================================================


def test_get_r2_client_configuration():
    """Test that R2 client is configured correctly."""
    from apps.user_service.app.api.presigned_url import get_r2_client

    # Config is imported at the top of the file

    with (
        patch("apps.user_service.app.api.presigned_url.R2_ACCESS_KEY", "test-access-key"),
        patch("apps.user_service.app.api.presigned_url.R2_SECRET_KEY", "test-secret-key"),
        patch("apps.user_service.app.api.presigned_url.R2_ACCOUNT_ID", "test-account-id"),
        patch("apps.user_service.app.api.presigned_url.R2_BUCKET", "test-bucket"),
        patch("boto3.client") as mock_boto3_client,
    ):
        mock_client_instance = MagicMock()
        mock_boto3_client.return_value = mock_client_instance

        get_r2_client()

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

    with (
        patch("apps.user_service.app.api.presigned_url.R2_ACCESS_KEY", None),
        patch("apps.user_service.app.api.presigned_url.R2_SECRET_KEY", None),
        patch("apps.user_service.app.api.presigned_url.R2_ACCOUNT_ID", None),
    ):
        with pytest.raises(HTTPException) as exc_info:
            get_r2_client()

        assert exc_info.value.status_code == 500
        assert "R2 credentials not configured" in exc_info.value.detail
