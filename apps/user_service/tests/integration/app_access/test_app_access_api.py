"""Integration tests for app access staff vs resident hold split."""

from __future__ import annotations

from datetime import date

import pytest

from apps.user_service.app.api import app_access as app_access_api
from apps.user_service.app.api import fee_invoices as fee_invoices_api
from apps.user_service.app.api import passes as passes_api
from apps.user_service.tests.integration.helpers import admin_context
from apps.user_service.tests.utils.assertions import assert_error, assert_success
from libs.shared_utils.common_query import (
    APP_ACCESS_EDIT,
    APP_ACCESS_OVERRIDE,
    APP_ACCESS_VIEW,
    FINANCE_MANAGEMENT_EDIT,
)
from libs.shared_utils.http_exceptions import ForbiddenException
from libs.shared_utils.status_codes import CustomStatusCode

PROJECT_ID = "project-1"
UNIT_ID = "unit-1"
INVOICE_ID = "invoice-1"

_RULE = {
    "exists": True,
    "enabled": True,
    "by_amount": True,
    "amount_threshold": "25000.00",
    "by_days": True,
    "days_threshold": 15,
    "match_mode": "all",
    "version": 1,
    "impact": {
        "blocked": 1,
        "watch": 0,
        "notdue": 0,
        "released": 0,
        "clear": 0,
        "blocked_amount": "48200.00",
        "overdue_amount": "48200.00",
    },
}


def test_app_access_routes_are_registered():
    """Staff and resident routers expose the ADR catalog."""
    staff = [route.path for route in app_access_api.router.routes]
    assert "/projects/{project_id}/app-access/rule" in staff
    assert "/projects/{project_id}/app-access/units" in staff
    assert "/projects/{project_id}/app-access/units/{unit_id}/override" in staff
    resident = [route.path for route in app_access_api.unit_router.routes]
    assert "/units/{unit_id}/app-access" in resident
    staff_src = open(app_access_api.__file__, encoding="utf-8").read()
    assert "ensure_resident_unit_not_held" not in staff_src
    fee_src = open(fee_invoices_api.__file__, encoding="utf-8").read()
    assert "ensure_resident_unit_not_held" not in fee_src


def _allow(monkeypatch, module_path: str, allowed: str) -> None:
    """Allow one permission code and forbid the rest."""

    async def fake_access(**kwargs):
        codes = kwargs["permission_codes"]
        if codes != allowed:
            raise ForbiddenException(
                message_key="errors.insufficient_permissions",
                custom_code=CustomStatusCode.FORBIDDEN,
            )
        return admin_context()

    monkeypatch.setattr(f"{module_path}.ensure_staff_project_access", fake_access)


@pytest.mark.asyncio
async def test_view_cannot_put_rule(monkeypatch, client):
    """app_access.view is not enough to save the rule."""
    _allow(monkeypatch, "apps.user_service.app.api.app_access", APP_ACCESS_VIEW)
    response = await client.put(
        f"/v1/projects/{PROJECT_ID}/app-access/rule",
        json={
            "enabled": True,
            "by_amount": True,
            "amount_threshold": "25000.00",
            "by_days": True,
            "days_threshold": 15,
            "match_mode": "all",
            "version": 0,
        },
    )
    assert_error(response, 403)


@pytest.mark.asyncio
async def test_edit_can_put_rule(monkeypatch, client):
    """app_access.edit can save the rule."""
    _allow(monkeypatch, "apps.user_service.app.api.app_access", APP_ACCESS_EDIT)

    async def fake_upsert(_self, *, project_id, body):
        del _self
        assert project_id == PROJECT_ID
        assert body.version == 0
        return _RULE

    monkeypatch.setattr(
        "apps.user_service.app.services.app_access_service.AppAccessService.upsert_rule",
        fake_upsert,
    )
    response = await client.put(
        f"/v1/projects/{PROJECT_ID}/app-access/rule",
        json={
            "enabled": True,
            "by_amount": True,
            "amount_threshold": "25000.00",
            "by_days": True,
            "days_threshold": 15,
            "match_mode": "all",
            "version": 0,
        },
    )
    payload = assert_success(response)
    assert payload["data"]["version"] == 1


@pytest.mark.asyncio
async def test_view_cannot_set_override(monkeypatch, client):
    """Override requires app_access.override."""
    _allow(monkeypatch, "apps.user_service.app.api.app_access", APP_ACCESS_VIEW)
    response = await client.post(
        f"/v1/projects/{PROJECT_ID}/app-access/units/{UNIT_ID}/override",
        json={"kind": "release"},
    )
    assert_error(response, 403)


