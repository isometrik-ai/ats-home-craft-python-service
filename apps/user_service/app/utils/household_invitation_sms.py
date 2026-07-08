"""SMS delivery helper for household invitations."""

from __future__ import annotations

from libs.shared_utils.logger import get_logger

logger = get_logger("household_invitation_sms")


def mask_phone(*, phone_isd_code: str, phone_number: str) -> str:
    """Return a masked phone for API responses."""
    digits = "".join(ch for ch in phone_number if ch.isdigit())
    if len(digits) <= 4:
        return f"{phone_isd_code} ****"
    return f"{phone_isd_code} {'*' * (len(digits) - 4)}{digits[-4:]}"


def send_household_invitation_sms(
    *,
    phone_isd_code: str,
    phone_number: str,
    inviter_name: str,
    invitee_name: str,
    invite_url: str,
) -> bool:
    """Dispatch a household invitation SMS.

    Wire your SMS provider here. Until then this logs the payload so the
    invitation flow can be exercised in development.
    """
    try:
        logger.info(
            "Household invitation SMS to %s%s for %s from %s: %s",
            phone_isd_code,
            phone_number,
            invitee_name,
            inviter_name,
            invite_url,
        )
        return True
    except Exception as error:
        logger.error("Failed to send household invitation SMS: %s", str(error))
        return False
