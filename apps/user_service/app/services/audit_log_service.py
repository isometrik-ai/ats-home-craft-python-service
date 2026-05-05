"""Service for audit log business logic

This service handles all business logic related to audit logs, including
validation, formatting, and orchestration of audit log operations.
"""

import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.audit_log_repository import (
    AuditLogRepository,
)
from apps.user_service.app.schemas.audit_logs import (
    AuditLogDetailItem,
    AuditLogFilter,
    AuditLogItem,
)
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
)
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("audit_log_service")


def _make_json_serializable(obj: Any) -> Any:
    """Recursively convert UUID, date/datetime, and Decimal to JSON-serializable types.

    Args:
        obj: Object to convert (dict, list, UUID, date, datetime, Decimal, or primitive)

    Returns:
        JSON-serializable version of the object
    """
    if obj is None:
        return None
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _make_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_make_json_serializable(item) for item in obj]
    return obj


def _prepare_jsonb_value(obj: Any) -> str | None:
    """Prepare a value for JSONB column storage.

    Converts UUID/date/datetime/Decimal to JSON-serializable types, then serializes to JSON string.
    asyncpg's JSONB codec expects a JSON string, not a dict.

    Args:
        obj: Object to prepare (dict, list, UUID, date, datetime, Decimal, or primitive)

    Returns:
        JSON string or None
    """
    if obj is None:
        return None
    serializable = _make_json_serializable(obj)
    return json.dumps(serializable)


