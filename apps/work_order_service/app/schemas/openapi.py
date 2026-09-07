"""Concrete OpenAPI response models (envelope + entity)."""

from __future__ import annotations

from apps.user_service.app.schemas.presigned_url import PresignedUrlResponse
from apps.work_order_service.app.schemas.entities import (
    ApiKeyCreatedResponse,
    ApiKeyResponse,
    AssetCategoryResponse,
    AssetResponse,
    AuditEventResponse,
    ContractResponse,
    FormTemplateResponse,
    InvoiceResponse,
    PaymentResponse,
    TriggerResponse,
    WebhookDeliveryResponse,
    WorkOrderResponse,
)
from apps.work_order_service.app.schemas.responses import (
    DataApiResponse,
    DeleteIdData,
    ItemsTotalData,
    ListApiResponse,
    SchedulerRunData,
    TimelineListData,
    TriggerTestData,
)

WorkOrderApiResponse = DataApiResponse[WorkOrderResponse]
WorkOrderListApiResponse = ListApiResponse[WorkOrderResponse]
WorkOrderTimelineApiResponse = DataApiResponse[TimelineListData]

AssetApiResponse = DataApiResponse[AssetResponse]
AssetListApiResponse = ListApiResponse[AssetResponse]

AssetCategoryApiResponse = DataApiResponse[AssetCategoryResponse]
AssetCategoryListApiResponse = ListApiResponse[AssetCategoryResponse]

ContractApiResponse = DataApiResponse[ContractResponse]
ContractListApiResponse = ListApiResponse[ContractResponse]

FormTemplateApiResponse = DataApiResponse[FormTemplateResponse]
FormTemplateListApiResponse = ListApiResponse[FormTemplateResponse]

InvoiceApiResponse = DataApiResponse[InvoiceResponse]
InvoiceListApiResponse = ListApiResponse[InvoiceResponse]
InvoiceTimelineApiResponse = DataApiResponse[TimelineListData]

PaymentApiResponse = DataApiResponse[PaymentResponse]
PaymentListApiResponse = ListApiResponse[PaymentResponse]

TriggerApiResponse = DataApiResponse[TriggerResponse]
TriggerListApiResponse = DataApiResponse[ItemsTotalData[TriggerResponse]]
TriggerTestApiResponse = DataApiResponse[TriggerTestData]

ApiKeyApiResponse = DataApiResponse[ApiKeyResponse]
ApiKeyCreatedApiResponse = DataApiResponse[ApiKeyCreatedResponse]
ApiKeyListApiResponse = DataApiResponse[ItemsTotalData[ApiKeyResponse]]

AuditEventListApiResponse = ListApiResponse[AuditEventResponse]
WebhookDeliveryListApiResponse = ListApiResponse[WebhookDeliveryResponse]

SchedulerRunApiResponse = DataApiResponse[SchedulerRunData]
PresignedUrlApiResponse = DataApiResponse[PresignedUrlResponse]

DeleteIdApiResponse = DataApiResponse[DeleteIdData]

VendorInvoiceListApiResponse = DataApiResponse[ItemsTotalData[InvoiceResponse]]
