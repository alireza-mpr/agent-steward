"""Unit tests for the shared OpenTelemetry setup (NFR4): when the SDK is on, the trace IDs for audit
lines, and the span link between the two phases. No exporter and no network."""

import json

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


# --- Audit lines as OpenTelemetry log records (FR8) ---

@pytest.fixture
def audit_records():
    """The audit logger, sending through an SDK processor that keeps every record in memory."""
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
    exporter = InMemoryLogRecordExporter()
    telemetry.setup_log_export(SimpleLogRecordProcessor(exporter))
    yield exporter
    telemetry._audit_logger = telemetry._logger_provider = None


def test_an_audit_line_goes_to_stdout_and_as_a_log_record_with_the_same_trace(audit_records, capsys):
    with TracerProvider().get_tracer("test").start_as_current_span("work") as span:
        telemetry.audit(event="disable_failed", http_status=403)

    stdout_line = json.loads(capsys.readouterr().out)
    [record] = [readable.log_record for readable in audit_records.get_finished_logs()]
    assert json.loads(record.body) == stdout_line                       # the same line in both places
    assert stdout_line["event"] == "disable_failed" and stdout_line["http_status"] == 403
    assert record.attributes["event"] == "disable_failed"               # filterable without parsing
    assert record.trace_id == span.get_span_context().trace_id          # the link to the trace
    assert record.span_id == span.get_span_context().span_id


def test_without_the_export_an_audit_line_goes_only_to_stdout(capsys):
    telemetry.audit(event="tool_call")

    assert json.loads(capsys.readouterr().out)["event"] == "tool_call"
    assert telemetry._audit_logger is None


def test_an_exported_logger_sends_its_lines_with_the_current_trace(audit_records, capsys):
    import logging
    logger = logging.getLogger("test.progress")
    logger.setLevel(logging.INFO)
    assert telemetry.export_logging("test.progress")

    with TracerProvider().get_tracer("test").start_as_current_span("work") as span:
        logger.warning("round 1: stopped with tool_use")
    logger.handlers = []

    [record] = [readable.log_record for readable in audit_records.get_finished_logs()]
    assert record.body == "round 1: stopped with tool_use"
    assert record.severity_text == "WARNING"
    assert record.attributes["logger"] == "test.progress"
    assert record.trace_id == span.get_span_context().trace_id


def test_without_the_export_a_logger_is_left_alone():
    assert telemetry.export_logging("test.other") is False


# --- HTTP_BODY_LOG: every outgoing urllib call, with its bodies (debugging only) ---

@pytest.fixture
def local_api():
    """A local HTTP server: 200 with a JSON body for /ok, 403 for anything else."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            status, body = (200, b'{"status": "running"}') if self.path == "/ok" else (403, b'{"error": "denied"}')
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def body_log(audit_records, monkeypatch):
    import urllib.request
    monkeypatch.setenv("HTTP_BODY_LOG", "1")
    assert telemetry.setup_http_body_log()
    yield audit_records
    urllib.request.install_opener(None)


def post(url):
    import urllib.request
    request = urllib.request.Request(url, data=b'{"outcome": "succeeded"}', method="POST",
                                     headers={"Authorization": "Bearer test-token"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def test_a_call_is_logged_with_both_bodies_and_the_caller_still_gets_the_answer(body_log, local_api):
    assert post(local_api + "/ok") == {"status": "running"}   # the caller's code sees no change

    [record] = [readable.log_record for readable in body_log.get_finished_logs()]
    logged = json.loads(record.body)
    assert (logged["method"], logged["url"], logged["status"]) == ("POST", local_api + "/ok", 200)
    assert logged["authorization"] == "Bearer test-token"
    assert json.loads(logged["request_body"]) == {"outcome": "succeeded"}
    assert json.loads(logged["response_body"]) == {"status": "running"}


def test_an_error_status_is_logged_and_still_raised_with_its_body(body_log, local_api):
    import urllib.error
    with pytest.raises(urllib.error.HTTPError) as error:
        post(local_api + "/denied")

    assert error.value.code == 403 and json.load(error.value) == {"error": "denied"}
    [record] = [readable.log_record for readable in body_log.get_finished_logs()]
    assert json.loads(record.body)["status"] == 403


def test_the_body_log_is_off_unless_asked_for(audit_records, monkeypatch):
    monkeypatch.delenv("HTTP_BODY_LOG", raising=False)
    assert telemetry.setup_http_body_log() is False
