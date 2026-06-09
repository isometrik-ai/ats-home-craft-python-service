"""Unit tests for AI Overview Settings on OrganizationService."""

from apps.user_service.app.constants.ai_overview_defaults import (
    AI_OVERVIEW_SETTINGS_KEY,
    DEFAULT_CONTACT_OVERVIEW_PROMPT,
    DEFAULT_LEAD_OVERVIEW_PROMPT,
)
from apps.user_service.app.services.organization_service import OrganizationService


def test_resolve_effective_defaults() -> None:
    """Missing settings yields platform default prompts."""
    effective = OrganizationService._resolve_effective_ai_overview_settings(None)
    assert effective.business_overview is None
    assert effective.overview_prompts.lead == DEFAULT_LEAD_OVERVIEW_PROMPT
    assert effective.overview_prompts.contact == DEFAULT_CONTACT_OVERVIEW_PROMPT


def test_resolve_effective_merges_stored_overrides() -> None:
    """Stored values override defaults; unset prompts still use defaults."""
    settings = {
        AI_OVERVIEW_SETTINGS_KEY: {
            "business_overview": "Healthcare SaaS",
            "overview_prompts": {"lead": "Custom lead prompt for {{entity_name}}"},
        }
    }
    effective = OrganizationService._resolve_effective_ai_overview_settings(settings)
    assert effective.business_overview == "Healthcare SaaS"
    assert effective.overview_prompts.lead == "Custom lead prompt for {{entity_name}}"
    assert effective.overview_prompts.contact == DEFAULT_CONTACT_OVERVIEW_PROMPT


def test_merge_clears_overview_fields() -> None:
    """Null or empty patch values remove stored overrides."""
    settings = {
        AI_OVERVIEW_SETTINGS_KEY: {
            "business_overview": "Old facts",
            "overview_prompts": {
                "lead": "Custom lead",
                "contact": "Custom contact",
            },
        }
    }
    OrganizationService._merge_ai_overview_settings_into_settings(
        settings,
        {
            "business_overview": None,
            "overview_prompts": {"lead": None, "contact": ""},
        },
    )
    assert AI_OVERVIEW_SETTINGS_KEY not in settings


def test_merge_repopulate_prompts_clears_all_overrides() -> None:
    """Repopulate clears all stored prompt overrides."""
    settings = {
        AI_OVERVIEW_SETTINGS_KEY: {
            "overview_prompts": {
                "lead": "Custom lead",
                "contact": "Custom contact",
                "company": "Custom company",
            }
        }
    }
    OrganizationService._merge_ai_overview_settings_into_settings(
        settings,
        {
            "overview_prompts": {
                "lead": None,
                "contact": None,
                "company": None,
            }
        },
    )
    assert AI_OVERVIEW_SETTINGS_KEY not in settings


def test_merge_clears_one_prompt() -> None:
    """Resetting one entity type does not touch other stored prompts."""
    settings = {
        AI_OVERVIEW_SETTINGS_KEY: {
            "overview_prompts": {
                "lead": "Custom lead",
                "contact": "Custom contact",
            }
        }
    }
    OrganizationService._merge_ai_overview_settings_into_settings(
        settings,
        {"overview_prompts": {"lead": None}},
    )
    stored = settings[AI_OVERVIEW_SETTINGS_KEY]
    assert stored.get("overview_prompts") == {"contact": "Custom contact"}


def test_update_persists_ai_overview() -> None:
    """Organization update payload writes ai_overview_settings into settings JSON."""
    service = OrganizationService.__new__(OrganizationService)
    db_payload = service._build_update_payload(
        existing_settings={"organization_memory": True},
        update_data={
            "ai_overview_settings": {
                "business_overview": "HRssr org - Healthcare",
                "overview_prompts": {"lead": "Lead prompt {{entity_name}}"},
            }
        },
    )
    stored = db_payload["settings"][AI_OVERVIEW_SETTINGS_KEY]
    assert stored["business_overview"] == "HRssr org - Healthcare"
    assert stored["overview_prompts"]["lead"] == "Lead prompt {{entity_name}}"
    assert db_payload["settings"]["organization_memory"] is True


def test_create_skips_ai_overview() -> None:
    """Org creation should not persist ai_overview_settings; background tasks fill it later."""
    service = OrganizationService.__new__(OrganizationService)
    settings = service._build_settings(
        type(
            "Body",
            (),
            {
                "company_data": type(
                    "CompanyData",
                    (),
                    {
                        "settings": None,
                        "primary_practice_areas": None,
                        "secondary_practice_areas": None,
                        "specializations": None,
                        "preferred_integration": None,
                        "need_help_importing_data": None,
                        "need_migration_assistance": None,
                        "compliance_security": None,
                        "enterprise_features": None,
                        "team_setup": None,
                        "address": None,
                        "website_url": None,
                    },
                )()
            },
        )()
    )
    assert AI_OVERVIEW_SETTINGS_KEY not in settings
    assert settings["organization_memory"] is True
