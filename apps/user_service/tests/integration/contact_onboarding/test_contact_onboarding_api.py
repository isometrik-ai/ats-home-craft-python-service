"""Integration tests for contact onboarding endpoints."""

import pytest

from apps.user_service.tests.integration.helpers import admin_context
from apps.user_service.tests.utils.assertions import assert_success

CONTACT_ID = "contact-1"
CONTACT_UNIT_ID = "cu-1"

_FAKE_STATUS = {
    "onboarding_completed": False,
    "setup_current_step": "complete_profile",
    "steps": [{"step_key": "complete_profile", "status": "pending"}],
}

_FAKE_PROFILE = {
    "id": CONTACT_ID,
    "first_name": "Jane",
    "last_name": "Doe",
    "profile_photo_url": None,
}

_FAKE_HOUSEHOLD_MEMBER = {
    "contact_unit_id": CONTACT_UNIT_ID,
    "first_name": "John",
    "last_name": "Doe",
    "relationship": "spouse",
}

_FAKE_REVIEW = {
    "profile": _FAKE_PROFILE,
    "units": [{"contact_unit_id": CONTACT_UNIT_ID, "unit_code": "A-101"}],
}

_FAKE_COMPLETE = {
    "onboarding_completed": True,
    "default_contact_unit_id": CONTACT_UNIT_ID,
}


def _patch_contact_context(monkeypatch) -> None:
    """Patch onboarding contact context for authenticated contact routes."""

    async def fake_extract_onboarding_contact_context(current_user, db_connection, request=None):
        del current_user, db_connection, request
        return admin_context(org_id="org-123"), {
            "id": CONTACT_ID,
            "contact_type": "owner",
        }

    monkeypatch.setattr(
        "apps.user_service.app.api.contact_onboarding.extract_onboarding_contact_context",
        fake_extract_onboarding_contact_context,
    )


@pytest.mark.asyncio
async def test_get_onboarding_status(monkeypatch, client):
    """GET /contact-onboarding/status returns wizard status."""

    _patch_contact_context(monkeypatch)

    async def fake_get_status(_self, *, contact_id: str, contact_type: str):
        del _self
        assert contact_id == CONTACT_ID
        assert contact_type == "owner"
        return _FAKE_STATUS

    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service."
        "ContactOnboardingService.get_status",
        fake_get_status,
    )

    res = await client.get("/v1/contact-onboarding/status")
    body = assert_success(res, 200)
    assert body["data"]["setup_current_step"] == "complete_profile"
    assert body["data"]["onboarding_completed"] is False


@pytest.mark.asyncio
async def test_get_profile(monkeypatch, client):
    """GET /contact-onboarding/profile returns contact profile."""

    _patch_contact_context(monkeypatch)

    async def fake_get_profile(_self, *, contact_id: str):
        del _self
        assert contact_id == CONTACT_ID
        return _FAKE_PROFILE

    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service."
        "ContactOnboardingService.get_profile",
        fake_get_profile,
    )

    res = await client.get("/v1/contact-onboarding/profile")
    body = assert_success(res, 200)
    assert body["data"]["first_name"] == "Jane"
    assert body["data"]["id"] == CONTACT_ID


@pytest.mark.asyncio
async def test_complete_profile(monkeypatch, client):
    """PATCH /contact-onboarding/profile completes the profile step."""

    _patch_contact_context(monkeypatch)

    async def fake_complete_profile(_self, *, contact_id: str, body):
        del _self
        assert contact_id == CONTACT_ID
        assert body.first_name == "Jane"
        assert body.last_name == "Smith"
        return {**_FAKE_PROFILE, "last_name": "Smith"}

    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service."
        "ContactOnboardingService.complete_profile",
        fake_complete_profile,
    )

    res = await client.patch(
        "/v1/contact-onboarding/profile",
        json={"first_name": "Jane", "last_name": "Smith"},
    )
    body = assert_success(res, 200)
    assert body["data"]["last_name"] == "Smith"


