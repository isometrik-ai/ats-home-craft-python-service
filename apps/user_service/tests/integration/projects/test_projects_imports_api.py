"""Integration tests for projects import endpoints."""

from __future__ import annotations

import pytest

from apps.user_service.app.schemas.enums import ContactsImportJobStatus
from apps.user_service.tests.integration.helpers import patch_check_permissions
from apps.user_service.tests.utils.assertions import assert_success

ADMIN_ID = "770e8400-e29b-41d4-a716-446655440002"
JOB_ID = "imp_projects_1"
ORG_ID = "org-123"

_FAKE_JOB = {
    "job_id": JOB_ID,
    "organization_id": ORG_ID,
    "status": ContactsImportJobStatus.QUEUED.value,
    "import_type": "projects",
    "file_url": "https://example.com/project.xlsx",
    "file_type": "xlsx",
    "schema_version": 1,
    "total_rows": 10,
    "processed_rows": 10,
    "success_rows": 9,
    "error_rows": 1,
    "created_at": "2026-10-13T00:00:00Z",
    "updated_at": "2026-10-13T00:00:00Z",
    "started_at": "2026-10-13T00:00:00Z",
    "finished_at": "2026-10-13T00:00:01Z",
}


@pytest.mark.asyncio
async def test_create_projects_import_job(monkeypatch, client):
    """POST /projects/imports queues the job and returns 202 immediately."""
    patch_check_permissions(monkeypatch, "apps.user_service.app.api.projects_imports")
    captured: dict[str, object] = {}
    scheduled: dict[str, object] = {}

    async def fake_create_job(_self, **kwargs):
        captured.update(kwargs)
        return _FAKE_JOB

    def fake_schedule(**kwargs):
        scheduled.update(kwargs)

    monkeypatch.setattr(
        "apps.user_service.app.api.projects_imports.ProjectsImportService.create_job",
        fake_create_job,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.projects_imports.schedule_projects_import_job",
        fake_schedule,
    )

    res = await client.post(
        "/v1/projects/imports",
        json={
            "file_url": "https://example.com/project.xlsx",
            "community_admin_user_id": ADMIN_ID,
        },
    )
    body = assert_success(res, 202)
    assert body["data"]["job_id"] == JOB_ID
    assert body["data"]["status"] == ContactsImportJobStatus.QUEUED.value
    assert captured["community_admin_user_id"] == ADMIN_ID
    assert captured["file_type"] == "xlsx"
    assert scheduled["job_id"] == JOB_ID


@pytest.mark.asyncio
async def test_get_projects_import_job(monkeypatch, client):
    """GET /projects/imports/{job_id} returns job progress."""
    patch_check_permissions(monkeypatch, "apps.user_service.app.api.projects_imports")

    async def fake_get_job(_self, **kwargs):
        del kwargs
        return _FAKE_JOB

    monkeypatch.setattr(
        "apps.user_service.app.services.projects_imports_service.ProjectsImportService.get_job",
        fake_get_job,
    )

    res = await client.get(f"/v1/projects/imports/{JOB_ID}")
    body = assert_success(res, 200)
    assert body["data"]["job_id"] == JOB_ID
    assert body["data"]["progress"]["error_rows"] == 1
