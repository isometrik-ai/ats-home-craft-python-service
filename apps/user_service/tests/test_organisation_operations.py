# pylint: disable=all

import pytest
import uuid
from unittest.mock import AsyncMock, patch, MagicMock
from postgrest import APIError
from httpx import HTTPError

from libs.shared_db.postgres_db.user_service_operations.organisation_operations import (
    create_new_organisation,
    get_organisation_details_by_id,
    get_organisation_by_slug,
    update_organisation_details,
    delete_organisation,
    check_organisation_exists,
    get_list_of_organisations,
    get_organisations_count,
    get_organisations_with_members,
    check_organisation_slug_unique,
    check_organisation_name_unique,
    get_organisation_members,
    get_organisation_members_count,
    add_member_to_organisation,
    remove_member_from_organisation,
    update_member_role,
    get_organisation_settings,
    update_organisation_settings,
    get_organisation_preferences,
    update_organisation_preferences,
    get_organisation_statistics,
    get_organisation_member_stats,
    get_organisation_activity_stats,
    get_organisation_usage_stats,
    get_organisation_health_status,
    get_organisation_compliance_status,
    bulk_delete_organisations,
    bulk_add_members,
    create_default_permissions_for_organisation,
    create_super_admin_role,
    assign_all_permissions_to_role,
    get_organisation_permissions
)

from libs.shared_db.postgres_db.user_service_operations.exception_handling import (
    SupabaseAPIError,
    NetworkError,
    DataValidationError
)

from libs.shared_utils.common_query import SETTINGS_USERS_MANAGE


