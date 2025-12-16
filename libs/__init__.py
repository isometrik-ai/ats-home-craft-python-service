"""Module for defining constants used throughout the application.
This module contains all constants used throughout the application.
All constants should be centralized here.
"""

# Define now constant
from datetime import datetime, timezone

NOW_CONSTANT = "now()"
DATETIME_NOW_CONSTANT = datetime.now(timezone.utc)
