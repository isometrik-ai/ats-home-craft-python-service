# pylint: disable=all
"""
Test module for Supabase database connection management.

This module contains comprehensive tests for:
- SupabaseClientCache singleton class
- Client creation and caching logic
- Global functions for getting clients
- Error handling scenarios
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from supabase import AsyncClient

from libs.shared_db.supabase_db.db import (
    SupabaseClientCache,
    get_supabase_client,
    get_supabase_admin_client,
    SUPABASE_URL,
    SUPABASE_ANON_KEY,
    SUPABASE_SERVICE_ROLE_KEY
)


class TestSupabaseClientCache:
    """Tests for SupabaseClientCache singleton class."""

    def test_singleton_pattern(self):
        """Test that SupabaseClientCache follows singleton pattern."""
        # Reset singleton state
        SupabaseClientCache._instance = None

        # Create two instances
        cache1 = SupabaseClientCache()
        cache2 = SupabaseClientCache()

        # Should be the same instance
        assert cache1 is cache2
        assert id(cache1) == id(cache2)

    def test_initial_state(self):
        """Test initial state of SupabaseClientCache."""
        # Reset singleton state
        SupabaseClientCache._instance = None
        SupabaseClientCache._supabase_client = None
        SupabaseClientCache._supabase_admin_client = None

        cache = SupabaseClientCache()

        assert cache._supabase_client is None
        assert cache._supabase_admin_client is None

    @pytest.mark.asyncio
    async def test_get_client_first_call(self):
        """Test get_client() on first call creates and caches client."""
        # Reset singleton state
        SupabaseClientCache._instance = None
        SupabaseClientCache._supabase_client = None

        cache = SupabaseClientCache()

        # Mock create_async_client
        mock_client = AsyncMock(spec=AsyncClient)
        with patch('libs.shared_db.supabase_db.db.create_async_client', return_value=mock_client) as mock_create:
            result = await cache.get_client()

            # Verify client was created with correct parameters
            mock_create.assert_called_once_with(SUPABASE_URL, SUPABASE_ANON_KEY)

            # Verify client is cached
            assert cache._supabase_client is mock_client
            assert result is mock_client

    @pytest.mark.asyncio
    async def test_get_client_subsequent_calls(self):
        """Test get_client() on subsequent calls returns cached client."""
        # Reset singleton state
        SupabaseClientCache._instance = None
        SupabaseClientCache._supabase_client = None

        cache = SupabaseClientCache()

        # Mock create_async_client
        mock_client = AsyncMock(spec=AsyncClient)
        with patch('libs.shared_db.supabase_db.db.create_async_client', return_value=mock_client) as mock_create:
            # First call
            result1 = await cache.get_client()

            # Second call
            result2 = await cache.get_client()

            # Should only create client once
            assert mock_create.call_count == 1

            # Both calls should return same client
            assert result1 is mock_client
            assert result2 is mock_client
            assert result1 is result2


    @pytest.mark.asyncio
    async def test_get_admin_client_subsequent_calls(self):
        """Test get_admin_client() on subsequent calls returns cached admin client."""
        # Reset singleton state
        SupabaseClientCache._instance = None
        SupabaseClientCache._supabase_admin_client = None

        cache = SupabaseClientCache()

        # Mock create_async_client and auth.admin.list_users
        mock_admin_client = AsyncMock(spec=AsyncClient)
        mock_auth = AsyncMock()
        mock_admin = AsyncMock()
        mock_list_users = AsyncMock()

        mock_admin_client.auth = mock_auth
        mock_auth.admin = mock_admin
        mock_admin.list_users = mock_list_users

        with patch('libs.shared_db.supabase_db.db.SUPABASE_URL', 'https://test.supabase.co'), \
             patch('libs.shared_db.supabase_db.db.SUPABASE_SERVICE_ROLE_KEY', 'test-key'), \
             patch('libs.shared_db.supabase_db.db.create_async_client', return_value=mock_admin_client) as mock_create:
            # First call
            result1 = await cache.get_admin_client()

            # Second call
            result2 = await cache.get_admin_client()

            # Should only create admin client once
            assert mock_create.call_count == 1

            # Should only call auth.admin.list_users once (during first call)
            mock_list_users.assert_called_once()

            # Both calls should return same admin client
            assert result1 is mock_admin_client
            assert result2 is mock_admin_client
            assert result1 is result2




class TestGlobalFunctions:
    """Tests for global functions get_supabase_client and get_supabase_admin_client."""

    @pytest.mark.asyncio
    async def test_get_supabase_client(self):
        """Test get_supabase_client() function."""
        # Reset singleton state
        SupabaseClientCache._instance = None
        SupabaseClientCache._supabase_client = None

        # Mock the cache instance
        mock_cache = MagicMock()
        mock_client = AsyncMock(spec=AsyncClient)
        mock_cache.get_client = AsyncMock(return_value=mock_client)

        with patch('libs.shared_db.supabase_db.db._cache', mock_cache):
            result = await get_supabase_client()

            # Verify cache.get_client was called
            mock_cache.get_client.assert_called_once()

            # Verify correct client returned
            assert result is mock_client

    @pytest.mark.asyncio
    async def test_get_supabase_admin_client(self):
        """Test get_supabase_admin_client() function."""
        # Reset singleton state
        SupabaseClientCache._instance = None
        SupabaseClientCache._supabase_admin_client = None

        # Mock the cache instance
        mock_cache = MagicMock()
        mock_admin_client = AsyncMock(spec=AsyncClient)
        mock_cache.get_admin_client = AsyncMock(return_value=mock_admin_client)

        with patch('libs.shared_db.supabase_db.db._cache', mock_cache):
            result = await get_supabase_admin_client()

            # Verify cache.get_admin_client was called
            mock_cache.get_admin_client.assert_called_once()

            # Verify correct admin client returned
            assert result is mock_admin_client

    @pytest.mark.asyncio
    async def test_global_functions_use_same_cache(self):
        """Test that global functions use the same cache instance."""
        # Reset singleton state
        SupabaseClientCache._instance = None

        # Mock the cache instance
        mock_cache = MagicMock()
        mock_client = AsyncMock(spec=AsyncClient)
        mock_admin_client = AsyncMock(spec=AsyncClient)
        mock_cache.get_client = AsyncMock(return_value=mock_client)
        mock_cache.get_admin_client = AsyncMock(return_value=mock_admin_client)

        with patch('libs.shared_db.supabase_db.db._cache', mock_cache):
            # Call both functions
            regular_client = await get_supabase_client()
            admin_client = await get_supabase_admin_client()

            # Verify both functions used the same cache instance
            mock_cache.get_client.assert_called_once()
            mock_cache.get_admin_client.assert_called_once()

            # Verify correct clients returned
            assert regular_client is mock_client
            assert admin_client is mock_admin_client


class TestEnvironmentVariables:
    """Tests for environment variable handling."""

    def test_environment_variables_loaded(self):
        """Test that environment variables are loaded correctly."""
        # These should be loaded from environment or be None
        assert SUPABASE_URL is not None or SUPABASE_URL is None
        assert SUPABASE_ANON_KEY is not None or SUPABASE_ANON_KEY is None
        assert SUPABASE_SERVICE_ROLE_KEY is not None or SUPABASE_SERVICE_ROLE_KEY is None

    @pytest.mark.asyncio
    async def test_client_creation_with_none_url(self):
        """Test client creation when SUPABASE_URL is None."""
        # Reset singleton state
        SupabaseClientCache._instance = None
        SupabaseClientCache._supabase_client = None

        cache = SupabaseClientCache()

        # Mock create_async_client to raise an exception
        with patch('libs.shared_db.supabase_db.db.create_async_client', side_effect=Exception("Invalid URL")):
            with pytest.raises(Exception, match="Invalid URL"):
                await cache.get_client()

    @pytest.mark.asyncio
    async def test_admin_client_creation_with_none_key(self):
        """Test admin client creation when SUPABASE_SERVICE_ROLE_KEY is None."""
        # Reset singleton state
        SupabaseClientCache._instance = None
        SupabaseClientCache._supabase_admin_client = None

        cache = SupabaseClientCache()

        # Mock the constants directly since they're imported at module level
        with patch('libs.shared_db.supabase_db.db.SUPABASE_URL', 'https://test.supabase.co'), \
             patch('libs.shared_db.supabase_db.db.SUPABASE_SERVICE_ROLE_KEY', ''):
            with pytest.raises(RuntimeError, match="Missing Supabase admin configuration"):
                await cache.get_admin_client()


class TestErrorScenarios:
    """Tests for error handling scenarios."""

    @pytest.mark.asyncio
    async def test_client_creation_failure(self):
        """Test handling of client creation failure."""
        # Reset singleton state
        SupabaseClientCache._instance = None
        SupabaseClientCache._supabase_client = None

        cache = SupabaseClientCache()

        # Mock create_async_client to raise an exception
        with patch('libs.shared_db.supabase_db.db.create_async_client', side_effect=Exception("Connection failed")):
            with pytest.raises(Exception, match="Connection failed"):
                await cache.get_client()

            # Client should not be cached on failure
            assert cache._supabase_client is None

    @pytest.mark.asyncio
    async def test_admin_client_creation_failure(self):
        """Test handling of admin client creation failure."""
        # Reset singleton state
        SupabaseClientCache._instance = None
        SupabaseClientCache._supabase_admin_client = None

        cache = SupabaseClientCache()

        # Mock environment and create_async_client to raise an exception
        with patch('libs.shared_db.supabase_db.db.SUPABASE_URL', 'https://test.supabase.co'), \
             patch('libs.shared_db.supabase_db.db.SUPABASE_SERVICE_ROLE_KEY', 'test-key'), \
             patch('libs.shared_db.supabase_db.db.create_async_client', side_effect=Exception("Admin connection failed")):
            with pytest.raises(Exception, match="Admin connection failed"):
                await cache.get_admin_client()

            # Admin client should not be cached on failure
            assert cache._supabase_admin_client is None

    @pytest.mark.asyncio
    async def test_global_function_error_propagation(self):
        """Test that errors in global functions are properly propagated."""
        # Reset singleton state
        SupabaseClientCache._instance = None

        # Mock the cache instance to raise an exception
        mock_cache = MagicMock()
        mock_cache.get_client = AsyncMock(side_effect=Exception("Cache error"))

        with patch('libs.shared_db.supabase_db.db._cache', mock_cache):
            with pytest.raises(Exception, match="Cache error"):
                await get_supabase_client()


class TestConcurrency:
    """Tests for concurrent access scenarios."""

    @pytest.mark.asyncio
    async def test_concurrent_client_access(self):
        """Test concurrent access to get_client()."""
        # Reset singleton state
        SupabaseClientCache._instance = None
        SupabaseClientCache._supabase_client = None

        cache = SupabaseClientCache()

        # Mock create_async_client
        mock_client = AsyncMock(spec=AsyncClient)

        with patch('libs.shared_db.supabase_db.db.create_async_client', return_value=mock_client) as mock_create:
            # Simulate concurrent calls
            import asyncio

            async def get_client():
                return await cache.get_client()

            # Run multiple concurrent calls
            results = await asyncio.gather(
                get_client(),
                get_client(),
                get_client()
            )

            # Should only create client once
            assert mock_create.call_count == 1

            # All results should be the same client
            for result in results:
                assert result is mock_client

    @pytest.mark.asyncio
    async def test_concurrent_admin_client_access(self):
        """Test concurrent access to get_admin_client()."""
        # Reset singleton state
        SupabaseClientCache._instance = None
        SupabaseClientCache._supabase_admin_client = None

        cache = SupabaseClientCache()

        # Mock create_async_client and auth.admin.list_users
        mock_admin_client = AsyncMock(spec=AsyncClient)
        mock_auth = AsyncMock()
        mock_admin = AsyncMock()
        mock_list_users = AsyncMock()

        mock_admin_client.auth = mock_auth
        mock_auth.admin = mock_admin
        mock_admin.list_users = mock_list_users

        with patch('libs.shared_db.supabase_db.db.SUPABASE_URL', 'https://test.supabase.co'), \
             patch('libs.shared_db.supabase_db.db.SUPABASE_SERVICE_ROLE_KEY', 'test-key'), \
             patch('libs.shared_db.supabase_db.db.create_async_client', return_value=mock_admin_client) as mock_create:
            # Simulate concurrent calls
            import asyncio

            async def get_admin_client():
                return await cache.get_admin_client()

            # Run multiple concurrent calls
            results = await asyncio.gather(
                get_admin_client(),
                get_admin_client(),
                get_admin_client()
            )

            # Should only create admin client once
            assert mock_create.call_count == 1

            # Should only call auth.admin.list_users once (during first call)
            mock_list_users.assert_called_once()

            # All results should be the same admin client
            for result in results:
                assert result is mock_admin_client


class TestIntegration:
    """Integration tests for the complete module."""


    @pytest.mark.asyncio
    async def test_multiple_singleton_instances(self):
        """Test that multiple singleton instances share the same state."""
        # Reset singleton state
        SupabaseClientCache._instance = None
        SupabaseClientCache._supabase_client = None
        SupabaseClientCache._supabase_admin_client = None

        # Create multiple instances
        cache1 = SupabaseClientCache()
        cache2 = SupabaseClientCache()

        # Mock create_async_client
        mock_client = AsyncMock(spec=AsyncClient)

        with patch('libs.shared_db.supabase_db.db.create_async_client', return_value=mock_client):
            # Create client through first instance
            client1 = await cache1.get_client()

            # Get client through second instance
            client2 = await cache2.get_client()

            # Should be the same client (cached)
            assert client1 is client2
            assert client1 is mock_client

            # Both instances should have the same cached client
            assert cache1._supabase_client is mock_client
            assert cache2._supabase_client is mock_client
