"""Extensive unit tests for OrgBusinessOverviewEnrichmentService (mocked external I/O)."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from apps.user_service.app.constants.ai_overview_defaults import (
    AI_OVERVIEW_SETTINGS_KEY,
    DEFAULT_OVERVIEW_PROMPTS,
    OVERVIEW_PROMPT_ENTITY_TYPES,
)
from apps.user_service.app.services.org_business_overview_enrichment_service import (
    OrgBusinessOverviewEnrichmentService,
    _has_stored_overview_prompts,
    _is_safe_http_url,
    _log_strands_agent_failure,
    _normalize_website_url,
    _parse_json_object_text,
    _parse_overview_prompts_response,
    _sanitize_org_name,
    _strands_response_text,
    strands_enrichment_enabled,
)
from libs.shared_utils.http_exceptions import BadRequestException, NotFoundException

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"


def _valid_prompt(entity_type: str) -> str:
    """Build a minimal valid overview prompt for parsing tests."""
    return f"Prompt for {entity_type} with {{{{entity_name}}}} placeholder."


def _mock_org_repo(
    *,
    org_row: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a mock OrganizationRepository."""
    repo = MagicMock()
    repo.get_organization_by_id = AsyncMock(return_value=org_row)
    repo.update_organization = AsyncMock()
    return repo


def _strands_enabled_patch():
    """Patch strands_enrichment_enabled to True."""
    return patch(
        "apps.user_service.app.services.org_business_overview_enrichment_service."
        "strands_enrichment_enabled",
        return_value=True,
    )


def _iso_settings_patch(**overrides: Any):
    """Patch shared isometrik settings used by strands/OpenAI calls."""
    iso = MagicMock()
    iso.org_business_overview_on_create_enabled = True
    iso.strands_auth_token = "token"
    iso.domain_discovery_agent_id = "domain-agent"
    iso.business_overview_agent_id = "overview-agent"
    iso.org_overview_openai_timeout_seconds = 30
    for key, value in overrides.items():
        setattr(iso, key, value)
    return patch(
        "apps.user_service.app.services.org_business_overview_enrichment_service.shared_settings",
        MagicMock(isometrik=iso, org_memory_llm_model="gpt-test"),
    )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def test_strands_enrichment_enabled_all_configured():
    """strands_enrichment_enabled is True when all flags/tokens are set."""
    with _iso_settings_patch():
        assert strands_enrichment_enabled() is True


def test_strands_enrichment_enabled_missing_token():
    """strands_enrichment_enabled is False when auth token is blank."""
    with _iso_settings_patch(strands_auth_token=""):
        assert strands_enrichment_enabled() is False


def test_sanitize_org_name_trims_and_bounds():
    """Organization names are trimmed and capped at 255 chars."""
    assert _sanitize_org_name("  Acme  ") == "Acme"
    assert len(_sanitize_org_name("x" * 300)) == 255


def test_is_safe_http_url():
    """Only http(s) URLs with a host are considered safe."""
    assert _is_safe_http_url("https://example.com") is True
    assert _is_safe_http_url("ftp://example.com") is False
    assert _is_safe_http_url("not-a-url") is False


def test_normalize_website_url_non_string():
    """Non-string website inputs return None."""
    assert _normalize_website_url(None) is None
    assert _normalize_website_url(123) is None  # type: ignore[arg-type]


def test_parse_json_object_text_non_object_raises():
    """JSON arrays are rejected by _parse_json_object_text."""
    with pytest.raises(ValueError, match="must be an object"):
        _parse_json_object_text("[1, 2]")


def test_has_stored_overview_prompts_all_entities():
    """_has_stored_overview_prompts requires every entity prompt."""
    settings = {
        AI_OVERVIEW_SETTINGS_KEY: {
            "overview_prompts": {
                entity: f"Prompt {entity} {{{{entity_name}}}}"
                for entity in OVERVIEW_PROMPT_ENTITY_TYPES
            }
        }
    }
    assert _has_stored_overview_prompts(settings) is True


