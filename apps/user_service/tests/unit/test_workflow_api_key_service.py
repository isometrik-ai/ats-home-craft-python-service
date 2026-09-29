"""Unit tests for workflow API key provisioning."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from apps.user_service.app.services.workflow_api_key_service import (
    WorkflowApiKeyService,
    provision_project_api_key,
    workflow_api_key_provisioning_enabled,
)

ORG_ID = "b0f897a5-3e36-43e0-a5b3-4744c7f797bb"
PROJECT_ID = "243a3a8d-b84f-4393-a19d-64f945d50d8b"
PROJECT_NAME = "Temp Test project"


def _workflow_settings(**overrides):
    """Build workflow service settings mock."""
    base = {
        "enabled": True,
        "base_url": "http://localhost:8080",
        "internal_token": "secret-token",
        "timeout_seconds": 10.0,
        "raise_on_failure": False,
    }
    base.update(overrides)
    return MagicMock(**base)


def test_workflow_api_key_provisioning_enabled():
    """Provisioning requires enabled flag, base URL, and internal token."""
    with patch(
        "apps.user_service.app.services.workflow_api_key_service.app_settings",
        MagicMock(workflow_service=_workflow_settings()),
    ):
        assert workflow_api_key_provisioning_enabled() is True

    with patch(
        "apps.user_service.app.services.workflow_api_key_service.app_settings",
        MagicMock(workflow_service=_workflow_settings(enabled=False)),
    ):
        assert workflow_api_key_provisioning_enabled() is False


@pytest.mark.asyncio
async def test_create_project_api_key_posts_expected_payload():
    """Service posts tenant/project/name to workflow-service."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(
        return_value={
            "status": "success",
            "data": {
                "id": "api_key_10be816bfbb9",
                "tenant_id": ORG_ID,
                "project_id": PROJECT_ID,
                "name": PROJECT_NAME,
            },
        }
    )

    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "apps.user_service.app.services.workflow_api_key_service.httpx.AsyncClient",
        return_value=client,
    ):
        service = WorkflowApiKeyService(
            base_url="http://localhost:8080",
            internal_token="secret-token",
            timeout_seconds=10.0,
        )
        result = await service.create_project_api_key(
            tenant_id=ORG_ID,
            project_id=PROJECT_ID,
            name=PROJECT_NAME,
        )

    client.post.assert_awaited_once_with(
        "http://localhost:8080/api/v1/api-keys",
        json={
            "tenant_id": ORG_ID,
            "project_id": PROJECT_ID,
            "name": PROJECT_NAME,
        },
        headers={
            "Content-Type": "application/json",
            "x-internal-token": "secret-token",
        },
    )
    assert result["id"] == "api_key_10be816bfbb9"


@pytest.mark.asyncio
async def test_provision_project_api_key_skips_when_disabled():
    """Disabled provisioning is a no-op."""
    with patch(
        "apps.user_service.app.services.workflow_api_key_service.workflow_api_key_provisioning_enabled",
        return_value=False,
    ):
        result = await provision_project_api_key(
            tenant_id=ORG_ID,
            project_id=PROJECT_ID,
            name=PROJECT_NAME,
        )
    assert result is None


@pytest.mark.asyncio
async def test_provision_project_api_key_logs_and_returns_none_on_failure():
    """HTTP failures do not raise when raise_on_failure is false."""
    service = MagicMock()
    service.create_project_api_key = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "bad request",
            request=MagicMock(),
            response=MagicMock(status_code=400),
        )
    )

    with (
        patch(
            "apps.user_service.app.services.workflow_api_key_service.workflow_api_key_provisioning_enabled",
            return_value=True,
        ),
        patch(
            "apps.user_service.app.services.workflow_api_key_service.WorkflowApiKeyService.from_settings",
            return_value=service,
        ),
        patch(
            "apps.user_service.app.services.workflow_api_key_service.app_settings",
            MagicMock(workflow_service=_workflow_settings(raise_on_failure=False)),
        ),
    ):
        result = await provision_project_api_key(
            tenant_id=ORG_ID,
            project_id=PROJECT_ID,
            name=PROJECT_NAME,
        )

    assert result is None


