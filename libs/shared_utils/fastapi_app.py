"""
FastAPI application initialization utilities.

This module provides shared functionality for initializing FastAPI applications
with common configurations like rate limiting.
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware


def create_fastapi_app() -> tuple[FastAPI, Limiter]:
    """
    Create a FastAPI application with rate limiting configured.

    Returns:
        tuple[FastAPI, Limiter]: A tuple containing the FastAPI app instance and limiter
    """
    app = FastAPI()
    # Initialize the Limiter
    limiter = Limiter(key_func=get_remote_address)
    # Attach the Limiter to the app's state
    app.state.limiter = limiter
    # Add the SlowAPI middleware to the app
    app.add_middleware(SlowAPIMiddleware)

    # Add a global exception handler for rate limit exceeded
    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_exceeded_handler(_request, _exc):
        """
        Handles the RateLimitExceeded exception globally.

        This function is triggered when a request exceeds the defined rate limit.
        It returns a JSON response with a 429 status code and a message indicating
        that the rate limit has been exceeded.

        Args:
            request: The incoming HTTP request object.
            exc: The exception instance containing details about the rate limit exceedance.

        Returns:
            JSONResponse: A response object with a 429 status code and a message.
        """
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded"},
        )

    return app, limiter
