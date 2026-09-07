"""Aggregate API routers for work_order_service."""

from fastapi import APIRouter

from apps.work_order_service.app.api.api_keys import router as api_keys_router
from apps.work_order_service.app.api.asset_categories import (
    router as asset_categories_router,
)
from apps.work_order_service.app.api.assets import router as assets_router
from apps.work_order_service.app.api.contracts import router as contracts_router
from apps.work_order_service.app.api.form_templates import (
    router as form_templates_router,
)
from apps.work_order_service.app.api.invoices import router as invoices_router
from apps.work_order_service.app.api.logs import router as logs_router
from apps.work_order_service.app.api.payments import router as payments_router
from apps.work_order_service.app.api.presigned_url import router as presigned_url_router
from apps.work_order_service.app.api.scheduler import router as scheduler_router
from apps.work_order_service.app.api.triggers import router as triggers_router
from apps.work_order_service.app.api.vendor_portal import router as vendor_portal_router
from apps.work_order_service.app.api.work_orders import router as work_orders_router

router = APIRouter(prefix="/v1")

router.include_router(asset_categories_router)
router.include_router(assets_router)
router.include_router(form_templates_router)
router.include_router(contracts_router)
router.include_router(work_orders_router)
router.include_router(invoices_router)
router.include_router(payments_router)
router.include_router(presigned_url_router)
router.include_router(triggers_router)
router.include_router(logs_router)
router.include_router(api_keys_router)
router.include_router(scheduler_router)
router.include_router(vendor_portal_router)
