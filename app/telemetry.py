"""Bounded, content-free application telemetry; one provider set per process."""
import logging
from urllib.parse import unquote

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.config import Settings

meter = metrics.get_meter("network-concierge")
search_counter = meter.create_counter("network.search.requests", unit="1")
search_latency = meter.create_histogram("network.search.latency", unit="ms")
fallback_counter = meter.create_counter("network.retrieval.fallbacks", unit="1")
connection_counter = meter.create_counter("network.connection.requests", unit="1")
accepted_counter = meter.create_counter("network.connection.accepted", unit="1")
ingestion_failures = meter.create_counter("network.ingestion.failures", unit="1")
agent_errors = meter.create_counter("network.agent.errors", unit="1")
operation_latency = meter.create_histogram("network.operation.latency", unit="ms")
# Denied requests by reason (csrf, rate_limit:<rule>, unauthenticated, forbidden) and route template. Alerted on in Dash0.
security_rejections = meter.create_counter("network.security.rejections", unit="1")
_providers = None


def exporter_config(settings: Settings) -> tuple[str | None, dict]:
    base = settings.dash0_otlp_http_base or settings.otel_exporter_otlp_endpoint
    headers = {}
    for entry in (settings.otel_exporter_otlp_headers or "").split(","):
        if "=" in entry:
            key, value = entry.split("=", 1)
            headers[key.strip()] = unquote(value.strip())
    if settings.dash0_auth_token:
        headers.update({"Authorization": "Bearer " + settings.dash0_auth_token, "Dash0-Dataset": settings.dash0_dataset})
    if base:
        base = base.rstrip("/")
        # Accept the old full trace endpoint while correctly separating signals.
        for suffix in ("/v1/traces", "/v1/metrics"):
            if base.endswith(suffix):
                base = base[:-len(suffix)]
    return base, headers


def configure_telemetry(app, settings: Settings):
    global _providers
    if _providers is None:
        resource = Resource.create({"service.name": settings.otel_service_name, "service.version": settings.app_version,
                                    "deployment.environment.name": settings.environment})
        tp = TracerProvider(resource=resource)
        base, headers = exporter_config(settings)
        readers = []
        if base:
            tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=base + "/v1/traces", headers=headers)))
            readers.append(PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=base + "/v1/metrics", headers=headers), export_interval_millis=5000))
        mp = MeterProvider(resource=resource, metric_readers=readers)
        trace.set_tracer_provider(tp)
        metrics.set_meter_provider(mp)
        providers = [tp, mp]
        if base:
            # Application warnings (already content-free: fallbacks, queue retries) also go to Dash0 as OTLP logs,
            # correlated with the active trace. Only the `app` logger tree: library logs can carry document ids/URLs.
            lp = LoggerProvider(resource=resource)
            lp.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=base + "/v1/logs", headers=headers)))
            set_logger_provider(lp)
            app_logger = logging.getLogger("app")
            app_logger.addHandler(LoggingHandler(level=logging.WARNING, logger_provider=lp))
            providers.append(lp)
        _providers = tuple(providers)
    if app is not None:
        instrument_app(app)
    return _providers


def instrument_app(app):
    # Install middleware before startup; exporters are initialized in the lifespan.
    # Do not attach token-bearing URLs to automatic spans. No HTTP client auto instrumentation.
    FastAPIInstrumentor.instrument_app(app, excluded_urls=r".*/connect/.*,.*/api/connection-requests/.*")


def shutdown_telemetry():
    if _providers:
        for provider in _providers:
            provider.force_flush()
            provider.shutdown()


def tracer():
    return trace.get_tracer("network-concierge")
