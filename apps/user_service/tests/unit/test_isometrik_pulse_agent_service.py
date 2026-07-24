"""Unit tests for Isometrik Pulse Agent provisioning helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.services.isometrik_pulse_agent_service import (
    IsometrikPulseAgentService,
    _typesense_tool_host,
    build_pulse_agent_payload,
    pulse_agent_id_from_response,
)

_PULSE = "apps.user_service.app.services.isometrik_pulse_agent_service"


def test_typesense_tool_host_strips_protocol_and_port():
    """Host helper accepts bare hostnames and URL forms."""
    settings = SimpleNamespace(typesense=SimpleNamespace(host="typesense.example.com:8108"))
    with patch(f"{_PULSE}.shared_settings", settings):
        assert _typesense_tool_host() == "typesense.example.com"

    settings.typesense.host = "https://search.example.com:443"
    with patch(f"{_PULSE}.shared_settings", settings):
        assert _typesense_tool_host() == "search.example.com"


def test_build_pulse_agent_payload_includes_typesense_tool():
    """Payload includes Typesense tool config and model settings."""
    settings = SimpleNamespace(
        typesense=SimpleNamespace(
            host="localhost:8108",
            search_only_api_key="ts-key",
            contacts_collection_name="contacts",
        ),
        openai_api_key="sk-test",
    )
    with patch(f"{_PULSE}.shared_settings", settings):
        payload = build_pulse_agent_payload(project_id="proj-1")

    assert payload["project_id"] == "proj-1"
    assert payload["name"] == "Pulse Agent"
    assert payload["tools"][0]["name"] == "typesense"
    assert payload["tools"][0]["config"]["collection"] == "contacts"


@pytest.mark.parametrize(
    "response,expected",
    [
        ({"bot_id": " bot-123 "}, "bot-123"),
        ({"bot_id": ""}, None),
        ({"bot_id": 42}, None),
        ({}, None),
    ],
)
def test_pulse_agent_id_from_response(response, expected):
    """bot_id extraction trims strings and ignores invalid values."""
    assert pulse_agent_id_from_response(response) == expected


@pytest.mark.asyncio
async def test_create_for_organization_skips_when_disabled():
    """Best-effort create returns immediately when Isometrik is disabled."""
    settings = SimpleNamespace(isometrik=SimpleNamespace(is_enabled=False))
    with patch(f"{_PULSE}.shared_settings", settings):
        with patch(f"{_PULSE}.create_isometrik_ai_agent", AsyncMock()) as mock_create:
            await IsometrikPulseAgentService.create_for_organization_best_effort(
                organization_id="org-1",
                isometrik_details={"projectId": "p1", "appSecret": "s", "licenseKey": "k"},
                organization_repository=MagicMock(),
            )
    mock_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_for_organization_skips_missing_repository():
    """Missing organization repository logs and returns without raising."""
    settings = SimpleNamespace(isometrik=SimpleNamespace(is_enabled=True))
    with patch(f"{_PULSE}.shared_settings", settings):
        with patch(f"{_PULSE}.create_isometrik_ai_agent", AsyncMock()) as mock_create:
            await IsometrikPulseAgentService.create_for_organization_best_effort(
                organization_id="org-1",
                isometrik_details={"projectId": "p1", "appSecret": "s", "licenseKey": "k"},
                organization_repository=None,
            )
    mock_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_for_organization_skips_missing_credentials():
    """Incomplete Isometrik credentials skip agent creation."""
    settings = SimpleNamespace(isometrik=SimpleNamespace(is_enabled=True))
    repo = MagicMock()
    with patch(f"{_PULSE}.shared_settings", settings):
        with patch(f"{_PULSE}.create_isometrik_ai_agent", AsyncMock()) as mock_create:
            await IsometrikPulseAgentService.create_for_organization_best_effort(
                organization_id="org-1",
                isometrik_details={"projectId": "p1"},
                organization_repository=repo,
            )
    mock_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_for_organization_persists_bot_id():
    """Successful create persists pulse agent id into organization settings."""
    settings = SimpleNamespace(
        isometrik=SimpleNamespace(is_enabled=True),
        typesense=SimpleNamespace(
            host="localhost:8108",
            search_only_api_key="ts-key",
            contacts_collection_name="contacts",
        ),
        openai_api_key="sk-test",
    )
    repo = MagicMock()
    repo.get_organization_by_id = AsyncMock(return_value={"settings": "{}"})
    repo.update_organization = AsyncMock()

    with patch(f"{_PULSE}.shared_settings", settings):
        with patch(
            f"{_PULSE}.create_isometrik_ai_agent",
            AsyncMock(return_value={"bot_id": "bot-99"}),
        ):
            await IsometrikPulseAgentService.create_for_organization_best_effort(
                organization_id="org-1",
                isometrik_details={
                    "projectId": "proj-1",
                    "appSecret": "secret",
                    "licenseKey": "license",
                },
                organization_repository=repo,
            )

    repo.update_organization.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_for_organization_swallows_exceptions():
    """Best-effort create never raises on downstream failures."""
    settings = SimpleNamespace(isometrik=SimpleNamespace(is_enabled=True))
    repo = MagicMock()
    with patch(f"{_PULSE}.shared_settings", settings):
        with patch(
            f"{_PULSE}.create_isometrik_ai_agent",
            AsyncMock(side_effect=RuntimeError("network down")),
        ):
            await IsometrikPulseAgentService.create_for_organization_best_effort(
                organization_id="org-1",
                isometrik_details={
                    "projectId": "proj-1",
                    "appSecret": "secret",
                    "licenseKey": "license",
                },
                organization_repository=repo,
            )
