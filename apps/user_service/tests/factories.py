"""Factory helpers for test payloads."""

import secrets


def user_payload(
    email: str | None = None,
    password: str = "StrongPass123!",
    first_name: str = "Test",
    last_name: str = "User",
    verification_id: str = "vid",
    verification_code: str = "123456",
    timezone: str = "UTC",
) -> dict:
    """Build a signup payload with sensible defaults."""
    return {
        "email": email or f"user-{secrets.token_hex(3)}@example.com",
        "password": password,
        "first_name": first_name,
        "last_name": last_name,
        "verification_id": verification_id,
        "verification_code": verification_code,
        "timezone": timezone,
    }


def auth_tokens(
    access_token: str = "atk",
    refresh_token: str = "rtk",
    expires_in: int = 3600,
) -> dict:
    """Build a dictionary of auth tokens with sensible defaults.
    Args:
        access_token: str: The access token.
        refresh_token: str: The refresh token.
        expires_in: int: The expiration time in seconds.
    Returns:
        dict: The dictionary of auth tokens.
    """
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": expires_in,
    }