class TestOrganisationCRUD:
    """Test cases for organisation CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_new_organisation_success(self):
        """Test successful organisation creation."""
        organisation_data = {
            "organization_id": str(uuid.uuid4()),
            "name": "Test Organisation",
            "slug": "test-org",
            "domain": "test.com",
            "logo_url": "https://example.com/logo.png",
            "plan_type": "starter",
            "status": "trial",
            "industry": "Technology",
            "company_size": "10-50",
            "description": "Test organisation",
            "referral_source": "organic",
            "max_users": 10,
            "user_id": str(uuid.uuid4())
        }

        mock_created_org = {
            "id": organisation_data["organization_id"],
            "name": organisation_data["name"],
            "slug": organisation_data["slug"],
            "domain": organisation_data["domain"],
            "logo_url": organisation_data["logo_url"],
            "plan_type": organisation_data["plan_type"],
            "status": organisation_data["status"],
            "industry": organisation_data["industry"],
            "company_size": organisation_data["company_size"],
            "description": organisation_data["description"],
            "referral_source": organisation_data["referral_source"],
            "max_users": organisation_data["max_users"],
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
            "created_by_id": organisation_data["user_id"]
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [mock_created_org]
            mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await create_new_organisation(organisation_data)

            assert result == mock_created_org
            mock_supabase.table.assert_called_once_with("organizations")

    @pytest.mark.asyncio
    async def test_create_new_organisation_no_data_returned(self):
        """Test organisation creation when no data is returned."""
        organisation_data = {
            "organization_id": str(uuid.uuid4()),
            "name": "Test Organisation",
            "slug": "test-org",
            "user_id": str(uuid.uuid4())
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await create_new_organisation(organisation_data)

            assert result == {}

    @pytest.mark.asyncio
    async def test_get_organisation_details_by_id_success(self):
        """Test successful organisation details retrieval by ID."""
        organisation_id = str(uuid.uuid4())

        mock_org_data = {
            "id": organisation_id,
            "name": "Test Organisation",
            "slug": "test-org",
            "domain": "test.com",
            "logo_url": None,
            "plan_type": "starter",
            "status": "active",
            "max_users": 10,
            "timezone": "UTC",
            "settings": {"theme": "light"},
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
            "organization_members": [
                {"status": "active"},
                {"status": "active"},
                {"status": "inactive"}
            ]
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [mock_org_data]
            mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_details_by_id(organisation_id)

            assert result["id"] == organisation_id
            assert result["member_count"] == 2  # Only active members
            assert "organization_members" not in result  # Should be removed

    @pytest.mark.asyncio
    async def test_get_organisation_details_by_id_not_found(self):
        """Test organisation details retrieval when organisation not found."""
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_details_by_id(organisation_id)

            assert result is None

    @pytest.mark.asyncio
    async def test_get_organisation_by_slug_success(self):
        """Test successful organisation retrieval by slug."""
        slug = "test-org"

        mock_org = {
            "id": str(uuid.uuid4()),
            "name": "Test Organisation",
            "slug": slug,
            "domain": "test.com",
            "logo_url": None,
            "plan_type": "starter",
            "status": "active",
            "account_type": "business",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z"
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [mock_org]
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_by_slug(slug)

            assert result == mock_org

    @pytest.mark.asyncio
    async def test_get_organisation_by_slug_not_found(self):
        """Test organisation retrieval by slug when not found."""
        slug = "non-existent-org"

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_by_slug(slug)

            assert result is None

    @pytest.mark.asyncio
    async def test_update_organisation_details_success(self):
        """Test successful organisation update."""
        organisation_id = str(uuid.uuid4())
        update_data = {
            "name": "Updated Organisation",
            "domain": "updated.com",
            "timezone": "EST",
            "max_users": 25
        }

        mock_updated_org = {
            "id": organisation_id,
            "name": "Updated Organisation",
            "slug": "test-org",
            "domain": "updated.com",
            "logo_url": None,
            "plan_type": "starter",
            "status": "active",
            "max_users": 25,
            "timezone": "EST",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z"
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [mock_updated_org]
            mock_supabase.table.return_value.update.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await update_organisation_details(organisation_id, update_data)

            assert result == mock_updated_org

    @pytest.mark.asyncio
    async def test_update_organisation_details_no_fields_to_update(self):
        """Test organisation update with no fields to update."""
        organisation_id = str(uuid.uuid4())
        update_data = {}

        result = await update_organisation_details(organisation_id, update_data)

        assert result == {}

    @pytest.mark.asyncio
    async def test_update_organisation_details_filters_none_and_empty_strings(self):
        """Test organisation update filters None values and empty strings."""
        organisation_id = str(uuid.uuid4())
        update_data = {
            "name": "Updated Organisation",
            "domain": "",  # Empty string should be filtered out
            "timezone": None,  # None should be filtered out
            "max_users": 25
        }

        mock_updated_org = {
            "id": organisation_id,
            "name": "Updated Organisation",
            "max_users": 25,
            "updated_at": "2024-01-01T00:00:00Z"
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [mock_updated_org]
            mock_supabase.table.return_value.update.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await update_organisation_details(organisation_id, update_data)

            assert result == mock_updated_org

    @pytest.mark.asyncio
    async def test_delete_organisation_success(self):
        """Test successful organisation deletion."""
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "deleted_id"}]
            mock_supabase.table.return_value.delete.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await delete_organisation(organisation_id)

            assert result is True

    @pytest.mark.asyncio
    async def test_delete_organisation_no_data_returned(self):
        """Test organisation deletion when no data is returned."""
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.delete.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await delete_organisation(organisation_id)

            assert result is False

    @pytest.mark.asyncio
    async def test_check_organisation_exists_true(self):
        """Test organisation existence check when organisation exists."""
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": organisation_id}]
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await check_organisation_exists(organisation_id)

            assert result is True

    @pytest.mark.asyncio
    async def test_check_organisation_exists_false(self):
        """Test organisation existence check when organisation doesn't exist."""
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await check_organisation_exists(organisation_id)

            assert result is False


