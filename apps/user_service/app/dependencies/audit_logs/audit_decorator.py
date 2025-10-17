# pylint: disable=R0902
"""
Audit Decorator Module

"""
from functools import wraps
from typing import Optional, Any, List
from uuid import uuid4
from datetime import datetime, timezone
import json
from fastapi import Request

from apps.user_service.app.dependencies.audit_logs.audit_logger import (
    audit_logger,
    AuditEventData,
)
from apps.user_service.app.dependencies.logger import get_logger

# Use the shared application logger
logger = get_logger()


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
        async def wrapper(**kwargs):
            print("Audit Decorator wrapper Starts\n\n")
            request: Request = kwargs.get("request")
            if request is None:
                raise ValueError("Request must be passed as a keyword argument")

            request.state.audit_metadata = func.audit_metadata
            
            result = await func(**kwargs)
            print("Audit Decorator wrapper Ends\n\n")
            if not _should_log_audit(request):
                print("Audit Decorator wrapper Skips\n\n")
                return result

            await _log_audit_event(request, result, action_type, data_classification,
                                 table_name, compliance_tags, category)
            return result

            # except HTTPException as http_exc:
            #     await maybe_log_audit_on_error(request, str(http_exc.detail))
            #     raise
            # except Exception as e:
            #     logger.exception("Unexpected error during audit logging")
            #     raise

        return wrapper

    return decorator


def _should_log_audit(request: Request) -> bool:
    """Check if audit logging should proceed based on user context."""
    user_context = getattr(request.state, "audit_user_context", {})
    return (
        all(user_context.get(k) for k in ["organization_id", "user_id", "user_email"])
        and user_context.get("user_email") != "unknown"
    )


async def _log_audit_event(request: Request, result: Any, action_type: str,
                          data_classification: str, table_name: str,
                          compliance_tags: Optional[List[str]], category: Optional[str]):
    """Log the audit event with collected data."""
    user_context = getattr(request.state, "audit_user_context", {})
    audit_state = _collect_audit_state(request, table_name)

    if not audit_state["description"]:
        raise ValueError("Missing required audit description")

    request_body = await _extract_request_body(request)
    status_code = getattr(result, "status_code", 200)
    request.state.audit_new_values = _build_new_values(request, request_body,
                                                      audit_state, status_code, table_name)

    if audit_state["raw_old"]:
        request.state.audit_old_values = {"data": audit_state["raw_old"]}
        request.state.audit_changed_fields = get_changed_fields(
            audit_state["raw_old"], audit_state["raw_new"]
        )

    audit_event_data = AuditEventData(
        user_context=user_context,
        action_type=action_type,
        data_classification=data_classification,
        table_name=audit_state["table"],
        record_id=str(uuid4()),
        old_values=getattr(request.state, "audit_old_values", None),
        new_values=request.state.audit_new_values,
        changed_fields=getattr(request.state, "audit_changed_fields", None),
        compliance_tags=compliance_tags or [],
        risk_level=audit_state["risk_level"],
        description=audit_state["description"],
        status_code=status_code,
        category=category,
    )

    await audit_logger.log_audit_event(audit_event_data, request)


def _collect_audit_state(request: Request, table_name: Optional[str]) -> dict:
    """Collect audit state data from request."""
    return {
        "table": getattr(request.state, "audit_table", table_name or ""),
        "requested_id": getattr(request.state, "audit_requested_id", ""),
        "raw_old": getattr(request.state, "raw_audit_old_data", None),
        "raw_new": getattr(request.state, "raw_audit_new_data", None),
        "description": getattr(request.state, "audit_description", ""),
        "risk_level": getattr(request.state, "audit_risk_level", "low")
    }


def _build_new_values(request: Request, request_body: Any, audit_state: dict,
                    status_code: int, table_name: Optional[str]) -> dict:
    """Build new values for audit logging."""
    return {
        "meta": {
            "path": str(request.url.path),
            "method": request.method,
            "status_code": status_code,
            "table": table_name,
            "requested_id": audit_state["requested_id"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_agent": request.headers.get("user-agent"),
            "ip": request.client.host,
            "query_params": dict(request.query_params) if request.query_params else {},
            "request_body": request_body,
            "content_type": request.headers.get("content-type"),
        },
        "data": audit_state["raw_new"]
    }


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


async def maybe_log_audit_on_error(
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
                "timestamp": datetime.now(timezone.utc).isoformat(),
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

    except (AttributeError, KeyError) as e:
        # Handle missing attributes in request.state or missing keys in dictionaries
        logger.warning("Audit logging failed - missing required attribute: %s", str(e))
    except json.JSONDecodeError as e:
        # Handle JSON serialization errors in request body
        logger.warning("Audit logging failed - JSON encoding error: %s", str(e))
    except UnicodeError as e:
        # Handle string encoding/decoding errors
        logger.warning("Audit logging failed - encoding error: %s", str(e))
    except (ValueError, TypeError) as e:
        # Handle data type conversion errors (str, dict, etc.)
        logger.warning("Audit logging failed - data conversion error: %s", str(e))
    except OSError as e:
        # Handle network/system errors (e.g., client.host access)
        logger.warning("Audit logging failed - connection error: %s", str(e))
    except (RuntimeError, LookupError) as e:
        # Handle runtime errors (e.g., loop closed, task cancelled)
        logger.warning("Audit logging failed - runtime error: %s", str(e))


async def _extract_request_body(request: Request) -> Any:
    """
    Safely extracts request body (uses cached bytes from middleware).
    """
    try:
        body_bytes = getattr(request.state, "_cached_body", None)
        content_type = request.headers.get("content-type", "")

        if not body_bytes or not content_type:
            return {}

        return await parse_body_by_content_type(request, body_bytes, content_type)

    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        logger.warning("Failed to decode request body: %s", str(err))
        return {"_error": f"Failed to decode request body: {str(err)}"}
    except AttributeError as err:
        logger.warning("Missing request attribute: %s", str(err))
        return {"_error": f"Missing request attribute: {str(err)}"}
    except (ValueError, TypeError) as err:
        logger.warning("Invalid request data: %s", str(err))
        return {"_error": f"Invalid request data: {str(err)}"}


async def parse_body_by_content_type(request: Request, body_bytes: bytes, content_type: str) -> Any:
    """Parse request body based on content type."""
    if content_type.startswith("application/json"):
        return _parse_json_body(body_bytes)
    elif content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
        return await _parse_form_data(request, content_type)
    return {}


def _parse_json_body(body_bytes: bytes) -> Any:
    """Parse JSON request body."""
    try:
        return json.loads(body_bytes.decode("utf-8"))
    except json.JSONDecodeError:
        return body_bytes.decode("utf-8")


async def _parse_form_data(request: Request, content_type: str) -> dict:
    """Parse form data request body."""
    form_data = await request.form()

    if content_type.startswith("multipart/form-data"):
        return {
            k: f"<file: {v.filename}>" if hasattr(v, "filename") else v
            for k, v in form_data.items()
        }
    return dict(form_data)
