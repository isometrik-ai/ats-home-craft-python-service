"""Unit tests for webhook URL validation and vendor request schemas."""

import socket

import pytest
from pydantic import ValidationError

from apps.work_order_service.app.schemas.work_orders import VendorUpdateWorkOrderRequest
from apps.work_order_service.app.utils.webhook_url import validate_outbound_webhook_url
from libs.shared_utils.http_exceptions import ValidationException

_PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def mock_public_dns(monkeypatch):
    """Resolve hostnames to a stable public IP in unit tests."""

    def fake_getaddrinfo(host, port, *_args, **_kwargs):
        if host in {"127.0.0.1", "localhost", "169.254.169.254"}:
            raise socket.gaierror("blocked")
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (host if host.replace(".", "").isdigit() else _PUBLIC_IP, port),
            )
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def test_vendor_update_rejects_timeline_field():
    """Vendor updates must not accept timeline payloads."""
    with pytest.raises(ValidationError):
        VendorUpdateWorkOrderRequest.model_validate(
            {"timeline": [{"type": "forged", "note": "bad"}]}
        )


def test_vendor_update_accepts_state_only():
    """Vendor updates allow restricted operational fields."""
    body = VendorUpdateWorkOrderRequest.model_validate({"state": "in_progress"})
    dumped = body.model_dump(exclude_unset=True, mode="json")
    assert dumped == {"state": "in_progress"}


def test_vendor_update_accepts_line_items():
    """Vendor portal can submit cost breakdown line items."""
    body = VendorUpdateWorkOrderRequest.model_validate(
        {"line_items": [{"description": "Labor", "total_minor": 1000}]}
    )
    assert body.line_items is not None


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.example.com/work-order",
        "https://customer.io/webhooks/abc",
    ],
)
def test_validate_webhook_url_accepts_public_https(url: str):
    """Public HTTPS webhook URLs are accepted."""
    assert validate_outbound_webhook_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.com/insecure",
        "https://127.0.0.1/hook",
        "https://localhost/hook",
        "https://169.254.169.254/",
        "ftp://hooks.example.com/hook",
    ],
)
def test_validate_webhook_url_rejects_unsafe_urls(url: str):
    """Private, non-HTTPS, and localhost webhook URLs are rejected."""
    with pytest.raises(ValidationException):
        validate_outbound_webhook_url(url)


def test_validate_webhook_url_rejects_hostname_resolving_to_private(monkeypatch):
    """Hostnames that resolve to private IPs are rejected."""

    def fake_getaddrinfo(host, port, *_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(ValidationException):
        validate_outbound_webhook_url("https://rebind.example/hook")