class AuditLogService:
    """Service for audit log business logic.

    Handles all business logic related to audit logs, including validation,
    formatting, and orchestration of audit log operations.
    """

    def __init__(
        self,
        user_context: UserContext,
        db_connection: asyncpg.Connection,
    ) -> None:
        """Initialize AuditLogService with user context and database connection.

        Args:
            user_context: Authenticated user context
            db_connection: database connection for postgresql
        """
        self.user_context = user_context
        # Initialize repository with database connection
        self.audit_log_repository = AuditLogRepository(db_connection=db_connection)

    @staticmethod
    def prepare_audit_log_for_db(audit_data: dict[str, Any]) -> dict[str, Any]:
        """Prepare audit log data for database insertion.

        Converts business objects to database-compatible format, including
        JSONB serialization for old_values and new_values fields.

        Args:
            audit_data: Raw audit log data dictionary

        Returns:
            Prepared audit log data ready for database insertion
        """
        return {
            "organization_id": audit_data["organization_id"],
            "user_id": audit_data["user_id"],
            "user_email": audit_data["user_email"],
            "user_role": audit_data["user_role"],
            "action_type": audit_data["action_type"],
            "data_classification": audit_data["data_classification"],
            "table_name": audit_data["table_name"],
            "record_id": audit_data.get("record_id"),
            "old_values": _prepare_jsonb_value(audit_data.get("old_values", None)),
            "new_values": _prepare_jsonb_value(audit_data.get("new_values", None)),
            "changed_fields": audit_data.get("changed_fields"),
            "compliance_tags": audit_data.get("compliance_tags"),
            "risk_level": audit_data["risk_level"],
            "ip_address": audit_data["ip_address"],
            "timestamp": audit_data["timestamp"],
            "hash_signature": audit_data["hash_signature"],
            "previous_hash": audit_data.get("previous_hash"),
            "description": audit_data["description"],
            "retention_date": audit_data.get("retention_date"),
            "status_code": audit_data.get("status_code"),
            "category": audit_data.get("category"),
        }

    @staticmethod
    def prepare_bulk_audit_logs_for_db(
        audit_logs_data: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Prepare multiple audit log records for bulk database insertion.

        Normalizes all records to have consistent structure and prepares
        JSONB fields for efficient bulk insert.

        Args:
            audit_logs_data: List of raw audit log data dictionaries

        Returns:
            List of prepared audit log records ready for bulk insertion
        """
        if not audit_logs_data:
            return []

        prepared_records = []
        for audit_data in audit_logs_data:
            prepared_record = AuditLogService.prepare_audit_log_for_db(audit_data)
            prepared_records.append(prepared_record)

        return prepared_records

    @staticmethod
    def _parse_json_field(value: Any, default: Any = None) -> Any:
        """Parse JSON field from database, handling double-encoded strings.
        Args:
            value: Value to parse
            default: Default value if parsing fails
        Returns:
            Any: Parsed value
        """
        if value is None:
            return default

        if isinstance(value, (dict, list)):
            return AuditLogService._normalize_embedded_json_strings(value)

        if not isinstance(value, str):
            return default

        candidate = value.strip()
        if not candidate:
            return default

        parsed = AuditLogService._decode_json_candidate(candidate=candidate, default=default)
        return AuditLogService._normalize_embedded_json_strings(parsed)

    @staticmethod
    def _normalize_embedded_json_strings(value: Any) -> Any:
        """Recursively decode embedded JSON strings for keys ending in `_json`.

        This prevents "double encoded" payloads such as:
        `{"data": {"companies_json": "[{...}]"}}` leaking into API responses.
        """
        if value is None:
            return None

        if isinstance(value, list):
            return [AuditLogService._normalize_embedded_json_strings(v) for v in value]

        if not isinstance(value, dict):
            return value

        normalized: dict[str, Any] = {}
        for key, val in value.items():
            if isinstance(val, str) and isinstance(key, str) and key.endswith("_json"):
                decoded = AuditLogService._decode_json_candidate(candidate=val.strip(), default=val)
                normalized[key] = AuditLogService._normalize_embedded_json_strings(decoded)
            else:
                normalized[key] = AuditLogService._normalize_embedded_json_strings(val)
        return normalized

    @staticmethod
    def _decode_json_candidate(candidate: str, default: Any = None) -> Any:
        """Decode JSON string once or twice, returning dict/list or default.
        Args:
            candidate: Candidate string to decode
            default: Default value if decoding fails
        Returns:
            Any: Decoded value
        """
        for _ in range(2):
            try:
                candidate = json.loads(candidate)
            except (json.JSONDecodeError, TypeError, ValueError):
                return default

            if candidate in (None, "null"):
                return default
            if isinstance(candidate, (dict, list)):
                return candidate
            if isinstance(candidate, str):
                continue

        return default

    @staticmethod
    def _format_ip_address(ip_address: Any) -> str:
        """Convert IP address to string format.

        Args:
            ip_address: IP address (any type that can be converted to string, or None)

        Returns:
            IP address as string, or empty string if None
        """
        if ip_address is None:
            return ""
        return str(ip_address)

    @staticmethod
    def _format_audit_log_item(audit_log_data: dict) -> AuditLogItem:
        """Format audit log data into AuditLogItem.

        Args:
            audit_log_data: Raw audit log data from database

        Returns:
            AuditLogItem: Formatted audit log item
        """
        return AuditLogItem(
            id=str(audit_log_data["id"]),
            organization_id=str(audit_log_data["organization_id"]),
            user_id=str(audit_log_data["user_id"]),
            user_email=audit_log_data["user_email"],
            user_role=audit_log_data["user_role"],
            action_type=audit_log_data["action_type"],
            data_classification=audit_log_data["data_classification"],
            table_name=audit_log_data["table_name"],
            record_id=audit_log_data["record_id"],
            old_values=AuditLogService._parse_json_field(audit_log_data.get("old_values"), None),
            new_values=AuditLogService._parse_json_field(audit_log_data.get("new_values"), None),
            changed_fields=AuditLogService._parse_json_field(
                audit_log_data.get("changed_fields"), None
            ),
            compliance_tags=audit_log_data["compliance_tags"],
            risk_level=audit_log_data["risk_level"],
            ip_address=AuditLogService._format_ip_address(audit_log_data.get("ip_address")),
            description=audit_log_data["description"],
            timestamp=(
                audit_log_data["timestamp"]
                if isinstance(audit_log_data["timestamp"], str)
                else format_iso_datetime(audit_log_data["timestamp"]) or ""
            ),
            actor_name=audit_log_data.get("actor_name"),
            status_code=audit_log_data.get("status_code"),
            category=audit_log_data.get("category"),
        )

    @staticmethod
    def _format_audit_log_detail(audit_log_data: dict) -> AuditLogDetailItem:
        """Format audit log data into AuditLogDetailItem.

        Args:
            audit_log_data: Raw audit log data from database

        Returns:
            AuditLogDetailItem: Formatted audit log detail item
        """
        return AuditLogDetailItem(
            id=str(audit_log_data["id"]),
            organization_id=str(audit_log_data["organization_id"]),
            user_id=str(audit_log_data["user_id"]),
            user_email=audit_log_data["user_email"],
            user_role=audit_log_data["user_role"],
            action_type=audit_log_data["action_type"],
            data_classification=audit_log_data["data_classification"],
            table_name=audit_log_data["table_name"],
            record_id=audit_log_data["record_id"],
            old_values=AuditLogService._parse_json_field(audit_log_data.get("old_values"), None),
            new_values=AuditLogService._parse_json_field(audit_log_data.get("new_values"), None),
            changed_fields=audit_log_data.get("changed_fields"),
            compliance_tags=audit_log_data["compliance_tags"],
            risk_level=audit_log_data["risk_level"],
            ip_address=AuditLogService._format_ip_address(audit_log_data.get("ip_address")),
            description=audit_log_data["description"],
            timestamp=(
                audit_log_data["timestamp"]
                if isinstance(audit_log_data["timestamp"], str)
                else format_iso_datetime(audit_log_data["timestamp"]) or ""
            ),
            actor_name=audit_log_data.get("actor_name"),
            hash_signature=audit_log_data.get("hash_signature"),
            previous_hash=audit_log_data.get("previous_hash"),
            retention_date=(
                audit_log_data["retention_date"]
                if isinstance(audit_log_data["retention_date"], str)
                else format_iso_datetime(audit_log_data["retention_date"]) or None
            ),
            status_code=audit_log_data.get("status_code"),
            category=audit_log_data.get("category"),
        )

    async def get_audit_logs(
        self,
        filter_params: AuditLogFilter,
    ) -> dict[str, Any]:
        """Get paginated list of audit logs for the current organization and user.

        Args:
            filter_params: Filter parameters

        Returns:
            dict containing paginated audit logs and total count
        """
        # Get audit logs using repository
        audit_logs_data = await self.audit_log_repository.get_audit_logs_list(filter_params)

        # Get total count using repository
        total_count = await self.audit_log_repository.get_audit_logs_count(filter_params)

        # Format audit logs data
        audit_logs = [self._format_audit_log_item(audit_log) for audit_log in audit_logs_data]

        return {
            "audit_logs": audit_logs,
            "total_count": total_count,
        }

    async def get_audit_log_by_id(self, audit_log_id: str) -> AuditLogDetailItem:
        """Get audit log by ID.

        Args:
            audit_log_id: Audit log ID

        Returns:
            AuditLogDetailItem: Detailed audit log

        Raises:
            NotFoundException: If audit log not found
        """
        # Get audit log using repository
        audit_log_data = await self.audit_log_repository.get_audit_log_by_id(
            audit_log_id=audit_log_id,
            organization_id=self.user_context.organization_id,
            user_id=self.user_context.user_id,
        )

        # Check if audit log exists
        if not audit_log_data:
            raise NotFoundException(
                message_key="audit_logs.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Format audit log data
        return self._format_audit_log_detail(audit_log_data)

    async def delete_all_audit_logs(self) -> int:
        """Delete all audit logs from the system.

        Returns:
            Total count of audit logs deleted
        """
        # Delete all audit logs using repository
        total_count = await self.audit_log_repository.delete_all_audit_logs()
        return total_count
