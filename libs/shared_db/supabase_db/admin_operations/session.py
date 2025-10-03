"""
Session Admin Operations Module

This module contains all session-related admin operations.
All Supabase Auth admin API operations for session management should be centralized here.
"""

from postgrest import APIError
from httpx import HTTPError, RequestError, TimeoutException
from apps.user_service.app.dependencies.logger import get_logger
from apps.user_service.app.schemas.auth import CODE_VERIFIER, CODE_CHALLENGE
logger = get_logger("session_admin_operations")

from libs.shared_db.supabase_db.db import get_supabase_admin_client


async def get_session_by_id_admin(code: str):
    """
    Retrieve a Supabase session by authorization code (admin operation).

    This function exchanges an authorization code for a Supabase session using the admin client.
    It is intended for use in admin-level flows where direct session management is required.

    Args:
        code (str): The authorization code received from the Supabase Auth flow.

    Returns:
        dict: The session object returned by Supabase upon successful code exchange.

    Raises:
        APIError: If the Supabase API returns an error during the exchange.
        HTTPError, RequestError, TimeoutException: For network-related errors.
        KeyError, TypeError, ValueError: For data validation or parsing errors.

    Logging:
        - Logs the code challenge and code verifier used in the exchange.
        - Logs errors with context if exceptions are raised.

    Example:
        session = await get_session_by_id_admin(auth_code)
    """
    try:
        supabase = await get_supabase_admin_client()
        logger.info("CODE_CHALLENGE: %s", CODE_CHALLENGE)
        logger.info("CODE_VERIFIER: %s", CODE_VERIFIER)
        result = await supabase.auth.exchange_code_for_session(
            {
                "auth_code": code,
                "code_verifier": CODE_VERIFIER
            })
        return result
    except APIError as e:
        logger.error("Supabase API error getting session by id: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error getting session by id: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error getting session by id: %s", e, exc_info=True)
        raise
