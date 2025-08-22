# pylint: disable=too-many-return-statements
"""
Audit Decorator Module

"""
import logging
from functools import wraps
from typing import Optional, Any, List
from uuid import uuid4
from datetime import datetime
import json
from fastapi import Request

from apps.user_service.app.dependencies.audit_logs.audit_logger import (
    audit_logger,
    AuditEventData,
)

logger = logging.getLogger(__name__)


def audit_api_call(
    action_type: Optional[str] = None,
    table_name: Optional[str] = None,
    data_classification: str = "general",
    compliance_tags: Optional[List[str]] = None,
    category: Optional[str] = None,
):
    """
    Decorator to automatically log audit metadata for API calls.

    Args:
        action_type (str): Type of action (e.g., CREATE, UPDATE, DELETE).
        table_name (str): Table involved.
        data_classification (str): Sensitivity of data (default: 'general').
        compliance_tags (List[str]): List of compliance-related tags.
        category (str): Category classification for the audit log.
    """

    def decorator(func):
        # Attach audit metadata
        func.audit_metadata = {
            "action_type": action_type,
            "data_classification": data_classification,
            "compliance_tags": compliance_tags,
            "table_name": table_name,
            "category": category,
        }

        @wraps(func)
        async def wrapper(*args, **kwargs):  # pylint: disable=too-many-locals
            request: Request = kwargs.get("request")
            if not request:
                raise ValueError("Request must be passed as a keyword argument")

            request.state.audit_metadata = func.audit_metadata

            # try:
            result = await func(*args, **kwargs)

            user_context = getattr(request.state, "audit_user_context", {})
            organization_id = user_context.get("organization_id")
            user_id = user_context.get("user_id")
            user_email = user_context.get("user_email")
            print("Audit logs called")
            print(organization_id, user_id, user_email)

            if (
                not all([organization_id, user_id, user_email])
                or user_email == "unknown"
            ):
                return result  # Skip audit logging

            record_id = str(uuid4())
            audit_table = getattr(request.state, "audit_table", table_name or "")
            requested_id = getattr(request.state, "audit_requested_id", "")

            raw_old = getattr(request.state, "raw_audit_old_data", None)
            raw_new = getattr(request.state, "raw_audit_new_data", None)

            request_body = await _extract_request_body(request)

            query_params = dict(request.query_params) if request.query_params else {}
            status_code = getattr(result, "status_code", 200)
            request.state.audit_new_values = {
                "meta": {
                    "path": str(request.url.path),
                    "method": request.method,
                    "status_code": status_code,
                    "table": table_name,
                    "requested_id": requested_id,
                    "timestamp": datetime.utcnow().isoformat(),
                    "user_agent": request.headers.get("user-agent"),
                    "ip": request.client.host,
                    "query_params": query_params,
                    "request_body": request_body,
                    "content_type": request.headers.get("content-type"),
                },
                "data": raw_new,
            }

            if raw_old:
                request.state.audit_old_values = {"data": raw_old}
                request.state.audit_changed_fields = get_changed_fields(
                    raw_old, raw_new
                )

            # Final audit values
            final_description = getattr(request.state, "audit_description", "")
            risk_level = getattr(request.state, "audit_risk_level", "low")

            if not user_context or not final_description:
                raise ValueError("Missing required audit context")

            # Create AuditEventData object
            audit_event_data = AuditEventData(
                user_context=user_context,
                action_type=action_type,
                data_classification=data_classification,
                table_name=audit_table,
                record_id=record_id,
                old_values=getattr(request.state, "audit_old_values", None),
                new_values=request.state.audit_new_values,
                changed_fields=getattr(request.state, "audit_changed_fields", None),
                compliance_tags=compliance_tags or [],
                risk_level=risk_level,
                description=final_description,
                status_code=status_code,
                category=category,
            )

            await audit_logger.log_audit_event(audit_event_data, request)

            return result

            # except HTTPException as http_exc:
            #     await maybe_log_audit_on_error(request, str(http_exc.detail))
            #     raise
            # except Exception as e:
            #     logger.exception("Unexpected error during audit logging")
            #     raise

        return wrapper

    return decorator


