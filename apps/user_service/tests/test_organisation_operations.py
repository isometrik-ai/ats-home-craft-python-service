# pylint: disable=all

import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from fastapi import HTTPException

from apps.user_service.app.dependencies.organisation_utils import (
    validate_organisation_status,
    validate_organisation_name_filter,
    build_organisation_filter_message,
)

from libs.shared_utils.organisation_utils import create_organisation_with_super_admin
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
    bulk_delete_organisations,
    bulk_add_members,
    create_default_permissions_for_organisation,
    create_super_admin_role,
    assign_all_permissions_to_role,
    get_organisation_permissions,
    cleanup_organisation_data,
    archive_organisation,
    restore_organisation,
    get_organisation_health_status,
    get_organisation_usage_stats,
    get_organisation_compliance_status
)


class TestOrganisationCRUDOperations:
    """Test cases for organisation CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_new_organisation_success(self):
        """Test successful organisation creation."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "name": "Test Org",
            "slug": "test-org",
            "domain": "test.com",
            "logo_url": "https://example.com/logo.png",
            "plan_type": "premium",
            "status": "active",
            "industry": "Technology",
            "company_size": "50-100",
            "description": "Test organisation",
            "referral_source": "Google",
            "max_users": 100,
            "user_id": str(uuid.uuid4())
        }

        expected_result = {
            "id": org_data["organization_id"],
            "name": org_data["name"],
            "slug": org_data["slug"],
            "domain": org_data["domain"],
            "logo_url": org_data["logo_url"],
            "plan_type": org_data["plan_type"],
            "status": org_data["status"],
            "industry": org_data["industry"],
            "company_size": org_data["company_size"],
            "description": org_data["description"],
            "referral_source": org_data["referral_source"],
            "max_users": org_data["max_users"],
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z",
            "created_by_id": org_data["user_id"]
        }

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = AsyncMock()
            mock_insert_query = AsyncMock()
            mock_result = MagicMock()
            mock_result.data = [expected_result]

            # Set up the mock chain
            mock_insert_query.execute = AsyncMock(return_value=mock_result)
            mock_table.insert = AsyncMock(return_value=mock_insert_query)
            mock_supabase.table = AsyncMock(return_value=mock_table)
            mock_get_client.return_value = mock_supabase

            result = await create_new_organisation(org_data)

            assert result == expected_result
            mock_supabase.table.assert_called_once_with("organizations")

    @pytest.mark.asyncio
    async def test_create_new_organisation_no_data_returned(self):
        """Test organisation creation when no data is returned."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "name": "Test Org",
            "slug": "test-org",
            "user_id": str(uuid.uuid4())
        }

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            mock_supabase = AsyncMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await create_new_organisation(org_data)

            assert result == {}

    @pytest.mark.asyncio
    async def test_get_organisation_details_by_id_success(self):
        """Test successful organisation details retrieval."""
        org_id = str(uuid.uuid4())
        expected_org = {
            "id": org_id,
            "name": "Test Org",
            "slug": "test-org",
            "domain": "test.com",
            "logo_url": "https://example.com/logo.png",
            "plan_type": "premium",
            "status": "active",
            "max_users": 100,
            "timezone": "UTC",
            "settings": {"theme": "dark"},
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z",
            "organization_members": [
                {"status": "active"},
                {"status": "inactive"},
                {"status": "active"}
            ]
        }

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = AsyncMock()
            mock_select_query = AsyncMock()
            mock_eq_query = AsyncMock()
            mock_limit_query = AsyncMock()
            mock_result = MagicMock()
            mock_result.data = [expected_org]

            # Set up the mock chain
            mock_limit_query.execute = AsyncMock(return_value=mock_result)
            mock_eq_query.limit = AsyncMock(return_value=mock_limit_query)
            mock_select_query.eq = AsyncMock(return_value=mock_eq_query)
            mock_table.select = AsyncMock(return_value=mock_select_query)
            mock_supabase.table = AsyncMock(return_value=mock_table)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_details_by_id(org_id)

            assert result is not None
            assert result["id"] == org_id
            assert result["member_count"] == 2  # Only active members
            assert "organization_members" not in result  # Should be removed

    @pytest.mark.asyncio
    async def test_get_organisation_details_by_id_not_found(self):
        """Test organisation details retrieval when organisation not found."""
        org_id = str(uuid.uuid4())

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            mock_supabase = AsyncMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_details_by_id(org_id)

            assert result is None

    @pytest.mark.asyncio
    async def test_get_organisation_by_slug_success(self):
        """Test successful organisation retrieval by slug."""
        slug = "test-org"
        expected_org = {
            "id": str(uuid.uuid4()),
            "name": "Test Org",
            "slug": slug,
            "domain": "test.com"
        }

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = MagicMock()
            mock_query = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [expected_org]

            # Set up the mock chain
            mock_query.eq.return_value = mock_query  # Chain continues
            mock_query.execute = AsyncMock(return_value=mock_result)  # Final execute is async
            mock_table.select.return_value = mock_query  # Returns query builder
            mock_supabase.table = AsyncMock(return_value=mock_table)  # table() is async
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_by_slug(slug)

            assert result == expected_org

    @pytest.mark.asyncio
    async def test_update_organisation_details_success(self):
        """Test successful organisation update."""
        org_id = str(uuid.uuid4())
        update_data = {
            "name": "Updated Org",
            "description": "Updated description",
            "plan_type": "enterprise"
        }

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = MagicMock()
            mock_query = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": org_id, **update_data}]

            # Set up the mock chain
            mock_query.eq.return_value = mock_query  # Chain continues
            mock_query.execute = AsyncMock(return_value=mock_result)  # Final execute is async
            mock_table.update.return_value = mock_query  # Returns query builder
            mock_supabase.table = AsyncMock(return_value=mock_table)  # table() is async
            mock_get_client.return_value = mock_supabase

            result = await update_organisation_details(org_id, update_data)

            assert result == {"id": org_id, **update_data}

    @pytest.mark.asyncio
    async def test_delete_organisation_success(self):
        """Test successful organisation deletion."""
        org_id = str(uuid.uuid4())

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = MagicMock()
            mock_query = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": org_id}]

            # Set up the mock chain
            mock_query.eq.return_value = mock_query  # Chain continues
            mock_query.execute = AsyncMock(return_value=mock_result)  # Final execute is async
            mock_table.delete.return_value = mock_query  # Returns query builder
            mock_supabase.table = AsyncMock(return_value=mock_table)  # table() is async
            mock_get_client.return_value = mock_supabase

            result = await delete_organisation(org_id)

            assert result is True

    @pytest.mark.asyncio
    async def test_check_organisation_exists_true(self):
        """Test organisation existence check when organisation exists."""
        org_id = str(uuid.uuid4())

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = AsyncMock()
            mock_select_query = AsyncMock()
            mock_eq_query = AsyncMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": org_id}]

            # Set up the mock chain
            mock_eq_query.execute = AsyncMock(return_value=mock_result)
            mock_select_query.eq = AsyncMock(return_value=mock_eq_query)
            mock_table.select = AsyncMock(return_value=mock_select_query)
            mock_supabase.table = AsyncMock(return_value=mock_table)
            mock_get_client.return_value = mock_supabase

            result = await check_organisation_exists(org_id)

            assert result is True

    @pytest.mark.asyncio
    async def test_check_organisation_exists_false(self):
        """Test organisation existence check when organisation doesn't exist."""
        org_id = str(uuid.uuid4())

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            mock_supabase = AsyncMock()
            mock_result = MagicMock()
            mock_result.data = []
            mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await check_organisation_exists(org_id)

            assert result is False


