"""OpenTelemetry for every service (NFR4, FR8): the SDK, its OTLP exporters, the trace IDs for
audit lines, and the audit lines themselves. Each service's image copies src/shared
(docker-compose.yml, additional_contexts). The names our code gives spans and attributes are in
span_names.py.

The libraries create spans through the OpenTelemetry API: the Anthropic SDK per model call, the
MCP SDK per MCP request, and the instrumentations each service turns on (urllib for outgoing HTTP
calls, FastAPI for incoming ones). Without an SDK, those spans are dropped, and the trace ID is all
zeros. setup_tracing turns the SDK on.

Three standard variables decide what happens:
    OTEL_SERVICE_NAME                 set: the SDK is on, so every span and audit line has a real trace ID
    OTEL_EXPORTER_OTLP_ENDPOINT       set: the spans also go to that address over OTLP/HTTP (+ /v1/traces)
    OTEL_EXPORTER_OTLP_LOGS_ENDPOINT  set: the audit lines also go to that address, as OpenTelemetry
                                      log records (Loki in docker-compose.yml). Used as given, with no
                                      path added: Jaeger takes traces only, so logs need their own address.
And one of our own:
    HTTP_BODY_LOG=1                   with the logs address set: every outgoing urllib call is also
                                      logged with its bodies and its Authorization header, tokens included.
                                      Off by default (THREATMODEL.md, A10).
Unit tests set none, so they send nothing. The SDK reads the other OTEL_* variables itself; the
sampler defaults to parentbased_always_on, and the propagator to W3C traceparent.

The trace ID ties the spans and the audit lines of one piece of work together. It comes from the
caller's traceparent, so it is a pointer for finding records, never proof of who did what: that
comes from the checked token in the audit line (THREATMODEL.md, A9).
"""

import io
import json
import logging
import os
import sys
import urllib.request
import urllib.response
from datetime import datetime, timezone

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Link, SpanContext, TraceFlags, format_span_id, format_trace_id

# Set once setup_log_export ran. Only what our code chooses is sent as log records: the audit lines,
# and the loggers passed to export_logging. Not uvicorn's access lines, not the libraries' logging.
_logger_provider = None
_audit_logger = None


