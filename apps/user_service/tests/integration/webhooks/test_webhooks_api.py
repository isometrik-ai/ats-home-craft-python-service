"""Integration tests for webhooks API (enrichment callback)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.tests.utils.assertions import assert_success


@pytest.mark.asyncio
async def test_enrichment_webhook_no_request_id_returns_200(client):
    """POST /webhooks/enrichment without request_id returns
    422 with success envelope.
    """
    res = await client.post("/v1/webhooks/enrichment", json={})
    body = assert_success(res, 422)
    assert body.get("status") == "success"


@pytest.mark.asyncio
async def test_enrichment_webhook_company_calls_process(client):
    """POST with request_id and enriched_company processes company enrichment."""
    mock_process = AsyncMock(return_value=("c1", "org-1"))

    with (
        patch(
            "apps.user_service.app.api.webhooks.ClientEnrichmentService.from_settings",
        ) as mock_from_settings,
        patch(
            "apps.user_service.app.api.webhooks.index_companies_background",
            new=AsyncMock(),
        ),
    ):
        mock_svc = MagicMock()
        mock_svc.process_company_enrichment_webhook = mock_process
        mock_svc.process_person_enrichment_webhook = AsyncMock()
        mock_from_settings.return_value = mock_svc

        res = await client.post(
            "/v1/webhooks/enrichment",
            json={
                "request_id": "req-1",
                "enriched_company": {"companyName": "Acme", "industry": "Tech"},
            },
        )

    body = assert_success(res, 200)
    assert body.get("status") == "success"
    mock_process.assert_called_once()


@pytest.mark.asyncio
async def test_enrichment_webhook_profile_calls_process(client):
    """POST with request_id and enriched_profile processes person enrichment."""
    mock_process = AsyncMock(return_value=("c1", "org-1"))

    with (
        patch(
            "apps.user_service.app.api.webhooks.ClientEnrichmentService.from_settings",
        ) as mock_from_settings,
        patch(
            "apps.user_service.app.api.webhooks.index_contacts_background",
            new=AsyncMock(),
        ),
    ):
        mock_svc = MagicMock()
        mock_svc.process_company_enrichment_webhook = AsyncMock()
        mock_svc.process_person_enrichment_webhook = mock_process
        mock_from_settings.return_value = mock_svc

        res = await client.post(
            "/v1/webhooks/enrichment",
            json={
                "request_id": "req-1",
                "enriched_profile": {
                    "personalInfo": {"firstName": "Jane", "lastName": "Doe"},
                },
            },
        )

    body = assert_success(res, 200)
    assert body.get("status") == "success"
    mock_process.assert_called_once()