def get_changed_fields(old_data: dict, new_data: dict, prefix: str = "") -> List[str]:
    """
    Recursively compares old and new data to identify changed fields.
    Only includes fields that exist in both old and new data and have different values.
    """
    changed = []

    # Only compare fields that exist in both old and new data
    common_keys = set(old_data.keys()) & set(new_data.keys())

    for key in common_keys:
        full_key = f"{prefix}.{key}" if prefix else key
        old_val = old_data.get(key)
        new_val = new_data.get(key)

        if isinstance(old_val, dict) and isinstance(new_val, dict):
            changed.extend(get_changed_fields(old_val, new_val, prefix=full_key))
        elif old_val != new_val:
            changed.append(full_key)

    return changed


async def maybe_log_audit_on_error(  # pylint: disable=too-many-return-statements
    request: Request, description: str, status_code: int = 500
):
    """
    Fallback audit logging for failed requests or unhandled exceptions.
    Captures query params and request body in addition to metadata.
    """
    # return

    try:
        metadata = getattr(request.state, "_audit_metadata", {})
        user_context = getattr(request.state, "audit_user_context", {})

        # Validate required user context
        organization_id = user_context.get("organization_id")
        user_id = user_context.get("user_id")
        user_email = user_context.get("user_email")

        if not all([organization_id, user_id, user_email]) or user_email == "unknown":
            print(
                "organization_id, user_id, user_email empty",
                organization_id,
                user_id,
                user_email,
            )
            return  # Skip audit logging

        if not user_context or user_context.get("user_email") == "unknown":
            print(f"[Audit Skipped] Incomplete user context for {request.url.path}")
            return

        record_id = str(uuid4())
        table_name = metadata.get("table_name", "")
        request.state.audit_table = table_name
        request.state.audit_risk_level = "high"

        # Extract query params and request body
        query_params = dict(request.query_params) if request.query_params else {}
        request_body = await _extract_request_body(request)

        # Fallback audit metadata
        request.state.audit_new_values = {
            "meta": {
                "path": str(request.url.path),
                "method": request.method,
                "status_code": status_code,
                "record_id": record_id,
                "timestamp": datetime.utcnow().isoformat(),
                "user_agent": request.headers.get("user-agent"),
                "ip": request.client.host,
                "query_params": query_params,
                "request_body": request_body,
                "content_type": request.headers.get("content-type"),
            }
        }

        request.state.audit_description = f"Failed request: {description}"

        # Create AuditEventData object for error logging
        audit_event_data = AuditEventData(
            user_context=user_context,
            action_type="ERROR",
            data_classification=metadata.get("data_classification", "general"),
            table_name=table_name,
            record_id=record_id,
            old_values=None,
            new_values=request.state.audit_new_values,
            changed_fields=None,
            compliance_tags=metadata.get("compliance_tags", []),
            risk_level="high",
            description=request.state.audit_description,
            status_code=status_code,
            category=metadata.get("category"),
        )

        await audit_logger.log_audit_event(audit_event_data, request)

    except Exception as e:  # pylint: disable=broad-except
        print(f"[Audit Logging Failed]: {e}")


async def _extract_request_body(request: Request) -> Any:
    """
    Safely extracts request body (uses cached bytes from middleware).
    """
    try:
        body_bytes = getattr(request.state, "_cached_body", None)
        if body_bytes is None or not body_bytes:
            return {}

        content_type = request.headers.get("content-type", "")

        if content_type.startswith("application/json"):
            try:
                return json.loads(body_bytes.decode("utf-8"))
            except json.JSONDecodeError:
                return body_bytes.decode("utf-8")

        elif content_type.startswith("application/x-www-form-urlencoded"):
            form_data = await request.form()
            return dict(form_data)

        elif content_type.startswith("multipart/form-data"):
            form_data = await request.form()
            parsed = {}
            for key, val in form_data.items():
                parsed[key] = (
                    f"<file: {val.filename}>" if hasattr(val, "filename") else val
                )
            return parsed

        return {}
    except Exception as err:  # pylint: disable=broad-except
        return {"_error": f"error reading body: {str(err)}"}
