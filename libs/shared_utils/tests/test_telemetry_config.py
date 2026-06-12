"""Tests for OpenTelemetry / SigNoz telemetry configuration."""

from unittest.mock import MagicMock, patch

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
