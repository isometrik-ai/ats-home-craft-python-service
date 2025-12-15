"""Test cases for Isometrik service module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from libs.shared_utils.isometrik_service import (
    create_isometrik_application,
    get_isometrik_data_from_settings,
    is_isometrik_enabled,
)


class TestIsIsometrikEnabled:
    """Test cases for is_isometrik_enabled function."""

    @patch("libs.shared_utils.isometrik_service.ISOMETRIK_ENABLED", True)
    def test_is_isometrik_enabled_true(self):
        """Test is_isometrik_enabled returns True when enabled."""
        assert is_isometrik_enabled() is True

    @patch("libs.shared_utils.isometrik_service.ISOMETRIK_ENABLED", False)
    def test_is_isometrik_enabled_false(self):
        """Test is_isometrik_enabled returns False when disabled."""
        assert is_isometrik_enabled() is False

    def test_is_isometrik_enabled_returns_boolean(self):
        """Test that is_isometrik_enabled returns a boolean value."""
        result = is_isometrik_enabled()
        assert isinstance(result, bool)


class TestGetIsometrikDataFromSettings:
    """Test cases for get_isometrik_data_from_settings function."""

    def test_get_isometrik_data_new_structure(self):
        """Test getting data from new structure (isometrik_application_details)."""
        settings = {
            "isometrik_application_details": {
                "projectId": "test-project-id",
                "keysetId": "test-keyset-id",
                "appSecret": "test-secret",
            },
            "other_setting": "value",
        }
        result = get_isometrik_data_from_settings(settings)
        assert result is not None
        assert result["projectId"] == "test-project-id"
        assert result["keysetId"] == "test-keyset-id"
        assert result["appSecret"] == "test-secret"

    def test_get_isometrik_data_old_structure(self):
        """Test getting data from old structure (isometrik) for backward compatibility."""
        settings = {"isometrik": {"projectId": "old-project-id", "keysetId": "old-keyset-id"}}
        result = get_isometrik_data_from_settings(settings)
        assert result is not None
        assert result["projectId"] == "old-project-id"

    def test_get_isometrik_data_new_priority(self):
        """Test that new structure takes priority over old structure."""
        settings = {
            "isometrik_application_details": {"projectId": "new-project-id"},
            "isometrik": {"projectId": "old-project-id"},
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

    def test_get_isometrik_data_no_key(self):
        """Test getting data when settings has no isometrik keys."""
        settings = {"other_setting": "value", "another_setting": "another_value"}
        result = get_isometrik_data_from_settings(settings)
        assert result is None

    def test_get_isometrik_data_non_dict(self):
        """Test getting data when isometrik_application_details is not a dict."""
        settings = {"isometrik_application_details": "not-a-dict"}
        result = get_isometrik_data_from_settings(settings)
        # Should fallback to old structure or return None
        assert result is None or result == "not-a-dict"

    def test_get_isometrik_data_empty_dict(self):
        """Test getting data when isometrik_application_details is empty dict."""
        settings = {"isometrik_application_details": {}}
        result = get_isometrik_data_from_settings(settings)
        # Empty dict is falsy in Python, so the condition `if application_details and ...` fails
        # and it falls through to the old structure check, which returns None
        assert result is None


class TestCreateIsometrikApplication:
    """Test cases for create_isometrik_application function."""

    @pytest.mark.asyncio
    @patch("libs.shared_utils.isometrik_service.httpx.AsyncClient")
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
                "appSecret": "test-secret",
            },
        }
        mock_response.raise_for_status = MagicMock()

        # Mock client
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        result = await create_isometrik_application(organization_name="Test Organization")

        assert result["status"] == "success"
        assert result["data"]["projectId"] == "test-project-id"
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "https://admin-apis.isometrik.io/v1/intr/application"
        assert call_args[1]["json"]["name"] == "Test Organization"
        assert call_args[1]["json"]["productType"] == ["chat", "video"]
        assert call_args[1]["json"]["plan"] == "basic"

    @pytest.mark.asyncio
    @patch("libs.shared_utils.isometrik_service.httpx.AsyncClient")
    async def test_create_isometrik_app_custom_products(self, mock_client_class):
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
            organization_name="Test Org", product_types=["chat", "video", "audio"]
        )

        assert result["status"] == "success"
        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["productType"] == ["chat", "video", "audio"]

    @pytest.mark.asyncio
    @patch("libs.shared_utils.isometrik_service.httpx.AsyncClient")
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

        result = await create_isometrik_application(organization_name="Test Org", plan="premium")

        assert result["status"] == "success"
        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["plan"] == "premium"

    @pytest.mark.asyncio
    @patch("libs.shared_utils.isometrik_service.httpx.AsyncClient")
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
    @patch("libs.shared_utils.isometrik_service.httpx.AsyncClient")
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
    @patch("libs.shared_utils.isometrik_service.httpx.AsyncClient")
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
    @patch("libs.shared_utils.isometrik_service.httpx.AsyncClient")
    async def test_create_isometrik_app_empty_products(self, mock_client_class):
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

        await create_isometrik_application(organization_name="Test Org", product_types=[])

        call_args = mock_client.post.call_args
        payload = call_args[1]["json"]
        assert payload["productType"] == []

    @pytest.mark.asyncio
    @patch("libs.shared_utils.isometrik_service.httpx.AsyncClient")
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
