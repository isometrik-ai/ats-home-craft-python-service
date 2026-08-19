"""Unit tests for NoticesResidentService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.notices import (
    ResidentBannerQuery,
    ResidentNoticeListQuery,
)
from apps.user_service.app.services.notices_resident_service import (
    NoticesResidentService,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import ForbiddenException, NotFoundException


def _service() -> NoticesResidentService:
    return NoticesResidentService(
        db_connection=MagicMock(),
        user_context=UserContext(
            user_id="user-1",
            email="user@example.com",
            organization_id="org-1",
        ),
    )


@pytest.mark.asyncio
async def test_list_notices_for_security_viewer():
    """Security project members receive staff-targeted live notices in the feed."""
    svc = _service()
    svc.repo = MagicMock()
    svc.recipient_service = MagicMock()
    svc.repo.list_live_notice_ids_for_project = AsyncMock(return_value=["notice-1"])
    svc.recipient_service.load_notice_contexts = AsyncMock(
        return_value=[
            {
                "id": "notice-1",
                "project_id": "project-1",
                "status": "live",
                "recipient_groups": ["Staff"],
                "scope_type": "whole_society",
                "tower_ids": [],
            }
        ]
    )
    svc.recipient_service.filter_visible_notice_ids = AsyncMock(return_value=["notice-1"])
    svc.repo.list_live_notices_for_resident = AsyncMock(
        return_value=(
            [
                {
                    "id": "notice-1",
                    "display_code": "NTC-1",
                    "title": "Staff notice",
                    "description": "Body",
                    "category": "general",
                    "published_at": None,
                    "view_count": 0,
                    "like_count": 0,
                    "pinned": False,
                    "pin_slot_index": None,
                    "scope_type": "whole_society",
                }
            ],
            1,
        )
    )
    svc.repo.list_attachments = AsyncMock(return_value=[])
    svc.repo.contact_has_liked = AsyncMock(return_value=False)

    items, total = await svc.list_notices(
        contact_id=None,
        contact_user_id="guard-1",
        query=ResidentNoticeListQuery(project_id="project-1"),
    )

    assert total == 1
    assert items[0].id == "notice-1"
    assert items[0].liked_by_me is False
    svc.recipient_service.filter_visible_notice_ids.assert_awaited_once_with(
        organization_id="org-1",
        project_id="project-1",
        notice_contexts=svc.recipient_service.load_notice_contexts.return_value,
        contact_id=None,
        contact_user_id="guard-1",
    )


@pytest.mark.asyncio
async def test_list_notices_passes_search_to_repository():
    """Search query is forwarded to the repository after visibility filtering."""
    svc = _service()
    svc.repo = MagicMock()
    svc.recipient_service = MagicMock()
    svc.repo.list_live_notice_ids_for_project = AsyncMock(return_value=["notice-1"])
    svc.recipient_service.load_notice_contexts = AsyncMock(return_value=[])
    svc.recipient_service.filter_visible_notice_ids = AsyncMock(return_value=["notice-1"])
    svc.repo.list_live_notices_for_resident = AsyncMock(return_value=([], 0))
    svc.repo.list_attachments = AsyncMock(return_value=[])
    svc.repo.contact_has_liked = AsyncMock(return_value=False)

    await svc.list_notices(
        contact_id="contact-1",
        contact_user_id="user-1",
        query=ResidentNoticeListQuery(project_id="project-1", search="water"),
    )

    svc.repo.list_live_notices_for_resident.assert_awaited_once_with(
        organization_id="org-1",
        project_id="project-1",
        notice_ids=["notice-1"],
        search="water",
        limit=20,
        offset=0,
    )


@pytest.mark.asyncio
async def test_get_banner_filters_to_visible_pins():
    """Banner returns only pinned notices visible to the caller."""
    svc = _service()
    svc.repo = MagicMock()
    svc.recipient_service = MagicMock()
    svc.recipient_service.load_notice_contexts = AsyncMock(return_value=[])
    svc.recipient_service.filter_visible_notice_ids = AsyncMock(return_value=["notice-1"])
    svc.repo.list_live_notice_ids_for_project = AsyncMock(return_value=["notice-1", "notice-2"])
    svc.repo.list_active_pins_with_notices = AsyncMock(
        return_value=[
            {"notice_id": "notice-1"},
            {"notice_id": "notice-2"},
        ]
    )
    svc.repo.fetch_notice_for_resident = AsyncMock(
        return_value={
            "id": "notice-1",
            "display_code": "NTC-1",
            "title": "Pinned",
            "description": "Body",
            "category": "general",
            "published_at": None,
            "view_count": 0,
            "like_count": 0,
            "pinned": True,
            "pin_slot_index": 1,
            "scope_type": "whole_society",
        }
    )
    svc.repo.list_attachments = AsyncMock(return_value=[])
    svc.repo.contact_has_liked = AsyncMock(return_value=False)

    items = await svc.get_banner(
        contact_id=None,
        contact_user_id="guard-1",
        query=ResidentBannerQuery(project_id="project-1"),
    )

    assert len(items) == 1
    assert items[0].id == "notice-1"
    assert svc.repo.fetch_notice_for_resident.await_count == 1


@pytest.mark.asyncio
async def test_get_notice_hidden_from_staff_for_security_only():
    """Security-only notices return not found for non-security viewers."""
    svc = _service()
    svc.repo = MagicMock()
    svc.recipient_service = MagicMock()
    svc.repo.fetch_notice_visibility_context = AsyncMock(
        return_value={
            "id": "notice-1",
            "project_id": "project-1",
            "status": "live",
            "recipient_groups": ["Security"],
            "scope_type": "whole_society",
            "tower_ids": [],
        }
    )
    svc.recipient_service.is_visible_to_contact = AsyncMock(return_value=False)

    with pytest.raises(NotFoundException):
        await svc.get_notice(
            contact_id=None,
            contact_user_id="staff-user-1",
            notice_id="notice-1",
        )


@pytest.mark.asyncio
async def test_like_notice_requires_viewer_identity():
    """Like/unlike require either a contact profile or authenticated user id."""
    svc = _service()

    with pytest.raises(ForbiddenException):
        await svc.like_notice(
            contact_id=None,
            contact_user_id=None,
            notice_id="notice-1",
        )


@pytest.mark.asyncio
async def test_staff_like_uses_user_id():
    """Staff/security viewers like notices by user_id."""
    svc = _service()
    svc.repo = MagicMock()
    svc.recipient_service = MagicMock()
    svc.repo.fetch_notice_visibility_context = AsyncMock(
        return_value={
            "id": "notice-1",
            "project_id": "project-1",
            "status": "live",
            "recipient_groups": ["Staff"],
            "scope_type": "whole_society",
            "tower_ids": [],
        }
    )
    svc.recipient_service.is_visible_to_contact = AsyncMock(return_value=True)
    svc.repo.fetch_notice_for_resident = AsyncMock(
        return_value={
            "id": "notice-1",
            "display_code": "NTC-1",
            "title": "Title",
            "description": "Body",
            "category": "general",
            "published_at": None,
            "view_count": 1,
            "like_count": 1,
            "pinned": False,
            "pin_slot_index": None,
            "scope_type": "whole_society",
            "scope_label": "Whole society",
        }
    )
    svc.repo.list_attachments = AsyncMock(return_value=[])
    svc.repo.contact_has_liked = AsyncMock(return_value=True)
    svc.repo.upsert_like = AsyncMock(return_value=True)

    data = await svc.like_notice(
        contact_id=None,
        contact_user_id="guard-1",
        notice_id="notice-1",
    )

    assert data.liked_by_me is True
    svc.repo.upsert_like.assert_awaited_once_with(
        organization_id="org-1",
        notice_id="notice-1",
        user_id="guard-1",
    )


@pytest.mark.asyncio
async def test_resident_like_uses_contact_id():
    """Residents with contacts can like visible notices."""
    svc = _service()
    svc.repo = MagicMock()
    svc.recipient_service = MagicMock()
    svc.repo.fetch_notice_visibility_context = AsyncMock(
        return_value={
            "id": "notice-1",
            "project_id": "project-1",
            "status": "live",
            "recipient_groups": ["Owner"],
            "scope_type": "whole_society",
            "tower_ids": [],
        }
    )
    svc.recipient_service.is_visible_to_contact = AsyncMock(return_value=True)
    svc.repo.fetch_notice_for_resident = AsyncMock(
        return_value={
            "id": "notice-1",
            "display_code": "NTC-1",
            "title": "Title",
            "description": "Body",
            "category": "general",
            "published_at": None,
            "view_count": 1,
            "like_count": 1,
            "pinned": False,
            "pin_slot_index": None,
            "scope_type": "whole_society",
            "scope_label": "Whole society",
        }
    )
    svc.repo.list_attachments = AsyncMock(return_value=[])
    svc.repo.contact_has_liked = AsyncMock(return_value=True)
    svc.repo.upsert_like = AsyncMock(return_value=True)

    data = await svc.like_notice(
        contact_id="contact-1",
        contact_user_id="user-1",
        notice_id="notice-1",
    )

    assert data.liked_by_me is True
    svc.repo.upsert_like.assert_awaited_once_with(
        organization_id="org-1",
        notice_id="notice-1",
        contact_id="contact-1",
    )