class TestOrganisationSearchAndFiltering:
    """Test cases for organisation search and filtering operations."""

    @pytest.mark.asyncio
    async def test_get_list_of_organisations_success(self):
        """Test successful organisations list retrieval."""
        expected_orgs = [
            {
                "id": str(uuid.uuid4()),
                "name": "Org 1",
                "slug": "org-1",
                "status": "active"
            },
            {
                "id": str(uuid.uuid4()),
                "name": "Org 2",
                "slug": "org-2",
                "status": "active"
            }
        ]

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = AsyncMock()
            mock_select_query = AsyncMock()
            mock_order_query = AsyncMock()
            mock_range_query = AsyncMock()
            mock_result = MagicMock()
            mock_result.data = expected_orgs

            # Set up the mock chain
            mock_range_query.execute = AsyncMock(return_value=mock_result)
            mock_order_query.range = AsyncMock(return_value=mock_range_query)
            mock_select_query.order = AsyncMock(return_value=mock_order_query)
            mock_table.select = AsyncMock(return_value=mock_select_query)
            mock_supabase.table = AsyncMock(return_value=mock_table)
            mock_get_client.return_value = mock_supabase

            result = await get_list_of_organisations()

            assert result == expected_orgs

    @pytest.mark.asyncio
    async def test_get_list_of_organisations_with_filters(self):
        """Test organisations list retrieval with search and status filters."""
        search = "test"
        status = "active"

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = AsyncMock()
            mock_select_query = AsyncMock()
            mock_or_query = AsyncMock()
            mock_eq_query = AsyncMock()
            mock_order_query = AsyncMock()
            mock_range_query = AsyncMock()
            mock_result = MagicMock()
            mock_result.data = []

            # Set up the mock chain
            mock_range_query.execute = AsyncMock(return_value=mock_result)
            mock_order_query.range = AsyncMock(return_value=mock_range_query)
            mock_eq_query.order = AsyncMock(return_value=mock_order_query)
            mock_or_query.eq = AsyncMock(return_value=mock_eq_query)
            mock_select_query.or_ = AsyncMock(return_value=mock_or_query)
            mock_table.select = AsyncMock(return_value=mock_select_query)
            mock_supabase.table = AsyncMock(return_value=mock_table)
            mock_get_client.return_value = mock_supabase

            result = await get_list_of_organisations(search=search, status=status)

            assert result == []

    @pytest.mark.asyncio
    async def test_get_organisations_count_success(self):
        """Test successful organisations count retrieval."""
        expected_count = 5

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            mock_supabase = AsyncMock()
            mock_result = MagicMock()
            mock_result.count = expected_count
            mock_supabase.table.return_value.select.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisations_count(None, None)

            assert result == expected_count

    @pytest.mark.asyncio
    async def test_get_organisations_with_members_success(self):
        """Test successful organisations with members retrieval."""
        expected_orgs = [
            {
                "id": str(uuid.uuid4()),
                "name": "Org 1",
                "member_count": 5
            }
        ]

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = AsyncMock()
            mock_select_query = AsyncMock()
            mock_order_query = AsyncMock()
            mock_range_query = AsyncMock()
            mock_result = MagicMock()
            mock_result.data = expected_orgs

            # Set up the mock chain
            mock_range_query.execute = AsyncMock(return_value=mock_result)
            mock_order_query.range = AsyncMock(return_value=mock_range_query)
            mock_select_query.order = AsyncMock(return_value=mock_order_query)
            mock_table.select = AsyncMock(return_value=mock_select_query)
            mock_supabase.table = AsyncMock(return_value=mock_table)
            mock_get_client.return_value = mock_supabase

            result = await get_organisations_with_members()

            assert result == expected_orgs


