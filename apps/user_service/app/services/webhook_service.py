"""Service-layer helpers for webhook-driven external integrations."""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import HTTPException

from libs.shared_utils.logger import get_logger

logger = get_logger("webhook_service")

_ISOMETRIK_WORKFLOW_EXECUTE_URL = "https://admin-apis.isometrik.io/v1/agents/workflow/execute"
_ISOMETRIK_APP_SECRET = (
    "SFMyNTY.g3QAAAACZAAEZGF0YXQAAAADbQAAAAlhY2NvdW50SWRtAAAAGDY5MWFkMjdjMzQ4ZjcwZjUxOGVlMDA1"
    "M20AAAAIa2V5c2V0SWRtAAAAJGE2Mjk1YWRkLTY0NDQtNDYwNy1iMGQ5LTBhOWZiYTU2MGJhMm0AAAAJcHJvamVj"
    "dElkbQAAACQ1YmQ2ZTM2OC1lZDJlLTRiNzMtYTEwMS00ZDdhOTQzYTFkZmJkAAZzaWduZWRuBgDt0T0hngE.j4X-"
    "qJhpuhBq2U8y3QpJJLBuyAG2ZJcvmDq6Ye-X2aw"
)
_ISOMETRIK_LICENSE_KEY = "lic-IMK9TZpGYcbiDkKQCD8s6CgmDbrketQgc24"
_ISOMETRIK_PROJECT_ID = "5bd6e368-ed2e-4b73-a101-4d7a943a1dfb"
_ISOMETRIK_USER_TOKEN = (
    "SFMyNTY.g2gDbQAAABg2YTA0NjlmMWRhYmJhYjAwMDE5NTkyYTduBgD4jmYhngFiAAFRgA.KuiD3kOBbcUzTEU7S"
    "8a7ENLSyXqMz_ZyPk4OahmatZs"
)
_ISOMETRIK_WORKFLOW_ID = "6a047406da7550b8dd2631f9"
_HTTP_TIMEOUT = httpx.Timeout(30.0)


class WebhookService:
    """Orchestrates outbound calls triggered from webhook endpoints."""

    async def execute_isometrik_whatsapp_workflow(
        self, *, webhook_payload: dict[str, Any]
    ) -> dict[str, Any]:
        """POST Isometrik workflow; JSON-serialize ``webhook_payload`` into workflow query."""
        query = json.dumps(webhook_payload, ensure_ascii=False)
        headers = {
            "accept": "application/json",
            "appsecret": _ISOMETRIK_APP_SECRET,
            "content-type": "application/json",
            "licensekey": _ISOMETRIK_LICENSE_KEY,
            "projectid": _ISOMETRIK_PROJECT_ID,
            "usertoken": _ISOMETRIK_USER_TOKEN,
        }
        payload = {"workflow_id": _ISOMETRIK_WORKFLOW_ID, "query": query}
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http_client:
            try:
                response = await http_client.post(
                    _ISOMETRIK_WORKFLOW_EXECUTE_URL,
                    headers=headers,
                    json=payload,
                )
            except httpx.RequestError as exc:
                logger.exception("isometrik_workflow_execute_request_failed")
                raise HTTPException(
                    status_code=502,
                    detail="Isometrik workflow request failed",
                ) from exc

        if not response.is_success:
            raise HTTPException(
                status_code=response.status_code,
                detail=response.text,
            )

        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}