def test_has_stored_overview_prompts_partial():
    """Missing one entity prompt returns False."""
    settings = {
        AI_OVERVIEW_SETTINGS_KEY: {
            "overview_prompts": {
                "lead": "Lead {{{{entity_name}}}}",
                "contact": "Contact {{{{entity_name}}}}",
            }
        }
    }
    assert _has_stored_overview_prompts(settings) is False


def test_parse_overview_prompts_response_valid():
    """Valid LLM JSON is parsed into overview_prompts."""
    raw = json.dumps({entity: _valid_prompt(entity) for entity in OVERVIEW_PROMPT_ENTITY_TYPES})
    parsed = _parse_overview_prompts_response(raw)
    assert parsed is not None
    assert set(parsed.keys()) == set(OVERVIEW_PROMPT_ENTITY_TYPES)


def test_parse_overview_prompts_response_missing_placeholder():
    """Prompts without {{entity_name}} are rejected."""
    raw = json.dumps({"lead": "No placeholder here"})
    assert _parse_overview_prompts_response(raw, entity_types=("lead",)) is None


def test_parse_overview_prompts_response_invalid_json():
    """Invalid JSON returns None."""
    assert _parse_overview_prompts_response("not-json") is None


def test_log_strands_agent_failure_http_status_error():
    """HTTP status errors include status code and response body."""
    request = httpx.Request("POST", "https://strands.example/agents/run")
    response = httpx.Response(502, text="upstream error", request=request)
    exc = httpx.HTTPStatusError("bad gateway", request=request, response=response)
    _log_strands_agent_failure("test_event", exc, agent_id="a1")


def test_log_strands_agent_failure_request_error():
    """Request errors log the request URL when available."""
    request = httpx.Request("GET", "https://strands.example/timeout")
    exc = httpx.ConnectError("timeout", request=request)
    _log_strands_agent_failure("test_event", exc)


# ---------------------------------------------------------------------------
# should_enqueue_after_organization_created
# ---------------------------------------------------------------------------


def test_should_enqueue_when_eligible(monkeypatch: pytest.MonkeyPatch):
    """Enqueue when strands enabled, name present, and no stored overview/prompts."""
    monkeypatch.setattr(
        "apps.user_service.app.services.org_business_overview_enrichment_service."
        "strands_enrichment_enabled",
        lambda: True,
    )
    assert (
        OrgBusinessOverviewEnrichmentService.should_enqueue_after_organization_created(
            organization_id=ORG_ID,
            organization_name="Acme Corp",
            settings={},
        )
        is True
    )


def test_should_enqueue_skips_when_prompts_stored(monkeypatch: pytest.MonkeyPatch):
    """Do not enqueue when all overview prompts are already stored."""
    monkeypatch.setattr(
        "apps.user_service.app.services.org_business_overview_enrichment_service."
        "strands_enrichment_enabled",
        lambda: True,
    )
    settings = {
        AI_OVERVIEW_SETTINGS_KEY: {
            "overview_prompts": {
                entity: f"P {entity} {{{{entity_name}}}}" for entity in OVERVIEW_PROMPT_ENTITY_TYPES
            }
        }
    }
    assert (
        OrgBusinessOverviewEnrichmentService.should_enqueue_after_organization_created(
            organization_id=ORG_ID,
            organization_name="Acme",
            settings=settings,
        )
        is False
    )


def test_has_business_overview_helper():
    """_has_business_overview detects non-empty stored text."""
    assert OrgBusinessOverviewEnrichmentService._has_business_overview(None) is False
    assert OrgBusinessOverviewEnrichmentService._has_business_overview({}) is False
    settings = {AI_OVERVIEW_SETTINGS_KEY: {"business_overview": "  Facts  "}}
    assert OrgBusinessOverviewEnrichmentService._has_business_overview(settings) is True


# ---------------------------------------------------------------------------
# enqueue_enrichment_requested
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_skips_when_should_not_enqueue(monkeypatch: pytest.MonkeyPatch):
    """enqueue_enrichment_requested is a no-op when scheduling guard fails."""
    produce = AsyncMock()
    monkeypatch.setattr(
        "apps.user_service.app.services.org_business_overview_enrichment_service."
        "strands_enrichment_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.org_business_overview_enrichment_service."
        "get_kafka_event_service",
        lambda: MagicMock(produce_event=produce),
    )

    await OrgBusinessOverviewEnrichmentService.enqueue_enrichment_requested(
        organization_id=ORG_ID,
        organization_name="Acme",
        settings={},
        actor_user_id="user-1",
    )

    produce.assert_not_called()