class TestOrganisationValidation:
    """Test cases for organisation validation operations."""

    @pytest.mark.asyncio
    async def test_check_organisation_slug_unique_true(self):
        """Test slug uniqueness check when slug is unique."""
        slug = "unique-slug"

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = MagicMock()
            mock_query = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []  # No existing org with this slug

            # Set up the mock chain
            mock_query.eq.return_value = mock_query  # Chain continues
            mock_query.execute = AsyncMock(return_value=mock_result)  # Final execute is async
            mock_table.select.return_value = mock_query  # Returns query builder
            mock_supabase.table = AsyncMock(return_value=mock_table)  # table() is async
            mock_get_client.return_value = mock_supabase

            result = await check_organisation_slug_unique(slug)

            assert result is True

    @pytest.mark.asyncio
    async def test_check_organisation_slug_unique_false(self):
        """Test slug uniqueness check when slug already exists."""
        slug = "existing-slug"

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = MagicMock()
            mock_query = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"slug": slug}]  # Existing org with this slug

            # Set up the mock chain
            mock_query.eq.return_value = mock_query  # Chain continues
            mock_query.execute = AsyncMock(return_value=mock_result)  # Final execute is async
            mock_table.select.return_value = mock_query  # Returns query builder
            mock_supabase.table = AsyncMock(return_value=mock_table)  # table() is async
            mock_get_client.return_value = mock_supabase

            result = await check_organisation_slug_unique(slug)

            assert result is False

    @pytest.mark.asyncio
    async def test_check_organisation_name_unique_true(self):
        """Test name uniqueness check when name is unique."""
        name = "Unique Name"

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = MagicMock()
            mock_query = MagicMock()
            mock_result = MagicMock()
            mock_result.data = []  # No existing org with this name

            # Set up the mock chain
            mock_query.eq.return_value = mock_query  # Chain continues
            mock_query.execute = AsyncMock(return_value=mock_result)  # Final execute is async
            mock_table.select.return_value = mock_query  # Returns query builder
            mock_supabase.table = AsyncMock(return_value=mock_table)  # table() is async
            mock_get_client.return_value = mock_supabase

            result = await check_organisation_name_unique(name)

            assert result is True

    def test_validate_organisation_status_valid(self):
        """Test organisation status validation with valid status."""
        valid_statuses = ["active", "suspended", "trial"]  # Only these are in ORG_STATUSES

        for status in valid_statuses:
            # Should not raise any exception
            validate_organisation_status(status)

    def test_validate_organisation_status_invalid(self):
        """Test organisation status validation with invalid status."""
        invalid_status = "invalid_status"

        with pytest.raises(HTTPException) as exc_info:
            validate_organisation_status(invalid_status)
        
        assert exc_info.value.status_code == 422
        assert "Status must be one of:" in exc_info.value.detail

    def test_validate_organisation_name_filter_valid(self):
        """Test organisation name filter validation with valid names."""
        valid_names = [
            "Test Organisation",
            "A",  # Minimum length
            "A" * 255,  # Maximum length
            "  Valid Name  ",  # With spaces
            "Test-Org_123"
        ]

        for name in valid_names:
            result = validate_organisation_name_filter(name)
            assert result == name.strip()

    def test_validate_organisation_name_filter_empty(self):
        """Test organisation name filter validation with empty name."""
        # Test empty string
        with pytest.raises(HTTPException) as exc_info:
            validate_organisation_name_filter("")
        
        assert exc_info.value.status_code == 422
        assert "Name filter cannot be empty" in exc_info.value.detail

        # Test None
        with pytest.raises(HTTPException) as exc_info:
            validate_organisation_name_filter(None)
        
        assert exc_info.value.status_code == 422
        assert "Name filter cannot be empty" in exc_info.value.detail

    def test_validate_organisation_name_filter_whitespace_only(self):
        """Test organisation name filter validation with whitespace-only name."""
        # Test whitespace-only string (gets stripped to empty)
        with pytest.raises(HTTPException) as exc_info:
            validate_organisation_name_filter("   ")
        
        assert exc_info.value.status_code == 422
        assert "Name filter must be between 1 and 255 characters" in exc_info.value.detail

    def test_validate_organisation_name_filter_too_short(self):
        """Test organisation name filter validation with too short name."""
        with pytest.raises(HTTPException) as exc_info:
            validate_organisation_name_filter("")
        
        assert exc_info.value.status_code == 422
        assert "Name filter cannot be empty" in exc_info.value.detail

    def test_validate_organisation_name_filter_too_long(self):
        """Test organisation name filter validation with too long name."""
        long_name = "A" * 256  # 256 characters

        with pytest.raises(HTTPException) as exc_info:
            validate_organisation_name_filter(long_name)
        
        assert exc_info.value.status_code == 422
        assert "Name filter must be between 1 and 255 characters" in exc_info.value.detail

    def test_build_organisation_filter_message_no_filters(self):
        """Test building organisation filter message with no filters."""
        result = build_organisation_filter_message()
        
        assert result == "All organizations retrieved successfully with filters: page_size=20"

    def test_build_organisation_filter_message_with_name(self):
        """Test building organisation filter message with name filter."""
        result = build_organisation_filter_message(name="Test Org")
        
        assert result == "All organizations retrieved successfully with filters: name='Test Org', page_size=20"

    def test_build_organisation_filter_message_with_status(self):
        """Test building organisation filter message with status filter."""
        result = build_organisation_filter_message(org_status="active")
        
        assert result == "All organizations retrieved successfully with filters: status='active', page_size=20"

    def test_build_organisation_filter_message_with_page(self):
        """Test building organisation filter message with page filter."""
        result = build_organisation_filter_message(page=2)
        
        assert result == "All organizations retrieved successfully with filters: page=2, page_size=20"

    def test_build_organisation_filter_message_with_custom_page_size(self):
        """Test building organisation filter message with custom page size."""
        result = build_organisation_filter_message(page_size=50)
        
        assert result == "All organizations retrieved successfully with filters: page_size=50"

    def test_build_organisation_filter_message_with_all_filters(self):
        """Test building organisation filter message with all filters."""
        result = build_organisation_filter_message(
            name="Test Org",
            org_status="active", 
            page=3,
            page_size=25
        )
        
        expected = "All organizations retrieved successfully with filters: name='Test Org', status='active', page=3, page_size=25"
        assert result == expected


