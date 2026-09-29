"""Integration tests for the fee configuration staff API."""

from __future__ import annotations

import pytest

from apps.user_service.app.api import fee_configuration as fee_configuration_api
from apps.user_service.tests.integration.helpers import admin_context
from apps.user_service.tests.utils.assertions import assert_error, assert_success
from libs.shared_utils.common_query import (
    FINANCE_MANAGEMENT_EDIT,
    FINANCE_MANAGEMENT_VIEW,
)
from libs.shared_utils.http_exceptions import ForbiddenException
from libs.shared_utils.status_codes import CustomStatusCode

PROJECT_ID = "project-1"
FEE_HEAD_ID = "fee-1"

_LIST_ITEM = {
    "id": FEE_HEAD_ID,
    "kind": "maintenance",
    "name": "Maintenance (CAM)",
    "category": "maintenance",
    "category_label": "Maintenance",
    "charge_summary": "₹0 / sq ft · no minimum",
    "frequency": "monthly",
    "frequency_label": "Monthly",
    "tax_label": "—",
    "status": "inactive",
    "version": 1,
}


def test_fee_configuration_routes_are_registered():
    """The staff router exposes list, create, detail, save, status, and settings."""
    paths = [route.path for route in fee_configuration_api.router.routes]
    assert "/projects/{project_id}/fee-configuration/fee-heads" in paths
    assert "/projects/{project_id}/fee-configuration/fee-heads/{fee_head_id}" in paths
    assert "/projects/{project_id}/fee-configuration/fee-heads/{fee_head_id}/status" in paths
    assert "/projects/{project_id}/fee-configuration/finance-settings" in paths
    post_paths = [
        route.path
        for route in fee_configuration_api.router.routes
        if "POST" in getattr(route, "methods", set())
    ]
    assert "/projects/{project_id}/fee-configuration/fee-heads" in post_paths
    assert "/projects/{project_id}/fee-configuration/finance-settings" in post_paths


def _allow_view_only(monkeypatch) -> None:
    async def fake_access(**kwargs):
        if kwargs["permission_codes"] == FINANCE_MANAGEMENT_EDIT:
            raise ForbiddenException(
                message_key="errors.insufficient_permissions",
                custom_code=CustomStatusCode.FORBIDDEN,
            )
        assert kwargs["permission_codes"] == FINANCE_MANAGEMENT_VIEW
        return admin_context()

    monkeypatch.setattr(
        "apps.user_service.app.api.fee_configuration.ensure_staff_project_access",
        fake_access,
    )


@pytest.mark.asyncio
async def test_view_cannot_patch_a_fee_head(monkeypatch, client):
    """finance_management.view is not enough to change a fee head."""
    _allow_view_only(monkeypatch)
    response = await client.patch(
        f"/v1/projects/{PROJECT_ID}/fee-configuration/fee-heads/{FEE_HEAD_ID}/status",
        json={"version": 1, "status": "active"},
    )
    assert_error(response, 403)


@pytest.mark.asyncio
async def test_edit_can_patch_a_fee_head(monkeypatch, client):
    """finance_management.edit can toggle status and receives the list item back."""

    async def allow_edit(**_kwargs):
        return admin_context()

    monkeypatch.setattr(
        "apps.user_service.app.api.fee_configuration.ensure_staff_project_access",
        allow_edit,
    )

    async def fake_update_status(_self, *, project_id, fee_head_id, version, status):
        del _self
        assert project_id == PROJECT_ID
        assert fee_head_id == FEE_HEAD_ID
        assert version == 1
        assert status.value == "active"
        return {**_LIST_ITEM, "status": "active", "version": 2}

    monkeypatch.setattr(
        "apps.user_service.app.services.fee_configuration_service.FeeConfigurationService.update_status",
        fake_update_status,
    )
    response = await client.patch(
        f"/v1/projects/{PROJECT_ID}/fee-configuration/fee-heads/{FEE_HEAD_ID}/status",
        json={"version": 1, "status": "active"},
    )
    payload = assert_success(response)
    assert payload["data"]["status"] == "active"
    assert payload["data"]["version"] == 2


