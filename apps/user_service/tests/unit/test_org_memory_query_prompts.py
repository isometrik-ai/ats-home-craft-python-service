"""Unit tests for org memory query overview prompt resolution."""

from apps.user_service.app.constants.ai_overview_defaults import (
    DEFAULT_CONTACT_OVERVIEW_PROMPT,
    DEFAULT_LEAD_OVERVIEW_PROMPT,
)
from apps.user_service.app.schemas.ai_overview_settings import (
    AiOverviewSettings,
    OverviewPrompts,
)
from apps.user_service.app.services.org_memory_query_service import (
    _overview_prompt_template,
)
from apps.user_service.app.services.organization_service import OrganizationService


def test_overview_prompt_default() -> None:
    """Missing ai_overview_settings yields platform default prompts."""
    effective = OrganizationService._resolve_effective_ai_overview_settings(None)
    assert _overview_prompt_template(effective, "contact") == DEFAULT_CONTACT_OVERVIEW_PROMPT
    assert _overview_prompt_template(effective, "lead") == DEFAULT_LEAD_OVERVIEW_PROMPT


def test_overview_prompt_template_uses_stored_override() -> None:
    """Stored per-entity prompt overrides the default for that type only."""
    custom_lead = "Custom lead prompt for {{entity_name}}"
    effective = AiOverviewSettings(
        business_overview=None,
        overview_prompts=OverviewPrompts(
            lead=custom_lead,
            contact=DEFAULT_CONTACT_OVERVIEW_PROMPT,
            company=DEFAULT_CONTACT_OVERVIEW_PROMPT,
        ),
    )
    assert _overview_prompt_template(effective, "lead") == custom_lead
    assert _overview_prompt_template(effective, "contact") == DEFAULT_CONTACT_OVERVIEW_PROMPT
