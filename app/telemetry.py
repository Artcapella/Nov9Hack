import logging

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.config import Settings

logger = logging.getLogger(__name__)
meter = metrics.get_meter("network-concierge")
search_counter = meter.create_counter("network.search.requests", unit="1")
search_latency = meter.create_histogram("network.search.latency", unit="ms")
connection_counter = meter.create_counter("network.connection.requests", unit="1")


def configure_telemetry(app, settings: Settings) -> None:
    resource = Resource.create({"service.name": settings.otel_service_name, "deployment.environment": settings.environment})
    provider = TracerProvider(resource=resource)
    if settings.otel_exporter_otlp_endpoint:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)))
        reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=settings.otel_exporter_otlp_endpoint))
        metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)


def tracer():
    return trace.get_tracer("network-concierge")
