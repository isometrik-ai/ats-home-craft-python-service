"""Unit tests for AI overview refetch."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.constants.ai_overview_defaults import (
    AI_OVERVIEW_SETTINGS_KEY,
)
from apps.user_service.app.services.org_business_overview_enrichment_service import (
    OrgBusinessOverviewEnrichmentService,
)


def _mock_repo(org_row: dict) -> MagicMock:
    """Build a mock organization repository returning ``org_row`` from get."""
    repo = MagicMock()
    repo.get_organization_by_id = AsyncMock(return_value=org_row)
    repo.update_organization = AsyncMock()
    return repo


@pytest.mark.asyncio
async def test_refetch_overview_uses_stored_website() -> None:
    """Business overview refetch does not discover website when already stored."""
    org_row = {
        "name": "Acme",
        "settings": {
            "website_url": "https://appscrip.com",
            AI_OVERVIEW_SETTINGS_KEY: {"business_overview": "Old overview"},
        },
    }
    repo = _mock_repo(org_row)

    with (
        patch(
            "apps.user_service.app.services.org_business_overview_enrichment_service."
            "strands_enrichment_enabled",
            return_value=True,
        ),
        patch.object(
            OrgBusinessOverviewEnrichmentService,
            "_ensure_organization_website",
            new=AsyncMock(),
        ) as ensure_website,
        patch.object(
            OrgBusinessOverviewEnrichmentService,
            "_fetch_business_overview",
            new=AsyncMock(return_value="Fresh overview text"),
        ) as fetch_overview,
        patch.object(
            OrgBusinessOverviewEnrichmentService,
            "_persist_ai_settings",
            new=AsyncMock(),
        ) as persist_settings,
        patch(
            "apps.user_service.app.services.organization_memory_service."
            "invalidate_organization_memory_cache",
        ),
    ):
        await OrgBusinessOverviewEnrichmentService.refetch_ai_overview_fields(
            organization_id="550e8400-e29b-41d4-a716-446655440000",
            fields=["business_overview"],
            organization_repository=repo,
        )

        ensure_website.assert_not_called()
        fetch_overview.assert_called_once_with("https://appscrip.com")
        persist_settings.assert_called_once()
        assert persist_settings.call_args.kwargs["business_overview"] == "Fresh overview text"


@pytest.mark.asyncio
async def test_refetch_overview_discovers_website() -> None:
    """Business overview refetch discovers website only when not stored."""
    org_row = {
        "name": "Acme",
        "settings": {AI_OVERVIEW_SETTINGS_KEY: {}},
    }
    repo = _mock_repo(org_row)

    with (
        patch(
            "apps.user_service.app.services.org_business_overview_enrichment_service."
            "strands_enrichment_enabled",
            return_value=True,
        ),
        patch.object(
            OrgBusinessOverviewEnrichmentService,
            "_ensure_organization_website",
            new=AsyncMock(return_value="https://discovered.example"),
        ) as ensure_website,
        patch.object(
            OrgBusinessOverviewEnrichmentService,
            "_fetch_business_overview",
            new=AsyncMock(return_value="Fresh overview text"),
        ),
        patch.object(
            OrgBusinessOverviewEnrichmentService,
            "_persist_ai_settings",
            new=AsyncMock(),
        ),
        patch(
            "apps.user_service.app.services.organization_memory_service."
            "invalidate_organization_memory_cache",
        ),
    ):
        await OrgBusinessOverviewEnrichmentService.refetch_ai_overview_fields(
            organization_id="550e8400-e29b-41d4-a716-446655440000",
            fields=["business_overview"],
            organization_repository=repo,
        )

        ensure_website.assert_called_once()
        ensure_website.assert_called_with(
            "550e8400-e29b-41d4-a716-446655440000",
            "Acme",
            organization_repository=repo,
        )


@pytest.mark.asyncio
async def test_refetch_lead_prompt_only() -> None:
    """Lead prompt refetch generates and persists only the lead prompt."""
    generated = {"lead": "New lead prompt for {{entity_name}}"}
    org_row = {
        "name": "Acme",
        "settings": {
            "website_url": "https://appscrip.com",
            AI_OVERVIEW_SETTINGS_KEY: {
                "business_overview": "Stored overview",
                "overview_prompts": {
                    "lead": "Old lead prompt",
                    "contact": "Keep contact",
                },
            },
        },
    }
    repo = _mock_repo(org_row)

    with (
        patch(
            "apps.user_service.app.services.org_business_overview_enrichment_service."
            "strands_enrichment_enabled",
            return_value=True,
        ),
        patch.object(
            OrgBusinessOverviewEnrichmentService,
            "_ensure_organization_website",
            new=AsyncMock(),
        ) as ensure_website,
        patch.object(
            OrgBusinessOverviewEnrichmentService,
            "_fetch_business_overview",
            new=AsyncMock(),
        ) as fetch_overview,
        patch.object(
            OrgBusinessOverviewEnrichmentService,
            "_generate_overview_prompts",
            new=AsyncMock(return_value=generated),
        ) as generate_prompts,
        patch.object(
            OrgBusinessOverviewEnrichmentService,
            "_persist_ai_settings",
            new=AsyncMock(),
        ) as persist_settings,
        patch(
            "apps.user_service.app.services.organization_memory_service."
            "invalidate_organization_memory_cache",
        ),
    ):
        await OrgBusinessOverviewEnrichmentService.refetch_ai_overview_fields(
            organization_id="550e8400-e29b-41d4-a716-446655440000",
            fields=["lead"],
            organization_repository=repo,
        )

        ensure_website.assert_not_called()
        fetch_overview.assert_not_called()
        generate_prompts.assert_called_once()
        assert generate_prompts.call_args.kwargs["entity_types"] == ("lead",)
        persist_settings.assert_called_once_with(
            "550e8400-e29b-41d4-a716-446655440000",
            overview_prompts=generated,
            organization_repository=repo,
        )


@pytest.mark.asyncio
async def test_refetch_lead_response_scoped() -> None:
    """Refetch response returns only the refetched prompt, not all settings."""
    generated = {"lead": "New lead prompt for {{entity_name}}"}
    org_row = {
        "name": "Acme",
        "settings": {
            AI_OVERVIEW_SETTINGS_KEY: {
                "business_overview": "Stored overview",
                "overview_prompts": {
                    "lead": "New lead prompt for {{entity_name}}",
                    "contact": "Contact stays",
                    "company": "Company stays",
                },
            },
        },
    }
    repo = _mock_repo(org_row)

    with (
        patch(
            "apps.user_service.app.services.org_business_overview_enrichment_service."
            "strands_enrichment_enabled",
            return_value=True,
        ),
        patch.object(
            OrgBusinessOverviewEnrichmentService,
            "_generate_overview_prompts",
            new=AsyncMock(return_value=generated),
        ),
        patch.object(
            OrgBusinessOverviewEnrichmentService,
            "_persist_ai_settings",
            new=AsyncMock(),
        ),
        patch(
            "apps.user_service.app.services.organization_memory_service."
            "invalidate_organization_memory_cache",
        ),
    ):
        result = await OrgBusinessOverviewEnrichmentService.refetch_ai_overview_fields(
            organization_id="550e8400-e29b-41d4-a716-446655440000",
            fields=["lead"],
            organization_repository=repo,
        )

    assert set(result.keys()) == {"overview_prompts"}
    assert set(result["overview_prompts"].keys()) == {"lead"}
    assert "business_overview" not in result
