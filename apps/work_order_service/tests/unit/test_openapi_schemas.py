"""OpenAPI schema smoke tests."""

from apps.work_order_service.app.main import app


def test_openapi_includes_work_order_response_schema() -> None:
    """Verify openapi includes work order response schema."""
    schema = app.openapi()
    components = schema.get("components", {}).get("schemas", {})
    assert "WorkOrderResponse" in components
    assert "ListApiResponse_WorkOrderResponse_" in components


def test_work_orders_list_route_documents_response() -> None:
    """Verify work orders list route documents response."""
    schema = app.openapi()
    path = schema["paths"]["/v1/projects/{project_id}/work-orders"]["get"]
    assert "200" in path["responses"]
    assert "content" in path["responses"]["200"]