class TestOrganisationListingAndCount:
    """Test cases for organisation listing and count operations."""

    @pytest.mark.asyncio
    async def test_get_list_of_organisations_success(self):
        """Test successful organisation list retrieval."""
        mock_organisations = [
            {
                "id": "org-1",
                "name": "Org 1",
                "slug": "org-1",
                "domain": "example1.com",
                "logo_url": None,
                "plan_type": "free",
                "status": "active",
                "account_type": "business",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "organization_members": [{"id": "1"}, {"id": "2"}, {"id": "3"}]
            },
            {
                "id": "org-2",
                "name": "Org 2",
                "slug": "org-2",
                "domain": "example2.com",
                "logo_url": None,
                "plan_type": "premium",
                "status": "active",
                "account_type": "business",
                "created_at": "2024-01-02T00:00:00Z",
                "updated_at": "2024-01-02T00:00:00Z",
                "organization_members": [{"id": "4"}, {"id": "5"}]
            }
        ]

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = mock_organisations
            mock_supabase.table.return_value.select.return_value.order.return_value.range.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_list_of_organisations()

            assert len(result) == 2
            assert result[0]["member_count"] == 3
            assert result[1]["member_count"] == 2
            assert "organization_members" not in result[0]  # Should be removed
            assert "organization_members" not in result[1]  # Should be removed

    @pytest.mark.asyncio
    async def test_get_list_of_organisations_with_search(self):
        """Test organisation list retrieval with search filter."""
        search = "test"
        mock_organisations = [
            {
                "id": "org-1",
                "name": "Test Organisation",
                "slug": "test-org",
                "domain": "test.com",
                "logo_url": None,
                "plan_type": "free",
                "status": "active",
                "account_type": "business",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "organization_members": [{"id": "1"}]
            }
        ]

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = mock_organisations
            mock_supabase.table.return_value.select.return_value.or_.return_value.order.return_value.range.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_list_of_organisations(search=search)

            assert len(result) == 1
            assert result[0]["name"] == "Test Organisation"

    @pytest.mark.asyncio
    async def test_get_list_of_organisations_with_status_filter(self):
        """Test organisation list retrieval with status filter."""
        status = "active"
        mock_organisations = [
            {
                "id": "org-1",
                "name": "Active Org",
                "slug": "active-org",
                "domain": "active.com",
                "logo_url": None,
                "plan_type": "free",
                "status": "active",
                "account_type": "business",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "organization_members": []
            }
        ]

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = mock_organisations
            mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value.range.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_list_of_organisations(status=status)

            assert len(result) == 1
            assert result[0]["status"] == "active"

    @pytest.mark.asyncio
    async def test_get_list_of_organisations_with_pagination(self):
        """Test organisation list retrieval with pagination."""
        limit = 10
        offset = 20

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.order.return_value.range.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_list_of_organisations(limit=limit, offset=offset)

            assert result == []

    @pytest.mark.asyncio
    async def test_get_organisations_count_success(self):
        """Test successful organisation count retrieval."""
        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.count = 25
            mock_supabase.table.return_value.select.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisations_count(None, None)

            assert result == 25

    @pytest.mark.asyncio
    async def test_get_organisations_count_with_search(self):
        """Test organisation count retrieval with search filter."""
        search = "test"

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.count = 5
            mock_supabase.table.return_value.select.return_value.or_.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisations_count(search, None)

            assert result == 5

    @pytest.mark.asyncio
    async def test_get_organisations_count_with_status_filter(self):
        """Test organisation count retrieval with status filter."""
        status = "active"

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.count = 10
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisations_count(None, status)

            assert result == 10

    @pytest.mark.asyncio
    async def test_get_organisations_count_none(self):
        """Test organisation count retrieval when count is None."""
        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.count = None
            mock_supabase.table.return_value.select.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisations_count(None, None)

            assert result == 0

    @pytest.mark.asyncio
    async def test_get_organisations_with_members_success(self):
        """Test successful organisation retrieval with members."""
        mock_organisations = [
            {
                "id": "org-1",
                "name": "Org 1",
                "slug": "org-1",
                "domain": "example1.com",
                "logo_url": None,
                "plan_type": "free",
                "status": "active",
                "account_type": "business",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "organization_members": [{"id": "1"}, {"id": "2"}]
            }
        ]

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = mock_organisations
            mock_supabase.table.return_value.select.return_value.order.return_value.range.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisations_with_members()

            assert len(result) == 1
            assert result[0]["member_count"] == 2
            assert "organization_members" not in result[0]  # Should be removed


class TestOrganisationValidation:
    """Test cases for organisation validation operations."""

    @pytest.mark.asyncio
    async def test_check_organisation_slug_unique_true(self):
        """Test organisation slug uniqueness check when slug is unique."""
        slug = "unique-slug"

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await check_organisation_slug_unique(slug)

            assert result is True

    @pytest.mark.asyncio
    async def test_check_organisation_slug_unique_false(self):
        """Test organisation slug uniqueness check when slug exists."""
        slug = "existing-slug"

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "existing-org-id"}]
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await check_organisation_slug_unique(slug)

            assert result is False

    @pytest.mark.asyncio
    async def test_check_organisation_slug_unique_with_exclude(self):
        """Test organisation slug uniqueness check with exclude_org_id."""
        slug = "test-slug"
        exclude_org_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.neq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await check_organisation_slug_unique(slug, exclude_org_id)

            assert result is True

    @pytest.mark.asyncio
    async def test_check_organisation_name_unique_true(self):
        """Test organisation name uniqueness check when name is unique."""
        name = "Unique Organisation Name"

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await check_organisation_name_unique(name)

            assert result is True

    @pytest.mark.asyncio
    async def test_check_organisation_name_unique_false(self):
        """Test organisation name uniqueness check when name exists."""
        name = "Existing Organisation Name"

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "existing-org-id"}]
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await check_organisation_name_unique(name)

            assert result is False

    @pytest.mark.asyncio
    async def test_check_organisation_name_unique_with_exclude(self):
        """Test organisation name uniqueness check with exclude_org_id."""
        name = "Test Organisation Name"
        exclude_org_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.neq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await check_organisation_name_unique(name, exclude_org_id)

            assert result is True

