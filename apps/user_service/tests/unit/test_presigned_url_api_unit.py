"""Unit tests for presigned URL API helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from apps.user_service.app.api import presigned_url as presigned_url_module
from apps.user_service.app.api.presigned_url import get_r2_client
from libs.shared_utils.http_exceptions import InternalServerErrorException


def test_get_r2_client_raises_when_credentials_missing():
    """Missing R2 credentials should surface a configured error."""
    with patch.object(presigned_url_module, "R2_ACCESS_KEY", None):
        with pytest.raises(InternalServerErrorException):
            get_r2_client()


def test_get_r2_client_builds_boto3_client():
    """Configured credentials should create an S3 client against the R2 endpoint."""
    fake_client = MagicMock()
    with (
        patch.object(presigned_url_module, "R2_ACCESS_KEY", "key"),
        patch.object(presigned_url_module, "R2_SECRET_KEY", "secret"),
        patch.object(presigned_url_module, "R2_ACCOUNT_ID", "acct"),
        patch(
            "apps.user_service.app.api.presigned_url.boto3.client", return_value=fake_client
        ) as mock_client,
    ):
        client = get_r2_client()

    assert client is fake_client
    mock_client.assert_called_once()
    kwargs = mock_client.call_args.kwargs
    assert kwargs["endpoint_url"] == "https://acct.r2.cloudflarestorage.com"
    assert kwargs["aws_access_key_id"] == "key"
    assert kwargs["aws_secret_access_key"] == "secret"
    assert kwargs["region_name"] == "auto"
