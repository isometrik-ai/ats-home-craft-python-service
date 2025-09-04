"""
Common utilities for database modules.
This module provides shared functionality for setting up import paths
and loading environment variables to avoid code duplication.
"""

import os
import sys
from dotenv import load_dotenv


def setup_import_paths_and_env():
    """
    Setup import paths and load environment variables.
    This function centralizes the common setup code used by both
    postgres_db and supabase_db modules.
    """
    # Configure import paths
    base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "."))
    monorepo_root = os.path.abspath(os.path.join(base_path, "../.."))

    # Add necessary paths to sys.path
    sys.path.insert(0, base_path)
    sys.path.insert(0, monorepo_root)

    # Load environment variables from .env
    load_dotenv(os.path.join(monorepo_root, ".env"))
