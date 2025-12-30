"""Exception middleware for the API service."""

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from libs.shared_utils.logger import get_logger

# Use the shared application logger
logger = get_logger()

# Initialize logger for exception middleware
exception_logger = get_logger("exception-middleware")


class CacheRequestBodyMiddleware(BaseHTTPMiddleware):
    """Middleware to cache request body for potential reuse.

    This middleware caches the request body in request.state.cached_body
    to allow multiple reads of the request body, which is useful for
    audit logging and other middleware that need to access the body.

    Note: Using a cached_body attribute is an accepted pattern in FastAPI
    middleware for caching request bodies.
    """

    async def dispatch(self, request: Request, call_next: Callable[[Request], Any]):
        """Process the request and cache its body for reuse.

        Args:
            request (Request): The incoming FastAPI request
            call_next (Callable[[Request], Any]): The next middleware or endpoint handler

        Returns:
            Any: The response from the next handler
        """
        if request.method == "OPTIONS":
            return await call_next(request)

        request_id = str(uuid.uuid4())

        if not hasattr(request.state, "cached_body"):
            try:
                body_bytes = await request.body()
                request.state.cached_body = body_bytes

                log_msg = (
                    "Request body cached successfully - Request ID: %s, "
                    "Method: %s, URL: %s, Body Size: %s bytes"
                )
                exception_logger.debug(
                    log_msg,
                    request_id,
                    request.method,
                    str(request.url),
                    len(body_bytes),
                )

            except (OSError, ValueError) as e:
                request.state.cached_body = b""

                log_msg = (
                    "Failed to cache request body - Request ID: %s, Method: %s, URL: %s, Error: %s"
                )
                exception_logger.warning(
                    log_msg,
                    request_id,
                    request.method,
                    str(request.url),
                    str(e),
                )

        response = await call_next(request)
        return response
