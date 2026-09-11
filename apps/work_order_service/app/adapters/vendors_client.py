"""Fetch vendors from user_service CRM (companies API)."""

from __future__ import annotations

from typing import Any

import httpx

from apps.work_order_service.app.config.app_settings import app_settings
from libs.shared_utils.logger import get_logger

logger = get_logger("work_order_vendors_client")


async def fetch_vendor(
    *,
    vendor_id: str,
    authorization_header: str | None = None,
) -> dict[str, Any] | None:
    """Load a vendor summary from user_service (best-effort)."""
    url = f"{app_settings.user_service_base_url.rstrip('/')}/v1/companies/{vendor_id}"
    headers: dict[str, str] = {}
    if authorization_header:
        headers["Authorization"] = authorization_header
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                payload = resp.json()
                return payload.get("data") or payload
    except httpx.HTTPError as exc:
        logger.warning("Failed to fetch vendor %s: %s", vendor_id, exc)
    return None
