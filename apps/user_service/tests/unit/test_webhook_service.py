"""Unit tests for WebhookService Isometrik workflow calls."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from apps.user_service.app.services.webhook_service import WebhookService


@pytest.mark.asyncio
async def test_execute_workflow_success():
    """Successful workflow POST returns parsed JSON."""
    response = MagicMock()
    response.is_success = True
    response.json.return_value = {"status": "ok", "run_id": "run-1"}

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "apps.user_service.app.services.webhook_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await WebhookService().execute_isometrik_whatsapp_workflow(
            webhook_payload={"message": "hello"},
        )

    assert result["status"] == "ok"
    call_kwargs = mock_client.post.await_args.kwargs
    assert call_kwargs["json"]["query"] == '{"message": "hello"}'


@pytest.mark.asyncio
async def test_execute_workflow_request_error():
    """Transport errors surface as HTTP 502."""
    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=httpx.RequestError("timeout"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "apps.user_service.app.services.webhook_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await WebhookService().execute_isometrik_whatsapp_workflow(
                webhook_payload={"message": "hello"},
            )

    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_execute_workflow_http_error():
    """Non-success HTTP responses propagate status and body."""
    response = MagicMock()
    response.is_success = False
    response.status_code = 503
    response.text = "service unavailable"

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "apps.user_service.app.services.webhook_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await WebhookService().execute_isometrik_whatsapp_workflow(
                webhook_payload={"message": "hello"},
            )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "service unavailable"


@pytest.mark.asyncio
async def test_execute_workflow_non_json_body():
    """Non-JSON success bodies wrap raw text."""
    response = MagicMock()
    response.is_success = True
    response.json.side_effect = ValueError("not json")
    response.text = "plain-text-ok"

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "apps.user_service.app.services.webhook_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await WebhookService().execute_isometrik_whatsapp_workflow(
            webhook_payload={"message": "hello"},
        )

    assert result == {"raw": "plain-text-ok"}