class TestOrganisationMembers:
    """Test cases for organisation member operations."""

    @pytest.mark.asyncio
    async def test_get_organisation_members_success(self):
        """Test successful organisation members retrieval."""
        organisation_id = str(uuid.uuid4())

        mock_members = [
            {
                "id": "member-1",
                "user_id": str(uuid.uuid4()),
                "email": "user1@example.com",
                "full_name": "User One",
                "phone": "+1234567890",
                "timezone": "UTC",
                "role_id": str(uuid.uuid4()),
                "status": "active",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "last_active_at": "2024-01-01T00:00:00Z",
                "roles": {
                    "id": str(uuid.uuid4()),
                    "name": "Admin",
                    "description": "Administrator role"
                }
            },
            {
                "id": "member-2",
                "user_id": str(uuid.uuid4()),
                "email": "user2@example.com",
                "full_name": "User Two",
                "phone": "+9876543210",
                "timezone": "EST",
                "role_id": str(uuid.uuid4()),
                "status": "active",
                "created_at": "2024-01-02T00:00:00Z",
                "updated_at": "2024-01-02T00:00:00Z",
                "last_active_at": "2024-01-02T00:00:00Z",
                "roles": {
                    "id": str(uuid.uuid4()),
                    "name": "Member",
                    "description": "Regular member role"
                }
            }
        ]

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = mock_members
            mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value.range.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_members(organisation_id)

            assert len(result) == 2
            assert result == mock_members

    @pytest.mark.asyncio
    async def test_get_organisation_members_with_search(self):
        """Test organisation members retrieval with search filter."""
        organisation_id = str(uuid.uuid4())
        search = "test"

        mock_members = [
            {
                "id": "member-1",
                "user_id": str(uuid.uuid4()),
                "email": "test@example.com",
                "full_name": "Test User",
                "phone": "+1234567890",
                "timezone": "UTC",
                "role_id": str(uuid.uuid4()),
                "status": "active",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "last_active_at": "2024-01-01T00:00:00Z",
                "roles": {
                    "id": str(uuid.uuid4()),
                    "name": "Admin",
                    "description": "Administrator role"
                }
            }
        ]

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = mock_members
            mock_supabase.table.return_value.select.return_value.eq.return_value.or_.return_value.order.return_value.range.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_members(organisation_id, search=search)

            assert len(result) == 1
            assert result[0]["email"] == "test@example.com"

    @pytest.mark.asyncio
    async def test_get_organisation_members_empty(self):
        """Test organisation members retrieval when no members found."""
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value.range.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_members(organisation_id)

            assert result == []

    @pytest.mark.asyncio
    async def test_get_organisation_members_count_success(self):
        """Test successful organisation members count retrieval."""
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.count = 15
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_members_count(organisation_id)

            assert result == 15

    @pytest.mark.asyncio
    async def test_get_organisation_members_count_with_search(self):
        """Test organisation members count retrieval with search filter."""
        organisation_id = str(uuid.uuid4())
        search = "admin"

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.count = 3
            mock_supabase.table.return_value.select.return_value.eq.return_value.or_.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_members_count(organisation_id, search=search)

            assert result == 3

    @pytest.mark.asyncio
    async def test_get_organisation_members_count_none(self):
        """Test organisation members count retrieval when count is None."""
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.count = None
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_members_count(organisation_id)

            assert result == 0

    @pytest.mark.asyncio
    async def test_add_member_to_organisation_success(self):
        """Test successful member addition to organisation."""
        organisation_id = str(uuid.uuid4())
        member_data = {
            "user_id": str(uuid.uuid4()),
            "email": "newmember@example.com",
            "first_name": "New",
            "last_name": "Member",
            "phone": "+1234567890",
            "timezone": "UTC",
            "role_id": str(uuid.uuid4()),
            "status": "active"
        }

        mock_user_data = MagicMock()
        mock_user_data.user.user_metadata = {
            "first_name": "New",
            "last_name": "Member",
            "phone": "+1234567890",
            "timezone": "UTC"
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client, \
             patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_user_by_id", return_value=mock_user_data), \
             patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.update_metadata_of_user", return_value=True):

            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "new-member-id"}]
            mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await add_member_to_organisation(organisation_id, member_data)

            assert result is True

    @pytest.mark.asyncio
    async def test_add_member_to_organisation_no_data_returned(self):
        """Test member addition when no data is returned."""
        organisation_id = str(uuid.uuid4())
        member_data = {
            "user_id": str(uuid.uuid4()),
            "email": "newmember@example.com",
            "first_name": "New",
            "last_name": "Member",
            "phone": "+1234567890",
            "timezone": "UTC",
            "role_id": str(uuid.uuid4()),
            "status": "active"
        }

        mock_user_data = MagicMock()
        mock_user_data.user.user_metadata = {
            "first_name": "New",
            "last_name": "Member",
            "phone": "+1234567890",
            "timezone": "UTC"
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client, \
             patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_user_by_id", return_value=mock_user_data), \
             patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.update_metadata_of_user", return_value=True):

            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await add_member_to_organisation(organisation_id, member_data)

            assert result is False

    @pytest.mark.asyncio
    async def test_remove_member_from_organisation_success(self):
        """Test successful member removal from organisation."""
        organisation_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "removed-member-id"}]
            mock_supabase.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await remove_member_from_organisation(organisation_id, user_id)

            assert result is True

    @pytest.mark.asyncio
    async def test_remove_member_from_organisation_no_data_returned(self):
        """Test member removal when no data is returned."""
        organisation_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await remove_member_from_organisation(organisation_id, user_id)

            assert result is False

    @pytest.mark.asyncio
    async def test_update_member_role_success(self):
        """Test successful member role update."""
        organisation_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        role_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "updated-member-id"}]
            mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await update_member_role(organisation_id, user_id, role_id)

            assert result is True

    @pytest.mark.asyncio
    async def test_update_member_role_no_data_returned(self):
        """Test member role update when no data is returned."""
        organisation_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        role_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await update_member_role(organisation_id, user_id, role_id)

            assert result is False


