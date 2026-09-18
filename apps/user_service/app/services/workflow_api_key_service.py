"""Workflow service client for provisioning project API keys."""

from __future__ import annotations

from typing import Any

import httpx

from apps.user_service.app.config.app_settings import app_settings
from libs.shared_utils.logger import get_logger

logger = get_logger("workflow_api_key_service")


def workflow_api_key_provisioning_enabled() -> bool:
    """Return True when workflow API key provisioning is configured."""
    settings = app_settings.workflow_service
    return bool(settings.enabled and settings.base_url.strip() and settings.internal_token.strip())


class WorkflowApiKeyService:
    """Calls workflow-service internal endpoints."""

    def __init__(self, base_url: str, internal_token: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._internal_token = internal_token
        self._timeout = timeout_seconds

    @classmethod
    def from_settings(cls) -> WorkflowApiKeyService:
        """Create an instance from app config."""
        settings = app_settings.workflow_service
        return cls(
            base_url=settings.base_url,
            internal_token=settings.internal_token,
            timeout_seconds=settings.timeout_seconds,
        )

    async def create_project_api_key(
        self,
        *,
        tenant_id: str,
        project_id: str,
        name: str,
    ) -> dict[str, Any]:
        """Create a workflow API key scoped to a project."""
        url = f"{self._base_url}/api/v1/api-keys"
        payload = {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "name": name,
        }
        headers = {
            "Content-Type": "application/json",
            "x-internal-token": self._internal_token,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            body = response.json()
        if isinstance(body, dict) and isinstance(body.get("data"), dict):
            return body["data"]
        if isinstance(body, dict):
            return body
        return {"response": body}


async def provision_project_api_key(
    *,
    tenant_id: str,
    project_id: str,
    name: str,
) -> dict[str, Any] | None:
    """Best-effort API key provisioning after project create."""
    if not workflow_api_key_provisioning_enabled():
        logger.debug("workflow_api_key_provisioning_skipped reason=disabled")
        return None

    settings = app_settings.workflow_service
    service = WorkflowApiKeyService.from_settings()
    try:
        result = await service.create_project_api_key(
            tenant_id=tenant_id,
            project_id=project_id,
            name=name,
        )
        logger.info(
            "workflow_api_key_created tenant_id=%s project_id=%s",
            tenant_id,
            project_id,
        )
        return result
    except Exception as exc:
        logger.error(
            "workflow_api_key_creation_failed tenant_id=%s project_id=%s error=%s",
            tenant_id,
            project_id,
            exc,
        )
        if settings.raise_on_failure:
            raise
        return None
