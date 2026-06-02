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
    return AiOverviewSettings(
        business_overview=business_overview,
        overview_prompts=OverviewPrompts(**effective_prompts),
    )


def merge_ai_overview_settings_into_settings(
    settings: dict[str, Any],
    update: dict[str, Any],
) -> None:
    """Apply a partial ``AiOverviewSettingsUpdate`` dict into ``settings`` in place."""
    stored = parse_stored_ai_overview_settings(settings)

    if "business_overview" in update:
        business = update["business_overview"]
        if business is None or (isinstance(business, str) and not business.strip()):
            stored.pop("business_overview", None)
        else:
            stored["business_overview"] = str(business).strip()

    prompts_patch = update.get("overview_prompts")
    if isinstance(prompts_patch, dict):
        stored_prompts = coerce_overview_prompts_dict(stored.get("overview_prompts"))
        for entity_type in OVERVIEW_PROMPT_ENTITY_TYPES:
            if entity_type not in prompts_patch:
                continue
            value = prompts_patch[entity_type]
            if value is None or (isinstance(value, str) and not value.strip()):
                stored_prompts.pop(entity_type, None)
            else:
                stored_prompts[entity_type] = str(value).strip()

        if stored_prompts:
            stored["overview_prompts"] = stored_prompts
        else:
            stored.pop("overview_prompts", None)

    if stored:
        settings[AI_OVERVIEW_SETTINGS_KEY] = stored
    else:
        settings.pop(AI_OVERVIEW_SETTINGS_KEY, None)


def default_ai_overview_settings() -> AiOverviewSettings:
    """Platform defaults for reset or new-org display."""
    return AiOverviewSettings(
        business_overview=None,
        overview_prompts=OverviewPrompts(**DEFAULT_OVERVIEW_PROMPTS),
    )