class TestOrganisationMemberManagement:
    """Test cases for organisation member management operations."""

    @pytest.mark.asyncio
    async def test_get_organisation_members_success(self):
        """Test successful organisation members retrieval."""
        org_id = str(uuid.uuid4())
        expected_members = [
            {
                "user_id": str(uuid.uuid4()),
                "role": "admin",
                "status": "active"
            },
            {
                "user_id": str(uuid.uuid4()),
                "role": "member",
                "status": "active"
            }
        ]

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = MagicMock()
            mock_query = MagicMock()
            mock_result = MagicMock()
            mock_result.data = expected_members

            # Set up the mock chain
            mock_query.eq.return_value = mock_query  # Chain continues
            mock_query.order.return_value = mock_query  # Chain continues
            mock_query.range.return_value = mock_query  # Chain continues
            mock_query.execute = AsyncMock(return_value=mock_result)  # Final execute is async
            mock_table.select.return_value = mock_query  # Returns query builder
            mock_supabase.table = AsyncMock(return_value=mock_table)  # table() is async
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_members(org_id)

            assert result == expected_members

    @pytest.mark.asyncio
    async def test_get_organisation_members_count_success(self):
        """Test successful organisation members count retrieval."""
        org_id = str(uuid.uuid4())
        expected_count = 10

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = MagicMock()
            mock_query = MagicMock()
            mock_result = MagicMock()
            mock_result.count = expected_count

            # Set up the mock chain
            mock_query.eq.return_value = mock_query  # Chain continues
            mock_query.execute = AsyncMock(return_value=mock_result)  # Final execute is async
            mock_table.select.return_value = mock_query  # Returns query builder
            mock_supabase.table = AsyncMock(return_value=mock_table)  # table() is async
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_members_count(org_id)

            assert result == expected_count

    @pytest.mark.asyncio
    async def test_add_member_to_organisation_success(self):
        """Test successful member addition to organisation."""
        org_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        role_id = str(uuid.uuid4())

        member_data = {
            "user_id": user_id,
            "email": "test@example.com",
            "first_name": "Test",
            "last_name": "User",
            "role_id": role_id
        }

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = MagicMock()
            mock_query = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "member_id"}]

            # Set up the mock chain
            mock_query.execute = AsyncMock(return_value=mock_result)  # Final execute is async
            mock_table.insert.return_value = mock_query  # Returns query builder
            mock_supabase.table = AsyncMock(return_value=mock_table)  # table() is async
            mock_get_client.return_value = mock_supabase

            result = await add_member_to_organisation(org_id, member_data)

            assert result is True

    @pytest.mark.asyncio
    async def test_remove_member_from_organisation_success(self):
        """Test successful member removal from organisation."""
        org_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = MagicMock()
            mock_query = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "member_id"}]

            # Set up the mock chain
            mock_query.eq.return_value = mock_query  # Chain continues
            mock_query.execute = AsyncMock(return_value=mock_result)  # Final execute is async
            mock_table.delete.return_value = mock_query  # Returns query builder
            mock_supabase.table = AsyncMock(return_value=mock_table)  # table() is async
            mock_get_client.return_value = mock_supabase

            result = await remove_member_from_organisation(org_id, user_id)

            assert result is True

    @pytest.mark.asyncio
    async def test_update_member_role_success(self):
        """Test successful member role update."""
        org_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        new_role_id = str(uuid.uuid4())

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = MagicMock()
            mock_query = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "member_id"}]

            # Set up the mock chain
            mock_query.eq.return_value = mock_query  # Chain continues
            mock_query.execute = AsyncMock(return_value=mock_result)  # Final execute is async
            mock_table.update.return_value = mock_query  # Returns query builder
            mock_supabase.table = AsyncMock(return_value=mock_table)  # table() is async
            mock_get_client.return_value = mock_supabase

            result = await update_member_role(org_id, user_id, new_role_id)

            assert result is True


