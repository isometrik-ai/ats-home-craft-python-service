"""Audit Logs Schemas Module

This module contains all Pydantic models and schemas related to audit logs.
These schemas are used for request/response validation and API documentation.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AuditLogFilter(BaseModel):
    """Filter parameters for audit log queries.

    Attributes:
        organization_id: Organization ID (required)
        search: Search term to filter by description, action_type, or table_name
        action_type: Filter by action type
        table_name: Filter by table name
        user_id: Filter by user ID
        start_date: Filter by start date
        end_date: Filter by end date
        limit: Maximum number of results to return
        offset: Number of results to skip for pagination
    """

    organization_id: str = Field(..., description="Organization ID")
    search: str | None = Field(None, description="Search term")
    action_type: str | None = Field(None, description="Action type filter")
    table_name: str | None = Field(None, description="Table name filter")
    user_id: str | None = Field(None, description="User ID filter")
    start_date: datetime | None = Field(None, description="Start date filter")
    end_date: datetime | None = Field(None, description="End date filter")
    limit: int = Field(20, ge=1, description="Maximum number of results")
    offset: int = Field(0, ge=0, description="Number of results to skip")


class AuditLogBase(BaseModel):
    """Base model for audit log information containing common fields

    Attributes:
        id (str): Unique identifier for the audit log
        organization_id (str): Organization ID where the audit log was created
        user_id (str): User ID who performed the action
        user_email (str): Email of the user who performed the action
        user_role (str): Role of the user who performed the action
        action_type (str): Type of action performed (e.g., CREATE, UPDATE, DELETE, READ)
        data_classification (str): Classification of the data involved (e.g., general,
            confidential, pii)
        table_name (str): Name of the table that was affected
        record_id (str | None): ID of the specific record that was affected
        old_values (dict[str, Any] | None): Previous values before the change
        new_values (dict[str, Any] | None): New values after the change
        changed_fields (list[str] | None): List of fields that were changed
        compliance_tags (list[str] | None): Compliance tags for the audit log
        risk_level (str): Risk level of the action (low, medium, high)
        ip_address (str): IP address of the user who performed the action
        description (str): Human-readable description of the action
        timestamp (str): ISO timestamp when the audit log was created
        status_code (int | None): HTTP status code of the API call
        category (str | None): Category classification for the audit log
    """

    id: str = Field(..., description="Unique identifier for the audit log")
    organization_id: str = Field(..., description="Organization ID where the audit log was created")
    user_id: str = Field(..., description="User ID who performed the action")
    user_email: str = Field(..., description="Email of the user who performed the action")
    user_role: str = Field(..., description="Role of the user who performed the action")
    action_type: str = Field(
        ..., description="Type of action performed (e.g., CREATE, UPDATE, DELETE, READ)"
    )
    data_classification: str = Field(
        ...,
        description="Classification of the data involved (e.g., general, confidential, pii)",
    )
    table_name: str = Field(..., description="Name of the table that was affected")
    record_id: str | None = Field(None, description="ID of the specific record that was affected")
    old_values: dict[str, Any] | None = Field(None, description="Previous values before the change")
    new_values: dict[str, Any] | None = Field(None, description="New values after the change")
    changed_fields: list[str] | None = Field(None, description="List of fields that were changed")
    compliance_tags: list[str] | None = Field(None, description="Compliance tags for the audit log")
    risk_level: str = Field(..., description="Risk level of the action (low, medium, high)")
    ip_address: str = Field(..., description="IP address of the user who performed the action")
    description: str = Field(..., description="Human-readable description of the action")
    timestamp: str = Field(..., description="ISO timestamp when the audit log was created")
    status_code: int | None = Field(None, description="HTTP status code of the API call")
    category: str | None = Field(None, description="Category classification for the audit log")


class AuditLogItem(AuditLogBase):
    """Model for audit log information in lists

    Inherits all fields from AuditLogBase.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "organization_id": "550e8400-e29b-41d4-a716-446655440001",
                "user_id": "550e8400-e29b-41d4-a716-446655440002",
                "user_email": "user@example.com",
                "user_role": "admin",
                "action_type": "CREATE",
                "data_classification": "general",
                "table_name": "users",
                "record_id": "550e8400-e29b-41d4-a716-446655440003",
                "old_values": None,
                "new_values": {"name": "John Doe", "email": "john@example.com"},
                "changed_fields": ["name", "email"],
                "compliance_tags": ["audit_required"],
                "risk_level": "low",
                "ip_address": "192.0.2.1",
                "description": "Created new user account",
                "timestamp": "2024-12-10T10:30:00Z",
                "status_code": 201,
                "category": "user_management",
            }
        }
    )


class AuditLogDetailItem(AuditLogBase):
    """Model for detailed audit log information

    Inherits all fields from AuditLogBase and adds additional fields for detailed audit logs.

    Additional Attributes:
        hash_signature (Optional[str]): Hash signature for audit integrity
        previous_hash (Optional[str]): Previous hash for chain verification
        retention_date (Optional[str]): Date when this audit log should be retained until
    """

    hash_signature: str | None = Field(None, description="Hash signature for audit integrity")
    previous_hash: str | None = Field(None, description="Previous hash for chain verification")
    retention_date: str | None = Field(
        None, description="Date when this audit log should be retained until"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "organization_id": "550e8400-e29b-41d4-a716-446655440001",
                "user_id": "550e8400-e29b-41d4-a716-446655440002",
                "user_email": "user_email@example.com",
                "user_role": "admin",
                "action_type": "CREATE",
                "data_classification": "general",
                "table_name": "users",
                "record_id": "550e8400-e29b-41d4-a716-446655440003",
                "old_values": None,
                "new_values": {
                    "name": "John Janardhan",
                    "email": "john_doe@example.com",
                },
                "changed_fields": ["name", "email"],
                "compliance_tags": ["audit_required"],
                "risk_level": "low",
                "ip_address": "192.0.2.1",
                "description": "Updated new user account",
                "timestamp": "2024-12-10T10:30:00Z",
                "hash_signature": "abc123def456...",
                "previous_hash": "xyz789abc123...",
                "retention_date": "2027-12-19T10:30:00Z",
                "status_code": 201,
                "category": "user_management",
            }
        }
    )
