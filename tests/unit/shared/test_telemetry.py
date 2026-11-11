"""Unit tests for the shared OpenTelemetry setup (NFR4): when the SDK is on, the trace IDs for audit
lines, and the span link between the two phases. No exporter and no network."""

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import format_span_id, format_trace_id

import telemetry

TRACE_ID = "0af7651916cd43dd8448eb211c80319c"   # the W3C Trace Context spec's example
SPAN_ID = "b7ad6b7169203331"


def test_without_a_service_name_the_sdk_stays_off(monkeypatch):
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)

    assert telemetry.setup_tracing() is False


def test_no_active_span_gives_no_ids_never_zeros():
    assert telemetry.trace_fields() == {}


def test_an_audit_line_gets_the_current_spans_ids_as_w3c_hex():
    with TracerProvider().get_tracer("test").start_as_current_span("work") as span:
        fields = telemetry.trace_fields()

    assert fields == {"trace_id": format_trace_id(span.get_span_context().trace_id),
                      "span_id": format_span_id(span.get_span_context().span_id)}
    assert len(fields["trace_id"]) == 32 and len(fields["span_id"]) == 16


def test_a_link_points_at_the_span_that_created_the_request():
    [link] = telemetry.link_to(TRACE_ID, SPAN_ID)

    assert (format_trace_id(link.context.trace_id), format_span_id(link.context.span_id)) == (TRACE_ID, SPAN_ID)
    assert link.context.is_remote


@pytest.mark.parametrize("trace_id, span_id", [(None, None), (TRACE_ID, None), ("not hex", SPAN_ID),
                                                ("0" * 32, SPAN_ID)])
def test_missing_or_broken_ids_give_no_link(trace_id, span_id):
    assert telemetry.link_to(trace_id, span_id) == []