class TestOrganisationSettings:
    """Test cases for organisation settings and preferences operations."""

    @pytest.mark.asyncio
    async def test_get_organisation_settings_success(self):
        """Test successful organisation settings retrieval."""
        organisation_id = str(uuid.uuid4())
        mock_settings = {
            "theme": "dark",
            "notifications": True,
            "timezone": "UTC",
            "language": "en"
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"settings": mock_settings}]
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_settings(organisation_id)

            assert result == mock_settings

    @pytest.mark.asyncio
    async def test_get_organisation_settings_not_found(self):
        """Test organisation settings retrieval when organisation not found."""
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_settings(organisation_id)

            assert result == {}

    @pytest.mark.asyncio
    async def test_get_organisation_settings_no_settings_field(self):
        """Test organisation settings retrieval when settings field is None."""
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{}]
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_settings(organisation_id)

            assert result == {}

    @pytest.mark.asyncio
    async def test_update_organisation_settings_success(self):
        """Test successful organisation settings update."""
        organisation_id = str(uuid.uuid4())
        settings = {
            "theme": "light",
            "notifications": False,
            "timezone": "EST",
            "language": "es"
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "updated-settings-id"}]
            mock_supabase.table.return_value.update.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await update_organisation_settings(organisation_id, settings)

            assert result is True

    @pytest.mark.asyncio
    async def test_update_organisation_settings_no_data_returned(self):
        """Test organisation settings update when no data is returned."""
        organisation_id = str(uuid.uuid4())
        settings = {"theme": "dark"}

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.update.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await update_organisation_settings(organisation_id, settings)

            assert result is False

    @pytest.mark.asyncio
    async def test_get_organisation_preferences_success(self):
        """Test successful organisation preferences retrieval."""
        organisation_id = str(uuid.uuid4())
        mock_preferences = {
            "email_notifications": True,
            "sms_notifications": False,
            "marketing_emails": True,
            "data_sharing": False
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"preferences": mock_preferences}]
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_preferences(organisation_id)

            assert result == mock_preferences

    @pytest.mark.asyncio
    async def test_get_organisation_preferences_not_found(self):
        """Test organisation preferences retrieval when organisation not found."""
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_preferences(organisation_id)

            assert result == {}

    @pytest.mark.asyncio
    async def test_get_organisation_preferences_no_preferences_field(self):
        """Test organisation preferences retrieval when preferences field is None."""
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{}]
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_preferences(organisation_id)

            assert result == {}

    @pytest.mark.asyncio
    async def test_update_organisation_preferences_success(self):
        """Test successful organisation preferences update."""
        organisation_id = str(uuid.uuid4())
        preferences = {
            "email_notifications": False,
            "sms_notifications": True,
            "marketing_emails": False,
            "data_sharing": True
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "updated-preferences-id"}]
            mock_supabase.table.return_value.update.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await update_organisation_preferences(organisation_id, preferences)

            assert result is True

    @pytest.mark.asyncio
    async def test_update_organisation_preferences_no_data_returned(self):
        """Test organisation preferences update when no data is returned."""
        organisation_id = str(uuid.uuid4())
        preferences = {"email_notifications": True}

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.update.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await update_organisation_preferences(organisation_id, preferences)

            assert result is False


