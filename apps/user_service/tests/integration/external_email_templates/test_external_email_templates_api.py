"""Integration tests for external email templates endpoints."""

import pytest
from fastapi import Request

from apps.user_service.app.dependencies.external_auth import get_organization_context
from apps.user_service.app.main import app
from apps.user_service.tests.utils.assertions import assert_success
from libs.shared_middleware.ross_ai_integration_auth import (
    verify_ross_ai_integration_api_key,
)

ORG_ID = "org-123"
TEMPLATE_ID = "tmpl-1"

_FAKE_TEMPLATE_DETAIL = {
    "id": TEMPLATE_ID,
    "name": "Welcome Email",
    "template_type": "trigger",
    "status": "draft",
    "is_default": False,
    "subject": "Welcome",
    "html_content": "<p>Hello</p>",
    "variables": [],
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}


async def _fake_org_context(request: Request) -> str:
    """Return a fixed organization id and set external audit actor email."""
    request.state.external_actor_email = "api@acme.com"
    return ORG_ID


async def _fake_ross_ai_api_key() -> None:
    """Bypass Ross AI API key verification in tests."""


@pytest.fixture
def external_org_context():
    """Override external organization context dependency."""
    app.dependency_overrides[get_organization_context] = _fake_org_context
    yield
    app.dependency_overrides.pop(get_organization_context, None)


@pytest.fixture
def ross_ai_auth():
    """Override Ross AI integration API key dependency."""
    app.dependency_overrides[verify_ross_ai_integration_api_key] = _fake_ross_ai_api_key
    yield
    app.dependency_overrides.pop(verify_ross_ai_integration_api_key, None)


@pytest.mark.asyncio
async def test_external_get_template(monkeypatch, client, external_org_context):
    """GET /integrations/email-templates/{id} returns detail."""

    async def fake_get(_self, template_id: str):
        del _self
        assert template_id == TEMPLATE_ID
        return _FAKE_TEMPLATE_DETAIL

    monkeypatch.setattr(
        "apps.user_service.app.services.email_template_service."
        "EmailTemplateService.get_email_template",
        fake_get,
    )

    res = await client.get(f"/v1/integrations/email-templates/{TEMPLATE_ID}")
    body = assert_success(res, 200)
    assert body["data"]["id"] == TEMPLATE_ID
    assert body["data"]["name"] == "Welcome Email"


@pytest.mark.asyncio
async def test_external_render_template(monkeypatch, client, external_org_context):
    """POST /integrations/email-templates/{id}/render merges vars."""

    async def fake_render(_self, template_id: str, body):
        del _self
        assert template_id == TEMPLATE_ID
        assert body.variable_values == {"brand": "Acme"}
        return {"html": "<p>Hello Acme</p>"}

    monkeypatch.setattr(
        "apps.user_service.app.services.email_template_service."
        "EmailTemplateService.render_email_template",
        fake_render,
    )

    res = await client.post(
        f"/v1/integrations/email-templates/{TEMPLATE_ID}/render",
        json={"variable_values": {"brand": "Acme"}},
    )
    body = assert_success(res, 200)
    assert body["data"]["html"] == "<p>Hello Acme</p>"


@pytest.mark.asyncio
async def test_external_create_template(monkeypatch, client, ross_ai_auth):
    """POST /integrations/email-templates creates a template."""

    async def fake_resolve_org(*, request, organization_id, db_connection):
        del db_connection
        request.state.external_actor_email = "api@acme.com"
        assert organization_id == ORG_ID
        return ORG_ID

    async def fake_create(_self, body):
        del _self
        assert body.name == "Welcome Email"
        return {
            "id": TEMPLATE_ID,
            "name": body.name,
            "template_type": "trigger",
            "status": "draft",
        }

    monkeypatch.setattr(
        "apps.user_service.app.api.external_email_templates.resolve_external_organization_id",
        fake_resolve_org,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.email_template_service."
        "EmailTemplateService.create_email_template",
        fake_create,
    )

    res = await client.post(
        "/v1/integrations/email-templates",
        json={
            "organization_id": ORG_ID,
            "name": "Welcome Email",
            "template_type": "trigger",
            "html_content": "<p>Hello</p>",
        },
        headers={"Rossai-Api-Key": "test-key"},
    )
    body = assert_success(res, 201)
    assert body["data"]["template_id"] == TEMPLATE_ID