@pytest.mark.asyncio
async def test_list_household(monkeypatch, client):
    """GET /contact-onboarding/household lists household members."""

    _patch_contact_context(monkeypatch)

    async def fake_list_household(_self, *, contact_id: str, unit_id=None):
        del _self
        assert contact_id == CONTACT_ID
        assert unit_id is None
        return [_FAKE_HOUSEHOLD_MEMBER]

    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service."
        "ContactOnboardingService.list_household",
        fake_list_household,
    )

    res = await client.get("/v1/contact-onboarding/household")
    body = assert_success(res, 200)
    assert body["data"][0]["contact_unit_id"] == CONTACT_UNIT_ID
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_add_household_member(monkeypatch, client):
    """POST /contact-onboarding/household adds a household member."""

    _patch_contact_context(monkeypatch)

    async def fake_add_household_member(_self, *, primary_contact_id: str, body):
        del _self
        assert primary_contact_id == CONTACT_ID
        assert body.first_name == "John"
        assert body.relationship.value == "spouse"
        return _FAKE_HOUSEHOLD_MEMBER

    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service."
        "ContactOnboardingService.add_household_member",
        fake_add_household_member,
    )

    res = await client.post(
        "/v1/contact-onboarding/household",
        json={
            "unit_id": "unit-1",
            "first_name": "John",
            "last_name": "Doe",
            "relationship": "spouse",
            "phones": [
                {
                    "phone_number": "9876543210",
                    "phone_isd_code": "+91",
                    "is_primary": True,
                }
            ],
        },
    )
    body = assert_success(res, 201)
    assert body["data"]["first_name"] == "John"


@pytest.mark.asyncio
async def test_skip_onboarding_step(monkeypatch, client):
    """POST /contact-onboarding/steps/skip skips an optional step."""

    _patch_contact_context(monkeypatch)

    async def fake_skip_step(_self, *, contact_id: str, step_key: str, contact_unit_id=None):
        del _self
        assert contact_id == CONTACT_ID
        assert step_key == "choose_unit"
        assert contact_unit_id is None

    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service."
        "ContactOnboardingService.skip_step",
        fake_skip_step,
    )

    res = await client.post(
        "/v1/contact-onboarding/steps/skip",
        json={"step_key": "choose_unit"},
    )
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_get_review(monkeypatch, client):
    """GET /contact-onboarding/review returns onboarding summary."""

    _patch_contact_context(monkeypatch)

    async def fake_get_review(_self, *, contact_id: str):
        del _self
        assert contact_id == CONTACT_ID
        return _FAKE_REVIEW

    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service."
        "ContactOnboardingService.get_review",
        fake_get_review,
    )

    res = await client.get("/v1/contact-onboarding/review")
    body = assert_success(res, 200)
    assert body["data"]["profile"]["first_name"] == "Jane"
    assert body["data"]["units"][0]["unit_code"] == "A-101"


@pytest.mark.asyncio
async def test_complete_onboarding(monkeypatch, client):
    """POST /contact-onboarding/complete finalizes onboarding."""

    _patch_contact_context(monkeypatch)

    async def fake_complete_onboarding(_self, *, contact_id: str):
        del _self
        assert contact_id == CONTACT_ID
        return _FAKE_COMPLETE

    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service."
        "ContactOnboardingService.complete_onboarding",
        fake_complete_onboarding,
    )

    res = await client.post("/v1/contact-onboarding/complete")
    body = assert_success(res, 200)
    assert body["data"]["onboarding_completed"] is True
    assert body["data"]["default_contact_unit_id"] == CONTACT_UNIT_ID


_FAKE_PROPERTY = {
    "contact_unit_id": CONTACT_UNIT_ID,
    "unit_id": "unit-1",
    "unit_code": "A-101",
    "status": "pending",
}

_FAKE_VEHICLE = {
    "id": "veh-1",
    "unit_id": "unit-1",
    "registration_number": "MH12AB1234",
    "vehicle_type": "four_wheeler",
    "status": "pending",
}


@pytest.mark.asyncio
async def test_list_properties(monkeypatch, client):
    """GET /contact-onboarding/properties lists claimable units."""

    _patch_contact_context(monkeypatch)

    async def fake_list(_self, *, contact_id: str):
        del _self
        assert contact_id == CONTACT_ID
        return [_FAKE_PROPERTY]

    monkeypatch.setattr(
        "apps.user_service.app.services.contact_units_service."
        "ContactUnitsService.list_my_properties",
        fake_list,
    )

    res = await client.get("/v1/contact-onboarding/properties")
    body = assert_success(res, 200)
    assert body["data"][0]["unit_code"] == "A-101"


