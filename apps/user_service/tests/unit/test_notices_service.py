"""Unit tests for NoticesService validation helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from apps.user_service.app.schemas.enums import (
    NoticePinDuration,
    NoticePublishMode,
    NoticeScopeType,
    NoticeStatus,
)
from apps.user_service.app.services.notices_service import NoticesService
from libs.shared_utils.http_exceptions import ConflictException, ValidationException


def test_validate_content_title_too_long():
    with pytest.raises(ValidationException):
        NoticesService._validate_content(None, "x" * 71, "ok")


def test_validate_content_description_too_long():
    with pytest.raises(ValidationException):
        NoticesService._validate_content(None, "title", "x" * 601)


def test_validate_publish_requirements_missing_recipients():
    with pytest.raises(ValidationException):
        NoticesService._validate_publish_requirements(
            None,
            recipient_groups=[],
            scope_type=NoticeScopeType.WHOLE_SOCIETY,
            tower_ids=[],
        )


def test_validate_publish_requirements_missing_towers():
    with pytest.raises(ValidationException):
        NoticesService._validate_publish_requirements(
            None,
            recipient_groups=["Owner"],
            scope_type=NoticeScopeType.BY_TOWER,
            tower_ids=[],
        )


def test_validate_schedule_too_far():
    future = datetime.now(timezone.utc) + timedelta(days=90)
    with pytest.raises(ValidationException):
        NoticesService._validate_schedule(None, future)


def test_assert_editable_live():
    with pytest.raises(ConflictException):
        NoticesService._assert_editable(None, NoticeStatus.LIVE.value)


def test_assert_duplicatable_live():
    with pytest.raises(ConflictException):
        NoticesService._assert_duplicatable(None, NoticeStatus.LIVE.value)


def test_resolve_publish_state_draft():
    status, publish_at, published_at = NoticesService._resolve_publish_state(
        None,
        publish_mode=NoticePublishMode.DRAFT,
        publish_at=None,
        recipient_groups=[],
        scope_type=NoticeScopeType.WHOLE_SOCIETY,
        tower_ids=[],
    )
    assert status == NoticeStatus.DRAFT.value
    assert publish_at is None
    assert published_at is None


def test_pin_expires_at_manual():
    assert NoticesService._pin_expires_at(NoticePinDuration.MANUAL) is None