@pytest.mark.asyncio
async def test_enqueue_kafka_disabled(monkeypatch: pytest.MonkeyPatch):
    """When Kafka is disabled, enqueue logs and returns without producing."""
    produce = AsyncMock()
    monkeypatch.setattr(
        "apps.user_service.app.services.org_business_overview_enrichment_service."
        "strands_enrichment_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.org_business_overview_enrichment_service.app_settings",
        MagicMock(kafka=MagicMock(enabled=False)),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.org_business_overview_enrichment_service."
        "get_kafka_event_service",
        lambda: MagicMock(produce_event=produce),
    )

    await OrgBusinessOverviewEnrichmentService.enqueue_enrichment_requested(
        organization_id=ORG_ID,
        organization_name="Acme",
        organization_website="https://acme.example",
        settings={},
        actor_user_id="user-1",
    )

    produce.assert_not_called()


@pytest.mark.asyncio
async def test_enqueue_success(monkeypatch: pytest.MonkeyPatch):
    """Successful enqueue publishes enrichment event to Kafka."""
    produce = AsyncMock()
    event = {"event_id": "evt-1"}
    monkeypatch.setattr(
        "apps.user_service.app.services.org_business_overview_enrichment_service."
        "strands_enrichment_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.org_business_overview_enrichment_service.app_settings",
        MagicMock(kafka=MagicMock(enabled=True)),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.org_business_overview_enrichment_service.EventService",
        lambda: MagicMock(build_event=lambda **kwargs: event),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.org_business_overview_enrichment_service."
        "get_kafka_event_service",
        lambda: MagicMock(produce_event=produce),
    )

    await OrgBusinessOverviewEnrichmentService.enqueue_enrichment_requested(
        organization_id=ORG_ID,
        organization_name="Acme Corp",
        organization_website="http://acme.example",
        settings={},
        actor_user_id="user-1",
    )

    produce.assert_awaited_once()
    assert produce.await_args.kwargs["key"] == ORG_ID


@pytest.mark.asyncio
async def test_enqueue_swallows_exceptions(monkeypatch: pytest.MonkeyPatch):
    """enqueue_enrichment_requested never raises to callers."""
    monkeypatch.setattr(
        "apps.user_service.app.services.org_business_overview_enrichment_service."
        "strands_enrichment_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.org_business_overview_enrichment_service.app_settings",
        MagicMock(kafka=MagicMock(enabled=True)),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.org_business_overview_enrichment_service.EventService",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    await OrgBusinessOverviewEnrichmentService.enqueue_enrichment_requested(
        organization_id=ORG_ID,
        organization_name="Acme",
        settings={},
        actor_user_id=None,
    )


