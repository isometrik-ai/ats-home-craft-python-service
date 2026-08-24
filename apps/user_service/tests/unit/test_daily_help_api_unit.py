"""Unit tests for daily help admin API route handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from apps.user_service.app.api.daily_help import (
    add_daily_help_document,
    approve_project_daily_help_profile,
    create_project_daily_help_category,
    create_project_daily_help_profile,
    deactivate_project_daily_help_profile,
    delete_daily_help_document,
    delete_project_daily_help_profile,
    export_project_daily_help_profiles,
    get_daily_help_attendance,
    get_project_daily_help_profile,
    get_project_daily_help_summary,
    get_security_daily_help_submission,
    link_project_daily_help_to_unit,
    list_project_daily_help_categories,
    list_project_daily_help_household_links,
    list_project_daily_help_profiles,
    list_security_daily_help_submissions,
    reactivate_project_daily_help_profile,
    reject_project_daily_help_profile,
    replace_daily_help_availability,
    restore_project_daily_help_profile,
    resubmit_project_daily_help_profile,
    submit_project_daily_help_profile,
    unlink_project_daily_help_from_unit,
    update_project_daily_help_category,
    update_project_daily_help_profile,
)
from apps.user_service.app.schemas.daily_help import (
    AddDailyHelpDocumentRequest,
    AdminLinkDailyHelpUnitRequest,
    CreateDailyHelpCategoryRequest,
    CreateDailyHelpRequest,
    DailyHelpCategoryResponse,
    DailyHelpDetailResponse,
    DailyHelpDocumentResponse,
    DailyHelpHouseholdLinkResponse,
    DailyHelpListQuery,
    DailyHelpSubmissionListQuery,
    DailyHelpSummaryResponse,
    RejectDailyHelpRequest,
    ReplaceDailyHelpAvailabilityRequest,
    UpdateDailyHelpCategoryRequest,
    UpdateDailyHelpRequest,
)
from apps.user_service.app.schemas.enums import DailyHelpDocumentType, DailyHelpStatus
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
PROFILE_ID = "22222222-2222-2222-2222-222222222222"
CATEGORY_ID = "33333333-3333-3333-3333-333333333333"


@pytest.fixture(autouse=True)
def _skip_audit_logging():
    with patch(
        "apps.user_service.app.dependencies.audit_logs.audit_decorator._log_audit_event",
        new_callable=AsyncMock,
    ):
        yield


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/daily-help",
            "headers": [],
            "client": ("127.0.0.1", 50000),
        }
    )


def _user_context() -> UserContext:
    return UserContext(
        user_id="staff-1",
        email="staff@example.com",
        organization_id="org-1",
    )


def _detail(**overrides) -> DailyHelpDetailResponse:
    base = {
        "id": PROFILE_ID,
        "organization_id": "org-1",
        "project_id": PROJECT_ID,
        "first_name": "Lakshmi",
        "last_name": "Devi",
        "display_name": "Mrs. Lakshmi Devi",
        "category_id": CATEGORY_ID,
        "category_name": "Maid",
        "phone_isd_code": "+91",
        "phone_number": "9655011223",
        "status": DailyHelpStatus.ACTIVE.value,
        "gate_passcode": "4821",
        "open_to_work": False,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    base.update(overrides)
    return DailyHelpDetailResponse(**base)


def _category(**overrides) -> DailyHelpCategoryResponse:
    base = {
        "id": CATEGORY_ID,
        "organization_id": "org-1",
        "project_id": PROJECT_ID,
        "name": "Maid",
        "sort_order": 1,
        "status": "active",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    base.update(overrides)
    return DailyHelpCategoryResponse(**base)


def _document(**overrides) -> DailyHelpDocumentResponse:
    base = {
        "id": "doc-1",
        "document_type": "id_proof",
        "file_path": "org/docs/a.pdf",
        "file_name": "a.pdf",
    }
    base.update(overrides)
    return DailyHelpDocumentResponse(**base)


@pytest.mark.asyncio
@patch("apps.user_service.app.api.daily_help.ensure_staff_project_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.daily_help.DailyHelpService")
async def test_get_project_daily_help_summary(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    mock_service_cls.return_value.get_summary = AsyncMock(
        return_value=DailyHelpSummaryResponse(total=5, active=3)
    )

    response = await get_project_daily_help_summary(
        request=_request(),
        project_id=PROJECT_ID,
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.daily_help.ensure_staff_project_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.daily_help.DailyHelpService")
async def test_list_project_daily_help_profiles(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    mock_service_cls.return_value.list_profiles = AsyncMock(return_value=([], 0))

    response = await list_project_daily_help_profiles(
        request=_request(),
        project_id=PROJECT_ID,
        query=DailyHelpListQuery(),
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.daily_help.ensure_staff_project_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.daily_help.DailyHelpService")
async def test_list_and_create_categories(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    service = mock_service_cls.return_value
    service.list_categories = AsyncMock(return_value=[_category()])
    service.create_category = AsyncMock(return_value=_category())

    list_resp = await list_project_daily_help_categories(
        request=_request(),
        project_id=PROJECT_ID,
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
    )
    assert list_resp.status_code == 200

    create_resp = await create_project_daily_help_category(
        request=_request(),
        project_id=PROJECT_ID,
        body=CreateDailyHelpCategoryRequest(name="Cook", sort_order=2),
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
    )
    assert create_resp.status_code == 201


@pytest.mark.asyncio
@patch("apps.user_service.app.api.daily_help.ensure_staff_project_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.daily_help.DailyHelpService")
async def test_update_category(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    mock_service_cls.return_value.update_category = AsyncMock(return_value=_category(name="Cook"))

    response = await update_project_daily_help_category(
        request=_request(),
        project_id=PROJECT_ID,
        category_id=CATEGORY_ID,
        body=UpdateDailyHelpCategoryRequest(name="Cook"),
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.daily_help.ensure_staff_project_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.daily_help.DailyHelpService")
async def test_create_and_get_profile(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    service = mock_service_cls.return_value
    service.create_profile = AsyncMock(return_value=_detail())
    service.get_detail = AsyncMock(return_value=_detail())

    create_resp = await create_project_daily_help_profile(
        request=_request(),
        project_id=PROJECT_ID,
        body=CreateDailyHelpRequest(
            first_name="Lakshmi",
            last_name="Devi",
            category_id=CATEGORY_ID,
            phone_isd_code="+91",
            phone_number="9655011223",
        ),
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
    )
    assert create_resp.status_code == 201

    get_resp = await get_project_daily_help_profile(
        request=_request(),
        project_id=PROJECT_ID,
        profile_id=PROFILE_ID,
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
    )
    assert get_resp.status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.daily_help.ensure_staff_project_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.daily_help.DailyHelpService")
async def test_update_profile(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    mock_service_cls.return_value.update_profile = AsyncMock(return_value=_detail())

    response = await update_project_daily_help_profile(
        request=_request(),
        project_id=PROJECT_ID,
        profile_id=PROFILE_ID,
        body=UpdateDailyHelpRequest(first_name="Updated"),
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.daily_help.ensure_daily_help_reviewer_access", new_callable=AsyncMock
)
@patch("apps.user_service.app.api.daily_help.DailyHelpService")
async def test_approve_and_reject_profile(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    service = mock_service_cls.return_value
    service.approve_profile = AsyncMock(return_value=_detail(status=DailyHelpStatus.ACTIVE.value))
    service.reject_profile = AsyncMock(return_value=_detail(status=DailyHelpStatus.REJECTED.value))

    approve_resp = await approve_project_daily_help_profile(
        request=_request(),
        project_id=PROJECT_ID,
        profile_id=PROFILE_ID,
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
    )
    assert approve_resp.status_code == 200

    reject_resp = await reject_project_daily_help_profile(
        request=_request(),
        project_id=PROJECT_ID,
        profile_id=PROFILE_ID,
        body=RejectDailyHelpRequest(rejection_reason="Incomplete documents"),
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
    )
    assert reject_resp.status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.daily_help.ensure_staff_project_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.daily_help.DailyHelpService")
async def test_deactivate_and_delete_profile(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    service = mock_service_cls.return_value
    service.deactivate_profile = AsyncMock(
        return_value=_detail(status=DailyHelpStatus.INACTIVE.value)
    )
    service.delete_profile = AsyncMock(return_value=_detail(status=DailyHelpStatus.DELETED.value))

    deactivate_resp = await deactivate_project_daily_help_profile(
        request=_request(),
        project_id=PROJECT_ID,
        profile_id=PROFILE_ID,
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
    )
    assert deactivate_resp.status_code == 200

    delete_resp = await delete_project_daily_help_profile(
        request=_request(),
        project_id=PROJECT_ID,
        profile_id=PROFILE_ID,
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
    )
    assert delete_resp.status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.daily_help.ensure_staff_project_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.daily_help.DailyHelpService")
async def test_document_endpoints(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    service = mock_service_cls.return_value
    service.add_document = AsyncMock(return_value=_document())
    service.delete_document = AsyncMock(return_value=_document())

    add_resp = await add_daily_help_document(
        request=_request(),
        project_id=PROJECT_ID,
        profile_id=PROFILE_ID,
        body=AddDailyHelpDocumentRequest(
            document_type=DailyHelpDocumentType.ID_PROOF,
            file_path="org/docs/a.pdf",
            file_name="a.pdf",
        ),
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
    )
    assert add_resp.status_code == 201

    delete_resp = await delete_daily_help_document(
        request=_request(),
        project_id=PROJECT_ID,
        profile_id=PROFILE_ID,
        document_id="doc-1",
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
    )
    assert delete_resp.status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.daily_help.ensure_staff_project_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.daily_help.DailyHelpService")
async def test_get_profile_not_found_propagates(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    mock_service_cls.return_value.get_detail = AsyncMock(
        side_effect=NotFoundException(
            message_key="daily_help.errors.profile_not_found",
            custom_code=404,
        )
    )

    with pytest.raises(NotFoundException):
        await get_project_daily_help_profile(
            request=_request(),
            project_id=PROJECT_ID,
            profile_id=PROFILE_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )


@pytest.mark.asyncio
@patch("apps.user_service.app.api.daily_help.ensure_staff_project_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.daily_help.DailyHelpService")
async def test_export_profiles(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    mock_service_cls.return_value.export_csv = AsyncMock(return_value="name\nLakshmi\n")

    response = await export_project_daily_help_profiles(
        request=_request(),
        project_id=PROJECT_ID,
        query=DailyHelpListQuery(),
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.daily_help.ensure_security_project_member_access",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.daily_help.DailyHelpService")
async def test_submit_resubmit_restore_profiles(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    service = mock_service_cls.return_value
    service.submit_profile = AsyncMock(
        return_value=_detail(status=DailyHelpStatus.PENDING_APPROVAL.value)
    )
    service.resubmit_profile = AsyncMock(
        return_value=_detail(status=DailyHelpStatus.PENDING_APPROVAL.value)
    )
    body = CreateDailyHelpRequest(
        first_name="Lakshmi",
        last_name="Devi",
        category_id=CATEGORY_ID,
        phone_isd_code="+91",
        phone_number="9655011223",
    )

    assert (
        await submit_project_daily_help_profile(
            request=_request(),
            project_id=PROJECT_ID,
            body=body,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 201

    assert (
        await resubmit_project_daily_help_profile(
            request=_request(),
            project_id=PROJECT_ID,
            profile_id=PROFILE_ID,
            body=body,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.daily_help.ensure_staff_project_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.daily_help.DailyHelpService")
async def test_restore_and_reactivate_profiles(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    service = mock_service_cls.return_value
    service.restore_profile = AsyncMock(return_value=_detail())
    service.reactivate_profile = AsyncMock(
        return_value=_detail(status=DailyHelpStatus.ACTIVE.value)
    )

    assert (
        await restore_project_daily_help_profile(
            request=_request(),
            project_id=PROJECT_ID,
            profile_id=PROFILE_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await reactivate_project_daily_help_profile(
            request=_request(),
            project_id=PROJECT_ID,
            profile_id=PROFILE_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.daily_help.ensure_staff_project_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.daily_help.DailyHelpService")
async def test_household_links_and_availability(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    service = mock_service_cls.return_value
    service.list_household_links = AsyncMock(return_value=[])
    service.replace_availability_slots = AsyncMock(return_value=[])

    assert (
        await list_project_daily_help_household_links(
            request=_request(),
            project_id=PROJECT_ID,
            profile_id=PROFILE_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await replace_daily_help_availability(
            request=_request(),
            project_id=PROJECT_ID,
            profile_id=PROFILE_ID,
            body=ReplaceDailyHelpAvailabilityRequest(slots=[]),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.daily_help.ensure_staff_project_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.daily_help.DailyHelpService")
async def test_admin_household_link_and_unlink(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    service = mock_service_cls.return_value
    service.add_admin_household_link = AsyncMock(
        return_value=DailyHelpHouseholdLinkResponse(
            id="link-1",
            unit_id="unit-1",
            status="active",
        )
    )
    service.remove_admin_household_link = AsyncMock(
        return_value=DailyHelpHouseholdLinkResponse(
            id="link-1",
            unit_id="unit-1",
            status="removed",
        )
    )

    assert (
        await link_project_daily_help_to_unit(
            request=_request(),
            project_id=PROJECT_ID,
            profile_id=PROFILE_ID,
            body=AdminLinkDailyHelpUnitRequest(unit_id="unit-1"),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 201

    assert (
        await unlink_project_daily_help_from_unit(
            request=_request(),
            project_id=PROJECT_ID,
            profile_id=PROFILE_ID,
            link_id="link-1",
            body=None,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.daily_help.ensure_staff_project_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.daily_help.DailyHelpService")
async def test_attendance_endpoint(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    mock_service_cls.return_value.get_attendance = AsyncMock(return_value={"days": []})

    response = await get_daily_help_attendance(
        request=_request(),
        project_id=PROJECT_ID,
        profile_id=PROFILE_ID,
        month=8,
        year=2026,
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.daily_help.ensure_security_project_member_access",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.daily_help.DailyHelpService")
async def test_security_submission_endpoints(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    service = mock_service_cls.return_value
    service.list_my_submissions = AsyncMock(return_value=([], 0))
    service.get_my_submission = AsyncMock(
        return_value=_detail(status=DailyHelpStatus.PENDING_APPROVAL.value)
    )

    assert (
        await list_security_daily_help_submissions(
            request=_request(),
            project_id=PROJECT_ID,
            query=DailyHelpSubmissionListQuery(),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await get_security_daily_help_submission(
            request=_request(),
            project_id=PROJECT_ID,
            profile_id=PROFILE_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200
