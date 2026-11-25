"""OpenTelemetry bootstrap shared by every Python service of the platform.

Each service calls :func:`configure_telemetry` once at start-up. Exporters follow the
standard ``OTEL_*`` environment variables (``OTEL_EXPORTER_OTLP_ENDPOINT``,
``OTEL_METRIC_EXPORT_INTERVAL``, ``OTEL_SDK_DISABLED``...); this module adds the service
identity and installs the SDK providers, exporting traces and metrics over OTLP/HTTP to
the collector. When an SDK provider is already installed (tests install in-memory ones),
it is left untouched and the caller does not own its shutdown.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

SERVICE_NAMESPACE = "mcp-enterprise-gateway"

__all__ = ["SERVICE_NAMESPACE", "Telemetry", "configure_telemetry"]


@dataclass(frozen=True, slots=True)
class Telemetry:
    """Handle on the active providers; ``owned`` providers are shut down by this handle."""

    tracer_provider: TracerProvider | None
    meter_provider: MeterProvider | None
    owned: bool

    def shutdown(self) -> None:
        """Flush and stop the providers this handle installed (no-op otherwise)."""
        if not self.owned:
            return
        if self.meter_provider is not None:
            self.meter_provider.shutdown()
        if self.tracer_provider is not None:
            self.tracer_provider.shutdown()


def configure_telemetry(
    service_name: str, service_version: str, *, environment: str | None = None
) -> Telemetry:
    """Install global tracer and meter providers exporting OTLP/HTTP, once per process."""
    if os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() == "true":
        return Telemetry(tracer_provider=None, meter_provider=None, owned=False)

    current = trace.get_tracer_provider()
    if isinstance(current, TracerProvider):
        meter_provider = metrics.get_meter_provider()
        return Telemetry(
            tracer_provider=current,
            meter_provider=meter_provider if isinstance(meter_provider, MeterProvider) else None,
            owned=False,
        )

    attributes: dict[str, str] = {
        "service.name": service_name,
        "service.version": service_version,
        "service.namespace": SERVICE_NAMESPACE,
    }
    if environment:
        attributes["deployment.environment.name"] = environment
    resource = Resource.create(attributes)

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    meter_provider = MeterProvider(
        resource=resource, metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())]
    )
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(meter_provider)
    return Telemetry(tracer_provider=tracer_provider, meter_provider=meter_provider, owned=True)
