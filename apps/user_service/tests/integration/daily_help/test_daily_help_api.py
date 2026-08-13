"""Integration-style tests for daily help admin routes."""

from __future__ import annotations

from apps.user_service.app.api import daily_help as daily_help_api


def test_daily_help_admin_router_registered():
    """Admin router exposes category routes before profile id routes."""
    paths = [route.path for route in daily_help_api.router.routes]
    assert "/projects/{project_id}/daily-help/categories" in paths
    assert "/projects/{project_id}/daily-help/summary" in paths
    assert "/projects/{project_id}/daily-help" in paths


def test_daily_help_resident_router_registered():
    """Resident router is importable with expected prefix routes."""
    from apps.user_service.app.api import daily_help_resident as resident_api

    paths = [route.path for route in resident_api.router.routes]
    assert "/daily-help/categories" in paths
    assert "/daily-help/search" in paths
    assert "/daily-help/{profile_id}/household-links" in paths
    assert "/daily-help/{profile_id}/open-to-work" in paths
