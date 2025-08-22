"""
Module for managing PostgreSQL database connections and interactions.
This module provides a centralized interface for managing
PostgreSQL database connections, loading environment variables,
and creating a Supabase client for database operations.
"""

# pylint: disable=import-error
import os
import sys
from typing import Optional

# Third-party imports
# Local application imports
from dotenv import load_dotenv
from supabase import create_client, Client

# Configure import paths first
base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
monorepo_root = os.path.abspath(os.path.join(base_path, "../.."))
# Add necessary paths to sys.path
sys.path.insert(0, base_path)
sys.path.insert(0, monorepo_root)

# Load environment variables from .env
load_dotenv(os.path.join(monorepo_root, ".env"))

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

# Cache for Supabase client to avoid creating new instances
# pylint: disable=invalid-name
_supabase_client: Optional[Client] = None
_supabase_admin_client: Optional[Client] = None


def get_supabase_client() -> Client:
    """
    Get or create a cached Supabase client instance.
    Uses caching to improve performance by reusing the same client.
    """
    # pylint: disable=global-statement
    global _supabase_client
    if _supabase_client is None:
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
        print("Supabase client created and cached")
    return _supabase_client


def get_supabase_admin_client() -> Client:
    """
    Get or create a cached Supabase admin client instance.
    Uses caching to improve performance by reusing the same client.
    """
    # pylint: disable=global-statement
    global _supabase_admin_client
    if _supabase_admin_client is None:
        _supabase_admin_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        print("Supabase admin client created and cached")
    return _supabase_admin_client