def setup_tracing() -> bool:
    """Registers the SDK's tracer provider when OTEL_SERVICE_NAME is set, and an exporter that sends
    spans in batches when OTEL_EXPORTER_OTLP_ENDPOINT is set too. When OTEL_EXPORTER_OTLP_LOGS_ENDPOINT
    is set, the audit lines are sent as log records as well (setup_log_export). Each provider
    sends its last batch when the process exits. Returns whether the SDK is on."""
    if not os.environ.get("OTEL_SERVICE_NAME"):
        return False
    provider = TracerProvider()   # its resource (service.name) comes from the OTEL_* variables
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    if os.environ.get("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        setup_log_export(BatchLogRecordProcessor(OTLPLogExporter()))
        setup_http_body_log()   # only when HTTP_BODY_LOG is 1
    return True


def setup_log_export(processor) -> None:
    """Sends every audit line, and every line of a logger passed to export_logging, through processor
    as an OpenTelemetry log record. A record takes the current span's trace ID and span ID, as a span
    does. The logs API and SDK are still in opentelemetry._logs and opentelemetry.sdk._logs: their
    status is not stable yet."""
    global _logger_provider, _audit_logger
    from opentelemetry.sdk._logs import LoggerProvider
    _logger_provider = LoggerProvider()   # the same resource as the spans: service.name from OTEL_SERVICE_NAME
    _logger_provider.add_log_record_processor(processor)
    _audit_logger = _logger_provider.get_logger("audit")


class _LogRecordHandler(logging.Handler):
    """A Python logging handler that sends each line as an OpenTelemetry log record. The SDK's own
    LoggingHandler is deprecated in favour of a separate package, and this needs only a few lines."""

    def __init__(self, otel_logger):
        super().__init__()
        self.otel_logger = otel_logger

    def emit(self, record: logging.LogRecord) -> None:
        from opentelemetry._logs import SeverityNumber
        try:
            severity = SeverityNumber[{"WARNING": "WARN", "CRITICAL": "FATAL"}.get(record.levelname, record.levelname)]
        except KeyError:
            severity = SeverityNumber.UNSPECIFIED
        try:
            self.otel_logger.emit(body=record.getMessage(), severity_number=severity,
                                  severity_text=record.levelname, attributes={"logger": record.name})
        except Exception:   # a failed export must never stop the program that logs
            self.handleError(record)


BODY_LIMIT = 100_000   # characters kept per body, so a large Graph page stays one Loki line


def _text(data) -> str:
    """A body as text, cut at BODY_LIMIT. Bytes are decoded as UTF-8."""
    if data is None:
        return ""
    text = data.decode("utf-8", errors="replace") if isinstance(data, (bytes, bytearray)) else str(data)
    return text if len(text) <= BODY_LIMIT else text[:BODY_LIMIT] + f"... (cut at {BODY_LIMIT} characters)"


class _HttpBodyLog(urllib.request.BaseHandler):
    """HTTP_BODY_LOG=1: one log record per outgoing urllib call, with the method, the URL, the
    status, the Authorization header, the request body and the response body. It reads the
    response, logs it, and hands the caller an identical response, so the caller's code is not
    changed. It runs before urllib turns an error status into an HTTPError, so a 403 or a 409 is
    logged too, and the HTTPError still carries the body."""

    handler_order = 900   # before HTTPErrorProcessor (1000), which raises HTTPError

    def __init__(self, otel_logger):
        self.otel_logger = otel_logger

    def http_response(self, request, response):
        data = response.read()
        record = {
            "method": request.get_method(), "url": request.full_url, "status": response.status,
            # Deliberately recorded, tokens included (THREATMODEL.md, A10): this log is for debugging only.
            "authorization": request.get_header("Authorization") or request.unredirected_hdrs.get("Authorization"),
            "request_body": _text(request.data), "response_body": _text(data),
        }
        try:
            self.otel_logger.emit(body=json.dumps(record), attributes={
                "http.request.method": record["method"], "url.full": record["url"],
                "http.response.status_code": record["status"]})
        except Exception:   # a failed export must never break the call
            pass
        copy = urllib.response.addinfourl(io.BytesIO(data), response.headers, response.url, response.status)
        copy.msg = response.msg
        return copy

    https_response = http_response


def setup_http_body_log() -> bool:
    """Turns the body log on for every urllib call in this process, when HTTP_BODY_LOG is 1 and
    setup_tracing turned the log export on. Off by default: bodies hold tenant data, and with the
    Authorization header, live tokens. Returns whether it did."""
    if os.environ.get("HTTP_BODY_LOG") != "1" or _logger_provider is None:
        return False
    urllib.request.install_opener(urllib.request.build_opener(_HttpBodyLog(_logger_provider.get_logger("http"))))
    return True


def export_logging(logger_name: str) -> bool:
    """Also sends the lines of a Python logger (and its children) as OpenTelemetry log records, after
    setup_tracing turned the export on. Its other handlers, such as the terminal, stay as they are.
    Returns whether it did."""
    if _logger_provider is None:
        return False
    logging.getLogger(logger_name).addHandler(_LogRecordHandler(_logger_provider.get_logger(logger_name)))
    return True


def trace_fields() -> dict:
    """The current span's trace ID and span ID, for an audit line, as W3C hex strings. Empty when
    no span is active or the SDK is off, so a line never carries an ID of zeros."""
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return {}
    return {"trace_id": format_trace_id(context.trace_id), "span_id": format_span_id(context.span_id)}


def audit(**fields) -> None:
    """Write one audit line (FR8) with the current trace ID and span ID (NFR4): as JSON on stdout,
    and, when setup_tracing turned it on, as a log record whose body is the same JSON line. Every
    service that audits uses this one function. Never pass a token in here."""
    line = json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), **trace_fields(), **fields})
    print(line, file=sys.stdout, flush=True)
    if _audit_logger is not None:
        from opentelemetry._logs import SeverityNumber
        # The event as an attribute too, so a log viewer can filter on it without parsing the line.
        _audit_logger.emit(body=line, severity_number=SeverityNumber.INFO, severity_text="INFO",
                           attributes={"event": str(fields.get("event", ""))})


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
