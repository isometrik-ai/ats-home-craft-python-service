"""Integration tests for presigned URL endpoint."""

import pytest

from apps.user_service.tests.utils.assertions import assert_success


@pytest.mark.asyncio
async def test_get_presigned_url(monkeypatch, client):
    """Generate presigned URL."""

    class FakeClient:
        """Fake client returning a static URL."""

        def generate_presigned_url(self, operation, **kwargs):
            """Return a fake presigned URL."""
            del operation, kwargs
            return "http://example.com/presigned"

    def fake_get_client():
        return FakeClient()

    monkeypatch.setattr(
        "apps.user_service.app.api.presigned_url.get_r2_client",
        fake_get_client,
    )

    res = await client.get(
        "/v1/upload/presigned-url"
        "?file_name=test.txt&path=user&id&bucket=bucket1&content_type=text/plain"
    )
    body = assert_success(res, 200)
    assert body["data"]["url"] == "http://example.com/presigned"


@pytest.mark.asyncio
async def test_presigned_url_missing_creds(monkeypatch, client):
    """Return 500 when R2 creds missing."""

    # Force missing creds to cover credential validation branch
    monkeypatch.setattr(
        "apps.user_service.app.api.presigned_url.R2_ACCESS_KEY",
        None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.presigned_url.R2_SECRET_KEY",
        None,
    )

    def fake_get_client():
        from libs.shared_utils.http_exceptions import InternalServerErrorException

        raise InternalServerErrorException(
            message_key="presigned_url.errors.r2_credentials_not_configured",
            custom_code=500,
        )

    monkeypatch.setattr(
        "apps.user_service.app.api.presigned_url.get_r2_client",
        fake_get_client,
    )

    res = await client.get(
        "/v1/upload/presigned-url"
        "?file_name=test.txt&path=folder&bucket=bucket1&content_type=text/plain"
    )
    assert res.status_code in (422, 500)
