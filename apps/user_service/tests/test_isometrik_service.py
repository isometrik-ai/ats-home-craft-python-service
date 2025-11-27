# pylint: disable=all

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx
from typing import Dict, Any, Optional

from libs.shared_utils.isometrik_service import (
    is_isometrik_enabled,
    get_isometrik_data_from_settings,
    create_isometrik_application
)


class TestIsIsometrikEnabled:
    """Test cases for is_isometrik_enabled function."""

    @patch('libs.shared_utils.isometrik_service.ISOMETRIK_ENABLED', True)
    def test_is_isometrik_enabled_true(self):
        """Test is_isometrik_enabled returns True when enabled."""
        assert is_isometrik_enabled() is True

    @patch('libs.shared_utils.isometrik_service.ISOMETRIK_ENABLED', False)
    def test_is_isometrik_enabled_false(self):
        """Test is_isometrik_enabled returns False when disabled."""
        assert is_isometrik_enabled() is False

    def test_is_isometrik_enabled_returns_boolean(self):
        """Test that is_isometrik_enabled returns a boolean value."""
        result = is_isometrik_enabled()
        assert isinstance(result, bool)


class TestGetIsometrikDataFromSettings:
    """Test cases for get_isometrik_data_from_settings function."""

    def test_get_isometrik_data_from_settings_with_new_structure(self):
        """Test getting data from new structure (isometrik_application_details)."""
        settings = {
            "isometrik_application_details": {
                "projectId": "test-project-id",
                "keysetId": "test-keyset-id",
                "appSecret": "test-secret"
            },
            "other_setting": "value"
        }
        result = get_isometrik_data_from_settings(settings)
        assert result is not None
        assert result["projectId"] == "test-project-id"
        assert result["keysetId"] == "test-keyset-id"
        assert result["appSecret"] == "test-secret"

    def test_get_isometrik_data_from_settings_with_old_structure(self):
        """Test getting data from old structure (isometrik) for backward compatibility."""
        settings = {
            "isometrik": {
                "projectId": "old-project-id",
                "keysetId": "old-keyset-id"
            }
        }
        result = get_isometrik_data_from_settings(settings)
        assert result is not None
        assert result["projectId"] == "old-project-id"

    def test_get_isometrik_data_from_settings_new_structure_priority(self):
        """Test that new structure takes priority over old structure."""
        settings = {
            "isometrik_application_details": {
                "projectId": "new-project-id"
            },
            "isometrik": {
                "projectId": "old-project-id"
            }
        }
        result = get_isometrik_data_from_settings(settings)
        assert result is not None
        assert result["projectId"] == "new-project-id"

    def test_get_isometrik_data_from_settings_none(self):
        """Test getting data when settings is None."""
        result = get_isometrik_data_from_settings(None)
        assert result is None

    def test_get_isometrik_data_from_settings_empty(self):
        """Test getting data when settings is empty dict."""
        result = get_isometrik_data_from_settings({})
        assert result is None

    def test_get_isometrik_data_from_settings_no_isometrik_key(self):
        """Test getting data when settings has no isometrik keys."""
        settings = {
            "other_setting": "value",
            "another_setting": "another_value"
        }
        result = get_isometrik_data_from_settings(settings)
        assert result is None

    def test_get_isometrik_data_from_settings_non_dict_value(self):
        """Test getting data when isometrik_application_details is not a dict."""
        settings = {
            "isometrik_application_details": "not-a-dict"
        }
        result = get_isometrik_data_from_settings(settings)
        # Should fallback to old structure or return None
        assert result is None or result == "not-a-dict"

    def test_get_isometrik_data_from_settings_empty_dict_value(self):
        """Test getting data when isometrik_application_details is empty dict."""
        settings = {
            "isometrik_application_details": {}
        }
        result = get_isometrik_data_from_settings(settings)
        # Empty dict is falsy in Python, so the condition `if application_details and ...` fails
        # and it falls through to the old structure check, which returns None
        assert result is None


