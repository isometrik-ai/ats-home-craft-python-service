# pylint: disable=all

"""
Async integration tests for main application components.
Tests main.py, routes.py, and auth.py endpoints with proper AsyncMock usage.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

@pytest.fixture
def main_app_client():
    """Test client for the main application with minimal mocking"""
    with patch('ddtrace.patch_all'), \
         patch('dotenv.load_dotenv'), \
         patch('apps.user_service.app.dependencies.logger.setup_logging') as mock_logger, \
         patch('libs.shared_middleware.jwt_auth.get_user_from_token') as mock_get_user:

        # Mock logger
        mock_logger.return_value = MagicMock()

        # Mock JWT to allow requests through
        mock_get_user.return_value = {
            "user_id": "test-user-id",
            "email": "test@example.com",
            "organization_id": "test-org-id"
        }

        # Import after mocking to avoid real dependencies
        from apps.user_service.app.main import app
        with TestClient(app) as client:
            yield client

@pytest.fixture
def async_main_app_client():
    """Async test client for the main application"""
    with patch('ddtrace.patch_all'), \
         patch('dotenv.load_dotenv'), \
         patch('apps.user_service.app.dependencies.logger.setup_logging') as mock_logger, \
         patch('libs.shared_middleware.jwt_auth.get_user_from_token') as mock_get_user:

        mock_logger.return_value = MagicMock()
        mock_get_user.return_value = {
            "user_id": "test-user-id",
            "email": "test@example.com",
            "organization_id": "test-org-id"
        }

        from apps.user_service.app.main import app
        return app

def test_health_endpoint(main_app_client):
    """Test the health check endpoint - covers main.py"""
    with patch('apps.user_service.app.main.get_session_by_id_admin') as mock_get_session:
        mock_get_session.return_value = {"session_id": "test-session"}
        response = main_app_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["version"] == "1.0.0"

def test_api_status_endpoint(main_app_client):
    """Test API status endpoint - covers routes.py"""
    response = main_app_client.get("/v1/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "API routes are active" in data["message"]
    assert "available_endpoints" in data
    assert "/admin/organisation" in data["available_endpoints"]

@pytest.mark.asyncio
async def test_health_endpoint_async(async_main_app_client):
    """Test the health check endpoint asynchronously - covers main.py"""
    from apps.user_service.app.main import health_check

    with patch('apps.user_service.app.main.get_session_by_id_admin') as mock_get_session:
        mock_get_session.return_value = {"session_id": "test-session"}
        # Test the async function directly
        result = await health_check()
        assert result.status == "healthy"
        assert result.version == "1.0.0"

@pytest.mark.asyncio
async def test_api_status_endpoint_async(async_main_app_client):
    """Test API status endpoint asynchronously - covers routes.py"""
    from apps.user_service.app.api.routes import api_status

    # Test the async function directly
    result = await api_status()
    assert result["status"] == "success"
    assert "API routes are active" in result["message"]
    assert "available_endpoints" in result

def test_application_metadata():
    """Test application metadata is set correctly - covers main.py"""
    with patch('ddtrace.patch_all'), \
         patch('dotenv.load_dotenv'), \
         patch('apps.user_service.app.dependencies.logger.setup_logging') as mock_logger:

        mock_logger.return_value = MagicMock()

        from apps.user_service.app.main import app
        assert app.title == "House Of Apps AI"
        assert app.description == "API For House Of Apps AI"
        assert app.version == "1.0.0"

def test_cors_middleware_setup():
    """Test CORS middleware is properly configured - covers main.py"""
    with patch('ddtrace.patch_all'), \
         patch('dotenv.load_dotenv'), \
         patch('apps.user_service.app.dependencies.logger.setup_logging') as mock_logger:

        mock_logger.return_value = MagicMock()

        from apps.user_service.app.main import app
        # Check that CORS middleware is added by checking middleware classes
        middleware_classes = [middleware.cls.__name__ for middleware in app.user_middleware]
        assert 'CORSMiddleware' in middleware_classes

def test_health_response_model():
    """Test HealthResponse model - covers main.py"""
    with patch('ddtrace.patch_all'), \
         patch('dotenv.load_dotenv'), \
         patch('apps.user_service.app.dependencies.logger.setup_logging') as mock_logger:

        mock_logger.return_value = MagicMock()

        from apps.user_service.app.main import HealthResponse

        # Test default values
        health = HealthResponse()
        assert health.status == "healthy"
        assert health.version == "1.0.0"

        # Test custom values
        health_custom = HealthResponse(status="custom", version="2.0.0")
        assert health_custom.status == "custom"
        assert health_custom.version == "2.0.0"

def test_routes_include_all_sub_routers():
    """Test that all sub-routers are properly included - covers routes.py"""
    from apps.user_service.app.api.routes import router

    # Check that router has the expected routes
    route_paths = [route.path for route in router.routes]

    # Should have the status endpoint (with /v1 prefix)
    assert "/v1/status" in route_paths

    # Should have admin prefixes for sub-routers
    admin_routes = [path for path in route_paths if "/admin" in path]
    assert len(admin_routes) > 0

def test_router_prefix():
    """Test router prefix configuration - covers routes.py"""
    from apps.user_service.app.api.routes import router
    assert router.prefix == "/v1"

@pytest.mark.asyncio
async def test_application_startup_async():
    """Test application startup process asynchronously - covers main.py"""
    # Test that the application can be imported and configured
    from apps.user_service.app.main import app

    # Verify app configuration
    assert app.title == "House Of Apps AI"
    assert app.description == "API For House Of Apps AI"
    assert app.version == "1.0.0"

    # Verify app has the expected routes
    assert "/health" in [route.path for route in app.routes]
    # Check that v1 routes are included (they have /v1 prefix)
    v1_routes = [route.path for route in app.routes if route.path.startswith("/v1")]
    assert len(v1_routes) > 0, "No /v1 routes found"

@pytest.mark.asyncio
async def test_middleware_configuration_async():
    """Test middleware configuration asynchronously - covers main.py"""
    with patch('ddtrace.patch_all'), \
         patch('dotenv.load_dotenv'), \
         patch('apps.user_service.app.dependencies.logger.setup_logging') as mock_logger:

        mock_logger.return_value = MagicMock()

        from apps.user_service.app.main import app

        # Test that all expected middleware are configured
        middleware_classes = [middleware.cls.__name__ for middleware in app.user_middleware]

        # Should have CORS, JWT, and other middleware
        expected_middleware = ['CORSMiddleware', 'JWTAuthMiddleware']
        for middleware_name in expected_middleware:
            assert middleware_name in middleware_classes

@pytest.mark.asyncio
async def test_exception_handlers_async():
    """Test exception handlers are properly configured - covers main.py"""
    with patch('ddtrace.patch_all'), \
         patch('dotenv.load_dotenv'), \
         patch('apps.user_service.app.dependencies.logger.setup_logging') as mock_logger:

        mock_logger.return_value = MagicMock()

        from apps.user_service.app.main import app

        # Test that exception handlers are registered
        assert len(app.exception_handlers) > 0

        # Should have handlers for common exceptions
        exception_types = list(app.exception_handlers.keys())
        assert Exception in exception_types

@pytest.mark.asyncio
async def test_rate_limit_exceeded_handler():
    """Test rate limit exceeded handler - covers fastapi_app.py"""
    from slowapi.errors import RateLimitExceeded
    from fastapi.responses import JSONResponse

    # Import the app to get access to the exception handler
    with patch('ddtrace.patch_all'), \
         patch('dotenv.load_dotenv'), \
         patch('apps.user_service.app.dependencies.logger.setup_logging') as mock_logger:

        mock_logger.return_value = MagicMock()

        from apps.user_service.app.main import app

        # Get the rate limit exceeded handler from the app
        handler = app.exception_handlers.get(RateLimitExceeded)
        assert handler is not None, "RateLimitExceeded handler not found"

        # Test the handler function directly
        mock_request = MagicMock()
        # Create a mock RateLimitExceeded exception
        mock_exception = MagicMock(spec=RateLimitExceeded)
        mock_exception.status_code = 429
        mock_exception.detail = "Rate limit exceeded"

        # Call the handler
        response = await handler(mock_request, mock_exception)

        # Verify the response
        assert isinstance(response, JSONResponse)
        assert response.status_code == 429
        assert response.body == b'{"detail":"Rate limit exceeded"}'