@pytest.mark.asyncio
async def test_confirm_properties(monkeypatch, client):
    """POST /contact-onboarding/properties/confirm confirms units."""

    _patch_contact_context(monkeypatch)

    async def fake_confirm(_self, *, contact_id: str, contact_unit_ids, default_contact_unit_id):
        del _self
        assert contact_id == CONTACT_ID
        assert contact_unit_ids == [CONTACT_UNIT_ID]
        assert default_contact_unit_id == CONTACT_UNIT_ID
        return [_FAKE_PROPERTY]

    monkeypatch.setattr(
        "apps.user_service.app.services.contact_units_service."
        "ContactUnitsService.confirm_properties",
        fake_confirm,
    )

    res = await client.post(
        "/v1/contact-onboarding/properties/confirm",
        json={
            "contact_unit_ids": [CONTACT_UNIT_ID],
            "default_contact_unit_id": CONTACT_UNIT_ID,
        },
    )
    body = assert_success(res, 200)
    assert body["data"]["items"][0]["contact_unit_id"] == CONTACT_UNIT_ID


@pytest.mark.asyncio
async def test_claim_properties(monkeypatch, client):
    """POST /contact-onboarding/properties/claim claims units."""

    _patch_contact_context(monkeypatch)

    async def fake_claim(_self, *, contact_id: str, contact_unit_ids):
        del _self
        assert contact_id == CONTACT_ID
        assert contact_unit_ids == [CONTACT_UNIT_ID]
        return {
            "items": [{"id": CONTACT_UNIT_ID, "status": "active"}],
            "requires_default_unit": False,
        }

    monkeypatch.setattr(
        "apps.user_service.app.services.contact_units_service.ContactUnitsService.claim_properties",
        fake_claim,
    )

    res = await client.post(
        "/v1/contact-onboarding/properties/claim",
        json={"contact_unit_ids": [CONTACT_UNIT_ID]},
    )
    body = assert_success(res, 200)
    assert body["data"]["requires_default_unit"] is False


@pytest.mark.asyncio
async def test_get_vehicle_catalog(monkeypatch, client):
    """GET /contact-onboarding/vehicles/options returns catalog."""

    _patch_contact_context(monkeypatch)

    def fake_catalog(*, vehicle_type, brand_id=None, search=None):
        del brand_id, search
        assert vehicle_type == "four_wheeler"
        return {
            "vehicle_type": "four_wheeler",
            "brands": [{"id": "tata", "name": "Tata", "models": []}],
            "colors": [{"id": "white", "name": "White"}],
        }

    monkeypatch.setattr(
        "apps.user_service.app.api.contact_onboarding.VehicleCatalogService.get_catalog",
        fake_catalog,
    )

    res = await client.get(
        "/v1/contact-onboarding/vehicles/options",
        params={"vehicle_type": "four_wheeler"},
    )
    body = assert_success(res, 200)
    assert body["data"]["brands"][0]["id"] == "tata"


@pytest.mark.asyncio
async def test_list_vehicles(monkeypatch, client):
    """GET /contact-onboarding/vehicles lists registered vehicles."""

    _patch_contact_context(monkeypatch)

    async def fake_list(_self, *, contact_id: str, unit_id=None):
        del _self
        assert contact_id == CONTACT_ID
        assert unit_id is None
        return [_FAKE_VEHICLE]

    monkeypatch.setattr(
        "apps.user_service.app.services.vehicles_service.VehiclesService.list_vehicles",
        fake_list,
    )

    res = await client.get("/v1/contact-onboarding/vehicles")
    body = assert_success(res, 200)
    assert body["data"][0]["registration_number"] == "MH12AB1234"


@pytest.mark.asyncio
async def test_create_vehicle(monkeypatch, client):
    """POST /contact-onboarding/vehicles registers a vehicle."""

    _patch_contact_context(monkeypatch)

    async def fake_create(_self, *, contact_id: str, body):
        del _self
        assert contact_id == CONTACT_ID
        assert body.registration_number == "MH12AB1234"
        return _FAKE_VEHICLE

    monkeypatch.setattr(
        "apps.user_service.app.services.vehicles_service.VehiclesService.create_vehicle",
        fake_create,
    )

    res = await client.post(
        "/v1/contact-onboarding/vehicles",
        json={
            "unit_id": "unit-1",
            "vehicle_type": "four_wheeler",
            "registration_number": "MH12AB1234",
        },
    )
    body = assert_success(res, 201)
    assert body["data"]["id"] == "veh-1"


@pytest.mark.asyncio
async def test_update_vehicle(monkeypatch, client):
    """PATCH /contact-onboarding/vehicles/{id} updates vehicle."""

    _patch_contact_context(monkeypatch)

    async def fake_update(_self, *, contact_id: str, vehicle_id: str, body):
        del _self, body
        assert contact_id == CONTACT_ID
        assert vehicle_id == "veh-1"
        return {**_FAKE_VEHICLE, "color": "red"}

    monkeypatch.setattr(
        "apps.user_service.app.services.vehicles_service.VehiclesService.update_vehicle",
        fake_update,
    )

    res = await client.patch(
        "/v1/contact-onboarding/vehicles/veh-1",
        json={"color": "red"},
    )
    body = assert_success(res, 200)
    assert body["data"]["color"] == "red"


