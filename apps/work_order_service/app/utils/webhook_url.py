"""Outbound webhook URL validation (SSRF guard)."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from libs.shared_utils.http_exceptions import ValidationException


def validate_outbound_webhook_url(url: str) -> str:
    """Validate a customer webhook URL before save or delivery."""
    cleaned = (url or "").strip()
    if not cleaned or len(cleaned) > 2000:
        raise ValidationException(message_key="errors.validation_error")

    parsed = urlparse(cleaned)
    if parsed.scheme != "https":
        raise ValidationException(message_key="errors.validation_error")
    if parsed.username or parsed.password:
        raise ValidationException(message_key="errors.validation_error")
    if not parsed.hostname:
        raise ValidationException(message_key="errors.validation_error")

    host = parsed.hostname.lower()
    if host in {"localhost"} or host.endswith(".localhost"):
        raise ValidationException(message_key="errors.validation_error")

    try:
        ip = ipaddress.ip_address(host)
        if any(
            (
                ip.is_private,
                ip.is_loopback,
                ip.is_link_local,
                ip.is_multicast,
                ip.is_reserved,
                ip.is_unspecified,
            )
        ):
            raise ValidationException(message_key="errors.validation_error")
    except ValueError:
        # Hostname — allowed; DNS resolution is not performed here.
        pass

    return cleaned
