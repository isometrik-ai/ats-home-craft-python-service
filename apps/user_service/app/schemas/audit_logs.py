# pylint: disable=invalid-name,E0213
"""
Audit Logs Schemas Module

This module contains all Pydantic models and schemas related to audit logs.
These schemas are used for request/response validation and API documentation.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict

from apps.user_service.app.schemas import ResponseModel


class AuditLogItem(BaseModel):
    """Model for audit log information in lists

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
        record_id (Optional[str]): ID of the specific record that was affected
        old_values (Optional[Dict]): Previous values before the change
        new_values (Optional[Dict]): New values after the change
        changed_fields (Optional[List[str]]): List of fields that were changed
        compliance_tags (Optional[List[str]]): Compliance tags for the audit log
        risk_level (str): Risk level of the action (low, medium, high)
        ip_address (str): IP address of the user who performed the action
        description (str): Human-readable description of the action
        timestamp (str): ISO timestamp when the audit log was created
        status_code (Optional[int]): HTTP status code of the API call
        category (Optional[str]): Category classification for the audit log
    """

    id: str = Field(..., description="Unique identifier for the audit log")
    organization_id: str = Field(
        ..., description="Organization ID where the audit log was created"
    )
    user_id: str = Field(..., description="User ID who performed the action")
    user_email: str = Field(
        ..., description="Email of the user who performed the action"
    )
    user_role: str = Field(..., description="Role of the user who performed the action")
    action_type: str = Field(
        ..., description="Type of action performed (e.g., CREATE, UPDATE, DELETE, READ)"
    )
    data_classification: str = Field(
        ...,
        description="Classification of the data involved (e.g., general, confidential, pii)",
    )
    table_name: str = Field(..., description="Name of the table that was affected")
    record_id: Optional[str] = Field(
        None, description="ID of the specific record that was affected"
    )
    old_values: Optional[Dict[str, Any]] = Field(
        None, description="Previous values before the change"
    )
    new_values: Optional[Dict[str, Any]] = Field(
        None, description="New values after the change"
    )
    changed_fields: Optional[List[str]] = Field(
        None, description="List of fields that were changed"
    )
    compliance_tags: Optional[List[str]] = Field(
        None, description="Compliance tags for the audit log"
    )
    risk_level: str = Field(
        ..., description="Risk level of the action (low, medium, high)"
    )
    ip_address: str = Field(
        ..., description="IP address of the user who performed the action"
    )
    description: str = Field(
        ..., description="Human-readable description of the action"
    )
    timestamp: str = Field(
        ..., description="ISO timestamp when the audit log was created"
    )
    status_code: Optional[int] = Field(
        None, description="HTTP status code of the API call"
    )
    category: Optional[str] = Field(
        None, description="Category classification for the audit log"
    )

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
                "ip_address": "192.168.1.1",
                "description": "Created new user account",
                "timestamp": "2024-12-10T10:30:00Z",
                "status_code": 201,
                "category": "user_management",
            }
        }
    )


class AuditLogDetailItem(BaseModel):
    """Model for detailed audit log information

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
        record_id (Optional[str]): ID of the specific record that was affected
        old_values (Optional[Dict]): Previous values before the change
        new_values (Optional[Dict]): New values after the change
        changed_fields (Optional[List[str]]): List of fields that were changed
        compliance_tags (Optional[List[str]]): Compliance tags for the audit log
        risk_level (str): Risk level of the action (low, medium, high)
        ip_address (str): IP address of the user who performed the action
        description (str): Human-readable description of the action
        timestamp (str): ISO timestamp when the audit log was created
        hash_signature (Optional[str]): Hash signature for audit integrity
        previous_hash (Optional[str]): Previous hash for chain verification
        retention_date (Optional[str]): Date when this audit log should be retained until
        status_code (Optional[int]): HTTP status code of the API call
        category (Optional[str]): Category classification for the audit log
    """

    id: str = Field(..., description="Unique identifier for the audit log")
    organization_id: str = Field(
        ..., description="Organization ID where the audit log was created"
    )
    user_id: str = Field(..., description="User ID who performed the action")
    user_email: str = Field(
        ..., description="Email of the user who performed the action"
    )
    user_role: str = Field(..., description="Role of the user who performed the action")
    action_type: str = Field(
        ..., description="Type of action performed (e.g., CREATE, UPDATE, DELETE, READ)"
    )
    data_classification: str = Field(
        ...,
        description="Classification of the data involved (e.g., general, confidential, pii)",
    )
    table_name: str = Field(..., description="Name of the table that was affected")
    record_id: Optional[str] = Field(
        None, description="ID of the specific record that was affected"
    )
    old_values: Optional[Dict[str, Any]] = Field(
        None, description="Previous values before the change"
    )
    new_values: Optional[Dict[str, Any]] = Field(
        None, description="New values after the change"
    )
    changed_fields: Optional[List[str]] = Field(
        None, description="List of fields that were changed"
    )
    compliance_tags: Optional[List[str]] = Field(
        None, description="Compliance tags for the audit log"
    )
    risk_level: str = Field(
        ..., description="Risk level of the action (low, medium, high)"
    )
    ip_address: str = Field(
        ..., description="IP address of the user who performed the action"
    )
    description: str = Field(
        ..., description="Human-readable description of the action"
    )
    timestamp: str = Field(
        ..., description="ISO timestamp when the audit log was created"
    )
    hash_signature: Optional[str] = Field(
        None, description="Hash signature for audit integrity"
    )
    previous_hash: Optional[str] = Field(
        None, description="Previous hash for chain verification"
    )
    retention_date: Optional[str] = Field(
        None, description="Date when this audit log should be retained until"
    )
    status_code: Optional[int] = Field(
        None, description="HTTP status code of the API call"
    )
    category: Optional[str] = Field(
        None, description="Category classification for the audit log"
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
                "new_values": {"name": "John Janardhan", "email": "john_doe@example.com"},
                "changed_fields": ["name", "email"],
                "compliance_tags": ["audit_required"],
                "risk_level": "low",
                "ip_address": "192.168.1.1",
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


class AuditLogsResponse(ResponseModel):
    """Response model for audit logs operations

    Attributes:
        message (str): Response message describing the operation result
        audit_logs (List[AuditLogItem]): List of audit logs
        total_count (int): Total number of audit logs available (for pagination)
    """

    audit_logs: List[AuditLogItem] = Field(..., description="List of audit logs")
    total_count: int = Field(
        ..., description="Total number of audit logs available (for pagination)"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Audit logs retrieved successfully (showing 10 of 150 total)",
                "audit_logs": [
                    {
                        "id": "550e8400-e29b-41d4-a716-446655440000",
                        "organization_id": "550e8400-e29b-41d4-a716-446655440001",
                        "user_id": "550e8400-e29b-41d4-a716-446655440002",
                        "user_email": "user_email2@example.com",
                        "user_role": "admin",
                        "action_type": "CREATE",
                        "data_classification": "general",
                        "table_name": "users",
                        "record_id": "550e8400-e29b-41d4-a716-446655440003",
                        "old_values": None,
                        "new_values": {"name": "John Jonnah", "email": "doe@example.com"},
                        "changed_fields": ["name", "email"],
                        "compliance_tags": ["audit_required"],
                        "risk_level": "low",
                        "ip_address": "192.168.1.1",
                        "description": "Deleted new user account",
                        "timestamp": "2024-12-19T101:30:00Z",
                        "status_code": 201,
                        "category": "user_management",
                    }
                ],
                "total_count": 150,
            }
        }
    )


class AuditLogDetailResponse(ResponseModel):
    """Response model for single audit log detail operations

    Attributes:
        message (str): Response message describing the operation result
        audit_log (AuditLogDetailItem): Detailed audit log information
    """

    audit_log: AuditLogDetailItem = Field(
        ..., description="Detailed audit log information"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Audit log details retrieved successfully",
                "audit_log": {
                    "id": "550e8400-e29b-41d4-a716-446655440000",
                    "organization_id": "550e8400-e29b-41d4-a716-446655440001",
                    "user_id": "550e8400-e29b-41d4-a716-446655440002",
                    "user_email": "user_email3@example.com",
                    "user_role": "admin",
                    "action_type": "CREATE",
                    "data_classification": "general",
                    "table_name": "users",
                    "record_id": "550e8400-e29b-41d4-a716-446655440003",
                    "old_values": None,
                    "new_values": {"name": "John Wick", "email": "john_doe_email@example.com"},
                    "changed_fields": ["name", "email"],
                    "compliance_tags": ["audit_required"],
                    "risk_level": "low",
                    "ip_address": "192.168.1.1",
                    "description": "Created new user account",
                    "timestamp": "2024-12-19T10:30:00Z",
                    "hash_signature": "abc123def456...",
                    "previous_hash": "xyz789abc123...",
                    "retention_date": "2027-12-19T10:30:00Z",
                    "status_code": 201,
                    "category": "user_management",
                },
            }
        }
    )


class DeleteAuditLogsResponse(ResponseModel):
    """Response model for delete audit logs operations

    Attributes:
        message (str): Response message describing the operation result
        deleted_count (int): Number of audit logs that were deleted
    """

    deleted_count: int = Field(
        ..., description="Number of audit logs that were deleted"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "All audit logs deleted successfully",
                "deleted_count": 150,
            }
        }
    )
