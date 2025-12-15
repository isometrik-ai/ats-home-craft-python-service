"""Audit Logger Module.

This module provides a comprehensive audit logging system for tracking user actions
and data changes in the application. It implements a queue-based asynchronous
logging system with database persistence and audit chain integrity.

The module includes:
- AuditEventData: Data class for structured audit event information
- AuditLogger: Main audit logging class with queue processing and database storage
- audit_logger: Singleton instance for application-wide use

Features:
- Asynchronous queue-based processing for performance
- Audit chain integrity with hash linking
- Configurable retention periods based on data classification
- Comprehensive error handling and retry logic
- Support for multiple data classifications (PII, PHI, financial, etc.)
"""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import Request

from apps.user_service.app.dependencies.logger import get_logger
from libs.shared_db.postgres_db.user_service_operations.audit_operations import (
    bulk_create_audit_logs,
    get_last_audit_log_hash,
)
from libs.shared_utils.http_exceptions import InternalServerErrorException
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("audit_logs")


@dataclass
class AuditEventData:
    """Data class for audit event information."""

    user_context: dict
    action_type: str
    data_classification: str
    table_name: str
    record_id: str | None
    old_values: dict | None
    new_values: dict | None
    changed_fields: list[str] | None
    compliance_tags: list[str]
    risk_level: str
    description: str
    status_code: int | None = None
    category: str | None = None