@pytest.mark.asyncio
async def test_edit_can_save_the_fee_head_document(monkeypatch, client):
    """finance_management.edit can save the full fee-head document."""

    async def allow_edit(**_kwargs):
        return admin_context()

    monkeypatch.setattr(
        "apps.user_service.app.api.fee_configuration.ensure_staff_project_access",
        allow_edit,
    )

    async def fake_update(_self, *, project_id, fee_head_id, body):
        del _self
        assert project_id == PROJECT_ID
        assert fee_head_id == FEE_HEAD_ID
        assert body.name == "Maintenance (CAM)"
        return {**_LIST_ITEM, "version": 2}

    monkeypatch.setattr(
        "apps.user_service.app.services.fee_configuration_service.FeeConfigurationService.update_fee_head",
        fake_update,
    )
    response = await client.patch(
        f"/v1/projects/{PROJECT_ID}/fee-configuration/fee-heads/{FEE_HEAD_ID}",
        json={
            "version": 1,
            "name": "Maintenance (CAM)",
            "status": "active",
            "frequency": "monthly",
            "fee_start_rule": "first_of_next_month",
            "due_within_days": 10,
            "invoice_day": 1,
            "tax": {"applicable": False},
            "late_fee": {"mode": "none"},
            "scopes": [
                {
                    "property_type": "residential",
                    "enabled": True,
                    "rate_per_sqft": "0",
                    "minimum_amount": "0",
                },
                {
                    "property_type": "plots",
                    "enabled": True,
                    "rate_per_sqft": "0",
                    "minimum_amount": "0",
                },
                {
                    "property_type": "commercial",
                    "enabled": True,
                    "rate_per_sqft": "0",
                    "minimum_amount": "0",
                },
            ],
        },
    )
    payload = assert_success(response)
    assert payload["data"]["version"] == 2


_CREATE_BODY = {
    "kind": "maintenance",
    "name": "Maintenance (CAM)",
    "status": "inactive",
    "frequency": "monthly",
    "fee_start_rule": "first_of_next_month",
    "due_within_days": 10,
    "invoice_day": 1,
    "tax": {"applicable": False},
    "late_fee": {"mode": "none"},
    "scopes": [
        {
            "property_type": "residential",
            "enabled": True,
            "rate_per_sqft": "0",
            "minimum_amount": "0",
        },
        {"property_type": "plots", "enabled": True, "rate_per_sqft": "0", "minimum_amount": "0"},
        {
            "property_type": "commercial",
            "enabled": True,
            "rate_per_sqft": "0",
            "minimum_amount": "0",
        },
    ],
}


@pytest.mark.asyncio
async def test_view_cannot_create_a_fee_head(monkeypatch, client):
    """finance_management.view is not enough to create a fee head."""
    _allow_view_only(monkeypatch)
    response = await client.post(
        f"/v1/projects/{PROJECT_ID}/fee-configuration/fee-heads",
        json=_CREATE_BODY,
    )
    assert_error(response, 403)


@pytest.mark.asyncio
async def test_edit_can_create_a_fee_head(monkeypatch, client):
    """finance_management.edit can create one fee head."""

    async def allow_edit(**_kwargs):
        return admin_context()

    monkeypatch.setattr(
        "apps.user_service.app.api.fee_configuration.ensure_staff_project_access",
        allow_edit,
    )

    async def fake_create(_self, *, project_id, body):
        del _self
        assert project_id == PROJECT_ID
        assert body.kind.value == "maintenance"
        return _LIST_ITEM

    monkeypatch.setattr(
        "apps.user_service.app.services.fee_configuration_service.FeeConfigurationService.create_fee_head",
        fake_create,
    )
    response = await client.post(
        f"/v1/projects/{PROJECT_ID}/fee-configuration/fee-heads",
        json=_CREATE_BODY,
    )
    payload = assert_success(response, 201)
    assert payload["data"]["kind"] == "maintenance"
