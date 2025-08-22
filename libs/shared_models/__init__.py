"""
Shared models and constants for the application.
"""

# Allowed user statuses for authentication
ALLOWED_USER_STATUSES = ["active", "invited"]

def is_allowed_user_status(status: str) -> bool:
    """
    Check if a user status is allowed for authentication.
    
    Args:
        status (str): User status to check
        
    Returns:
        bool: True if status is allowed for authentication, False otherwise
    """
    return status in ALLOWED_USER_STATUSES