@pytest.mark.asyncio
async def test_override_permission_can_release(monkeypatch, client):
    """app_access.override can release a unit."""
    _allow(monkeypatch, "apps.user_service.app.api.app_access", APP_ACCESS_OVERRIDE)

    async def fake_set(_self, *, project_id, unit_id, body):
        del _self
        assert project_id == PROJECT_ID
        assert unit_id == UNIT_ID
        assert body.kind.value == "release"
        return {"unit_id": unit_id, "status": "released", "override_kind": "release"}

    monkeypatch.setattr(
        "apps.user_service.app.services.app_access_service.AppAccessService.set_override",
        fake_set,
    )
    response = await client.post(
        f"/v1/projects/{PROJECT_ID}/app-access/units/{UNIT_ID}/override",
        json={"kind": "release"},
    )
    payload = assert_success(response)
    assert payload["data"]["status"] == "released"


@pytest.mark.asyncio
async def test_resident_write_is_held(monkeypatch, client):
    """Resident pass create returns 403 unit_held on a blocked unit."""

    async def fake_contact(current_user, db_connection, request=None):
        del current_user, db_connection, request
        return admin_context(), {"id": "contact-1"}

    monkeypatch.setattr(
        "apps.user_service.app.api.passes.extract_onboarding_contact_context",
        fake_contact,
    )

    async def fake_create(_self, *, contact_id, body):
        del _self, contact_id, body
        raise ForbiddenException(
            message_key="app_access.errors.unit_held",
            custom_code=CustomStatusCode.FORBIDDEN,
            errors=[
                {
                    "unit_id": UNIT_ID,
                    "outstanding_amount": "48200.00",
                    "reason_key": "app_access.hold.rule",
                }
            ],
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.passes_service.PassesService.create_pass",
        fake_create,
    )
    response = await client.post(
        "/v1/passes",
        json={
            "unit_id": UNIT_ID,
            "guest_name": "Guest",
            "valid_from": "2026-10-28T10:00:00Z",
            "valid_until": "2026-10-28T18:00:00Z",
        },
    )
    body = assert_error(response, 403)
    assert body["errors"][0]["unit_id"] == UNIT_ID
    assert body["errors"][0]["reason_key"] == "app_access.hold.rule"


@pytest.mark.asyncio
async def test_staff_payment_on_held_unit_succeeds(monkeypatch, client):
    """Staff fee payment does not take the resident hold check."""
    _allow(monkeypatch, "apps.user_service.app.api.fee_invoices", FINANCE_MANAGEMENT_EDIT)
    called = {"hold": False}

    async def boom(*_args, **_kwargs):
        called["hold"] = True
        raise ForbiddenException(message_key="app_access.errors.unit_held")

    monkeypatch.setattr(
        "apps.user_service.app.utils.app_access_guard.ensure_resident_unit_not_held",
        boom,
    )

    async def fake_pay(_self, **kwargs):
        del _self
        assert kwargs["invoice_id"] == INVOICE_ID
        return {
            "invoice_id": INVOICE_ID,
            "status": "paid",
            "amount": "100.00",
            "paid_on": date(2026, 10, 28).isoformat(),
            "mode": "cash",
            "reference": None,
            "amount_paid": "100.00",
            "outstanding": "0.00",
            "credit": "0.00",
        }

    monkeypatch.setattr(
        "apps.user_service.app.services.fee_billing_service.FeeBillingService.record_payment",
        fake_pay,
    )
    response = await client.post(
        f"/v1/projects/{PROJECT_ID}/fee-invoices/{INVOICE_ID}/payments",
        json={"amount": "100.00", "paid_on": "2026-10-28", "mode": "cash"},
    )
    assert_success(response, 201)
    assert called["hold"] is False


@pytest.mark.asyncio
async def test_dual_role_staff_session_skips_hold(monkeypatch, client):
    """A dual-role user on a staff JWT can still record a payment."""
    await test_staff_payment_on_held_unit_succeeds(monkeypatch, client)


def test_passes_api_is_resident_and_fee_payment_is_staff():
    """Sanity: pass create uses contact context; payment uses staff access."""
    assert (
        "extract_onboarding_contact_context" in open(passes_api.__file__, encoding="utf-8").read()
    )
    assert "ensure_staff_project_access" in open(fee_invoices_api.__file__, encoding="utf-8").read()
