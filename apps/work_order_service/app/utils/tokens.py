"""Vendor portal token utilities."""

from __future__ import annotations

import hashlib
import secrets


def generate_vendor_token() -> tuple[str, str]:
    """Return (raw_token, sha256_hash) for storage."""
    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    return raw, token_hash


def hash_vendor_token(raw: str) -> str:
    """Hash vendor token."""
    return hashlib.sha256(raw.encode()).hexdigest()
