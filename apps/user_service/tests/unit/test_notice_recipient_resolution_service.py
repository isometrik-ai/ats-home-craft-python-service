"""Unit tests for NoticeRecipientResolutionService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums import ProjectMemberRole
from apps.user_service.app.services.notice_recipient_resolution_service import (
    NoticeRecipientResolutionService,
)


@pytest.mark.asyncio
async def test_estimate_reach_staff_uses_project_members():
    """Staff reach counts non-security project members and security members."""
    db = MagicMock()
    db.fetch = AsyncMock(
        side_effect=[
            [{"user_id": "staff-1"}, {"user_id": "staff-2"}],
            [{"user_id": "guard-1"}],
        ]
    )
    service = NoticeRecipientResolutionService(db_connection=db)

    total, breakdown = await service.estimate_reach(
        organization_id="org-1",
        project_id="project-1",
        recipient_groups=["Staff"],
        scope_type="whole_society",
        tower_ids=[],
    )

    assert total == 3
    assert breakdown == {"Staff": 3}


@pytest.mark.asyncio
async def test_estimate_reach_security_uses_project_security_role():
    """Security reach counts active project members with security role."""
    db = MagicMock()
    db.fetch = AsyncMock(return_value=[{"user_id": "guard-1"}])
    service = NoticeRecipientResolutionService(db_connection=db)

    total, breakdown = await service.estimate_reach(
        organization_id="org-1",
        project_id="project-1",
        recipient_groups=["Security"],
        scope_type="whole_society",
        tower_ids=[],
    )

    assert total == 1
    assert breakdown == {"Security": 1}
    assert ProjectMemberRole.SECURITY.value in db.fetch.await_args.args


@pytest.mark.asyncio
async def test_estimate_reach_owner_tenant_uses_contact_roles():
    """Owner/Tenant reach resolves contacts via contact_roles and portal user ids."""
    db = MagicMock()
    db.fetch = AsyncMock(
        side_effect=[
            [{"contact_id": "contact-1"}, {"contact_id": "contact-2"}],
            [{"user_id": "resident-1"}],
        ]
    )
    service = NoticeRecipientResolutionService(db_connection=db)

    total, breakdown = await service.estimate_reach(
        organization_id="org-1",
        project_id="project-1",
        recipient_groups=["Owner"],
        scope_type="whole_society",
        tower_ids=[],
    )

    assert breakdown == {"Owner": 2}
    assert total == 1
    owner_query = db.fetch.await_args_list[0].args[0]
    assert "FROM contact_roles cr" in owner_query


@pytest.mark.asyncio
async def test_is_visible_to_contact_staff_checks_project_members():
    """Staff-targeted notices are visible to assigned non-security project members."""
    db = MagicMock()
    db.fetchrow = AsyncMock(return_value={"?column?": 1})
    service = NoticeRecipientResolutionService(db_connection=db)

    visible = await service.is_visible_to_contact(
        organization_id="org-1",
        project_id="project-1",
        notice={
            "status": "live",
            "recipient_groups": ["Staff"],
            "scope_type": "whole_society",
            "tower_ids": [],
        },
        contact_id="contact-1",
        contact_user_id="staff-user-1",
    )

    assert visible is True
    query = db.fetchrow.await_args.args[0]
    assert "FROM project_members pm" in query


@pytest.mark.asyncio
async def test_is_visible_skips_owner_tenant_when_contact_id_missing():
    """Staff viewers without a contact must not query contact_roles with an empty UUID."""
    db = MagicMock()
    db.fetchrow = AsyncMock(return_value={"?column?": 1})
    service = NoticeRecipientResolutionService(db_connection=db)

    visible = await service.is_visible_to_contact(
        organization_id="org-1",
        project_id="project-1",
        notice={
            "status": "live",
            "recipient_groups": ["Owner", "Security"],
            "scope_type": "whole_society",
            "tower_ids": [],
        },
        contact_id=None,
        contact_user_id="guard-1",
    )

    assert visible is True
    query = db.fetchrow.await_args.args[0]
    assert "FROM project_members pm" in query
    assert "contact_roles" not in query


@pytest.mark.asyncio
async def test_is_visible_staff_notice_visible_to_security_user():
    """Notices targeting Staff are also visible to security project members."""
    db = MagicMock()
    db.fetchrow = AsyncMock(
        side_effect=[
            None,
            {"?column?": 1},
        ]
    )
    service = NoticeRecipientResolutionService(db_connection=db)

    visible = await service.is_visible_to_contact(
        organization_id="org-1",
        project_id="project-1",
        notice={
            "status": "live",
            "recipient_groups": ["Staff"],
            "scope_type": "whole_society",
            "tower_ids": [],
        },
        contact_id=None,
        contact_user_id="guard-1",
    )

    assert visible is True


@pytest.mark.asyncio
async def test_is_visible_security_only_notice_hidden_from_staff_user():
    """Security-only notices are not visible to non-security project members."""
    db = MagicMock()
    db.fetchrow = AsyncMock(return_value=None)
    service = NoticeRecipientResolutionService(db_connection=db)

    visible = await service.is_visible_to_contact(
        organization_id="org-1",
        project_id="project-1",
        notice={
            "status": "live",
            "recipient_groups": ["Security"],
            "scope_type": "whole_society",
            "tower_ids": [],
        },
        contact_id=None,
        contact_user_id="staff-user-1",
    )

    assert visible is False