class TestOrganisationSettingsAndPreferences:
    """Test cases for organisation settings and preferences operations."""

    @pytest.mark.asyncio
    async def test_get_organisation_settings_success(self):
        """Test successful organisation settings retrieval."""
        org_id = str(uuid.uuid4())
        expected_settings = {
            "theme": "dark",
            "notifications": True,
            "language": "en"
        }

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = MagicMock()
            mock_query = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"settings": expected_settings}]

            # Set up the mock chain
            mock_query.eq.return_value = mock_query  # Chain continues
            mock_query.execute = AsyncMock(return_value=mock_result)  # Final execute is async
            mock_table.select.return_value = mock_query  # Returns query builder
            mock_supabase.table = AsyncMock(return_value=mock_table)  # table() is async
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_settings(org_id)

            assert result == expected_settings

    @pytest.mark.asyncio
    async def test_update_organisation_settings_success(self):
        """Test successful organisation settings update."""
        org_id = str(uuid.uuid4())
        new_settings = {
            "theme": "light",
            "notifications": False
        }

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = MagicMock()
            mock_query = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": org_id}]

            # Set up the mock chain
            mock_query.eq.return_value = mock_query  # Chain continues
            mock_query.execute = AsyncMock(return_value=mock_result)  # Final execute is async
            mock_table.update.return_value = mock_query  # Returns query builder
            mock_supabase.table = AsyncMock(return_value=mock_table)  # table() is async
            mock_get_client.return_value = mock_supabase

            result = await update_organisation_settings(org_id, new_settings)

            assert result is True

    @pytest.mark.asyncio
    async def test_get_organisation_preferences_success(self):
        """Test successful organisation preferences retrieval."""
        org_id = str(uuid.uuid4())
        expected_preferences = {
            "timezone": "UTC",
            "date_format": "YYYY-MM-DD",
            "currency": "USD"
        }

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = MagicMock()
            mock_query = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"preferences": expected_preferences}]

            # Set up the mock chain
            mock_query.eq.return_value = mock_query  # Chain continues
            mock_query.execute = AsyncMock(return_value=mock_result)  # Final execute is async
            mock_table.select.return_value = mock_query  # Returns query builder
            mock_supabase.table = AsyncMock(return_value=mock_table)  # table() is async
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_preferences(org_id)

            assert result == expected_preferences

    @pytest.mark.asyncio
    async def test_update_organisation_preferences_success(self):
        """Test successful organisation preferences update."""
        org_id = str(uuid.uuid4())
        new_preferences = {
            "timezone": "EST",
            "date_format": "MM/DD/YYYY"
        }

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = MagicMock()
            mock_query = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": org_id}]

            # Set up the mock chain
            mock_query.eq.return_value = mock_query  # Chain continues
            mock_query.execute = AsyncMock(return_value=mock_result)  # Final execute is async
            mock_table.update.return_value = mock_query  # Returns query builder
            mock_supabase.table = AsyncMock(return_value=mock_table)  # table() is async
            mock_get_client.return_value = mock_supabase

            result = await update_organisation_preferences(org_id, new_preferences)

            assert result is True


