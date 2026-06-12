"""App instance module.

This module initializes and configures the FastAPI
application instance with rate limiting capabilities using SlowAPI.

Modules:
- FastAPI: The main application framework.
- SlowAPI: A rate limiting library for ASGI applications.

Configuration:
- Initializes a FastAPI application.
- Sets up a rate limiter using SlowAPI with the client's remote address as the key.
- Attaches the rate limiter to the application's state.
- Adds SlowAPI middleware to handle rate limiting.
- Defines a global exception handler for rate limit exceeded errors,
  returning a 429 status code with a JSON response.

Usage:
This module should be imported and used to create the FastAPI
application instance with rate limiting enabled.
"""

from apps.user_service.app.lifespan import lifespan
from libs.shared_utils.fastapi_app import create_fastapi_app
from libs.shared_utils.telemetry_config import telemetry_config

telemetry_config.setup_telemetry()

app, limiter = create_fastapi_app(lifespan=lifespan)