@pytest.mark.asyncio
async def test_withdraw_vehicle(monkeypatch, client):
    """POST /contact-onboarding/vehicles/{id}/withdraw withdraws."""

    _patch_contact_context(monkeypatch)

    async def fake_withdraw(_self, *, contact_id: str, vehicle_id: str):
        del _self
        assert contact_id == CONTACT_ID
        assert vehicle_id == "veh-1"

    monkeypatch.setattr(
        "apps.user_service.app.services.vehicles_service.VehiclesService.withdraw_vehicle",
        fake_withdraw,
    )

    res = await client.post("/v1/contact-onboarding/vehicles/veh-1/withdraw")
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_remove_vehicle(monkeypatch, client):
    """DELETE /contact-onboarding/vehicles/{id} removes vehicle."""

    _patch_contact_context(monkeypatch)

    async def fake_remove(_self, *, contact_id: str, vehicle_id: str):
        del _self
        assert contact_id == CONTACT_ID
        assert vehicle_id == "veh-1"
        return {**_FAKE_VEHICLE, "status": "removed"}

    monkeypatch.setattr(
        "apps.user_service.app.services.vehicles_service.VehiclesService.remove_vehicle",
        fake_remove,
    )

    res = await client.delete("/v1/contact-onboarding/vehicles/veh-1")
    body = assert_success(res, 200)
    assert body["data"]["status"] == "removed"


@pytest.mark.asyncio
async def test_complete_vehicles_step(monkeypatch, client):
    """POST /contact-onboarding/steps/vehicles/complete."""

    _patch_contact_context(monkeypatch)

    async def fake_complete(_self, *, contact_id: str, contact_unit_id: str):
        del _self
        assert contact_id == CONTACT_ID
        assert contact_unit_id == CONTACT_UNIT_ID

    monkeypatch.setattr(
        "apps.user_service.app.services.vehicles_service.VehiclesService.complete_vehicles_step",
        fake_complete,
    )

    res = await client.post(
        "/v1/contact-onboarding/steps/vehicles/complete",
        json={"contact_unit_id": CONTACT_UNIT_ID},
    )
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_update_household_member(monkeypatch, client):
    """PATCH /contact-onboarding/household/{id} updates member."""

    _patch_contact_context(monkeypatch)

    async def fake_update(_self, *, primary_contact_id: str, contact_unit_id: str, body):
        del _self, body
        assert primary_contact_id == CONTACT_ID
        assert contact_unit_id == CONTACT_UNIT_ID
        return {**_FAKE_HOUSEHOLD_MEMBER, "first_name": "Johnny"}

    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service."
        "ContactOnboardingService.update_household_member",
        fake_update,
    )

    res = await client.patch(
        f"/v1/contact-onboarding/household/{CONTACT_UNIT_ID}",
        json={"first_name": "Johnny"},
    )
    body = assert_success(res, 200)
    assert body["data"]["first_name"] == "Johnny"


@pytest.mark.asyncio
async def test_validate_household_invitation(monkeypatch, client):
    """POST /contact-onboarding/household/invitations/validate."""

    async def fake_validate(_self, *, token: str):
        del _self
        assert token == "invite-token"
        return {"valid": True, "invitee_name": "John Doe"}

    monkeypatch.setattr(
        "apps.user_service.app.services.household_invitation_service."
        "HouseholdInvitationService.validate_token",
        fake_validate,
    )

    res = await client.post(
        "/v1/contact-onboarding/household/invitations/validate",
        json={"token": "invite-token"},
    )
    body = assert_success(res, 200)
    assert body["data"]["valid"] is True


@pytest.mark.asyncio
async def test_accept_household_invitation(monkeypatch, client):
    """POST /contact-onboarding/household/invitations/accept."""

    async def fake_accept(_self, *, token: str, password: str):
        del _self
        assert token == "invite-token"
        assert password == "SecurePass1!"
        return {
            "access_token": "jwt-token",
            "organization_id": "org-123",
            "contact_id": CONTACT_ID,
        }

    async def fake_warm_session(**_kwargs):
        return None

    monkeypatch.setattr(
        "apps.user_service.app.services.household_invitation_service."
        "HouseholdInvitationService.accept",
        fake_accept,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.contact_onboarding.warm_session_context_after_auth",
        fake_warm_session,
    )

    res = await client.post(
        "/v1/contact-onboarding/household/invitations/accept",
        json={"token": "invite-token", "password": "SecurePass1!"},
    )
    body = assert_success(res, 200)
    assert body["data"]["contact_id"] == CONTACT_ID