# ---------------------------------------------------------------------------
# process_enrichment_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_enrichment_event_strands_disabled(monkeypatch: pytest.MonkeyPatch):
    """process_enrichment_event returns immediately when strands is off."""
    monkeypatch.setattr(
        "apps.user_service.app.services.org_business_overview_enrichment_service."
        "strands_enrichment_enabled",
        lambda: False,
    )
    pipeline = AsyncMock()
    monkeypatch.setattr(
        OrgBusinessOverviewEnrichmentService,
        "_run_enrichment_pipeline",
        pipeline,
    )

    await OrgBusinessOverviewEnrichmentService.process_enrichment_event(
        organization_id=ORG_ID,
        organization_name="Acme",
    )

    pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_process_enrichment_event_invalid_uuid(monkeypatch: pytest.MonkeyPatch):
    """Invalid organization_id is logged and skipped."""
    monkeypatch.setattr(
        "apps.user_service.app.services.org_business_overview_enrichment_service."
        "strands_enrichment_enabled",
        lambda: True,
    )
    pipeline = AsyncMock()
    monkeypatch.setattr(
        OrgBusinessOverviewEnrichmentService,
        "_run_enrichment_pipeline",
        pipeline,
    )

    await OrgBusinessOverviewEnrichmentService.process_enrichment_event(
        organization_id="not-a-uuid",
        organization_name="Acme",
    )

    pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_process_enrichment_event_already_enriched(monkeypatch: pytest.MonkeyPatch):
    """Duplicate Kafka deliveries skip when prompts already stored."""
    repo = _mock_org_repo(
        org_row={
            "settings": {
                AI_OVERVIEW_SETTINGS_KEY: {
                    "overview_prompts": {
                        entity: f"P {entity} {{{{entity_name}}}}"
                        for entity in OVERVIEW_PROMPT_ENTITY_TYPES
                    }
                }
            }
        }
    )

    @asynccontextmanager
    async def fake_db_repository(_organization_repository):
        yield repo

    monkeypatch.setattr(
        "apps.user_service.app.services.org_business_overview_enrichment_service."
        "strands_enrichment_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.org_business_overview_enrichment_service._db_repository",
        fake_db_repository,
    )
    pipeline = AsyncMock()
    monkeypatch.setattr(
        OrgBusinessOverviewEnrichmentService,
        "_run_enrichment_pipeline",
        pipeline,
    )

    await OrgBusinessOverviewEnrichmentService.process_enrichment_event(
        organization_id=ORG_ID,
        organization_name="Acme",
    )

    pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_process_enrichment_event_runs_pipeline(monkeypatch: pytest.MonkeyPatch):
    """Valid event loads website hint and runs enrichment pipeline."""
    repo = _mock_org_repo(org_row={"settings": {}})

    @asynccontextmanager
    async def fake_db_repository(_organization_repository):
        yield repo

    monkeypatch.setattr(
        "apps.user_service.app.services.org_business_overview_enrichment_service."
        "strands_enrichment_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.org_business_overview_enrichment_service._db_repository",
        fake_db_repository,
    )
    pipeline = AsyncMock()
    monkeypatch.setattr(
        OrgBusinessOverviewEnrichmentService,
        "_run_enrichment_pipeline",
        pipeline,
    )

    await OrgBusinessOverviewEnrichmentService.process_enrichment_event(
        organization_id=ORG_ID,
        organization_name="Acme",
        organization_website="https://acme.example",
    )

    pipeline.assert_awaited_once()
    assert pipeline.await_args.kwargs["organization_website"] == "https://acme.example"


# ---------------------------------------------------------------------------
# Strands agents: domain discovery & business overview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_official_website_success():
    """Domain discovery agent returns official website when confidence is high."""
    body = {"text": json.dumps({"official_website": "https://found.example", "confidence": 90})}
    with (
        _iso_settings_patch(),
        patch(
            "apps.user_service.app.services.org_business_overview_enrichment_service."
            "call_strands_agent",
            new=AsyncMock(return_value=body),
        ),
    ):
        result = await OrgBusinessOverviewEnrichmentService._discover_official_website("Acme")

    assert result == "https://found.example"


@pytest.mark.asyncio
async def test_discover_official_website_low_confidence():
    """Low-confidence discovery results are discarded."""
    body = {"text": json.dumps({"official_website": "https://found.example", "confidence": 10})}
    with (
        _iso_settings_patch(),
        patch(
            "apps.user_service.app.services.org_business_overview_enrichment_service."
            "call_strands_agent",
            new=AsyncMock(return_value=body),
        ),
    ):
        result = await OrgBusinessOverviewEnrichmentService._discover_official_website("Acme")

    assert result is None


@pytest.mark.asyncio
async def test_discover_official_website_http_error():
    """HTTP errors from strands are logged and return None."""
    request = httpx.Request("POST", "https://strands.example/run")
    response = httpx.Response(500, request=request)
    exc = httpx.HTTPStatusError("err", request=request, response=response)
    with (
        _iso_settings_patch(),
        patch(
            "apps.user_service.app.services.org_business_overview_enrichment_service."
            "call_strands_agent",
            new=AsyncMock(side_effect=exc),
        ),
    ):
        result = await OrgBusinessOverviewEnrichmentService._discover_official_website("Acme")

    assert result is None


