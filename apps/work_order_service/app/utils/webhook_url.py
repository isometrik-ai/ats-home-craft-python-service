"""Outbound webhook URL validation (SSRF guard)."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

from libs.shared_utils.http_exceptions import ValidationException


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True when an IP must not be used for outbound webhooks."""
    return any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )


def _validate_resolved_ips(ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address]) -> None:
    """Reject empty or blocked DNS results."""
    if not ips:
        raise ValidationException(message_key="errors.validation_error")
    for ip in ips:
        if _is_blocked_ip(ip):
            raise ValidationException(message_key="errors.validation_error")


def _ips_from_addrinfo(
    addrinfo: list[tuple],
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Collect unique resolved IPs from getaddrinfo results."""
    seen: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for _family, _type, _proto, _canonname, sockaddr in addrinfo:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip not in seen:
            seen.add(ip)
            ips.append(ip)
    return ips


def resolve_webhook_hostname_sync(hostname: str, *, port: int = 443) -> str:
    """Resolve a hostname and return the first validated IP (sync)."""
    try:
        addrinfo = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValidationException(message_key="errors.validation_error") from exc
    ips = _ips_from_addrinfo(addrinfo)
    _validate_resolved_ips(ips)
    return str(ips[0])


async def resolve_webhook_hostname(hostname: str, *, port: int = 443) -> str:
    """Resolve a hostname at delivery time and return the first validated IP."""
    loop = asyncio.get_running_loop()
    try:
        addrinfo = await loop.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValidationException(message_key="errors.validation_error") from exc
    ips = _ips_from_addrinfo(addrinfo)
    _validate_resolved_ips(ips)
    return str(ips[0])


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

    port = parsed.port or 443
    try:
        ip = ipaddress.ip_address(host)
        if _is_blocked_ip(ip):
            raise ValidationException(message_key="errors.validation_error")
    except ValueError:
        resolve_webhook_hostname_sync(host, port=port)

    return cleaned