@pytest.mark.asyncio
async def test_decline_household_invitation(monkeypatch, client):
    """POST /contact-onboarding/household/invitations/decline."""

    async def fake_decline(_self, *, token: str):
        del _self
        assert token == "invite-token"
        return {"declined": True}

    monkeypatch.setattr(
        "apps.user_service.app.services.household_invitation_service."
        "HouseholdInvitationService.decline",
        fake_decline,
    )

    res = await client.post(
        "/v1/contact-onboarding/household/invitations/decline",
        json={"token": "invite-token"},
    )
    body = assert_success(res, 200)
    assert body["data"]["declined"] is True


@pytest.mark.asyncio
async def test_revoke_household_invitation(monkeypatch, client):
    """POST /contact-onboarding/household/{id}/revoke-invitation."""

    _patch_contact_context(monkeypatch)

    async def fake_revoke(_self, *, primary_contact_id: str, contact_unit_id: str):
        del _self
        assert primary_contact_id == CONTACT_ID
        assert contact_unit_id == CONTACT_UNIT_ID
        return {"revoked": True}

    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service."
        "ContactOnboardingService.revoke_household_invitation",
        fake_revoke,
    )

    res = await client.post(f"/v1/contact-onboarding/household/{CONTACT_UNIT_ID}/revoke-invitation")
    body = assert_success(res, 200)
    assert body["data"]["revoked"] is True


@pytest.mark.asyncio
async def test_resend_household_invitation(monkeypatch, client):
    """POST /contact-onboarding/household/{id}/resend-invitation."""

    _patch_contact_context(monkeypatch)

    async def fake_resend(_self, *, primary_contact_id: str, contact_unit_id: str):
        del _self
        assert primary_contact_id == CONTACT_ID
        assert contact_unit_id == CONTACT_UNIT_ID
        return {"sent": True}

    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service."
        "ContactOnboardingService.resend_household_invitation",
        fake_resend,
    )

    res = await client.post(f"/v1/contact-onboarding/household/{CONTACT_UNIT_ID}/resend-invitation")
    body = assert_success(res, 200)
    assert body["data"]["sent"] is True


@pytest.mark.asyncio
async def test_remove_household_member(monkeypatch, client):
    """DELETE /contact-onboarding/household/{id} removes member."""

    _patch_contact_context(monkeypatch)

    async def fake_remove(_self, *, primary_contact_id: str, contact_unit_id: str):
        del _self
        assert primary_contact_id == CONTACT_ID
        assert contact_unit_id == CONTACT_UNIT_ID
        return {"removed": True}

    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service."
        "ContactOnboardingService.remove_household_member",
        fake_remove,
    )

    res = await client.delete(f"/v1/contact-onboarding/household/{CONTACT_UNIT_ID}")
    body = assert_success(res, 200)
    assert body["data"]["removed"] is True


@pytest.mark.asyncio
async def test_complete_household_step(monkeypatch, client):
    """POST /contact-onboarding/steps/household/complete."""

    _patch_contact_context(monkeypatch)

    async def fake_complete(_self, *, contact_id: str, contact_unit_id: str):
        del _self
        assert contact_id == CONTACT_ID
        assert contact_unit_id == CONTACT_UNIT_ID

    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service."
        "ContactOnboardingService.complete_household_step",
        fake_complete,
    )

    res = await client.post(
        "/v1/contact-onboarding/steps/household/complete",
        json={"contact_unit_id": CONTACT_UNIT_ID},
    )
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_set_default_unit(monkeypatch, client):
    """POST /contact-onboarding/default-unit sets login unit."""

    _patch_contact_context(monkeypatch)

    async def fake_set_default(_self, *, contact_id: str, contact_unit_id: str):
        del _self
        assert contact_id == CONTACT_ID
        assert contact_unit_id == CONTACT_UNIT_ID
        return {"default_contact_unit_id": CONTACT_UNIT_ID}

    monkeypatch.setattr(
        "apps.user_service.app.services.contact_units_service.ContactUnitsService.set_default_unit",
        fake_set_default,
    )

    res = await client.post(
        "/v1/contact-onboarding/default-unit",
        json={"contact_unit_id": CONTACT_UNIT_ID},
    )
    body = assert_success(res, 200)
    assert body["data"]["default_contact_unit_id"] == CONTACT_UNIT_ID
