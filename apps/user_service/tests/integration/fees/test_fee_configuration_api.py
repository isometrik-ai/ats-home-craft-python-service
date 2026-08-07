"""Integration tests for fee configuration endpoints."""

import pytest

from apps.user_service.tests.integration.helpers import (
    patch_ensure_staff_project_access,
)
from apps.user_service.tests.utils.assertions import assert_success

PROJECT_ID = "proj-1"

_FAKE_CONFIG = {
    "project_id": PROJECT_ID,
    "is_configured": True,
    "configured_at": "2026-01-01T00:00:00Z",
    "settings": {
        "currency": "INR",
        "billing_cycle_type": "financial_year",
        "retry_count": 2,
        "retry_interval_days": 3,
        "reminder_count": 2,
        "reminder_interval_days": 2,
        "exhausted_retry_action": "escalate_to_billing_team",
        "first_reminder_lead_days": 4,
    },
    "applicable_tabs": ["apartment"],
    "rates": [
        {
            "unit_config_kind": "apartment",
            "rate_amount": 5.0,
            "measurement_unit": "sq_ft",
            "billing_frequency": "monthly",
            "fee_start_trigger": "possession_date",
            "start_offset_days": None,
            "minimum_fee": 0,
            "preview": None,
        }
    ],
    "warnings": {"possession_date_missing": False},
}

_FAKE_PREVIEW = {
    "unit_config_kind": "apartment",
    "area": 1000.0,
    "measurement_unit": "sq_ft",
    "billing_frequency": "monthly",
    "computed_period_fee": 5000.0,
    "minimum_applied": False,
    "currency": "INR",
}

_UPSERT_PAYLOAD = {
    "settings": {
        "currency": "INR",
        "billing_cycle_type": "financial_year",
        "retry_count": 2,
        "retry_interval_days": 3,
        "reminder_count": 2,
        "reminder_interval_days": 2,
        "exhausted_retry_action": "escalate_to_billing_team",
    },
    "rates": [
        {
            "unit_config_kind": "apartment",
            "rate_amount": 5.0,
            "measurement_unit": "sq_ft",
            "billing_frequency": "monthly",
            "fee_start_trigger": "possession_date",
            "minimum_fee": 0,
        }
    ],
}


@pytest.mark.asyncio
async def test_get_fee_configuration(monkeypatch, client):
    """GET fee configuration returns project settings and rates."""

    patch_ensure_staff_project_access(monkeypatch, "apps.user_service.app.api.fee_configuration")

    async def fake_get_configuration(_self, *, project_id: str):
        del _self
        assert project_id == PROJECT_ID
        return _FAKE_CONFIG

    monkeypatch.setattr(
        "apps.user_service.app.services.fee_configuration_service."
        "FeeConfigurationService.get_configuration",
        fake_get_configuration,
    )

    res = await client.get(f"/v1/projects/{PROJECT_ID}/fee-configuration")
    body = assert_success(res, 200)
    assert body["data"]["project_id"] == PROJECT_ID
    assert body["data"]["is_configured"] is True
    assert body["data"]["settings"]["currency"] == "INR"


@pytest.mark.asyncio
async def test_upsert_fee_configuration(monkeypatch, client):
    """PUT fee configuration upserts settings and rates."""

    patch_ensure_staff_project_access(monkeypatch, "apps.user_service.app.api.fee_configuration")

    async def fake_upsert_configuration(_self, *, project_id: str, body):
        del _self
        assert project_id == PROJECT_ID
        assert body.settings.currency == "INR"
        assert body.rates[0].unit_config_kind.value == "apartment"
        return _FAKE_CONFIG

    monkeypatch.setattr(
        "apps.user_service.app.services.fee_configuration_service."
        "FeeConfigurationService.upsert_configuration",
        fake_upsert_configuration,
    )

    res = await client.put(
        f"/v1/projects/{PROJECT_ID}/fee-configuration",
        json=_UPSERT_PAYLOAD,
    )
    body = assert_success(res, 200)
    assert body["data"]["project_id"] == PROJECT_ID
    assert body["data"]["rates"][0]["rate_amount"] == 5.0


@pytest.mark.asyncio
async def test_preview_fee_configuration(monkeypatch, client):
    """GET fee configuration preview returns computed fee."""

    patch_ensure_staff_project_access(monkeypatch, "apps.user_service.app.api.fee_configuration")

    async def fake_preview(
        _self,
        *,
        project_id: str,
        unit_config_kind,
        unit_id=None,
        area=None,
        measurement_unit=None,
    ):
        del _self, unit_id, measurement_unit
        assert project_id == PROJECT_ID
        assert unit_config_kind.value == "apartment"
        assert area == 1000.0
        return _FAKE_PREVIEW

    monkeypatch.setattr(
        "apps.user_service.app.services.fee_configuration_service.FeeConfigurationService.preview",
        fake_preview,
    )

    res = await client.get(
        f"/v1/projects/{PROJECT_ID}/fee-configuration/preview",
        params={
            "unit_config_kind": "apartment",
            "area": 1000,
            "measurement_unit": "sq_ft",
        },
    )
    body = assert_success(res, 200)
    assert body["data"]["computed_period_fee"] == 5000.0
    assert body["data"]["unit_config_kind"] == "apartment"
