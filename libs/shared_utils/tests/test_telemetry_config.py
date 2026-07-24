"""Tests for OpenTelemetry / SigNoz telemetry configuration."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from libs.shared_config.app_settings import TelemetrySettings
from libs.shared_utils.telemetry_config import TelemetryConfig


def _make_settings(**overrides) -> TelemetrySettings:
    """Build TelemetrySettings with optional overrides for tests."""
    defaults = {
        "enabled": True,
        "service_name": "legalai-user-service",
        "service_version": "1.0.0",
        "environment": "development",
        "signoz_cloud_url": "",
        "signoz_cloud_token": "",
        "signoz_endpoint": "",
    }
    defaults.update(overrides)
    return TelemetrySettings(**defaults)


@pytest.fixture
def telemetry_config():
    """Return a fresh TelemetryConfig instance for each test."""
    return TelemetryConfig(settings=_make_settings())


def test_setup_telemetry_disabled():
    """Telemetry setup is a no-op when telemetry is disabled in settings."""
    telemetry_config = TelemetryConfig(settings=_make_settings(enabled=False))

    telemetry_config.setup_telemetry()

    assert telemetry_config._is_setup is False
    assert telemetry_config.tracer_provider is None
    assert telemetry_config.meter_provider is None


def test_setup_telemetry_missing_signoz_config():
    """Missing SigNoz config skips provider setup without crashing."""
    telemetry_config = TelemetryConfig(settings=_make_settings(enabled=True))

    with (
        patch("opentelemetry.instrumentation.fastapi.FastAPIInstrumentor") as mock_fastapi,
        patch("opentelemetry.instrumentation.requests.RequestsInstrumentor") as mock_requests,
        patch("opentelemetry.instrumentation.logging.LoggingInstrumentor") as mock_logging,
    ):
        mock_fastapi.return_value.instrument = MagicMock()
        mock_requests.return_value.instrument = MagicMock()
        mock_logging.return_value.instrument = MagicMock()

        telemetry_config.setup_telemetry()

    assert telemetry_config.tracer_provider is None
    assert telemetry_config.meter_provider is None
    assert telemetry_config._is_setup is True


def test_setup_telemetry_skips_redundant_setup():
    """Second setup call is skipped when already configured."""
    telemetry_config = TelemetryConfig(settings=_make_settings(enabled=False))
    telemetry_config._is_setup = True

    telemetry_config.setup_telemetry()

    assert telemetry_config._is_setup is True


def test_shutdown_calls_provider_shutdown(telemetry_config):
    """Shutdown flushes tracer and meter providers."""
    mock_tracer_provider = MagicMock()
    mock_meter_provider = MagicMock()
    telemetry_config.tracer_provider = mock_tracer_provider
    telemetry_config.meter_provider = mock_meter_provider
    telemetry_config._is_setup = True

    telemetry_config.shutdown()

    mock_tracer_provider.shutdown.assert_called_once()
    mock_meter_provider.shutdown.assert_called_once()
    assert telemetry_config._is_setup is False


def test_get_endpoint_and_headers_cloud(telemetry_config):
    """Cloud config returns traces endpoint and bearer token header."""
    telemetry_config.signoz_cloud_url = "ingest.us.signoz.cloud"
    telemetry_config.signoz_cloud_token = "test-token"

    endpoint, headers = telemetry_config._get_endpoint_and_headers(use_grpc=False)

    assert endpoint == "https://ingest.us.signoz.cloud/v1/traces"
    assert headers == {"Authorization": "Bearer test-token"}


def test_get_endpoint_and_headers_self_hosted_grpc(telemetry_config):
    """Self-hosted gRPC config strips protocol from endpoint."""
    telemetry_config.signoz_endpoint = "http://signoz-otel-collector:4317"

    endpoint, headers = telemetry_config._get_endpoint_and_headers(use_grpc=True)

    assert endpoint == "signoz-otel-collector:4317"
    assert headers == {}


def test_get_endpoint_and_headers_missing_config(telemetry_config):
    """Missing config returns None endpoint."""
    telemetry_config.signoz_cloud_url = ""
    telemetry_config.signoz_cloud_token = ""
    telemetry_config.signoz_endpoint = ""

    endpoint, headers = telemetry_config._get_endpoint_and_headers()

    assert endpoint is None
    assert headers == {}


def test_get_endpoint_and_headers_self_hosted_http(telemetry_config):
    """Self-hosted HTTP config appends traces path."""
    telemetry_config.signoz_endpoint = "http://signoz:4318"

    endpoint, headers = telemetry_config._get_endpoint_and_headers(use_grpc=False)

    assert endpoint == "http://signoz:4318/v1/traces"
    assert headers == {}


def test_use_grpc_exporter_prefers_self_hosted(telemetry_config):
    """gRPC exporter is selected for self-hosted endpoints without cloud token."""
    telemetry_config.signoz_endpoint = "http://signoz:4317"
    assert telemetry_config._use_grpc_exporter() is True


def test_otel_fallback_span_details():
    """Fallback span details derive method and path from ASGI scope."""
    from libs.shared_utils.telemetry_config import _otel_fallback_span_details

    name, attrs = _otel_fallback_span_details({"method": "GET", "path": "/health"})
    assert name == "GET /health"
    assert attrs


def test_otel_route_path_from_starlette_route():
    """Route path helper prefers route.path over scope path."""
    from libs.shared_utils.telemetry_config import _otel_route_path_from_starlette_route

    route = type("Route", (), {"path": "/api/users"})()
    assert _otel_route_path_from_starlette_route(route, {"path": "/fallback"}) == "/api/users"


def test_otel_safe_get_default_span_details_fallback():
    """Span detail wrapper falls back when original resolver raises."""
    from libs.shared_utils.telemetry_config import _otel_safe_get_default_span_details

    def _broken(_scope):
        raise AttributeError("no path")

    name, attrs = _otel_safe_get_default_span_details(
        {"method": "POST", "path": "/items"},
        original_get_default_span_details=_broken,
    )
    assert name == "POST /items"
    assert attrs


@pytest.mark.asyncio
async def test_otel_safe_middleware_call_fallback():
    """Middleware wrapper serves request when instrumentation raises."""
    from libs.shared_utils.telemetry_config import _otel_safe_middleware_call

    middleware = MagicMock()
    middleware.app = AsyncMock(return_value="ok")

    async def _broken(_self, _scope, _receive, _send):
        raise RuntimeError("trace failed")

    result = await _otel_safe_middleware_call(
        middleware,
        {"type": "http"},
        AsyncMock(),
        AsyncMock(),
        original_middleware_call=_broken,
    )
    assert result == "ok"
    middleware.app.assert_awaited_once()


def test_setup_telemetry_with_cloud_config():
    """Cloud SigNoz config completes setup when exporters are available."""
    config = TelemetryConfig(
        settings=_make_settings(
            enabled=True,
            signoz_cloud_url="ingest.us.signoz.cloud",
            signoz_cloud_token="token-123",
        )
    )

    with (
        patch.object(config, "_import_otlp_exporters", return_value=(MagicMock, MagicMock())),
        patch.object(config, "_setup_tracer_provider") as mock_tracer,
        patch.object(config, "_setup_meter_provider") as mock_meter,
        patch.object(config, "_setup_instrumentations"),
        patch.object(config, "_re_instrument_mongodb"),
        patch.object(config, "_instrument_asyncpg"),
    ):
        config.setup_telemetry()

    mock_tracer.assert_called_once()
    mock_meter.assert_called_once()
    assert config._is_setup is True


def test_import_otlp_exporters_grpc_and_http(telemetry_config):
    """Exporter import helper returns gRPC and HTTP exporter classes."""
    grpc_exporters = telemetry_config._import_otlp_exporters(use_grpc=True)
    http_exporters = telemetry_config._import_otlp_exporters(use_grpc=False)
    assert len(grpc_exporters) == 2
    assert len(http_exporters) == 2


def test_instrument_httpx_import_error(telemetry_config):
    """_instrument_httpx logs warning when optional package is missing."""
    with patch.dict("sys.modules", {"opentelemetry.instrumentation.httpx": None}):
        telemetry_config._instrument_httpx()


def test_shutdown_handles_provider_errors(telemetry_config):
    """Shutdown logs errors from provider shutdown without raising."""
    telemetry_config.tracer_provider = MagicMock()
    telemetry_config.tracer_provider.shutdown.side_effect = RuntimeError("shutdown failed")
    telemetry_config.meter_provider = MagicMock()
    telemetry_config._is_setup = True

    telemetry_config.shutdown()

    telemetry_config.tracer_provider.shutdown.assert_called_once()


def test_setup_tracer_and_meter_providers_with_endpoint(telemetry_config):
    """Tracer and meter providers configure OTLP exporters when endpoint exists."""
    telemetry_config.signoz_cloud_url = "ingest.us.signoz.cloud"
    telemetry_config.signoz_cloud_token = "token-123"

    mock_span_exporter = MagicMock()
    mock_metric_exporter = MagicMock()
    mock_tracer_provider = MagicMock()
    mock_meter_provider = MagicMock()
    trace_module = MagicMock()
    metrics_module = MagicMock()
    resource = MagicMock()

    telemetry_config._setup_tracer_provider(
        resource=resource,
        trace_module=trace_module,
        tracer_provider_cls=MagicMock(return_value=mock_tracer_provider),
        batch_span_processor_cls=MagicMock(return_value=MagicMock()),
        span_exporter_cls=MagicMock(return_value=mock_span_exporter),
        use_grpc=False,
    )
    telemetry_config._setup_meter_provider(
        resource=resource,
        metrics_module=metrics_module,
        meter_provider_cls=MagicMock(return_value=mock_meter_provider),
        metric_reader_cls=MagicMock(return_value=MagicMock()),
        metric_exporter_cls=MagicMock(return_value=mock_metric_exporter),
        use_grpc=False,
    )

    trace_module.set_tracer_provider.assert_called_once_with(mock_tracer_provider)
    metrics_module.set_meter_provider.assert_called_once_with(mock_meter_provider)
    assert telemetry_config.tracer_provider is mock_tracer_provider
    assert telemetry_config.meter_provider is mock_meter_provider


def test_otel_resolve_route_path_fallback(monkeypatch):
    """Route resolution falls back to request path when matching raises."""
    from libs.shared_utils import telemetry_config as tc

    class _BrokenRoute:
        @staticmethod
        def matches(_scope):
            raise RuntimeError("match failed")

    scope = {"app": type("App", (), {"routes": [_BrokenRoute()]})(), "path": "/fallback"}
    assert tc._otel_resolve_route_path(scope) == "/fallback"
