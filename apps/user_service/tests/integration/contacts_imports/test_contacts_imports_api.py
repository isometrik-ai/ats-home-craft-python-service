"""Integration tests for contacts import endpoints."""

from __future__ import annotations

import pytest

from apps.user_service.tests.integration.helpers import patch_check_permissions
from apps.user_service.tests.utils.assertions import assert_success

JOB_ID = "job-12345"
ORG_ID = "org-123"

_FAKE_JOB = {
    "job_id": JOB_ID,
    "organization_id": ORG_ID,
    "status": "queued",
    "import_type": "contacts",
    "file_url": "https://example.com/contacts.csv",
    "file_type": "csv",
    "schema_version": 1,
    "total_rows": 0,
    "processed_rows": 0,
    "success_rows": 0,
    "error_rows": 0,
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
    "started_at": None,
    "finished_at": None,
}

_FAKE_LOG_ITEM = {
    "job_id": JOB_ID,
    "job_status": "queued",
    "payload": {},
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}


@pytest.mark.asyncio
async def test_create_contacts_import_job(monkeypatch, client):
    """POST /contacts/imports creates an import job."""
    patch_check_permissions(monkeypatch, "apps.user_service.app.api.contacts_imports")

    async def fake_create_job_and_enqueue(_self, **kwargs):
        del _self, kwargs
        return _FAKE_JOB, {"event_type": "contacts.import.requested"}

    async def fake_publish_event_background(**_kwargs):
        return None

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service."
        "ContactsImportService.create_job_and_enqueue",
        fake_create_job_and_enqueue,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.publish_event_background",
        fake_publish_event_background,
    )

    res = await client.post(
        "/v1/contacts/imports",
        json={
            "file_url": "https://example.com/contacts.csv",
            "schema_version": 1,
        },
    )
    body = assert_success(res, 202)
    assert body["data"]["job_id"] == JOB_ID


@pytest.mark.asyncio
async def test_list_contacts_import_logs(monkeypatch, client):
    """GET /contacts/imports/logs returns import logs."""
    patch_check_permissions(monkeypatch, "apps.user_service.app.api.contacts_imports")

    async def fake_list_job_logs(_self, **kwargs):
        del _self, kwargs
        return ([_FAKE_LOG_ITEM], 1)

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service."
        "ContactsImportService.list_job_logs",
        fake_list_job_logs,
    )

    res = await client.get("/v1/contacts/imports/logs")
    body = assert_success(res, 200)
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_get_contacts_import_job(monkeypatch, client):
    """GET /contacts/imports/{job_id} returns job details."""
    patch_check_permissions(monkeypatch, "apps.user_service.app.api.contacts_imports")

    async def fake_get_job(_self, *, job_id: str, organization_id: str):
        del _self, organization_id
        assert job_id == JOB_ID
        return _FAKE_JOB

    async def fake_list_job_rows(_self, **kwargs):
        del _self, kwargs
        return ([], 0)

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service.ContactsImportService.get_job",
        fake_get_job,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service."
        "ContactsImportService.list_job_rows",
        fake_list_job_rows,
    )

    res = await client.get(f"/v1/contacts/imports/{JOB_ID}")
    body = assert_success(res, 200)
    assert body["data"]["job_id"] == JOB_ID


@pytest.mark.asyncio
async def test_retry_contacts_import_job(monkeypatch, client):
    """POST /contacts/imports/{job_id}/retry re-queues a job."""
    patch_check_permissions(monkeypatch, "apps.user_service.app.api.contacts_imports")

    async def fake_retry_job_and_enqueue(_self, **kwargs):
        del _self, kwargs
        return (_FAKE_JOB, {"event_type": "contacts.import.requested"})

    async def fake_publish_event_background(**_kwargs):
        return None

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service."
        "ContactsImportService.retry_job_and_enqueue",
        fake_retry_job_and_enqueue,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.publish_event_background",
        fake_publish_event_background,
    )

    res = await client.post(f"/v1/contacts/imports/{JOB_ID}/retry")
    body = assert_success(res, 202)
    assert body["data"]["job_id"] == JOB_ID