class TestOrganisationStatistics:
    """Test cases for organisation statistics and health operations."""

    @pytest.mark.asyncio
    async def test_get_organisation_statistics_success(self):
        """Test successful organisation statistics retrieval."""
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()

            # Mock multiple table calls for different counts
            mock_members_result = MagicMock()
            mock_members_result.count = 25
            mock_roles_result = MagicMock()
            mock_roles_result.count = 5
            mock_permissions_result = MagicMock()
            mock_permissions_result.count = 15

            # Set up side_effect to return different results for different calls
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
                side_effect=[mock_members_result, mock_roles_result, mock_permissions_result]
            )
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_statistics(organisation_id)

            assert result["member_count"] == 25
            assert result["role_count"] == 5
            assert result["permission_count"] == 15

    @pytest.mark.asyncio
    async def test_get_organisation_member_stats_success(self):
        """Test successful organisation member statistics retrieval."""
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()

            # Mock multiple table calls for different member status counts
            mock_total_result = MagicMock()
            mock_total_result.count = 30
            mock_active_result = MagicMock()
            mock_active_result.count = 25
            mock_banned_result = MagicMock()
            mock_banned_result.count = 2

            # Create separate mock chains for each call
            mock_table = MagicMock()
            mock_select = MagicMock()
            mock_eq1 = MagicMock()
            mock_eq2 = MagicMock()

            # Set up the chain
            mock_supabase.table.return_value = mock_table
            mock_table.select.return_value = mock_select
            mock_select.eq.return_value = mock_eq1
            mock_eq1.eq.return_value = mock_eq2

            # Set up side_effect to return different results for different calls
            # First call: table().select().eq().execute() (total members)
            # Second call: table().select().eq().eq().execute() (active members)
            # Third call: table().select().eq().eq().execute() (banned members)
            mock_eq1.execute = AsyncMock(return_value=mock_total_result)
            mock_eq2.execute = AsyncMock(side_effect=[mock_active_result, mock_banned_result])
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_member_stats(organisation_id)

            assert result["total_members"] == 30
            assert result["active_members"] == 25
            assert result["banned_members"] == 2

    @pytest.mark.asyncio
    async def test_get_organisation_activity_stats_success(self):
        """Test successful organisation activity statistics retrieval."""
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.count = 12
            mock_supabase.table.return_value.select.return_value.eq.return_value.gte.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_activity_stats(organisation_id)

            assert result["recent_activity_count"] == 12
            assert result["period_days"] == 30

    @pytest.mark.asyncio
    async def test_get_organisation_usage_stats_success(self):
        """Test successful organisation usage statistics retrieval."""
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()

            # Mock multiple table calls for member and role counts
            mock_members_result = MagicMock()
            mock_members_result.count = 75
            mock_roles_result = MagicMock()
            mock_roles_result.count = 8

            # Set up side_effect to return different results for different calls
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
                side_effect=[mock_members_result, mock_roles_result]
            )
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_usage_stats(organisation_id)

            assert result["member_count"] == 75
            assert result["role_count"] == 8
            assert result["usage_percentage"] == 75  # 75/100 * 100

    @pytest.mark.asyncio
    async def test_get_organisation_health_status_active(self):
        """Test organisation health status when organisation is active."""
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{
                "status": "active",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z"
            }]
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_health_status(organisation_id)

            assert result["status"] == "active"
            assert result["healthy"] is True
            assert "created_at" in result
            assert "updated_at" in result

    @pytest.mark.asyncio
    async def test_get_organisation_health_status_not_found(self):
        """Test organisation health status when organisation not found."""
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_health_status(organisation_id)

            assert result["status"] == "not_found"
            assert result["healthy"] is False

    @pytest.mark.asyncio
    async def test_get_organisation_compliance_status_compliant(self):
        """Test organisation compliance status when compliant."""
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{
                "status": "active",
                "plan_type": "premium",
                "created_at": "2024-01-01T00:00:00Z"
            }]
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_compliance_status(organisation_id)

            assert result["compliant"] is True
            assert result["status"] == "active"
            assert result["plan_type"] == "premium"

    @pytest.mark.asyncio
    async def test_get_organisation_compliance_status_not_compliant(self):
        """Test organisation compliance status when not compliant."""
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{
                "status": "suspended",
                "plan_type": None,
                "created_at": "2024-01-01T00:00:00Z"
            }]
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_compliance_status(organisation_id)

            assert result["compliant"] is False
            assert result["status"] == "suspended"
            assert result["plan_type"] is None


