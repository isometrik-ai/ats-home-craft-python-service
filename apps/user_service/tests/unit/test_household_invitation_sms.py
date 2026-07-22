"""Unit tests for household invitation SMS helpers."""

from __future__ import annotations

from unittest.mock import patch

from apps.user_service.app.utils.household_invitation_sms import (
    mask_phone,
    send_household_invitation_sms,
)


def test_mask_phone_short_number() -> None:
    """Short numbers should mask entirely except the country code."""
    assert mask_phone(phone_isd_code="+91", phone_number="1234") == "+91 ****"


def test_mask_phone_long_number() -> None:
    """Long numbers should reveal only the last four digits."""
    masked = mask_phone(phone_isd_code="+91", phone_number="9876543210")
    assert masked.endswith("3210")
    assert "*" in masked


def test_send_invitation_sms_returns_true() -> None:
    """SMS helper should log payload and return success in development."""
    with patch("apps.user_service.app.utils.household_invitation_sms.logger") as mock_logger:
        ok = send_household_invitation_sms(
            phone_isd_code="+91",
            phone_number="9876543210",
            inviter_name="Admin",
            invitee_name="Resident",
            invite_url="https://example.com/invite",
        )
    assert ok is True
    mock_logger.info.assert_called_once()
