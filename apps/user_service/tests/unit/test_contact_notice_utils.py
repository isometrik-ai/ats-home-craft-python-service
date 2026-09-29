"""Unit tests for contact notice helper utilities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.utils.contact_notice_utils import purge_contact_notice_likes

ORG_ID = "11111111-1111-1111-1111-111111111111"
CONTACT_ID = "55555555-5555-5555-5555-555555555555"
_MODULE = "apps.user_service.app.utils.contact_notice_utils"


@pytest.mark.asyncio
@patch(f"{_MODULE}.NoticesRepository")
async def test_purge_contact_notice_likes_happy_path(mock_repo_cls: MagicMock) -> None:
    """Deletes all notice likes for the contact via the repository."""
    repo = MagicMock()
    repo.delete_all_likes_for_contact = AsyncMock()
    mock_repo_cls.return_value = repo
    db = MagicMock()

    await purge_contact_notice_likes(
        db_connection=db,
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
    )

    mock_repo_cls.assert_called_once_with(db)
    repo.delete_all_likes_for_contact.assert_awaited_once_with(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
    )


@pytest.mark.asyncio
@patch(f"{_MODULE}.NoticesRepository")
async def test_purge_contact_notice_likes_early_return_empty_org(
    mock_repo_cls: MagicMock,
) -> None:
    """Missing organization id skips repository work."""
    await purge_contact_notice_likes(
        db_connection=MagicMock(),
        organization_id="",
        contact_id=CONTACT_ID,
    )
    mock_repo_cls.assert_not_called()


@pytest.mark.asyncio
@patch(f"{_MODULE}.NoticesRepository")
async def test_purge_contact_notice_likes_early_return_empty_contact(
    mock_repo_cls: MagicMock,
) -> None:
    """Missing contact id skips repository work."""
    await purge_contact_notice_likes(
        db_connection=MagicMock(),
        organization_id=ORG_ID,
        contact_id="",
    )
    mock_repo_cls.assert_not_called()