def test_workflow_api_key_provisioning_disabled_when_url_or_token_blank():
    """Blank base URL or internal token disables provisioning."""
    with patch(
        "apps.user_service.app.services.workflow_api_key_service.app_settings",
        MagicMock(workflow_service=_workflow_settings(base_url="  ")),
    ):
        assert workflow_api_key_provisioning_enabled() is False

    with patch(
        "apps.user_service.app.services.workflow_api_key_service.app_settings",
        MagicMock(workflow_service=_workflow_settings(internal_token="")),
    ):
        assert workflow_api_key_provisioning_enabled() is False


def test_workflow_api_key_service_from_settings():
    """from_settings reads workflow service config."""
    with patch(
        "apps.user_service.app.services.workflow_api_key_service.app_settings",
        MagicMock(workflow_service=_workflow_settings(timeout_seconds=5.0)),
    ):
        service = WorkflowApiKeyService.from_settings()
    assert service._base_url == "http://localhost:8080"
    assert service._internal_token == "secret-token"
    assert service._timeout == 5.0


@pytest.mark.asyncio
async def test_create_project_api_key_returns_body_when_no_data_wrapper():
    """Non-wrapped JSON dict responses are returned as-is."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={"id": "plain-key"})

    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "apps.user_service.app.services.workflow_api_key_service.httpx.AsyncClient",
        return_value=client,
    ):
        service = WorkflowApiKeyService(
            base_url="http://localhost:8080/",
            internal_token="secret-token",
            timeout_seconds=10.0,
        )
        result = await service.create_project_api_key(
            tenant_id=ORG_ID,
            project_id=PROJECT_ID,
            name=PROJECT_NAME,
        )

    assert result["id"] == "plain-key"


@pytest.mark.asyncio
async def test_create_project_api_key_wraps_non_dict_body():
    """Scalar JSON bodies are wrapped under response key."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=["unexpected"])

    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "apps.user_service.app.services.workflow_api_key_service.httpx.AsyncClient",
        return_value=client,
    ):
        service = WorkflowApiKeyService(
            base_url="http://localhost:8080",
            internal_token="secret-token",
            timeout_seconds=10.0,
        )
        result = await service.create_project_api_key(
            tenant_id=ORG_ID,
            project_id=PROJECT_ID,
            name=PROJECT_NAME,
        )

    assert result == {"response": ["unexpected"]}


@pytest.mark.asyncio
async def test_provision_project_api_key_returns_created_key():
    """Successful provisioning should return workflow-service payload."""
    service = MagicMock()
    service.create_project_api_key = AsyncMock(return_value={"id": "api_key_1"})

    with (
        patch(
            "apps.user_service.app.services.workflow_api_key_service.workflow_api_key_provisioning_enabled",
            return_value=True,
        ),
        patch(
            "apps.user_service.app.services.workflow_api_key_service.WorkflowApiKeyService.from_settings",
            return_value=service,
        ),
    ):
        result = await provision_project_api_key(
            tenant_id=ORG_ID,
            project_id=PROJECT_ID,
            name=PROJECT_NAME,
        )

    assert result == {"id": "api_key_1"}


@pytest.mark.asyncio
async def test_provision_project_api_key_raises_when_configured():
    """HTTP failures propagate when raise_on_failure is true."""
    service = MagicMock()
    service.create_project_api_key = AsyncMock(side_effect=RuntimeError("workflow down"))

    with (
        patch(
            "apps.user_service.app.services.workflow_api_key_service.workflow_api_key_provisioning_enabled",
            return_value=True,
        ),
        patch(
            "apps.user_service.app.services.workflow_api_key_service.WorkflowApiKeyService.from_settings",
            return_value=service,
        ),
        patch(
            "apps.user_service.app.services.workflow_api_key_service.app_settings",
            MagicMock(workflow_service=_workflow_settings(raise_on_failure=True)),
        ),
    ):
        with pytest.raises(RuntimeError, match="workflow down"):
            await provision_project_api_key(
                tenant_id=ORG_ID,
                project_id=PROJECT_ID,
                name=PROJECT_NAME,
            )
