"""Vendor portal token authentication."""

from __future__ import annotations

import asyncpg
from fastapi import Depends, Header

from apps.work_order_service.app.dependencies.db import db_conn
from apps.work_order_service.app.services.work_orders_service import WorkOrdersService
from apps.work_order_service.app.utils.tokens import hash_vendor_token
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


async def get_work_order_from_vendor_token(
    x_vendor_token: str = Header(..., alias="X-Vendor-Token"),
    conn: asyncpg.Connection = Depends(db_conn),
) -> dict:
    """Resolve work order from vendor portal token header."""
    if not x_vendor_token or len(x_vendor_token) < 16:
        raise ValidationException(
            message_key="auth.errors.unauthorized",
            custom_code=CustomStatusCode.UNAUTHORIZED,
        )
    token_hash = hash_vendor_token(x_vendor_token)
    service = WorkOrdersService(conn)
    work_order = await service.get_by_vendor_token(token_hash)
    if not work_order:
        raise ValidationException(
            message_key="auth.errors.unauthorized",
            custom_code=CustomStatusCode.UNAUTHORIZED,
        )
    return work_order
