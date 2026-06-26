"""AI Overview settings merge and resolution (shared by org and enrichment services)."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.constants.ai_overview_defaults import (
    AI_OVERVIEW_SETTINGS_KEY,
    DEFAULT_OVERVIEW_PROMPTS,
    OVERVIEW_PROMPT_ENTITY_TYPES,
)
from apps.user_service.app.schemas.ai_overview_settings import (
    AiOverviewSettings,
    OverviewPrompts,
)


def coerce_overview_prompts_dict(raw: Any) -> dict[str, str]:
    """Return a dict of non-empty per-entity overview prompt strings."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for entity_type in OVERVIEW_PROMPT_ENTITY_TYPES:
        value = raw.get(entity_type)
        if isinstance(value, str) and value.strip():
            out[entity_type] = value.strip()
    return out


def parse_stored_ai_overview_settings(settings: Any) -> dict[str, Any]:
    """Return the raw ``ai_overview_settings`` object from organization settings JSON."""
    if not isinstance(settings, dict):
        return {}
    stored = settings.get(AI_OVERVIEW_SETTINGS_KEY)
    return dict(stored) if isinstance(stored, dict) else {}


def resolve_effective_ai_overview_settings(settings: Any) -> AiOverviewSettings:
    """Merge stored overrides with platform defaults for API responses."""
    stored = parse_stored_ai_overview_settings(settings)
    business_raw = stored.get("business_overview")
    business_overview: str | None = None
    if isinstance(business_raw, str) and business_raw.strip():
        business_overview = business_raw.strip()

    stored_prompts = coerce_overview_prompts_dict(stored.get("overview_prompts"))
    effective_prompts = {
        entity_type: stored_prompts.get(entity_type) or DEFAULT_OVERVIEW_PROMPTS[entity_type]
        for entity_type in OVERVIEW_PROMPT_ENTITY_TYPES
    }
    pulse_agent_raw = stored.get("pulse_agent_id")
    pulse_agent_id: str | None = None
    if isinstance(pulse_agent_raw, str) and pulse_agent_raw.strip():
        pulse_agent_id = pulse_agent_raw.strip()
    return AiOverviewSettings(
        business_overview=business_overview,
        pulse_agent_id=pulse_agent_id,
        overview_prompts=OverviewPrompts(**effective_prompts),
    )


def _should_clear_optional_string(value: Any) -> bool:
    """Return True when an optional string patch value clears storage."""
    return value is None or (isinstance(value, str) and not value.strip())


def _apply_optional_string_field(
    stored: dict[str, Any],
    *,
    field: str,
    value: Any,
) -> None:
    """Set or remove a single optional string field on stored ai overview settings."""
    if _should_clear_optional_string(value):
        stored.pop(field, None)
    else:
        stored[field] = str(value).strip()


def _apply_overview_prompts_patch(stored: dict[str, Any], prompts_patch: dict[str, Any]) -> None:
    """Merge partial overview prompt updates into stored ai overview settings."""
    stored_prompts = coerce_overview_prompts_dict(stored.get("overview_prompts"))
    for entity_type in OVERVIEW_PROMPT_ENTITY_TYPES:
        if entity_type not in prompts_patch:
            continue
        value = prompts_patch[entity_type]
        if _should_clear_optional_string(value):
            stored_prompts.pop(entity_type, None)
        else:
            stored_prompts[entity_type] = str(value).strip()

    if stored_prompts:
        stored["overview_prompts"] = stored_prompts
    else:
        stored.pop("overview_prompts", None)


def _write_stored_ai_overview_settings(settings: dict[str, Any], stored: dict[str, Any]) -> None:
    """Persist or remove ``ai_overview_settings`` on organization settings."""
    if stored:
        settings[AI_OVERVIEW_SETTINGS_KEY] = stored
    else:
        settings.pop(AI_OVERVIEW_SETTINGS_KEY, None)


def merge_ai_overview_settings_into_settings(
    settings: dict[str, Any],
    update: dict[str, Any],
) -> None:
    """Apply a partial ``AiOverviewSettingsUpdate`` dict into ``settings`` in place."""
    stored = parse_stored_ai_overview_settings(settings)

    if "business_overview" in update:
        _apply_optional_string_field(
            stored,
            field="business_overview",
            value=update["business_overview"],
        )

    if "pulse_agent_id" in update:
        _apply_optional_string_field(
            stored,
            field="pulse_agent_id",
            value=update["pulse_agent_id"],
        )

    prompts_patch = update.get("overview_prompts")
    if isinstance(prompts_patch, dict):
        _apply_overview_prompts_patch(stored, prompts_patch)

    _write_stored_ai_overview_settings(settings, stored)


def set_pulse_agent_id_in_settings(settings: dict[str, Any], pulse_agent_id: str) -> None:
    """Persist the Isometrik Pulse Agent bot id under ``ai_overview_settings``."""
    merge_ai_overview_settings_into_settings(settings, {"pulse_agent_id": pulse_agent_id})


def default_ai_overview_settings() -> AiOverviewSettings:
    """Platform defaults for reset or new-org display."""
    return AiOverviewSettings(
        business_overview=None,
        overview_prompts=OverviewPrompts(**DEFAULT_OVERVIEW_PROMPTS),
    )