class TestOrganisationErrorHandling:
    """Test cases for organisation operations error handling."""

    @pytest.mark.asyncio
    async def test_create_organisation_api_error(self):
        """Test organisation creation with API error."""
        organisation_data = {
            "organization_id": str(uuid.uuid4()),
            "name": "Test Organisation",
            "slug": "test-org",
            "user_id": str(uuid.uuid4())
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(side_effect=APIError({"message": "Database error"}))
            mock_get_client.return_value = mock_supabase

            with pytest.raises(SupabaseAPIError):
                await create_new_organisation(organisation_data)

    @pytest.mark.asyncio
    async def test_create_organisation_network_error(self):
        """Test organisation creation with network error."""
        organisation_data = {
            "organization_id": str(uuid.uuid4()),
            "name": "Test Organisation",
            "slug": "test-org",
            "user_id": str(uuid.uuid4())
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(side_effect=HTTPError("Network error"))
            mock_get_client.return_value = mock_supabase

            with pytest.raises(NetworkError):
                await create_new_organisation(organisation_data)

    @pytest.mark.asyncio
    async def test_update_organisation_validation_error(self):
        """Test organisation update with validation error."""
        organisation_id = str(uuid.uuid4())
        update_data = {
            "name": "Updated Organisation",
            "max_users": "invalid"  # Should be int, not string
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.table.return_value.update.return_value.eq.return_value.execute = AsyncMock(side_effect=ValueError("Invalid data type"))
            mock_get_client.return_value = mock_supabase

            with pytest.raises(DataValidationError):
                await update_organisation_details(organisation_id, update_data)


class TestOrganisationPermissionsAndBulk:
    """Test cases for organisation permissions, roles, and bulk operations."""

    @pytest.mark.asyncio
    async def test_bulk_delete_organisations_success(self):
        """Test successful bulk organisation deletion."""
        organisation_ids = [str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())]

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "deleted-1"}, {"id": "deleted-2"}, {"id": "deleted-3"}]
            mock_supabase.table.return_value.delete.return_value.in_.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await bulk_delete_organisations(organisation_ids)

            assert result == 3

    @pytest.mark.asyncio
    async def test_bulk_delete_organisations_no_data_returned(self):
        """Test bulk organisation deletion when no data is returned."""
        organisation_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.delete.return_value.in_.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await bulk_delete_organisations(organisation_ids)

            assert result == 0

    @pytest.mark.asyncio
    async def test_bulk_add_members_success(self):
        """Test successful bulk member addition."""
        organisation_id = str(uuid.uuid4())
        members_data = [
            {
                "user_id": str(uuid.uuid4()),
                "email": "member1@example.com",
                "full_name": "Member One",
                "phone": "+1234567890",
                "timezone": "UTC",
                "role_id": str(uuid.uuid4()),
                "status": "active"
            },
            {
                "user_id": str(uuid.uuid4()),
                "email": "member2@example.com",
                "full_name": "Member Two",
                "phone": "+9876543210",
                "timezone": "EST",
                "role_id": str(uuid.uuid4()),
                "status": "active"
            }
        ]

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "member-1"}, {"id": "member-2"}]
            mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await bulk_add_members(organisation_id, members_data)

            assert result is True

    @pytest.mark.asyncio
    async def test_bulk_add_members_no_data_returned(self):
        """Test bulk member addition when no data is returned."""
        organisation_id = str(uuid.uuid4())
        members_data = [
            {
                "user_id": str(uuid.uuid4()),
                "email": "member1@example.com",
                "full_name": "Member One",
                "phone": "+1234567890",
                "timezone": "UTC",
                "role_id": str(uuid.uuid4()),
                "status": "active"
            }
        ]

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await bulk_add_members(organisation_id, members_data)

            assert result is False

    @pytest.mark.asyncio
    async def test_create_default_permissions_for_organisation_success(self):
        """Test successful default permissions creation."""
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [
                {"id": "perm-1"},
                {"id": "perm-2"},
                {"id": "perm-3"}
            ]
            mock_supabase.table.return_value.upsert.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await create_default_permissions_for_organisation(organisation_id)

            assert len(result) == 3
            assert result == ["perm-1", "perm-2", "perm-3"]

    @pytest.mark.asyncio
    async def test_create_default_permissions_for_organisation_no_data_returned(self):
        """Test default permissions creation when no data is returned."""
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.upsert.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await create_default_permissions_for_organisation(organisation_id)

            assert result == []

    @pytest.mark.asyncio
    async def test_create_super_admin_role_success(self):
        """Test successful super admin role creation."""
        organisation_id = str(uuid.uuid4())

        mock_role = {
            "id": str(uuid.uuid4()),
            "name": "Super Admin",
            "description": "Full administrative access to all system features",
            "organization_id": organisation_id,
            "is_default": True,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z"
        }

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [mock_role]
            mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await create_super_admin_role(organisation_id)

            assert result == mock_role

    @pytest.mark.asyncio
    async def test_create_super_admin_role_no_data_returned(self):
        """Test super admin role creation when no data is returned."""
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await create_super_admin_role(organisation_id)

            assert result == {}

    @pytest.mark.asyncio
    async def test_assign_all_permissions_to_role_success(self):
        """Test successful assignment of all permissions to role."""
        role_id = str(uuid.uuid4())
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()

            # Mock permissions query
            mock_permissions_result = MagicMock()
            mock_permissions_result.data = [
                {"id": "perm-1"},
                {"id": "perm-2"},
                {"id": "perm-3"}
            ]

            # Mock role-permissions upsert
            mock_upsert_result = MagicMock()
            mock_upsert_result.data = [
                {"id": "rp-1"},
                {"id": "rp-2"},
                {"id": "rp-3"}
            ]

            # Set up side_effect to return different results for different calls
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_permissions_result)
            mock_supabase.table.return_value.upsert.return_value.execute = AsyncMock(return_value=mock_upsert_result)
            mock_get_client.return_value = mock_supabase

            result = await assign_all_permissions_to_role(role_id, organisation_id)

            assert result is True

    @pytest.mark.asyncio
    async def test_assign_all_permissions_to_role_no_permissions(self):
        """Test permission assignment when no permissions exist."""
        role_id = str(uuid.uuid4())
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_permissions_result = MagicMock()
            mock_permissions_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_permissions_result)
            mock_get_client.return_value = mock_supabase

            result = await assign_all_permissions_to_role(role_id, organisation_id)

            assert result is False

    @pytest.mark.asyncio
    async def test_get_organisation_permissions_success(self):
        """Test successful organisation permissions retrieval."""
        organisation_id = str(uuid.uuid4())

        mock_permissions = [
            {
                "id": "perm-1",
                "name": "View Dashboard",
                "code": "business.dashboard.view",
                "category": "business",
                "description": "Access to main dashboard",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z"
            },
            {
                "id": "perm-2",
                "name": "Manage Users",
                "code": SETTINGS_USERS_MANAGE,
                "category": "settings",
                "description": "Full user management",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z"
            }
        ]

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = mock_permissions
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_permissions(organisation_id)

            assert len(result) == 2
            assert result == mock_permissions

    @pytest.mark.asyncio
    async def test_get_organisation_permissions_empty(self):
        """Test organisation permissions retrieval when no permissions found."""
        organisation_id = str(uuid.uuid4())

        with patch("libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_permissions(organisation_id)

            assert result == []