@pytest.mark.asyncio
async def test_discover_official_website_generic_error():
    """Unexpected exceptions during discovery return None."""
    with (
        _iso_settings_patch(),
        patch(
            "apps.user_service.app.services.org_business_overview_enrichment_service."
            "call_strands_agent",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
    ):
        result = await OrgBusinessOverviewEnrichmentService._discover_official_website("Acme")

    assert result is None


@pytest.mark.asyncio
async def test_discover_official_website_empty_text():
    """Empty strands text response returns None."""
    with (
        _iso_settings_patch(),
        patch(
            "apps.user_service.app.services.org_business_overview_enrichment_service."
            "call_strands_agent",
            new=AsyncMock(return_value={"text": "   "}),
        ),
    ):
        result = await OrgBusinessOverviewEnrichmentService._discover_official_website("Acme")

    assert result is None


@pytest.mark.asyncio
async def test_fetch_business_overview_success():
    """Business overview agent returns trimmed overview text."""
    body = {"text": json.dumps({"business_overview": "  SaaS vendor.  "})}
    with (
        _iso_settings_patch(),
        patch(
            "apps.user_service.app.services.org_business_overview_enrichment_service."
            "call_strands_agent",
            new=AsyncMock(return_value=body),
        ),
    ):
        result = await OrgBusinessOverviewEnrichmentService._fetch_business_overview(
            "https://acme.example"
        )

    assert result == "SaaS vendor."


@pytest.mark.asyncio
async def test_fetch_business_overview_missing_field():
    """Missing business_overview field returns None."""
    body = {"text": json.dumps({"summary": "wrong key"})}
    with (
        _iso_settings_patch(),
        patch(
            "apps.user_service.app.services.org_business_overview_enrichment_service."
            "call_strands_agent",
            new=AsyncMock(return_value=body),
        ),
    ):
        result = await OrgBusinessOverviewEnrichmentService._fetch_business_overview(
            "https://acme.example"
        )

    assert result is None


@pytest.mark.asyncio
async def test_fetch_business_overview_http_error():
    """HTTP errors during overview fetch return None."""
    request = httpx.Request("POST", "https://strands.example/run")
    response = httpx.Response(503, request=request)
    exc = httpx.HTTPStatusError("err", request=request, response=response)
    with (
        _iso_settings_patch(),
        patch(
            "apps.user_service.app.services.org_business_overview_enrichment_service."
            "call_strands_agent",
            new=AsyncMock(side_effect=exc),
        ),
    ):
        result = await OrgBusinessOverviewEnrichmentService._fetch_business_overview(
            "https://acme.example"
        )

    assert result is None


# ---------------------------------------------------------------------------
# Pipeline, persistence, and OpenAI prompt generation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_website_url_provided():
    """Provided website is normalized and tagged as 'provided'."""
    website, source = await OrgBusinessOverviewEnrichmentService._resolve_website_url(
        organization_id=ORG_ID,
        organization_name="Acme",
        organization_website="http://acme.example",
    )
    assert website == "https://acme.example"
    assert source == "provided"


@pytest.mark.asyncio
async def test_resolve_website_url_discovered(monkeypatch: pytest.MonkeyPatch):
    """Missing website triggers domain discovery agent."""
    monkeypatch.setattr(
        OrgBusinessOverviewEnrichmentService,
        "_discover_official_website",
        AsyncMock(return_value="https://discovered.example"),
    )
    website, source = await OrgBusinessOverviewEnrichmentService._resolve_website_url(
        organization_id=ORG_ID,
        organization_name="Acme",
        organization_website=None,
    )
    assert website == "https://discovered.example"
    assert source == "discovered"


@pytest.mark.asyncio
async def test_run_enrichment_pipeline_no_website(monkeypatch: pytest.MonkeyPatch):
    """Pipeline stores default prompts when no website can be resolved."""
    persist_defaults = AsyncMock()
    monkeypatch.setattr(
        OrgBusinessOverviewEnrichmentService,
        "_resolve_website_url",
        AsyncMock(return_value=(None, "none")),
    )
    monkeypatch.setattr(
        OrgBusinessOverviewEnrichmentService,
        "_persist_default_prompts",
        persist_defaults,
    )

    await OrgBusinessOverviewEnrichmentService._run_enrichment_pipeline(
        ORG_ID,
        "Acme",
        organization_repository=_mock_org_repo(),
    )

    persist_defaults.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_enrichment_pipeline_success(monkeypatch: pytest.MonkeyPatch):
    """Full pipeline persists website, overview, and generated prompts."""
    repo = _mock_org_repo(org_row={"settings": {}})
    prompts = {entity: _valid_prompt(entity) for entity in OVERVIEW_PROMPT_ENTITY_TYPES}
    monkeypatch.setattr(
        OrgBusinessOverviewEnrichmentService,
        "_resolve_website_url",
        AsyncMock(return_value=("https://acme.example", "provided")),
    )
    monkeypatch.setattr(
        OrgBusinessOverviewEnrichmentService,
        "_persist_website_url",
        AsyncMock(),
    )
    monkeypatch.setattr(
        OrgBusinessOverviewEnrichmentService,
        "_fetch_business_overview",
        AsyncMock(return_value="Long overview text"),
    )
    monkeypatch.setattr(
        OrgBusinessOverviewEnrichmentService,
        "_generate_overview_prompts",
        AsyncMock(return_value=prompts),
    )
    persist = AsyncMock()
    monkeypatch.setattr(
        OrgBusinessOverviewEnrichmentService,
        "_persist_ai_settings",
        persist,
    )

    await OrgBusinessOverviewEnrichmentService._run_enrichment_pipeline(
        ORG_ID,
        "Acme",
        organization_repository=repo,
        organization_website="https://acme.example",
    )

    persist.assert_awaited_once()
    assert persist.await_args.kwargs["business_overview"] == "Long overview text"
    assert persist.await_args.kwargs["overview_prompts"] == prompts


@pytest.mark.asyncio
async def test_run_enrichment_pipeline_exception_fallback(monkeypatch: pytest.MonkeyPatch):
    """Pipeline exceptions trigger default prompt fallback."""
    monkeypatch.setattr(
        OrgBusinessOverviewEnrichmentService,
        "_resolve_website_url",
        AsyncMock(side_effect=RuntimeError("pipeline failed")),
    )
    persist_defaults = AsyncMock()
    monkeypatch.setattr(
        OrgBusinessOverviewEnrichmentService,
        "_persist_default_prompts",
        persist_defaults,
    )

    await OrgBusinessOverviewEnrichmentService._run_enrichment_pipeline(
        ORG_ID,
        "Acme",
        organization_repository=_mock_org_repo(),
    )

    persist_defaults.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_overview_prompts_openai_success():
    """OpenAI chat completion generates validated overview prompts."""
    raw = json.dumps({entity: _valid_prompt(entity) for entity in OVERVIEW_PROMPT_ENTITY_TYPES})
    with (
        _iso_settings_patch(),
        patch(
            "apps.user_service.app.services.org_business_overview_enrichment_service."
            "create_chat_completion",
            new=AsyncMock(return_value=raw),
        ),
    ):
        result = await OrgBusinessOverviewEnrichmentService._generate_overview_prompts(
            business_overview="B2B SaaS",
            organization_name="Acme",
            website_url="https://acme.example",
        )

    assert result is not None
    assert "lead" in result


@pytest.mark.asyncio
async def test_generate_overview_prompts_openai_failure():
    """OpenAI failures return None."""
    with (
        _iso_settings_patch(),
        patch(
            "apps.user_service.app.services.org_business_overview_enrichment_service."
            "create_chat_completion",
            new=AsyncMock(side_effect=RuntimeError("openai down")),
        ),
    ):
        result = await OrgBusinessOverviewEnrichmentService._generate_overview_prompts(
            business_overview="B2B SaaS",
            organization_name="Acme",
        )

    assert result is None


@pytest.mark.asyncio
async def test_persist_ai_settings_merges_and_updates():
    """_persist_ai_settings merges AI fields into organization settings."""
    repo = _mock_org_repo(org_row={"settings": {}})

    await OrgBusinessOverviewEnrichmentService._persist_ai_settings(
        ORG_ID,
        organization_repository=repo,
        business_overview="Overview text",
        overview_prompts={"lead": _valid_prompt("lead")},
        force_business_overview=True,
    )

    repo.update_organization.assert_awaited_once()
    update_payload = repo.update_organization.await_args.args[1]
    assert "settings" in update_payload


@pytest.mark.asyncio
async def test_persist_website_url_skips_when_unchanged():
    """Website URL is not rewritten when already normalized."""
    repo = _mock_org_repo(
        org_row={"settings": {"website_url": "https://acme.example"}},
    )

    await OrgBusinessOverviewEnrichmentService._persist_website_url(
        ORG_ID,
        "https://acme.example",
        organization_repository=repo,
    )

    repo.update_organization.assert_not_called()


@pytest.mark.asyncio
async def test_website_from_org_row_domain_and_settings():
    """Website is read from domain column or settings.website_url."""
    assert (
        OrgBusinessOverviewEnrichmentService._website_from_org_row(
            {"domain": "https://domain.example"}
        )
        == "https://domain.example"
    )
    assert (
        OrgBusinessOverviewEnrichmentService._website_from_org_row(
            {"settings": {"website_url": "https://settings.example"}}
        )
        == "https://settings.example"
    )
    assert OrgBusinessOverviewEnrichmentService._website_from_org_row(None) is None


@pytest.mark.asyncio
async def test_load_business_overview_text():
    """Stored business overview text is returned when present."""
    repo = _mock_org_repo(
        org_row={"settings": {AI_OVERVIEW_SETTINGS_KEY: {"business_overview": "  Stored  "}}}
    )

    result = await OrgBusinessOverviewEnrichmentService._load_business_overview_text(
        ORG_ID,
        organization_repository=repo,
    )

    assert result == "Stored"


@pytest.mark.asyncio
async def test_enrichment_already_persisted_missing_org():
    """Missing org row is treated as already persisted (skip)."""
    repo = _mock_org_repo(org_row=None)
    assert (
        await OrgBusinessOverviewEnrichmentService._enrichment_already_persisted(ORG_ID, repo)
        is True
    )


# ---------------------------------------------------------------------------
# Refetch error paths and cache invalidation
# ---------------------------------------------------------------------------


def test_require_strands_for_refetch_raises():
    """refetch requires strands enrichment to be configured."""
    with patch(
        "apps.user_service.app.services.org_business_overview_enrichment_service."
        "strands_enrichment_enabled",
        return_value=False,
    ):
        with pytest.raises(BadRequestException):
            OrgBusinessOverviewEnrichmentService._require_strands_for_refetch()


@pytest.mark.asyncio
async def test_refetch_org_context_not_found():
    """Refetch raises NotFound when organization is missing."""
    repo = _mock_org_repo(org_row=None)
    with pytest.raises(NotFoundException):
        await OrgBusinessOverviewEnrichmentService._refetch_org_context(ORG_ID, repo)


@pytest.mark.asyncio
async def test_refetch_org_context_empty_name():
    """Refetch rejects organizations with blank names."""
    repo = _mock_org_repo(org_row={"name": "   "})
    with pytest.raises(BadRequestException):
        await OrgBusinessOverviewEnrichmentService._refetch_org_context(ORG_ID, repo)


@pytest.mark.asyncio
async def test_refetch_business_overview_no_website():
    """Refetch business overview requires a resolvable website."""
    repo = _mock_org_repo(org_row={"settings": {}})
    with (
        patch.object(
            OrgBusinessOverviewEnrichmentService,
            "_ensure_organization_website",
            new=AsyncMock(return_value=None),
        ),
        pytest.raises(BadRequestException),
    ):
        await OrgBusinessOverviewEnrichmentService._refetch_business_overview_field(
            ORG_ID,
            "Acme",
            None,
            organization_repository=repo,
        )


@pytest.mark.asyncio
async def test_refetch_business_overview_fetch_failed():
    """Refetch raises when strands overview agent returns nothing."""
    repo = _mock_org_repo(org_row={"settings": {}})
    with (
        patch.object(
            OrgBusinessOverviewEnrichmentService,
            "_fetch_business_overview",
            new=AsyncMock(return_value=None),
        ),
        pytest.raises(BadRequestException),
    ):
        await OrgBusinessOverviewEnrichmentService._refetch_business_overview_field(
            ORG_ID,
            "Acme",
            "https://acme.example",
            organization_repository=repo,
        )


@pytest.mark.asyncio
async def test_refetch_prompts_requires_stored_overview():
    """Prompt refetch requires stored business overview text."""
    repo = _mock_org_repo(org_row={"settings": {}})
    with (
        patch.object(
            OrgBusinessOverviewEnrichmentService,
            "_load_business_overview_text",
            new=AsyncMock(return_value=None),
        ),
        pytest.raises(BadRequestException),
    ):
        await OrgBusinessOverviewEnrichmentService._refetch_overview_prompt_fields(
            ORG_ID,
            "Acme",
            "https://acme.example",
            ("lead",),
            organization_repository=repo,
        )


@pytest.mark.asyncio
async def test_refetch_prompts_uses_defaults_on_gen_failure():
    """Prompt refetch falls back to platform defaults when OpenAI fails."""
    repo = _mock_org_repo(org_row={"settings": {}})
    persist = AsyncMock()
    with (
        patch.object(
            OrgBusinessOverviewEnrichmentService,
            "_load_business_overview_text",
            new=AsyncMock(return_value="Stored overview"),
        ),
        patch.object(
            OrgBusinessOverviewEnrichmentService,
            "_generate_overview_prompts",
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            OrgBusinessOverviewEnrichmentService,
            "_persist_ai_settings",
            persist,
        ),
    ):
        await OrgBusinessOverviewEnrichmentService._refetch_overview_prompt_fields(
            ORG_ID,
            "Acme",
            "https://acme.example",
            ("lead",),
            organization_repository=repo,
        )

    assert persist.await_args.kwargs["overview_prompts"]["lead"] == DEFAULT_OVERVIEW_PROMPTS["lead"]


def test_invalidate_refetch_cache_noop_for_unrelated_fields():
    """Cache invalidation skips unrelated refetch field sets."""
    with patch(
        "apps.user_service.app.services.organization_memory_service."
        "invalidate_organization_memory_cache",
    ) as invalidate:
        OrgBusinessOverviewEnrichmentService._invalidate_refetch_cache(ORG_ID, {"other"})
        invalidate.assert_not_called()


def test_invalidate_refetch_cache_for_overview():
    """Cache invalidation runs when business_overview was refetched."""
    with patch(
        "apps.user_service.app.services.organization_memory_service."
        "invalidate_organization_memory_cache",
    ) as invalidate:
        OrgBusinessOverviewEnrichmentService._invalidate_refetch_cache(
            ORG_ID,
            {"business_overview"},
        )
        invalidate.assert_called_once_with(ORG_ID)


@pytest.mark.asyncio
async def test_build_refetch_response_scoped():
    """Refetch response includes only requested fields."""
    org_row = {
        "settings": {
            "website_url": "https://acme.example",
            AI_OVERVIEW_SETTINGS_KEY: {
                "business_overview": "Overview",
                "overview_prompts": {
                    entity: _valid_prompt(entity) for entity in OVERVIEW_PROMPT_ENTITY_TYPES
                },
            },
        }
    }
    repo = _mock_org_repo(org_row=org_row)

    result = await OrgBusinessOverviewEnrichmentService._build_refetch_response(
        ORG_ID,
        {"business_overview"},
        (),
        organization_repository=repo,
    )

    assert "business_overview" in result
    assert result["website_url"] == "https://acme.example"
    assert "overview_prompts" not in result


def test_strands_response_text_and_parse_json_roundtrip():
    """Helper round-trip for strands JSON payloads."""
    payload = {"official_website": "https://x.example", "confidence": 80}
    text = json.dumps(payload)
    assert _strands_response_text({"text": text}) == text
    assert _parse_json_object_text(text) == payload
