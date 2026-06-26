"""Create the Pulse Agent Isometrik AI agent when an organization is provisioned."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from apps.user_service.app.db.repositories import OrganizationRepository
from apps.user_service.app.services.ai_overview_settings_ops import (
    set_pulse_agent_id_in_settings,
)
from apps.user_service.app.utils.common_utils import (
    parse_json_field,
    serialize_pydantic_models,
)
from libs.shared_config.app_settings import shared_settings
from libs.shared_utils.isometrik_service import create_isometrik_ai_agent
from libs.shared_utils.logger import get_logger

logger = get_logger("isometrik_pulse_agent_service")

PULSE_AGENT_NAME = "Pulse Agent"
PULSE_AGENT_VAULT_ID = "6a3e75a2420a8d04e3c08480"
PULSE_AGENT_SYSTEM_PROMPT = (
    "Use the typesense tool and be a friendly assistant alwyas energetic and "
    "enthusiatic and always structure the responses in a proper format and if "
    "the user is asking a question always be on point and return only that "
    "relevant data please"
)
PULSE_AGENT_PERSONA = (
    "You are a helpfull CRM assistant, Always greet the user and use a encouraging tone"
)
PULSE_AGENT_DESCRIPTION = "AI Assistant for your contacts, companies and leads"
PULSE_AGENT_TYPESENSE_TOOL_TYPE = 39
PULSE_AGENT_TYPESENSE_SEARCH_TYPE = "semantic"
PULSE_AGENT_TYPESENSE_EMBEDDING_MODEL = "text-embedding-3-small"
PULSE_AGENT_TYPESENSE_EMBEDDING_FIELD = "embedding"
PULSE_AGENT_TYPESENSE_K = 5
PULSE_AGENT_MODEL_VENDOR_CLIENT_ID = 1


def _typesense_tool_host() -> str:
    """Return hostname for Typesense tool config (no protocol or port)."""
    host = shared_settings.typesense.host.strip()
    if "://" in host:
        parsed = urlparse(host)
        return parsed.hostname or host
    return host.split(":")[0]


def build_pulse_agent_payload(*, project_id: str) -> dict[str, Any]:
    """Build the admin API payload for creating the org Pulse Agent."""
    typesense = shared_settings.typesense
    return {
        "project_id": project_id,
        "name": PULSE_AGENT_NAME,
        "vault_id": PULSE_AGENT_VAULT_ID,
        "system_prompt": PULSE_AGENT_SYSTEM_PROMPT,
        "persona": PULSE_AGENT_PERSONA,
        "additional_instruction": "",
        "agent_description": PULSE_AGENT_DESCRIPTION,
        "llm_config": {
            "model_vendor": "OpenAI",
            "api-key": "",
            "model": "gpt-5-nano",
            "temperature": 0.45,
            "top_p": 1.0,
            "is_tool_response": False,
            "is_stream_response": True,
            "is_structured_output": False,
        },
        "features": [{"type": "MEMORY", "type_value": 1, "priority": 0}],
        "tools": [
            {
                "name": "typesense",
                "type": PULSE_AGENT_TYPESENSE_TOOL_TYPE,
                "config": {
                    "api_key": typesense.search_only_api_key,
                    "host": _typesense_tool_host(),
                    "collection": typesense.contacts_collection_name,
                    "search_type": PULSE_AGENT_TYPESENSE_SEARCH_TYPE,
                    "openai_api_key": shared_settings.openai_api_key,
                    "embedding_model": PULSE_AGENT_TYPESENSE_EMBEDDING_MODEL,
                    "embedding_field": PULSE_AGENT_TYPESENSE_EMBEDDING_FIELD,
                    "k": PULSE_AGENT_TYPESENSE_K,
                },
            }
        ],
        "modelVendorClientId": PULSE_AGENT_MODEL_VENDOR_CLIENT_ID,
        "variables": [],
    }


def pulse_agent_id_from_response(response: dict[str, Any]) -> str | None:
    """Extract ``bot_id`` from the Isometrik create-agent API response."""
    bot_id = response.get("bot_id")
    if isinstance(bot_id, str) and bot_id.strip():
        return bot_id.strip()
    return None


async def _persist_pulse_agent_id(
    organization_id: str,
    pulse_agent_id: str,
    organization_repository: OrganizationRepository,
) -> None:
    """Store Pulse Agent id in organization ``ai_overview_settings``."""
    org = await organization_repository.get_organization_by_id(organization_id)
    if not org:
        return
    settings = parse_json_field(org.get("settings"))
    if not isinstance(settings, dict):
        settings = {}
    set_pulse_agent_id_in_settings(settings, pulse_agent_id)
    serialized_settings = json.dumps(serialize_pydantic_models(settings))
    await organization_repository.update_organization(
        organization_id,
        {"settings": serialized_settings},
    )


class IsometrikPulseAgentService:
    """Provision the Pulse Agent for a new organization (best-effort)."""

    @staticmethod
    async def create_for_organization_best_effort(
        *,
        organization_id: str,
        isometrik_details: dict[str, Any] | None,
        organization_repository: OrganizationRepository | None = None,
    ) -> None:
        """Create Pulse Agent via Isometrik admin API (best-effort; never raises)."""
        try:
            if not shared_settings.isometrik.is_enabled or not isometrik_details:
                return
            if organization_repository is None:
                logger.warning(
                    "pulse_agent_create_skipped_missing_repository",
                    extra={"organization_id": organization_id},
                )
                return

            project_id = isometrik_details.get("projectId")
            app_secret = (isometrik_details.get("appSecret") or "").strip()
            license_key = (isometrik_details.get("licenseKey") or "").strip()
            if not project_id or not app_secret or not license_key:
                logger.warning(
                    "pulse_agent_create_skipped_missing_credentials",
                    extra={
                        "organization_id": organization_id,
                        "has_project_id": bool(project_id),
                        "has_app_secret": bool(app_secret),
                        "has_license_key": bool(license_key),
                    },
                )
                return

            project_id_str = str(project_id).strip()
            payload = build_pulse_agent_payload(project_id=project_id_str)
            response = await create_isometrik_ai_agent(
                payload=payload,
                app_secret=app_secret,
                license_key=license_key,
            )
            pulse_agent_id = pulse_agent_id_from_response(response)
            if pulse_agent_id is None:
                logger.warning(
                    "pulse_agent_create_missing_bot_id",
                    extra={"organization_id": organization_id, "project_id": project_id_str},
                )
                return

            await _persist_pulse_agent_id(
                organization_id,
                pulse_agent_id,
                organization_repository,
            )
            logger.info(
                "pulse_agent_created",
                extra={
                    "organization_id": organization_id,
                    "project_id": project_id_str,
                    "agent_name": PULSE_AGENT_NAME,
                    "pulse_agent_id": pulse_agent_id,
                },
            )
        except Exception:
            logger.exception(
                "pulse_agent_create_failed",
                extra={"organization_id": organization_id},
            )