class TestOrganisationStatistics:
    """Test cases for organisation statistics operations."""

    @pytest.mark.asyncio
    async def test_get_organisation_statistics_success(self):
        """Test successful organisation statistics retrieval."""
        org_id = str(uuid.uuid4())
        expected_stats = {
            "member_count": 50,
            "role_count": 5,
            "permission_count": 20
        }

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_members_table = MagicMock()
            mock_roles_table = MagicMock()
            mock_permissions_table = MagicMock()
            mock_query = MagicMock()
            mock_members_result = MagicMock()
            mock_roles_result = MagicMock()
            mock_permissions_result = MagicMock()

            # Set up counts
            mock_members_result.count = 50
            mock_roles_result.count = 5
            mock_permissions_result.count = 20

            # Set up the mock chain
            mock_query.eq.return_value = mock_query  # Chain continues
            mock_query.execute = AsyncMock()
            mock_query.execute.side_effect = [
                mock_members_result,
                mock_roles_result,
                mock_permissions_result
            ]

            # Set up table mocks
            mock_members_table.select.return_value = mock_query
            mock_roles_table.select.return_value = mock_query
            mock_permissions_table.select.return_value = mock_query

            # Set up supabase mock
            mock_supabase.table = AsyncMock()
            mock_supabase.table.side_effect = [
                mock_members_table,
                mock_roles_table,
                mock_permissions_table
            ]
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_statistics(org_id)

            assert result == expected_stats

    @pytest.mark.asyncio
    async def test_get_organisation_member_stats_success(self):
        """Test successful organisation member statistics retrieval."""
        org_id = str(uuid.uuid4())
        expected_stats = {
            "total_members": 52,
            "active_members": 45,
            "banned_members": 5
        }

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = MagicMock()
            mock_query = MagicMock()
            mock_total_result = MagicMock()
            mock_active_result = MagicMock()
            mock_banned_result = MagicMock()

            # Set up counts
            mock_total_result.count = 52
            mock_active_result.count = 45
            mock_banned_result.count = 5

            # Set up the mock chain
            mock_query.eq.return_value = mock_query  # Chain continues
            mock_query.execute = AsyncMock()
            mock_query.execute.side_effect = [
                mock_total_result,
                mock_active_result,
                mock_banned_result
            ]

            # Set up table mock
            mock_table.select.return_value = mock_query
            mock_supabase.table = AsyncMock(return_value=mock_table)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_member_stats(org_id)

            assert result == expected_stats

    @pytest.mark.asyncio
    async def test_get_organisation_activity_stats_success(self):
        """Test successful organisation activity statistics retrieval."""
        org_id = str(uuid.uuid4())
        expected_stats = {
            "recent_activity_count": 600,
            "period_days": 30
        }

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = MagicMock()
            mock_query = MagicMock()
            mock_result = MagicMock()
            mock_result.count = 600  # Recent activity count

            # Set up the mock chain
            mock_query.eq.return_value = mock_query  # Chain continues
            mock_query.gte.return_value = mock_query  # Chain continues
            mock_query.execute = AsyncMock(return_value=mock_result)  # Final execute is async
            mock_table.select.return_value = mock_query  # Returns query builder
            mock_supabase.table = AsyncMock(return_value=mock_table)  # table() is async
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_activity_stats(org_id)

            assert result == expected_stats


class TestBulkOperations:
    """Test cases for bulk operations."""

    @pytest.mark.asyncio
    async def test_bulk_delete_organisations_success(self):
        """Test successful bulk organisation deletion."""
        org_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        expected_deleted_count = 2

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = MagicMock()
            mock_query = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": org_id} for org_id in org_ids]

            # Set up the mock chain
            mock_query.in_.return_value = mock_query  # Chain continues
            mock_query.execute = AsyncMock(return_value=mock_result)  # Final execute is async
            mock_table.delete.return_value = mock_query  # Returns query builder
            mock_supabase.table = AsyncMock(return_value=mock_table)  # table() is async
            mock_get_client.return_value = mock_supabase

            result = await bulk_delete_organisations(org_ids)

            assert result == expected_deleted_count

    @pytest.mark.asyncio
    async def test_bulk_add_members_success(self):
        """Test successful bulk member addition."""
        org_id = str(uuid.uuid4())
        members_data = [
            {
                "user_id": str(uuid.uuid4()),
                "email": "test1@example.com",
                "full_name": "Test User 1",
                "role_id": str(uuid.uuid4())
            },
            {
                "user_id": str(uuid.uuid4()),
                "email": "test2@example.com",
                "full_name": "Test User 2",
                "role_id": str(uuid.uuid4())
            }
        ]

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = MagicMock()
            mock_query = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": "member_id"} for _ in members_data]

            # Set up the mock chain
            mock_query.execute = AsyncMock(return_value=mock_result)  # Final execute is async
            mock_table.insert.return_value = mock_query  # Returns query builder
            mock_supabase.table = AsyncMock(return_value=mock_table)  # table() is async
            mock_get_client.return_value = mock_supabase

            result = await bulk_add_members(org_id, members_data)

            assert result is True


class TestOrganisationPermissions:
    """Test cases for organisation permissions operations."""

    @pytest.mark.asyncio
    async def test_create_default_permissions_for_organisation_success(self):
        """Test successful default permissions creation for organisation."""
        org_id = str(uuid.uuid4())
        expected_permission_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = AsyncMock()
            mock_upsert_query = AsyncMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": pid} for pid in expected_permission_ids]

            # Set up the mock chain
            mock_upsert_query.execute = AsyncMock(return_value=mock_result)
            mock_table.upsert = AsyncMock(return_value=mock_upsert_query)
            mock_supabase.table = AsyncMock(return_value=mock_table)
            mock_get_client.return_value = mock_supabase

            result = await create_default_permissions_for_organisation(org_id)

            assert result == expected_permission_ids

    @pytest.mark.asyncio
    async def test_create_super_admin_role_success(self):
        """Test successful super admin role creation."""
        org_id = str(uuid.uuid4())
        expected_role = {
            "id": str(uuid.uuid4()),
            "name": "Super Admin",
            "organization_id": org_id
        }

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            mock_supabase = AsyncMock()
            mock_result = MagicMock()
            mock_result.data = [expected_role]
            mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await create_super_admin_role(org_id)

            assert result == expected_role

    @pytest.mark.asyncio
    async def test_assign_all_permissions_to_role_success(self):
        """Test successful assignment of all permissions to role."""
        role_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_permissions_table = AsyncMock()
            mock_role_permissions_table = AsyncMock()
            mock_select_query = AsyncMock()
            mock_eq_query = AsyncMock()
            mock_upsert_query = AsyncMock()
            mock_permissions_result = MagicMock()
            mock_role_permissions_result = MagicMock()

            # Set up permissions result
            mock_permissions_result.data = [{"id": "permission_id"}]

            # Set up role permissions result
            mock_role_permissions_result.data = [{"id": "assignment_id"}]

            # Set up the mock chain for permissions
            mock_eq_query.execute = AsyncMock(return_value=mock_permissions_result)
            mock_select_query.eq = AsyncMock(return_value=mock_eq_query)
            mock_permissions_table.select = AsyncMock(return_value=mock_select_query)

            # Set up the mock chain for role permissions
            mock_upsert_query.execute = AsyncMock(return_value=mock_role_permissions_result)
            mock_role_permissions_table.upsert = AsyncMock(return_value=mock_upsert_query)

            # Set up supabase mock
            mock_supabase.table = AsyncMock()
            mock_supabase.table.side_effect = [mock_permissions_table, mock_role_permissions_table]
            mock_get_client.return_value = mock_supabase

            result = await assign_all_permissions_to_role(role_id, org_id)

            assert result is True

    @pytest.mark.asyncio
    async def test_get_organisation_permissions_success(self):
        """Test successful organisation permissions retrieval."""
        org_id = str(uuid.uuid4())
        expected_permissions = [
            {"id": str(uuid.uuid4()), "name": "Read Users", "code": "users.read"},
            {"id": str(uuid.uuid4()), "name": "Write Users", "code": "users.write"}
        ]

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            mock_supabase = AsyncMock()
            mock_result = MagicMock()
            mock_result.data = expected_permissions
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_permissions(org_id)

            assert result == expected_permissions


