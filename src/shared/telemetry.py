"""OpenTelemetry for every service (NFR4, FR8): the SDK, its OTLP exporter, and the trace IDs for
audit lines. Each service's image copies src/shared (docker-compose.yml, additional_contexts). The names our
code gives spans and attributes are in span_names.py.

The libraries create spans through the OpenTelemetry API: the Anthropic SDK per model call, the
MCP SDK per MCP request, and the instrumentations each service turns on (urllib for outgoing HTTP
calls, FastAPI for incoming ones). Without an SDK, those spans are dropped, and the trace ID is all
zeros. setup_tracing turns the SDK on.

Two standard variables decide what happens:
    OTEL_SERVICE_NAME            set: the SDK is on, so every span and audit line has a real trace ID
    OTEL_EXPORTER_OTLP_ENDPOINT  set: the spans also go to that address over OTLP/HTTP (+ /v1/traces)
Unit tests set neither, so they send nothing. The SDK reads the other OTEL_* variables itself; the
sampler defaults to parentbased_always_on, and the propagator to W3C traceparent.

The trace ID ties the spans and the audit lines of one piece of work together. It comes from the
caller's traceparent, so it is a pointer for finding records, never proof of who did what: that
comes from the checked token in the audit line (THREATMODEL.md, A9).
"""

import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Link, SpanContext, TraceFlags, format_span_id, format_trace_id


def setup_tracing() -> bool:
    """Registers the SDK's tracer provider when OTEL_SERVICE_NAME is set, and an exporter that sends
    spans in batches when OTEL_EXPORTER_OTLP_ENDPOINT is set too. The provider sends the last batch
    when the process exits. Returns whether the SDK is on."""
    if not os.environ.get("OTEL_SERVICE_NAME"):
        return False
    provider = TracerProvider()   # its resource (service.name) comes from the OTEL_* variables
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    return True


def trace_fields() -> dict:
    """The current span's trace ID and span ID, for an audit line, as W3C hex strings. Empty when
    no span is active or the SDK is off, so a line never carries an ID of zeros."""
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return {}
    return {"trace_id": format_trace_id(context.trace_id), "span_id": format_span_id(context.span_id)}


def link_to(trace_id: str | None, span_id: str | None) -> list[Link]:
    """A span link to another trace's span: "caused by, in another trace". The approval of a
    disable request links to the span that created the request, in the guardian's run. Empty when
    the IDs are missing or not valid hex."""
    try:
        context = SpanContext(int(trace_id, 16), int(span_id, 16), is_remote=True,
                              trace_flags=TraceFlags(TraceFlags.SAMPLED))
    except (TypeError, ValueError):
        return []
    return [Link(context)] if context.is_valid else []
