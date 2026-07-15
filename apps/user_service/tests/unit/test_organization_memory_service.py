"""Unit tests for organization memory feature flag helpers."""

from apps.user_service.app.services.organization_memory_service import (
    ORGANIZATION_MEMORY_SETTINGS_KEY,
    effective_organization_memory_enabled,
)
from apps.user_service.app.services.organization_service import OrganizationService


def test_effective_organization_memory_enabled():
    """Missing key, null settings, or empty dict all mean enabled."""
    assert effective_organization_memory_enabled(None) is True
    assert effective_organization_memory_enabled({}) is True
    assert effective_organization_memory_enabled({ORGANIZATION_MEMORY_SETTINGS_KEY: None}) is True


def test_effective_memory_enabled_explicit_values():
    """Explicit true/false in settings is respected."""
    assert effective_organization_memory_enabled({ORGANIZATION_MEMORY_SETTINGS_KEY: True}) is True
    assert effective_organization_memory_enabled({ORGANIZATION_MEMORY_SETTINGS_KEY: False}) is False


def test_build_update_payload_when_settings_missing():
    """Toggle persists when organizations.settings is null or has no memory key."""
    service = OrganizationService.__new__(OrganizationService)

    db_payload = service._build_update_payload(
        existing_settings=None,
        update_data={"organization_memory": False},
    )

    assert db_payload["settings"][ORGANIZATION_MEMORY_SETTINGS_KEY] is False

    db_payload_on = service._build_update_payload(
        existing_settings={"practice_areas": {"primary": ["Litigation"]}},
        update_data={"organization_memory": True},
    )

    assert db_payload_on["settings"][ORGANIZATION_MEMORY_SETTINGS_KEY] is True
    assert db_payload_on["settings"]["practice_areas"]["primary"] == ["Litigation"]