class TestOrganisationCleanupAndArchive:
    """Test cases for organisation cleanup and archive operations."""

    @pytest.mark.asyncio
    async def test_cleanup_organisation_data_success(self):
        """Test successful organisation data cleanup."""
        org_id = str(uuid.uuid4())
        expected_cleanup_stats = {
            "members_deleted": 10,
            "roles_deleted": 5,
            "permissions_deleted": 20
        }

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_members_table = MagicMock()
            mock_roles_table = MagicMock()
            mock_permissions_table = MagicMock()
            mock_query = MagicMock()
            mock_members_result = MagicMock()
            mock_roles_result = MagicMock()
            mock_permissions_result = MagicMock()

            # Set up result data
            mock_members_result.data = [{"id": "member_id"} for _ in range(10)]
            mock_roles_result.data = [{"id": "role_id"} for _ in range(5)]
            mock_permissions_result.data = [{"id": "permission_id"} for _ in range(20)]

            # Set up the mock chain
            mock_query.eq.return_value = mock_query  # Chain continues
            mock_query.execute = AsyncMock()
            mock_query.execute.side_effect = [
                mock_members_result,
                mock_roles_result,
                mock_permissions_result
            ]

            # Set up table mocks
            mock_members_table.delete.return_value = mock_query
            mock_roles_table.delete.return_value = mock_query
            mock_permissions_table.delete.return_value = mock_query

            # Set up supabase mock
            mock_supabase.table = AsyncMock()
            mock_supabase.table.side_effect = [
                mock_members_table,
                mock_roles_table,
                mock_permissions_table
            ]
            mock_get_client.return_value = mock_supabase

            result = await cleanup_organisation_data(org_id)

            assert result == expected_cleanup_stats

    @pytest.mark.asyncio
    async def test_archive_organisation_success(self):
        """Test successful organisation archiving."""
        org_id = str(uuid.uuid4())

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = MagicMock()
            mock_query = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": org_id}]

            # Set up the mock chain
            mock_query.eq.return_value = mock_query  # Chain continues
            mock_query.execute = AsyncMock(return_value=mock_result)  # Final execute is async
            mock_table.update.return_value = mock_query  # Returns query builder
            mock_supabase.table = AsyncMock(return_value=mock_table)  # table() is async
            mock_get_client.return_value = mock_supabase

            result = await archive_organisation(org_id)

            assert result is True

    @pytest.mark.asyncio
    async def test_restore_organisation_success(self):
        """Test successful organisation restoration."""
        org_id = str(uuid.uuid4())

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_table = MagicMock()
            mock_query = MagicMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": org_id}]

            # Set up the mock chain
            mock_query.eq.return_value = mock_query  # Chain continues
            mock_query.execute = AsyncMock(return_value=mock_result)  # Final execute is async
            mock_table.update.return_value = mock_query  # Returns query builder
            mock_supabase.table = AsyncMock(return_value=mock_table)  # table() is async
            mock_get_client.return_value = mock_supabase

            result = await restore_organisation(org_id)

            assert result is True


