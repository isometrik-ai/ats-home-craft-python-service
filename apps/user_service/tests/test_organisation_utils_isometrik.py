# pylint: disable=all

import pytest
import uuid
from unittest.mock import AsyncMock, patch

from apps.user_service.app.dependencies.organisation_utils import (
    _save_isometrik_application_data,
    _save_isometrik_error_info,
    _create_isometrik_application_for_org
)


class TestIsometrikHelperFunctions:
    """Test cases for Isometrik helper functions."""

    @pytest.mark.asyncio
    async def test_save_isometrik_application_data_with_existing_settings(self):
        """Test saving Isometrik application data when settings exist."""
        organization_id = str(uuid.uuid4())
        isometrik_data = {
            "projectId": "test-project-id",
            "keysetId": "test-keyset-id",
            "appSecret": "test-secret"
        }
        existing_settings = {"other_setting": "value"}

        with patch("apps.user_service.app.dependencies.organisation_utils.get_organisation_details_by_id", 
                   AsyncMock(return_value={"settings": existing_settings})), \
             patch("apps.user_service.app.dependencies.organisation_utils.update_organisation_settings", 
                   AsyncMock(return_value=True)) as mock_update, \
             patch("apps.user_service.app.dependencies.organisation_utils.logger.info") as mock_logger:

            await _save_isometrik_application_data(organization_id, isometrik_data)

            mock_update.assert_called_once()
            call_args = mock_update.call_args[0]
            assert call_args[0] == organization_id
            assert call_args[1]["isometrik_application_details"] == isometrik_data
            assert call_args[1]["other_setting"] == "value"
            mock_logger.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_isometrik_application_data_without_settings(self):
        """Test saving Isometrik application data when no settings exist."""
        organization_id = str(uuid.uuid4())
        isometrik_data = {"projectId": "test-project-id"}

        with patch("apps.user_service.app.dependencies.organisation_utils.get_organisation_details_by_id", 
                   AsyncMock(return_value={})), \
             patch("apps.user_service.app.dependencies.organisation_utils.update_organisation_settings", 
                   AsyncMock(return_value=True)) as mock_update:

            await _save_isometrik_application_data(organization_id, isometrik_data)

            mock_update.assert_called_once()
            call_args = mock_update.call_args[0]
            assert call_args[1]["isometrik_application_details"] == isometrik_data

    @pytest.mark.asyncio
    async def test_save_isometrik_error_info_creation_failed(self):
        """Test saving Isometrik error info for creation failed."""
        organization_id = str(uuid.uuid4())
        organization_name = "Test Org"
        error_message = "Some error occurred"

        with patch("apps.user_service.app.dependencies.organisation_utils.get_organisation_details_by_id", 
                   AsyncMock(return_value={})), \
             patch("apps.user_service.app.dependencies.organisation_utils.update_organisation_settings", 
                   AsyncMock(return_value=True)) as mock_update, \
             patch("apps.user_service.app.dependencies.organisation_utils.logger.info") as mock_logger:

            await _save_isometrik_error_info(organization_id, organization_name, error_message)

            mock_update.assert_called_once()
            call_args = mock_update.call_args[0]
            error_info = call_args[1]["isometrik"]
            assert error_info["status"] == "error"
            assert error_info["error"] == error_message
            assert error_info["errorType"] == "creation_failed"
            assert error_info["organization_id"] == organization_id
            assert error_info["organization_name"] == organization_name
            mock_logger.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_isometrik_error_info_conflict(self):
        """Test saving Isometrik error info for conflict error."""
        organization_id = str(uuid.uuid4())
        organization_name = "Test Org"
        error_message = "Isometrik API error: 409 - Conflict"

        with patch("apps.user_service.app.dependencies.organisation_utils.get_organisation_details_by_id", 
                   AsyncMock(return_value={})), \
             patch("apps.user_service.app.dependencies.organisation_utils.update_organisation_settings", 
                   AsyncMock(return_value=True)) as mock_update:

            await _save_isometrik_error_info(organization_id, organization_name, error_message)

            call_args = mock_update.call_args[0]
            error_info = call_args[1]["isometrik"]
            assert error_info["errorType"] == "conflict"
            assert "suggestion" in error_info

    @pytest.mark.asyncio
    async def test_save_isometrik_error_info_settings_error(self):
        """Test saving Isometrik error info when settings update fails."""
        organization_id = str(uuid.uuid4())
        organization_name = "Test Org"
        error_message = "Some error"

        with patch("apps.user_service.app.dependencies.organisation_utils.get_organisation_details_by_id", 
                   AsyncMock(side_effect=Exception("Settings error"))), \
             patch("apps.user_service.app.dependencies.organisation_utils.logger.error") as mock_logger:

            await _save_isometrik_error_info(organization_id, organization_name, error_message)

            mock_logger.assert_called_once()
            assert "Failed to save Isometrik error information" in str(mock_logger.call_args)

    @pytest.mark.asyncio
    async def test_create_isometrik_application_for_org_disabled(self):
        """Test creating Isometrik application when disabled."""
        org_data = {"organization_id": str(uuid.uuid4()), "name": "Test Org"}

        with patch("libs.shared_utils.isometrik_service.is_isometrik_enabled", 
                   return_value=False):

            await _create_isometrik_application_for_org(org_data)
            # Should return early without calling create_isometrik_application

    @pytest.mark.asyncio
    async def test_create_isometrik_application_for_org_success(self):
        """Test creating Isometrik application successfully."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "name": "Test Org"
        }
        isometrik_response = {
            "data": {
                "projectId": "test-project-id",
                "keysetId": "test-keyset-id"
            }
        }

        with patch("libs.shared_utils.isometrik_service.is_isometrik_enabled", 
                   return_value=True), \
             patch("libs.shared_utils.isometrik_service.create_isometrik_application", 
                   AsyncMock(return_value=isometrik_response)) as mock_create, \
             patch("apps.user_service.app.dependencies.organisation_utils._save_isometrik_application_data", 
                   AsyncMock()) as mock_save:

            await _create_isometrik_application_for_org(org_data)

            mock_create.assert_called_once_with(
                organization_name="Test Org",
                product_types=["chat", "video"],
                plan="basic"
            )
            mock_save.assert_called_once_with(org_data["organization_id"], isometrik_response["data"])

    @pytest.mark.asyncio
    async def test_create_isometrik_application_for_org_missing_data(self):
        """Test creating Isometrik application when response missing data."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "name": "Test Org"
        }
        isometrik_response = {"status": "success"}  # Missing "data" key

        with patch("libs.shared_utils.isometrik_service.is_isometrik_enabled", 
                   return_value=True), \
             patch("libs.shared_utils.isometrik_service.create_isometrik_application", 
                   AsyncMock(return_value=isometrik_response)), \
             patch("apps.user_service.app.dependencies.organisation_utils.logger.warning") as mock_warning:

            await _create_isometrik_application_for_org(org_data)

            mock_warning.assert_called_once()
            assert "Isometrik response missing data" in str(mock_warning.call_args)

    @pytest.mark.asyncio
    async def test_create_isometrik_application_for_org_error(self):
        """Test creating Isometrik application when error occurs."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "name": "Test Org"
        }
        error = Exception("Isometrik API error")

        with patch("libs.shared_utils.isometrik_service.is_isometrik_enabled", 
                   return_value=True), \
             patch("libs.shared_utils.isometrik_service.create_isometrik_application", 
                   AsyncMock(side_effect=error)), \
             patch("apps.user_service.app.dependencies.organisation_utils.logger.warning") as mock_warning, \
             patch("apps.user_service.app.dependencies.organisation_utils._save_isometrik_error_info", 
                   AsyncMock()) as mock_save_error:

            await _create_isometrik_application_for_org(org_data)

            mock_warning.assert_called_once()
            mock_save_error.assert_called_once_with(
                org_data["organization_id"],
                org_data["name"],
                "Isometrik API error"
            )

    @pytest.mark.asyncio
    async def test_save_isometrik_application_data_with_existing_org_no_settings(self):
        """Test saving Isometrik application data when org exists but has no settings key."""
        organization_id = str(uuid.uuid4())
        isometrik_data = {"projectId": "test-project-id"}

        with patch("apps.user_service.app.dependencies.organisation_utils.get_organisation_details_by_id", 
                   AsyncMock(return_value={"id": organization_id})), \
             patch("apps.user_service.app.dependencies.organisation_utils.update_organisation_settings", 
                   AsyncMock(return_value=True)) as mock_update:

            await _save_isometrik_application_data(organization_id, isometrik_data)

            mock_update.assert_called_once()
            call_args = mock_update.call_args[0]
            assert call_args[1]["isometrik_application_details"] == isometrik_data

    @pytest.mark.asyncio
    async def test_save_isometrik_error_info_with_existing_settings(self):
        """Test saving Isometrik error info when settings already exist."""
        organization_id = str(uuid.uuid4())
        organization_name = "Test Org"
        error_message = "Some error"
        existing_settings = {"other_setting": "value"}

        with patch("apps.user_service.app.dependencies.organisation_utils.get_organisation_details_by_id", 
                   AsyncMock(return_value={"settings": existing_settings})), \
             patch("apps.user_service.app.dependencies.organisation_utils.update_organisation_settings", 
                   AsyncMock(return_value=True)) as mock_update:

            await _save_isometrik_error_info(organization_id, organization_name, error_message)

            call_args = mock_update.call_args[0]
            assert call_args[1]["isometrik"]["error"] == error_message
            assert call_args[1]["other_setting"] == "value"

