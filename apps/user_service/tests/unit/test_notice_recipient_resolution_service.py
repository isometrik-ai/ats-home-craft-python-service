"""Unit tests for NoticeRecipientResolutionService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from apps.user_service.app.schemas.enums import NoticeRecipientGroup, ProjectMemberRole
from apps.user_service.app.schemas.notices import (
    CreateNoticeRequest,
    ReachEstimateQuery,
)
from apps.user_service.app.services.notice_recipient_resolution_service import (
    NoticeRecipientResolutionService,
)

STAFF = NoticeRecipientGroup.STAFF.value


@pytest.mark.asyncio
async def test_estimate_reach_staff_manager_counts_one_not_three():
    """ATS-458: 1 Staff Manager + 1 Security must not show 3 when Staff Manager is selected.

    Example project members:
    - Alice -> community_admin (Staff Manager)
    - Bob   -> security
    - Carol -> accountant (other staff role, excluded)

    Old bug: Staff group counted all non-security roles + security => 3.
    Expected: Staff Manager selected => 1 (Alice only).
    """
    db = MagicMock()
    db.fetch = AsyncMock(return_value=[{"user_id": "alice"}])
    service = NoticeRecipientResolutionService(db_connection=db)

    total, breakdown = await service.estimate_reach(
        organization_id="org-1",
        project_id="project-1",
        recipient_groups=[STAFF],
        scope_type="whole_society",
        tower_ids=[],
    )

    assert breakdown == {STAFF: 1}
    assert total == 1
    assert db.fetch.await_count == 1


@pytest.mark.asyncio
async def test_estimate_reach_staff_manager_and_security_counts_two():
    """Success: selecting both Staff Manager and Security sums distinct people."""
    db = MagicMock()
    db.fetch = AsyncMock(
        side_effect=[
            [{"user_id": "alice"}],
            [{"user_id": "bob"}],
        ]
    )
    service = NoticeRecipientResolutionService(db_connection=db)

    total, breakdown = await service.estimate_reach(
        organization_id="org-1",
        project_id="project-1",
        recipient_groups=[STAFF, "Security"],
        scope_type="whole_society",
        tower_ids=[],
    )

    assert breakdown == {STAFF: 1, "Security": 1}
    assert total == 2


@pytest.mark.asyncio
async def test_estimate_reach_unknown_group_is_ignored_at_resolver_layer():
    """Resolver ignores unknown groups; API validates before calling estimate_reach."""
    db = MagicMock()
    db.fetch = AsyncMock()
    service = NoticeRecipientResolutionService(db_connection=db)

    total, breakdown = await service.estimate_reach(
        organization_id="org-1",
        project_id="project-1",
        recipient_groups=["Staff Manager"],
        scope_type="whole_society",
        tower_ids=[],
    )

    assert breakdown == {}
    assert total == 0
    db.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_create_notice_rejects_deprecated_staff_manager_label():
    """Failure case: API schema rejects deprecated 'Staff Manager' recipient group."""
    with pytest.raises(ValidationError):
        CreateNoticeRequest(
            title="Maintenance update",
            recipient_groups=["Staff Manager"],
        )


def test_create_notice_accepts_staff_recipient_group():
    """Success case: API schema accepts 'Staff' recipient group."""
    body = CreateNoticeRequest(
        title="Maintenance update",
        recipient_groups=[NoticeRecipientGroup.STAFF],
    )
    assert body.recipient_groups == [NoticeRecipientGroup.STAFF]


def test_reach_estimate_query_parses_staff_group():
    """Success case: reach-estimate query string accepts Staff."""
    query = ReachEstimateQuery(groups="Staff,Security")
    assert query.parsed_groups() == ["Staff", "Security"]


@pytest.mark.asyncio
async def test_estimate_reach_staff_manager_uses_project_members():
    """Staff Manager reach counts only staff manager project members."""
    db = MagicMock()
    db.fetch = AsyncMock(return_value=[{"user_id": "staff-1"}])
    service = NoticeRecipientResolutionService(db_connection=db)

    total, breakdown = await service.estimate_reach(
        organization_id="org-1",
        project_id="project-1",
        recipient_groups=[STAFF],
        scope_type="whole_society",
        tower_ids=[],
    )

    assert total == 1
    assert breakdown == {STAFF: 1}
    assert "staff_manager" in db.fetch.await_args.args[4]
    assert "Staff" in db.fetch.await_args.args[5]


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
    """Owner/Tenant reach resolves contacts via contact_roles aligned with contacts list."""
    db = MagicMock()
    db.fetch = AsyncMock(
        side_effect=[
            [{"contact_id": "contact-1"}, {"contact_id": "contact-2"}],
            [{"contact_id": "contact-1"}],
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
    assert total == 2
    owner_query = db.fetch.await_args_list[0].args[0]
    assert "FROM contacts ct" in owner_query
    assert "contact_units cu" in owner_query
    assert "cr.ended_at IS NULL" in owner_query


@pytest.mark.asyncio
async def test_is_visible_to_contact_staff_manager_checks_project_members():
    """Staff Manager notices are visible to assigned staff manager project members."""
    db = MagicMock()
    db.fetchrow = AsyncMock(return_value={"?column?": 1})
    service = NoticeRecipientResolutionService(db_connection=db)

    visible = await service.is_visible_to_contact(
        organization_id="org-1",
        project_id="project-1",
        notice={
            "status": "live",
            "recipient_groups": [NoticeRecipientGroup.STAFF.value],
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
    """Staff Manager viewers without a contact must not query contact_roles with an empty UUID."""
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
async def test_is_visible_staff_manager_notice_hidden_from_security_user():
    """Staff Manager notices are not visible to security-only project members."""
    db = MagicMock()
    db.fetchrow = AsyncMock(return_value=None)
    service = NoticeRecipientResolutionService(db_connection=db)

    visible = await service.is_visible_to_contact(
        organization_id="org-1",
        project_id="project-1",
        notice={
            "status": "live",
            "recipient_groups": [NoticeRecipientGroup.STAFF.value],
            "scope_type": "whole_society",
            "tower_ids": [],
        },
        contact_id=None,
        contact_user_id="guard-1",
    )

    assert visible is False


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


@pytest.mark.asyncio
async def test_resolve_recipient_user_ids_staff_manager_only():
    """resolve_recipient_user_ids returns staff manager user ids only."""
    db = MagicMock()
    db.fetch = AsyncMock(return_value=[{"user_id": "staff-1"}])
    service = NoticeRecipientResolutionService(db_connection=db)

    user_ids = await service.resolve_recipient_user_ids(
        organization_id="org-1",
        project_id="project-1",
        notice_id="notice-1",
        recipient_groups=[STAFF],
        scope_type="whole_society",
        tower_ids=[],
    )

    assert user_ids == ["staff-1"]


@pytest.mark.asyncio
async def test_resolve_recipient_user_ids_owner_tenant():
    """Owner group resolves contact ids then portal user ids."""
    db = MagicMock()
    db.fetch = AsyncMock(
        side_effect=[
            [{"contact_id": "contact-1"}],
            [{"user_id": "resident-1"}],
        ]
    )
    service = NoticeRecipientResolutionService(db_connection=db)

    user_ids = await service.resolve_recipient_user_ids(
        organization_id="org-1",
        project_id="project-1",
        notice_id="notice-1",
        recipient_groups=["Owner"],
        scope_type="by_tower",
        tower_ids=["tower-1"],
    )

    assert user_ids == ["resident-1"]
    owner_query = db.fetch.await_args_list[0].args[0]
    assert "tower_id = ANY" in owner_query


@pytest.mark.asyncio
async def test_is_visible_returns_false_for_non_live_notice():
    db = MagicMock()
    service = NoticeRecipientResolutionService(db_connection=db)

    visible = await service.is_visible_to_contact(
        organization_id="org-1",
        project_id="project-1",
        notice={"status": "draft", "recipient_groups": [NoticeRecipientGroup.STAFF.value]},
        contact_id=None,
        contact_user_id="staff-1",
    )

    assert visible is False
    db.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_is_visible_returns_false_when_no_recipient_groups():
    db = MagicMock()
    service = NoticeRecipientResolutionService(db_connection=db)

    visible = await service.is_visible_to_contact(
        organization_id="org-1",
        project_id="project-1",
        notice={"status": "live", "recipient_groups": []},
        contact_id="contact-1",
        contact_user_id="user-1",
    )

    assert visible is False


@pytest.mark.asyncio
async def test_is_visible_owner_with_contact_id():
    db = MagicMock()
    db.fetchrow = AsyncMock(return_value={"?column?": 1})
    service = NoticeRecipientResolutionService(db_connection=db)

    visible = await service.is_visible_to_contact(
        organization_id="org-1",
        project_id="project-1",
        notice={
            "status": "live",
            "recipient_groups": ["Owner"],
            "scope_type": "by_tower",
            "tower_ids": ["tower-1"],
        },
        contact_id="contact-1",
        contact_user_id=None,
    )

    assert visible is True
    query = db.fetchrow.await_args.args[0]
    assert "cr.contact_id" in query


@pytest.mark.asyncio
async def test_filter_visible_notice_ids():
    db = MagicMock()
    db.fetchrow = AsyncMock(return_value={"?column?": 1})
    service = NoticeRecipientResolutionService(db_connection=db)

    visible_ids = await service.filter_visible_notice_ids(
        organization_id="org-1",
        project_id="project-1",
        notice_contexts=[
            {
                "id": "n-1",
                "status": "live",
                "recipient_groups": [NoticeRecipientGroup.STAFF.value],
                "scope_type": "whole_society",
            },
            {
                "id": "n-2",
                "status": "draft",
                "recipient_groups": [NoticeRecipientGroup.STAFF.value],
            },
        ],
        contact_id=None,
        contact_user_id="staff-1",
    )

    assert visible_ids == ["n-1"]


@pytest.mark.asyncio
async def test_load_notice_contexts_empty():
    db = MagicMock()
    service = NoticeRecipientResolutionService(db_connection=db)

    contexts = await service.load_notice_contexts(organization_id="org-1", notice_ids=[])

    assert contexts == []
    db.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_load_notice_contexts_fetches_rows():
    db = MagicMock()
    db.fetch = AsyncMock(
        return_value=[
            {
                "id": "n-1",
                "project_id": "p-1",
                "status": "live",
                "scope_type": "whole_society",
                "recipient_groups": ["Owner"],
                "tower_ids": [],
            }
        ]
    )
    service = NoticeRecipientResolutionService(db_connection=db)

    contexts = await service.load_notice_contexts(
        organization_id="org-1",
        notice_ids=["n-1"],
    )

    assert contexts[0]["id"] == "n-1"
    assert "notice_recipients" in db.fetch.await_args.args[0]