class TestOrganisationUtils:
    """Test cases for organisation utility functions."""

    @pytest.mark.asyncio
    async def test_create_organisation_with_super_admin_success(self):
        """Test successful creation of organisation with super admin."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "name": "Test Org",
            "slug": "test-org",
            "domain": "test.com",
            "user_id": str(uuid.uuid4()),
            "email": "test@example.com",
            "first_name": "Test",
            "last_name": "User",
            "phone": "+1234567890",
            "timezone": "UTC"
        }

        # Mock results for each operation
        mock_org_result = {
            "id": org_data["organization_id"],
            "name": org_data["name"]
        }
        mock_role_result = {
            "id": str(uuid.uuid4()),
            "name": "Super Admin"
        }
        mock_permission_ids = [str(uuid.uuid4()) for _ in range(3)]
        mock_member_result = {
            "id": str(uuid.uuid4()),
            "user_id": org_data["user_id"]
        }

        # Set up patches for all operations
        with patch('libs.shared_utils.organisation_utils.create_new_organisation', return_value=mock_org_result) as mock_create_org, \
             patch('libs.shared_utils.organisation_utils.create_super_admin_role', return_value=mock_role_result) as mock_create_role, \
             patch('libs.shared_utils.organisation_utils.create_default_permissions_for_organisation', return_value=mock_permission_ids) as mock_create_perms, \
             patch('libs.shared_utils.organisation_utils.assign_all_permissions_to_role') as mock_assign_perms, \
             patch('libs.shared_utils.organisation_utils.add_member_to_organisation', return_value=mock_member_result) as mock_add_member:

            await create_organisation_with_super_admin(org_data)

            # Verify each operation was called with correct arguments
            mock_create_org.assert_called_once_with(org_data)
            mock_create_role.assert_called_once_with(org_data["organization_id"])
            mock_create_perms.assert_called_once_with(org_data["organization_id"])
            mock_assign_perms.assert_called_once_with(mock_role_result["id"], org_data["organization_id"])
            mock_add_member.assert_called_once_with(
                org_data["organization_id"],
                {
                    "user_id": org_data["user_id"],
                    "email": org_data["email"],
                    "first_name": org_data["first_name"],
                    "last_name": org_data["last_name"],
                    "phone": org_data["phone"],
                    "timezone": org_data["timezone"],
                    "role_id": mock_role_result["id"],
                    "status": "active"
                }
            )

    @pytest.mark.asyncio
    async def test_create_organisation_with_super_admin_db_error(self):
        """Test handling of database error during organisation creation."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "name": "Test Org",
            "user_id": str(uuid.uuid4()),
            "email": "test@example.com"
        }

        # Mock database error
        db_error = Exception("Database error")

        with patch('libs.shared_utils.organisation_utils.create_new_organisation', side_effect=db_error) as mock_create_org, \
             patch('libs.shared_utils.organisation_utils.delete_auth_user') as mock_delete_auth:

            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            assert exc_info.value.status_code == 500
            assert "Failed to create account" in str(exc_info.value.detail)
            mock_create_org.assert_called_once_with(org_data)
            # Verify cleanup was not attempted since it's commented out in the code
            mock_delete_auth.assert_not_called()


class TestOrganisationHealthAndCompliance:
    """Test cases for organisation health and compliance operations."""

    @pytest.mark.asyncio
    async def test_get_organisation_health_status_success(self):
        """Test successful organisation health status retrieval."""
        org_id = str(uuid.uuid4())
        expected_health = {
            "status": "active",
            "healthy": True,
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z"
        }

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            mock_supabase = AsyncMock()
            mock_result = MagicMock()
            mock_result.data = [expected_health]
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_health_status(org_id)

            assert result == expected_health

    @pytest.mark.asyncio
    async def test_get_organisation_usage_stats_success(self):
        """Test successful organisation usage statistics retrieval."""
        org_id = str(uuid.uuid4())
        expected_usage = {
            "member_count": 50,
            "role_count": 5,
            "usage_percentage": 50.0
        }

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            # Create mock objects
            mock_supabase = AsyncMock()
            mock_members_table = AsyncMock()
            mock_roles_table = AsyncMock()
            mock_members_select = AsyncMock()
            mock_roles_select = AsyncMock()
            mock_members_query = AsyncMock()
            mock_roles_query = AsyncMock()
            mock_members_result = MagicMock()
            mock_roles_result = MagicMock()

            # Set up members result
            mock_members_result.count = 50
            mock_members_query.execute = AsyncMock(return_value=mock_members_result)
            mock_members_select.eq = AsyncMock(return_value=mock_members_query)
            mock_members_table.select = AsyncMock(return_value=mock_members_select)

            # Set up roles result
            mock_roles_result.count = 5
            mock_roles_query.execute = AsyncMock(return_value=mock_roles_result)
            mock_roles_select.eq = AsyncMock(return_value=mock_roles_query)
            mock_roles_table.select = AsyncMock(return_value=mock_roles_select)

            # Set up supabase mock
            mock_supabase.table = AsyncMock()
            mock_supabase.table.side_effect = [mock_members_table, mock_roles_table]
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_usage_stats(org_id)

            assert result == expected_usage

    @pytest.mark.asyncio
    async def test_get_organisation_compliance_status_success(self):
        """Test successful organisation compliance status retrieval."""
        org_id = str(uuid.uuid4())
        expected_compliance = {
            "compliant": True,
            "status": "active",
            "plan_type": "enterprise",
            "created_at": "2025-01-01T00:00:00Z"
        }

        with patch('libs.shared_db.postgres_db.user_service_operations.organisation_operations.get_supabase_admin_client') as mock_get_client:
            mock_supabase = AsyncMock()
            mock_result = MagicMock()
            mock_result.data = [expected_compliance]
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            result = await get_organisation_compliance_status(org_id)

            assert result == expected_compliance
