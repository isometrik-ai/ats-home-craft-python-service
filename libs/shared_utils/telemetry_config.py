"""OpenTelemetry / SigNoz telemetry configuration."""

import logging
import traceback
from typing import Any

from libs.shared_config.app_settings import TelemetrySettings, shared_settings
from libs.shared_utils.logger import app_logger

logger = app_logger


def _otel_fallback_span_details(scope: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """Build span name and attributes from the ASGI scope when route lookup fails."""
    from opentelemetry.semconv.attributes.http_attributes import HTTP_ROUTE
    from opentelemetry.util.http import sanitize_method

    method = sanitize_method((scope.get("method") or "").strip()) or "HTTP"
    path = scope.get("path") or ""
    span_name = f"{method} {path}".strip() if path else method
    attributes = {HTTP_ROUTE: path} if path else {}
    return span_name, attributes


def _otel_route_path_from_starlette_route(
    starlette_route: Any,
    scope: dict[str, Any],
) -> str | None:
    """Return a route path from a Starlette route, falling back to the request path."""
    return getattr(starlette_route, "path", None) or scope.get("path")


def _otel_resolve_route_path(scope: dict[str, Any]) -> str | None:
    """Resolve the matched route path without assuming every route exposes ``path``."""
    from starlette.routing import Match, Route

    try:
        route = None
        for starlette_route in scope["app"].routes:
            match, _ = (
                Route.matches(starlette_route, scope)
                if isinstance(starlette_route, Route)
                else starlette_route.matches(scope)
            )
            if match not in (Match.FULL, Match.PARTIAL):
                continue
            route = _otel_route_path_from_starlette_route(starlette_route, scope)
            if match == Match.FULL:
                break
        return route
    except Exception as exc:
        logger.warning(
            "OpenTelemetry route resolution failed; using request path: %s",
            exc,
        )
        return scope.get("path")


def _otel_safe_get_default_span_details(
    scope: dict[str, Any],
    *,
    original_get_default_span_details: Any,
) -> tuple[str, dict[str, str]]:
    """Wrap OpenTelemetry span detail resolution with a scope-based fallback."""
    try:
        return original_get_default_span_details(scope)
    except Exception as exc:
        logger.warning(
            "OpenTelemetry span details failed; using fallback span name: %s",
            exc,
        )
        return _otel_fallback_span_details(scope)


async def _otel_safe_middleware_call(
    middleware: Any,
    scope: dict[str, Any],
    receive: Any,
    send: Any,
    *,
    original_middleware_call: Any,
) -> Any:
    """Serve the request without tracing when OpenTelemetry middleware raises."""
    try:
        return await original_middleware_call(middleware, scope, receive, send)
    except Exception as exc:
        logger.warning(
            "OpenTelemetry middleware failed; serving request without tracing: %s",
            exc,
            exc_info=True,
        )
        return await middleware.app(scope, receive, send)


def _make_fastapi_instrumentation_fail_safe() -> None:
    """Ensure OpenTelemetry instrumentation never crashes API requests.

    FastAPI 0.137+ nests included routers as ``_IncludedRouter`` objects that
    do not expose ``path``. Older OpenTelemetry FastAPI instrumentation can
    raise ``AttributeError`` while resolving span details. Any other telemetry
    failure during request handling is also bypassed so the API keeps serving.
    """
    import opentelemetry.instrumentation.fastapi as otel_fastapi
    from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware

    original_get_default_span_details = otel_fastapi._get_default_span_details
    original_middleware_call = OpenTelemetryMiddleware.__call__

    otel_fastapi._get_route_details = _otel_resolve_route_path
    otel_fastapi._get_default_span_details = lambda scope: _otel_safe_get_default_span_details(
        scope,
        original_get_default_span_details=original_get_default_span_details,
    )
    OpenTelemetryMiddleware.__call__ = (
        lambda self, scope, receive, send: _otel_safe_middleware_call(
            self,
            scope,
            receive,
            send,
            original_middleware_call=original_middleware_call,
        )
    )


class TelemetryConfig:
    """Configures OpenTelemetry tracing and metrics export to SigNoz."""

    def __init__(self, settings: TelemetrySettings | None = None):
        telemetry_settings = settings or shared_settings.telemetry
        self.service_name = telemetry_settings.service_name
        self.service_version = telemetry_settings.service_version
        self.environment = telemetry_settings.environment
        self.signoz_cloud_url = telemetry_settings.signoz_cloud_url
        self.signoz_cloud_token = telemetry_settings.signoz_cloud_token
        self.signoz_endpoint = telemetry_settings.signoz_endpoint
        self.enable_telemetry = telemetry_settings.enabled
        self.tracer_provider = None
        self.meter_provider = None
        self._is_setup = False

    def setup_telemetry(self, app: Any = None) -> None:
        """Initialize OpenTelemetry providers and instrument the application."""
        if not self.enable_telemetry:
            logger.info("Telemetry is disabled")
            return
        if self._is_setup:
            logger.info("Telemetry already set up, skipping redundant setup")
            return

        try:
            logger.info("Setting up OpenTelemetry telemetry...")

            from opentelemetry import metrics, trace
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.semconv.resource import ResourceAttributes

            use_grpc = self._use_grpc_exporter()
            span_exporter_cls, metric_exporter_cls = self._import_otlp_exporters(use_grpc)

            trace._TRACER_PROVIDER = None
            metrics._METER_PROVIDER = None

            resource = Resource.create(
                {
                    ResourceAttributes.SERVICE_NAME: self.service_name,
                    ResourceAttributes.SERVICE_VERSION: self.service_version,
                    ResourceAttributes.DEPLOYMENT_ENVIRONMENT: self.environment,
                }
            )

            self._setup_tracer_provider(
                resource=resource,
                trace_module=trace,
                tracer_provider_cls=TracerProvider,
                batch_span_processor_cls=BatchSpanProcessor,
                span_exporter_cls=span_exporter_cls,
                use_grpc=use_grpc,
            )
            self._setup_meter_provider(
                resource=resource,
                metrics_module=metrics,
                meter_provider_cls=MeterProvider,
                metric_reader_cls=PeriodicExportingMetricReader,
                metric_exporter_cls=metric_exporter_cls,
                use_grpc=use_grpc,
            )
            self._setup_instrumentations(app=app)

            self._re_instrument_mongodb()
            self._instrument_asyncpg()

            logger.info("OpenTelemetry telemetry setup completed successfully")
            self._is_setup = True

        except Exception as exc:
            logger.error("Failed to setup telemetry: %s", exc)

    def _use_grpc_exporter(self) -> bool:
        """Return True when self-hosted gRPC export should be used."""
        return bool(
            self.signoz_endpoint and not (self.signoz_cloud_url and self.signoz_cloud_token)
        )

    def _import_otlp_exporters(self, use_grpc: bool) -> tuple[type[Any], type[Any]]:
        """Import OTLP span and metric exporter classes for the selected transport."""
        if use_grpc:
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
                OTLPMetricExporter,
            )
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            logger.info("Using gRPC exporter for self-hosted SigNoz")
        else:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter,
            )
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            logger.info("Using HTTP exporter for SigNoz Cloud")

        return OTLPSpanExporter, OTLPMetricExporter

    def _re_instrument_mongodb(self) -> None:
        """Instrument PyMongo when the optional instrumentation package is installed."""
        try:
            from opentelemetry.instrumentation.pymongo import PymongoInstrumentor

            PymongoInstrumentor().instrument(
                enable_commenter=True,
                capture_parameters=True,
            )
            logger.info("MongoDB client re-instrumented successfully")
        except ImportError:
            logger.warning("PyMongo instrumentation not available - package not installed")
        except Exception as exc:
            logger.error("Failed to re-instrument MongoDB client: %s", exc)
            logger.error("Traceback: %s", traceback.format_exc())

    def _instrument_asyncpg(self) -> None:
        """Instrument asyncpg when the optional instrumentation package is installed."""
        try:
            from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor

            AsyncPGInstrumentor().instrument()
            logger.info("AsyncPG instrumentation setup completed")
        except ImportError:
            logger.warning("AsyncPG instrumentation not available - package not installed")
        except Exception as exc:
            logger.error("Failed to instrument AsyncPG: %s", exc)
            logger.error("Traceback: %s", traceback.format_exc())

    def _setup_tracer_provider(
        self,
        *,
        resource: Any,
        trace_module: Any,
        tracer_provider_cls: type[Any],
        batch_span_processor_cls: type[Any],
        span_exporter_cls: type[Any],
        use_grpc: bool,
    ) -> None:
        """Configure and register the OTLP trace exporter."""
        try:
            endpoint, headers = self._get_endpoint_and_headers(use_grpc)
            if endpoint is None:
                logger.warning(
                    "Skipping tracer provider setup due to missing SigNoz configuration."
                )
                return
            logger.info("Setting up tracer provider with endpoint: %s", endpoint)

            if use_grpc:
                endpoint = endpoint.split("/")[0]
                otlp_exporter = span_exporter_cls(
                    endpoint=endpoint,
                    headers=headers,
                    insecure=True,
                )
            else:
                otlp_exporter = span_exporter_cls(endpoint=endpoint, headers=headers)

            self.tracer_provider = tracer_provider_cls(
                resource=resource,
                active_span_processor=batch_span_processor_cls(otlp_exporter),
            )
            trace_module.set_tracer_provider(self.tracer_provider)
            logger.info("Tracer provider setup completed")
        except Exception as exc:
            logger.error("Failed to setup tracer provider: %s", exc)
            logger.error("Traceback: %s", traceback.format_exc())

    def _setup_meter_provider(
        self,
        *,
        resource: Any,
        metrics_module: Any,
        meter_provider_cls: type[Any],
        metric_reader_cls: type[Any],
        metric_exporter_cls: type[Any],
        use_grpc: bool,
    ) -> None:
        """Configure and register the OTLP metrics exporter."""
        try:
            endpoint, headers = self._get_endpoint_and_headers(use_grpc)
            if endpoint is None:
                logger.warning("Skipping meter provider setup due to missing SigNoz configuration.")
                return

            if use_grpc:
                metrics_endpoint = endpoint.split("/")[0]
                otlp_metric_exporter = metric_exporter_cls(
                    endpoint=metrics_endpoint,
                    headers=headers,
                    insecure=True,
                )
            else:
                metrics_endpoint = endpoint.replace("/v1/traces", "/v1/metrics")
                otlp_metric_exporter = metric_exporter_cls(
                    endpoint=metrics_endpoint,
                    headers=headers,
                )

            metric_reader = metric_reader_cls(
                exporter=otlp_metric_exporter,
                export_interval_millis=10000,
            )
            self.meter_provider = meter_provider_cls(
                resource=resource,
                metric_readers=[metric_reader],
            )
            metrics_module.set_meter_provider(self.meter_provider)
            logger.info("Meter provider setup completed")
        except Exception as exc:
            logger.error("Failed to setup meter provider: %s", exc)
            logger.error("Traceback: %s", traceback.format_exc())

    def _get_endpoint_and_headers(
        self, use_grpc: bool = False
    ) -> tuple[str | None, dict[str, str]]:
        """Resolve the OTLP endpoint URL and auth headers from SigNoz settings."""
        if self.signoz_cloud_url and self.signoz_cloud_token:
            cloud_url = self.signoz_cloud_url
            if not cloud_url.startswith(("http://", "https://")):
                cloud_url = f"https://{cloud_url}"
            endpoint = f"{cloud_url}/v1/traces"
            headers = {"Authorization": f"Bearer {self.signoz_cloud_token}"}
            logger.info("Using SigNoz Cloud configuration: %s", endpoint)
        elif self.signoz_endpoint:
            if use_grpc:
                endpoint = self.signoz_endpoint
                if endpoint.startswith("http://"):
                    endpoint = endpoint.replace("http://", "")
                elif endpoint.startswith("https://"):
                    endpoint = endpoint.replace("https://", "")
                logger.info("Using self-hosted SigNoz configuration (gRPC): %s", endpoint)
            else:
                endpoint = f"{self.signoz_endpoint}/v1/traces"
                logger.info("Using self-hosted SigNoz configuration (HTTP): %s", endpoint)
            headers = {}
        else:
            logger.warning("No SigNoz configuration found. Telemetry will be disabled.")
            return None, {}
        return endpoint, headers

    def _setup_instrumentations(self, app: Any = None) -> None:
        """Register OpenTelemetry auto-instrumentation for supported libraries."""
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            from opentelemetry.instrumentation.logging import LoggingInstrumentor
            from opentelemetry.instrumentation.requests import RequestsInstrumentor

            _make_fastapi_instrumentation_fail_safe()

            if app is not None:
                FastAPIInstrumentor.instrument_app(app)
            else:
                FastAPIInstrumentor().instrument()

            logger.info("FastAPI instrumentation setup completed")
            RequestsInstrumentor().instrument()
            logger.info("HTTP requests instrumentation setup completed")
            self._instrument_httpx()
            LoggingInstrumentor().instrument(set_logging_format=True, log_level=logging.INFO)
            logger.info("Logging instrumentation setup completed")
            logger.info("OpenTelemetry instrumentations setup completed")
        except Exception as exc:
            logger.error("Failed to setup instrumentations: %s", exc)
            logger.error("Traceback: %s", traceback.format_exc())

    def _instrument_httpx(self) -> None:
        """Instrument httpx when the optional instrumentation package is installed."""
        try:
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

            HTTPXClientInstrumentor().instrument()
            logger.info("HTTPX instrumentation setup completed")
        except ImportError:
            logger.warning("HTTPX instrumentation not available - package not installed")
        except Exception as exc:
            logger.error("Failed to instrument HTTPX: %s", exc)
            logger.error("Traceback: %s", traceback.format_exc())

    def shutdown(self) -> None:
        """Flush and shut down configured telemetry providers."""
        try:
            if self.tracer_provider:
                self.tracer_provider.shutdown()
            if self.meter_provider:
                self.meter_provider.shutdown()
            logger.info("Telemetry providers shutdown completed")
            self._is_setup = False
        except Exception as exc:
            logger.error("Failed to shutdown telemetry providers: %s", exc)


telemetry_config = TelemetryConfig()