class AuditLogger:
    """Simplified audit logger aligned with RBAC Audit Values Guide.
    Optimized for single-core processing with structured audit data.
    """

    def __init__(self):
        self._queue = asyncio.Queue(maxsize=500)
        self._processing_task = None
        self._last_hash = None
        self._shutdown_event = asyncio.Event()

        # Processing configuration
        self._batch_size = 10
        self._batch_timeout = 3.0
        self._max_retries = 3

    def start_processing(self):
        """Start the audit processing task.

        Note: Database operations are now handled by centralized operations.
        """
        if self._processing_task is None:
            self._processing_task = asyncio.create_task(self._process_audit_queue())

    async def shutdown(self):
        """Gracefully shutdown the audit processing task.

        Waits for the processing task to complete with a timeout,
        then cancels it if necessary.
        """
        self._shutdown_event.set()

        if self._processing_task:
            try:
                await asyncio.wait_for(self._processing_task, timeout=10.0)
            except asyncio.TimeoutError:
                self._processing_task.cancel()

    async def log_audit_event(self, event_data: AuditEventData, request: Request):
        """Log an audit event with the provided data.

        Args:
            event_data: AuditEventData object containing all audit information
            request: FastAPI Request object for extracting client information
        """
        try:
            audit_event = self._create_audit_event_dict(event_data, request)

            try:
                self._queue.put_nowait(audit_event)
            except asyncio.QueueFull:
                try:
                    await asyncio.wait_for(self._queue.put(audit_event), timeout=0.1)
                except asyncio.TimeoutError as e:
                    logger.warning("Queue timeout error in audit logging: %s", str(e))
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            # Handle data validation and access errors
            logger.warning("Data validation error in audit logging: %s", str(e))
        except (asyncio.QueueFull, asyncio.TimeoutError) as e:
            # Handle queue operation errors
            logger.warning("Queue operation error in audit logging: %s", str(e))
        except (RuntimeError, IOError) as e:
            # Handle runtime and I/O errors (e.g., event loop issues)
            logger.error("Runtime error in audit logging: %s", str(e), exc_info=True)

    def _create_audit_event_dict(
        self,
        event_data: AuditEventData,
        request: Request,
    ) -> dict[str, Any]:
        """Create the audit event dictionary from AuditEventData.

        Args:
            event_data: AuditEventData object containing audit information
            request: FastAPI Request object

        Returns:
            dict containing the formatted audit event
        """
        return {
            "organization_id": event_data.user_context.get("organization_id"),
            "user_id": event_data.user_context.get("user_id"),
            "user_email": event_data.user_context.get("user_email", "unknown"),
            "user_role": event_data.user_context.get("user_role", "unknown"),
            "action_type": event_data.action_type,
            "data_classification": event_data.data_classification,
            "table_name": event_data.table_name,
            "record_id": event_data.record_id,
            "old_values": event_data.old_values,
            "new_values": event_data.new_values,
            "changed_fields": event_data.changed_fields or [],
            "compliance_tags": event_data.compliance_tags,
            "risk_level": event_data.risk_level,
            "ip_address": self._get_client_ip(request),
            "description": event_data.description,
            "timestamp": datetime.now(timezone.utc),
            "status_code": event_data.status_code,
            "category": event_data.category,
        }

    async def _collect_batch_events(self, timeout_duration: float) -> tuple[list[dict], bool]:
        """Collect events from queue up to batch size or until queue is empty.
        Returns (batch, got_events) where got_events indicates if any events were collected.
        """
        batch = []
        got_events = False
        try:
            async with asyncio.timeout(timeout_duration):
                event = await self._queue.get()
                batch.append(event)
                got_events = True

            while len(batch) < self._batch_size:
                try:
                    event = self._queue.get_nowait()
                    batch.append(event)
                except asyncio.QueueEmpty:
                    break
        except asyncio.TimeoutError:
            pass
        return batch, got_events

    async def _process_audit_queue(self):
        """Process audit events from queue in batches."""
        batch = []
        consecutive_empty_batches = 0

        while not self._shutdown_event.is_set():
            try:
                timeout = 10.0 if consecutive_empty_batches > 3 else self._batch_timeout
                batch, got_events = await self._collect_batch_events(timeout)

                if not got_events:  # Only increment on timeout, not empty batch
                    consecutive_empty_batches += 1
                    continue

                consecutive_empty_batches = 0
                await self._write_audit_batch_with_retry(batch)
                batch.clear()
                await asyncio.sleep(5)

            except (asyncio.CancelledError, asyncio.TimeoutError):
                break
            except (
                OSError,
                RuntimeError,
                IOError,
                json.JSONDecodeError,
                UnicodeError,
                AttributeError,
                LookupError,
            ):
                await asyncio.sleep(1)

        # Process remaining events
        if batch:
            await self._write_audit_batch_with_retry(batch)

    async def _write_audit_batch_with_retry(self, events: list[dict]) -> None:
        """Write audit batch with retries on failure."""
        for attempt in range(self._max_retries):
            try:
                await self._write_audit_batch(events)
                return
            except (
                OSError,
                RuntimeError,
                json.JSONDecodeError,
                UnicodeError,
                LookupError,
                AttributeError,
                KeyError,
                ValueError,
                TypeError,
            ):
                if attempt == self._max_retries - 1:
                    break

                await asyncio.sleep(2**attempt)

    async def _get_last_hash_from_db(self, organization_id: str | None = None) -> str | None:
        """Fetch the last hash from the database to maintain audit chain integrity.

        Args:
            organization_id: Organization ID to filter by (optional)

        Returns:
            Optional[str]: The last hash from the database, or None if no audit logs exist
        """
        org_id = organization_id or "default"
        return await get_last_audit_log_hash(organization_id=org_id)

    async def _write_audit_batch(self, events: list[dict]) -> None:
        """Write a batch of audit events to the database.

        Args:
            events (list[dict]): List of audit event dictionaries to write
        """
        if not events:
            return

        try:
            # Get the last hash from database if we don't have it cached
            if self._last_hash is None:
                # Use organization_id from the first event if available
                org_id = events[0].get("organization_id") if events else None
                self._last_hash = await self._get_last_hash_from_db(org_id)

            # Prepare batch data for centralized operations
            batch_data = []
            for event in events:
                hash_signature = self._generate_hash(event)
                retention_date = self._calculate_retention_date(
                    event["timestamp"], event["data_classification"]
                )

                batch_data.append(
                    {
                        "organization_id": event["organization_id"],
                        "user_id": event["user_id"],
                        "user_email": event["user_email"],
                        "user_role": event["user_role"],
                        "action_type": event["action_type"],
                        "data_classification": event["data_classification"],
                        "table_name": event["table_name"],
                        "record_id": event["record_id"],
                        "old_values": event["old_values"],
                        "new_values": event["new_values"],
                        "changed_fields": event.get("changed_fields"),
                        "compliance_tags": event.get("compliance_tags"),
                        "risk_level": event["risk_level"],
                        "ip_address": event["ip_address"],
                        "timestamp": event["timestamp"],
                        "hash_signature": hash_signature,
                        "previous_hash": self._last_hash,
                        "description": event["description"],
                        "retention_date": retention_date,
                        "status_code": event.get("status_code"),
                        "category": event.get("category"),
                    }
                )

                # Update last hash for next event
                self._last_hash = hash_signature

            # Use centralized bulk create operation
            await bulk_create_audit_logs(batch_data)

        except Exception as e:
            logger.error("Unknown error: %s", e, exc_info=True)
            raise InternalServerErrorException(
                message_key="errors.internal_server_error",
                custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
            ) from e

    def _generate_hash(self, event: dict) -> str:
        """Generate a hash signature for an audit event.

        Args:
            event (dict): Audit event dictionary

        Returns:
            str: SHA256 hash signature
        """
        hash_data = (
            f"{event['organization_id']}"
            f"{event['user_id']}"
            f"{event['action_type']}"
            f"{event['timestamp'].isoformat()}"
            f"{event['description']}"
        )
        if self._last_hash:
            hash_data += self._last_hash

        return hashlib.sha256(hash_data.encode()).hexdigest()

    def _calculate_retention_date(self, timestamp: datetime, data_classification: str) -> datetime:
        """Calculate retention date based on data classification.

        Args:
            timestamp (datetime): Event timestamp
            data_classification (str): Data classification level

        Returns:
            datetime: Retention date
        """
        retention_years = {
            "phi": 7,
            "pii": 7,
            "financial": 7,
            "general": 3,
            "public": 1,
        }.get(data_classification, 3)

        return timestamp.replace(year=timestamp.year + retention_years)

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP address from request headers.

        Args:
            request (Request): FastAPI request object

        Returns:
            str: Client IP address or 'unknown' if not found
        """
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip

        return getattr(request.client, "host", "unknown") if request.client else "unknown"

    def get_queue_stats(self) -> dict[str, Any]:
        """Get current statistics about the audit queue and processing status.

        Returns:
            dict[str, Any]: Dictionary containing queue statistics including:
                - queue_size: Current number of items in the queue
                - max_queue_size: Maximum capacity of the queue
                - processing_active: Whether the processing task is active
                - shutdown_requested: Whether shutdown has been requested
        """
        return {
            "queue_size": self._queue.qsize(),
            "max_queue_size": self._queue.maxsize,
            "processing_active": self._processing_task and not self._processing_task.done(),
            "shutdown_requested": self._shutdown_event.is_set(),
        }


audit_logger = AuditLogger()
