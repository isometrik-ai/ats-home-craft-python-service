"""Unit tests for org business overview enrichment scheduling and helpers."""

import json

import pytest

from apps.user_service.app.constants.ai_overview_defaults import (
    AI_OVERVIEW_SETTINGS_KEY,
)
from apps.user_service.app.services.org_business_overview_enrichment_service import (
    OrgBusinessOverviewEnrichmentService,
    _normalize_website_url,
    _parse_json_object_text,
    _strands_response_text,
)


def test_schedule_skips_when_overview_already_set() -> None:
    """Do not enqueue when business_overview is already stored."""
    settings = {AI_OVERVIEW_SETTINGS_KEY: {"business_overview": "Existing facts"}}
    assert (
        OrgBusinessOverviewEnrichmentService.should_enqueue_after_organization_created(
            organization_id="org-1",
            organization_name="Acme Corp",
            settings=settings,
        )
        is False
    )


def test_schedule_skips_empty_name() -> None:
    """Do not enqueue when organization name is blank."""
    assert (
        OrgBusinessOverviewEnrichmentService.should_enqueue_after_organization_created(
            organization_id="org-1",
            organization_name="   ",
            settings={},
        )
        is False
    )


def test_parse_json_object_text_plain() -> None:
    """Parse a plain JSON object string."""
    raw = json.dumps({"official_website": "https://example.com", "confidence": 90})
    parsed = _parse_json_object_text(raw)
    assert parsed["official_website"] == "https://example.com"
    assert parsed["confidence"] == 90


def test_parse_json_object_text_fenced() -> None:
    """Strip markdown fences before parsing JSON."""
    inner = json.dumps({"business_overview": "A SaaS company."})
    raw = f"```json\n{inner}\n```"
    parsed = _parse_json_object_text(raw)
    assert parsed["business_overview"] == "A SaaS company."


def test_parse_json_object_text_invalid_raises() -> None:
    """Invalid JSON raises JSONDecodeError."""
    with pytest.raises(json.JSONDecodeError):
        _parse_json_object_text("not json")


def test_strands_response_text_extracts_non_empty() -> None:
    """Return stripped text when present."""
    assert _strands_response_text({"text": "  hello  "}) == "hello"


def test_strands_response_text_missing_or_blank() -> None:
    """Return None for missing, blank, or non-string text."""
    assert _strands_response_text({}) is None
    assert _strands_response_text({"text": "   "}) is None
    assert _strands_response_text({"text": 1}) is None


def test_normalize_website_url_adds_https() -> None:
    """Bare hostnames get an https scheme."""
    assert _normalize_website_url("appscrip.com") == "https://appscrip.com"


def test_normalize_website_url_upgrades_http() -> None:
    """http URLs are upgraded to https."""
    assert _normalize_website_url("http://appscrip.com") == "https://appscrip.com"


def test_normalize_website_url_rejects_empty() -> None:
    """Empty URLs return None."""
    assert _normalize_website_url("") is None
    assert _normalize_website_url("   ") is None