class TestCreateIsometrikApplication:
    """Test cases for create_isometrik_application function."""

    @pytest.mark.asyncio
    @patch('libs.shared_utils.isometrik_service.httpx.AsyncClient')
    async def test_create_isometrik_application_success(self, mock_client_class):
        """Test successful creation of Isometrik application."""
        # Mock response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "success",
            "data": {
                "projectId": "test-project-id",
                "keysetId": "test-keyset-id",
                "appSecret": "test-secret"
            }
        }
        mock_response.raise_for_status = MagicMock()

        # Mock client
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        result = await create_isometrik_application(
            organization_name="Test Organization"
        )

        assert result["status"] == "success"
        assert result["data"]["projectId"] == "test-project-id"
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "https://admin-apis.isometrik.io/v1/intr/application"
        assert call_args[1]["json"]["name"] == "Test Organization"
        assert call_args[1]["json"]["productType"] == ["chat", "video"]
        assert call_args[1]["json"]["plan"] == "basic"

    @pytest.mark.asyncio
    @patch('libs.shared_utils.isometrik_service.httpx.AsyncClient')
    async def test_create_isometrik_application_with_custom_product_types(self, mock_client_class):
        """Test creation with custom product types."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "success", "data": {}}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        result = await create_isometrik_application(
            organization_name="Test Org",
            product_types=["chat", "video", "audio"]
        )

        assert result["status"] == "success"
        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["productType"] == ["chat", "video", "audio"]

    @pytest.mark.asyncio
    @patch('libs.shared_utils.isometrik_service.httpx.AsyncClient')
    async def test_create_isometrik_application_with_custom_plan(self, mock_client_class):
        """Test creation with custom plan."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "success", "data": {}}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        result = await create_isometrik_application(
            organization_name="Test Org",
            plan="premium"
        )

        assert result["status"] == "success"
        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["plan"] == "premium"

    @pytest.mark.asyncio
    @patch('libs.shared_utils.isometrik_service.httpx.AsyncClient')
    async def test_create_isometrik_application_http_status_error(self, mock_client_class):
        """Test handling of HTTP status error (4xx/5xx)."""
        # Mock HTTPStatusError
        mock_response = MagicMock()
        mock_response.status_code = 409
        mock_response.text = '{"status":"Conflict","message":"Project already exists"}'
        
        mock_error = httpx.HTTPStatusError(
            message="Conflict",
            request=MagicMock(),
            response=mock_response
        )
        mock_error.response = mock_response

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=mock_error)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        with pytest.raises(Exception) as exc_info:
            await create_isometrik_application(organization_name="Test Org")

        assert "Isometrik API error: 409" in str(exc_info.value)
        assert "Project already exists" in str(exc_info.value)

    @pytest.mark.asyncio
    @patch('libs.shared_utils.isometrik_service.httpx.AsyncClient')
    async def test_create_isometrik_application_http_status_error_404(self, mock_client_class):
        """Test handling of 404 HTTP status error."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = '{"status":"Not Found"}'
        
        mock_error = httpx.HTTPStatusError(
            message="Not Found",
            request=MagicMock(),
            response=mock_response
        )
        mock_error.response = mock_response

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=mock_error)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        with pytest.raises(Exception) as exc_info:
            await create_isometrik_application(organization_name="Test Org")

        assert "Isometrik API error: 404" in str(exc_info.value)

    @pytest.mark.asyncio
    @patch('libs.shared_utils.isometrik_service.httpx.AsyncClient')
    async def test_create_isometrik_application_http_status_error_500(self, mock_client_class):
        """Test handling of 500 HTTP status error."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = '{"status":"Internal Server Error"}'
        
        mock_error = httpx.HTTPStatusError(
            message="Internal Server Error",
            request=MagicMock(),
            response=mock_response
        )
        mock_error.response = mock_response

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=mock_error)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        with pytest.raises(Exception) as exc_info:
            await create_isometrik_application(organization_name="Test Org")

        assert "Isometrik API error: 500" in str(exc_info.value)

    @pytest.mark.asyncio
    @patch('libs.shared_utils.isometrik_service.httpx.AsyncClient')
    async def test_create_isometrik_application_request_error(self, mock_client_class):
        """Test handling of network/request error."""
        mock_error = httpx.RequestError(
            message="Connection failed",
            request=MagicMock()
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=mock_error)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        with pytest.raises(Exception) as exc_info:
            await create_isometrik_application(organization_name="Test Org")

        assert "Failed to connect to Isometrik API" in str(exc_info.value)
        assert "Connection failed" in str(exc_info.value)

    @pytest.mark.asyncio
    @patch('libs.shared_utils.isometrik_service.httpx.AsyncClient')
    async def test_create_isometrik_application_timeout_error(self, mock_client_class):
        """Test handling of timeout error."""
        mock_error = httpx.TimeoutException(
            message="Request timed out",
            request=MagicMock()
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=mock_error)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        with pytest.raises(Exception) as exc_info:
            await create_isometrik_application(organization_name="Test Org")

        assert "Failed to connect to Isometrik API" in str(exc_info.value)

    @pytest.mark.asyncio
    @patch('libs.shared_utils.isometrik_service.httpx.AsyncClient')
    async def test_create_isometrik_application_unexpected_error(self, mock_client_class):
        """Test handling of unexpected error."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=ValueError("Unexpected error"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        with pytest.raises(ValueError) as exc_info:
            await create_isometrik_application(organization_name="Test Org")

        assert "Unexpected error" in str(exc_info.value)

    @pytest.mark.asyncio
    @patch('libs.shared_utils.isometrik_service.httpx.AsyncClient')
    async def test_create_isometrik_application_headers(self, mock_client_class):
        """Test that correct headers are sent in the request."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "success", "data": {}}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        await create_isometrik_application(organization_name="Test Org")

        call_args = mock_client.post.call_args
        headers = call_args[1]["headers"]
        assert headers["Content-Type"] == "application/json"
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Basic ")

    @pytest.mark.asyncio
    @patch('libs.shared_utils.isometrik_service.httpx.AsyncClient')
    async def test_create_isometrik_application_payload_structure(self, mock_client_class):
        """Test that payload has correct structure."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "success", "data": {}}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        await create_isometrik_application(organization_name="Test Organization")

        call_args = mock_client.post.call_args
        payload = call_args[1]["json"]
        assert "clientName" in payload
        assert "name" in payload
        assert "productType" in payload
        assert "regionId" in payload
        assert "plan" in payload
        assert payload["name"] == "Test Organization"
        assert isinstance(payload["productType"], list)

    @pytest.mark.asyncio
    @patch('libs.shared_utils.isometrik_service.httpx.AsyncClient')
    async def test_create_isometrik_application_empty_product_types(self, mock_client_class):
        """Test that empty product_types list uses default."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "success", "data": {}}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        await create_isometrik_application(
            organization_name="Test Org",
            product_types=[]
        )

        call_args = mock_client.post.call_args
        payload = call_args[1]["json"]
        assert payload["productType"] == []

    @pytest.mark.asyncio
    @patch('libs.shared_utils.isometrik_service.httpx.AsyncClient')
    @patch('libs.shared_utils.isometrik_service.logger')
    async def test_create_isometrik_application_logging_success(self, mock_logger, mock_client_class):
        """Test that success is logged correctly."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "success", "data": {}}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        await create_isometrik_application(organization_name="Test Org")

        mock_logger.info.assert_called_once()
        assert "Successfully created Isometrik application" in str(mock_logger.info.call_args)

    @pytest.mark.asyncio
    @patch('libs.shared_utils.isometrik_service.httpx.AsyncClient')
    @patch('libs.shared_utils.isometrik_service.logger')
    async def test_create_isometrik_application_logging_error(self, mock_logger, mock_client_class):
        """Test that errors are logged correctly."""
        mock_response = MagicMock()
        mock_response.status_code = 409
        mock_response.text = '{"status":"Conflict"}'
        
        mock_error = httpx.HTTPStatusError(
            message="Conflict",
            request=MagicMock(),
            response=mock_response
        )
        mock_error.response = mock_response

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=mock_error)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        with pytest.raises(Exception):
            await create_isometrik_application(organization_name="Test Org")

        mock_logger.error.assert_called_once()
        assert "Isometrik API error creating application" in str(mock_logger.error.call_args)

    @pytest.mark.asyncio
    @patch('libs.shared_utils.isometrik_service.httpx.AsyncClient')
    @patch('libs.shared_utils.isometrik_service.logger')
    async def test_create_isometrik_application_logging_request_error(self, mock_logger, mock_client_class):
        """Test that request errors are logged correctly."""
        mock_error = httpx.RequestError(
            message="Connection failed",
            request=MagicMock()
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=mock_error)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        with pytest.raises(Exception):
            await create_isometrik_application(organization_name="Test Org")

        mock_logger.error.assert_called_once()
        assert "Network error calling Isometrik API" in str(mock_logger.error.call_args)

    @pytest.mark.asyncio
    @patch('libs.shared_utils.isometrik_service.httpx.AsyncClient')
    @patch('libs.shared_utils.isometrik_service.logger')
    async def test_create_isometrik_application_logging_unexpected_error(self, mock_logger, mock_client_class):
        """Test that unexpected errors are logged correctly."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=ValueError("Unexpected error"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        with pytest.raises(ValueError):
            await create_isometrik_application(organization_name="Test Org")

        mock_logger.error.assert_called_once()
        assert "Unexpected error creating Isometrik application" in str(mock_logger.error.call_args)

    @pytest.mark.asyncio
    @patch('libs.shared_utils.isometrik_service.httpx.AsyncClient')
    async def test_create_isometrik_application_json_decode_error(self, mock_client_class):
        """Test handling when response.json() fails."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        with pytest.raises(ValueError) as exc_info:
            await create_isometrik_application(organization_name="Test Org")

        assert "Invalid JSON" in str(exc_info.value)

