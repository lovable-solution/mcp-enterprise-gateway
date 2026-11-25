"""Repository-wide pytest fixtures.

OpenTelemetry providers are process-global and can be installed only once, so they are
installed here, before any service under test starts, with in-memory exporters: tests can
then assert on the spans and metrics the code under test emits. (``configure_telemetry``
sees an SDK provider already in place and leaves it alone.)
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


@dataclass(frozen=True)
class TelemetryCapture:
    spans: InMemorySpanExporter
    metric_reader: InMemoryMetricReader

    def finished_spans(self) -> list[ReadableSpan]:
        return list(self.spans.get_finished_spans())

    def metric_points(self, name: str) -> list[tuple[dict[str, Any], float]]:
        """(attributes, value) of every data point of metric ``name`` collected so far."""
        data = self.metric_reader.get_metrics_data()
        points: list[tuple[dict[str, Any], float]] = []
        for resource_metrics in data.resource_metrics if data else []:
            for scope_metrics in resource_metrics.scope_metrics:
                for metric in scope_metrics.metrics:
                    if metric.name == name:
                        for point in metric.data.data_points:
                            value = getattr(point, "value", None)
                            if value is None:  # histogram points carry a count instead
                                value = getattr(point, "count", 0)
                            points.append((dict(point.attributes or {}), float(value or 0)))
        return points


@pytest.fixture(scope="session", autouse=True)
def telemetry() -> Iterator[TelemetryCapture]:
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider(resource=Resource.create({"service.name": "pytest"}))
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
    yield TelemetryCapture(spans=exporter, metric_reader=reader)
    tracer_provider.shutdown()


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"
