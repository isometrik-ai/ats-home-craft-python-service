"""FastAPI application initialization utilities.

This module provides shared functionality for initializing FastAPI applications
with common configurations like rate limiting.
"""

from fastapi import FastAPI
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.types import Lifespan

from libs.shared_utils.http_exceptions import RateLimitExceededException
from libs.shared_utils.status_codes import CustomStatusCode


def create_fastapi_app(lifespan: Lifespan | None = None) -> tuple[FastAPI, Limiter]:
    """Create a FastAPI application with rate limiting configured.

    Args:
        lifespan: Lifespan event handler for startup/shutdown events

    Returns:
        tuple[FastAPI, Limiter]: A tuple containing the FastAPI app instance and limiter
    """
    app = FastAPI(lifespan=lifespan)
    # Initialize the Limiter
    limiter = Limiter(key_func=get_remote_address)
    # Attach the Limiter to the app's state
    app.state.limiter = limiter
    # Add the SlowAPI middleware to the app
    app.add_middleware(SlowAPIMiddleware)

    # Add a global exception handler for rate limit exceeded
    # Note: This handler raises HTTPException instead of returning JSONResponse directly
    # to ensure CORS headers are properly added by the CORS middleware and to maintain
    # consistency with the unified exception handler pattern
    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_exceeded_handler(_request, _exc):
        """Handles the RateLimitExceeded exception globally.
        This function is triggered when a request exceeds the defined rate limit.
        It raises an HTTPException with a 429 status code, which will be handled by
        the unified exception handler to ensure proper CORS headers and consistent
        error response format."""
        raise RateLimitExceededException(
            message_key="errors.rate_limit_exceeded",
            custom_code=CustomStatusCode.RATE_LIMIT_EXCEEDED,
        )

    return app, limiter
