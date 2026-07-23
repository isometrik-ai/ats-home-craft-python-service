"""Integration tests for email templates endpoints."""

import pytest

from apps.user_service.tests.integration.helpers import patch_check_permissions
from apps.user_service.tests.utils.assertions import assert_success

TEMPLATE_ID = "tmpl-1"

_FAKE_TEMPLATE_SUMMARY = {
    "id": TEMPLATE_ID,
    "name": "Welcome Email",
    "template_type": "trigger",
    "status": "draft",
    "is_default": False,
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}

_FAKE_TEMPLATE_DETAIL = {
    **_FAKE_TEMPLATE_SUMMARY,
    "subject": "Welcome",
    "html_content": "<p>Hello</p>",
    "variables": [],
}


@pytest.mark.asyncio
async def test_create_email_template(monkeypatch, client):
    """POST /email-templates creates a template."""

    patch_check_permissions(monkeypatch, "apps.user_service.app.api.email_templates")

    async def fake_create(_self, body):
        del _self
        assert body.name == "Welcome Email"
        return {"id": TEMPLATE_ID, "name": body.name}

    monkeypatch.setattr(
        "apps.user_service.app.services.email_template_service."
        "EmailTemplateService.create_email_template",
        fake_create,
    )

    res = await client.post(
        "/v1/email-templates",
        json={
            "name": "Welcome Email",
            "template_type": "trigger",
            "html_content": "<p>Hello</p>",
        },
    )
    assert_success(res, 201)


@pytest.mark.asyncio
async def test_generate_email_template_ai(monkeypatch, client):
    """POST /email-templates/generate uses AI agent."""

    patch_check_permissions(monkeypatch, "apps.user_service.app.api.email_templates")

    async def fake_generate(_self, *, query: str):
        del _self
        assert query == "Create a welcome email"
        return TEMPLATE_ID

    monkeypatch.setattr(
        "apps.user_service.app.services.email_template_service."
        "EmailTemplateService.generate_email_template_with_ai",
        fake_generate,
    )

    res = await client.post(
        "/v1/email-templates/generate",
        json={"query": "Create a welcome email"},
    )
    body = assert_success(res, 200)
    assert body["data"]["template_id"] == TEMPLATE_ID


@pytest.mark.asyncio
async def test_list_email_templates(monkeypatch, client):
    """GET /email-templates returns template list."""

    patch_check_permissions(monkeypatch, "apps.user_service.app.api.email_templates")

    async def fake_list(_self, *, template_type=None, status=None):
        del _self, template_type, status
        return [_FAKE_TEMPLATE_SUMMARY], 1

    monkeypatch.setattr(
        "apps.user_service.app.services.email_template_service."
        "EmailTemplateService.list_email_templates",
        fake_list,
    )

    res = await client.get("/v1/email-templates")
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == TEMPLATE_ID
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_list_email_templates_empty(monkeypatch, client):
    """GET /email-templates returns empty collection."""

    patch_check_permissions(monkeypatch, "apps.user_service.app.api.email_templates")

    async def fake_list(_self, **kwargs):
        del _self, kwargs
        return [], 0

    monkeypatch.setattr(
        "apps.user_service.app.services.email_template_service."
        "EmailTemplateService.list_email_templates",
        fake_list,
    )

    res = await client.get("/v1/email-templates")
    body = assert_success(res, 200)
    assert body["data"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_get_email_template(monkeypatch, client):
    """GET /email-templates/{id} returns template detail."""

    patch_check_permissions(monkeypatch, "apps.user_service.app.api.email_templates")

    async def fake_get(_self, template_id: str):
        del _self
        assert template_id == TEMPLATE_ID
        return _FAKE_TEMPLATE_DETAIL

    monkeypatch.setattr(
        "apps.user_service.app.services.email_template_service."
        "EmailTemplateService.get_email_template",
        fake_get,
    )

    res = await client.get(f"/v1/email-templates/{TEMPLATE_ID}")
    body = assert_success(res, 200)
    assert body["data"]["id"] == TEMPLATE_ID
    assert body["data"]["name"] == "Welcome Email"


@pytest.mark.asyncio
async def test_render_email_template(monkeypatch, client):
    """POST /email-templates/{id}/render merges variables."""

    patch_check_permissions(monkeypatch, "apps.user_service.app.api.email_templates")

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
        f"/v1/email-templates/{TEMPLATE_ID}/render",
        json={"variable_values": {"brand": "Acme"}},
    )
    body = assert_success(res, 200)
    assert body["data"]["html"] == "<p>Hello Acme</p>"


@pytest.mark.asyncio
async def test_update_email_template(monkeypatch, client):
    """PATCH /email-templates/{id} updates a template."""

    patch_check_permissions(monkeypatch, "apps.user_service.app.api.email_templates")

    async def fake_update(_self, template_id: str, body):
        del _self
        assert template_id == TEMPLATE_ID
        assert body.name == "Updated Email"
        return _FAKE_TEMPLATE_DETAIL, {**_FAKE_TEMPLATE_DETAIL, "name": "Updated Email"}

    monkeypatch.setattr(
        "apps.user_service.app.services.email_template_service."
        "EmailTemplateService.update_email_template",
        fake_update,
    )

    res = await client.patch(
        f"/v1/email-templates/{TEMPLATE_ID}",
        json={"name": "Updated Email"},
    )
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_delete_email_template(monkeypatch, client):
    """DELETE /email-templates/{id} removes a template."""

    patch_check_permissions(monkeypatch, "apps.user_service.app.api.email_templates")

    async def fake_delete(_self, template_id: str):
        del _self
        assert template_id == TEMPLATE_ID
        return _FAKE_TEMPLATE_DETAIL

    monkeypatch.setattr(
        "apps.user_service.app.services.email_template_service."
        "EmailTemplateService.delete_email_template",
        fake_delete,
    )

    res = await client.delete(f"/v1/email-templates/{TEMPLATE_ID}")
    assert_success(res, 200)
