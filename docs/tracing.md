# Trace and Span Names

This page lists every span you see in Jaeger for this project: which service records it, who chose its name (our code or a library), and what it stands for. Use it to match a trace in Jaeger to the code. How to start Jaeger and run a trace is in [infra.md](infra.md#tracing-opentelemetry-and-jaeger); the setup code is [src/shared/telemetry.py](../src/shared/telemetry.py).

## Contents

- [Background: Logs, Metrics and Traces](#background-logs-metrics-and-traces)
- [How to Read a Name](#how-to-read-a-name)
- [The Two Traces](#the-two-traces)
  - [A Harness Run](#a-harness-run)
  - [An Approve CLI Run](#an-approve-cli-run)
- [Every Span](#every-span)
- [The Attributes We Set](#the-attributes-we-set)
- [How Jaeger Shows GenAI Spans](#how-jaeger-shows-genai-spans)
- [Notes](#notes)

## Background: Logs, Metrics and Traces

A system reports what it does in three main kinds of telemetry. OpenTelemetry calls them **signals** ([OpenTelemetry: Signals](https://opentelemetry.io/docs/concepts/signals/)):

- **Logs:** single events. "This happened at this time", with any text or fields.
- **Metrics:** numbers over time, for example requests per second or error rate.
- **Traces:** one piece of work followed across services, as a tree of timed spans.

OpenTelemetry also lists **baggage** as a signal: key-value pairs that travel with a request from service to service. It is context passed along, not a record that is stored. Profiles are still in development.

Each signal usually has its own kind of backend. Jaeger is a tracing backend: it stores spans only, no log lines. A log platform such as Splunk, Elastic or Grafana Loki stores log events and searches across them. The two are linked by the trace ID: a log line that carries it leads to its trace.

**In this project:**

- **Logs:** the audit lines (FR8). The Identity API, the MCP server and the executor print them as JSON to stdout, and also send them over OTLP to Loki, a log store, as OpenTelemetry log records. The harness's progress lines go there too, as operational lines. Each line carries the trace ID and span ID of the span that wrote it, so Grafana links a line to its trace in Jaeger ([infra.md](infra.md#audit-logs-loki-and-grafana)).
- **Metrics:** none yet. The token counts are span attributes, not metrics.
- **Traces:** every service sends its spans over OTLP to Jaeger. The rest of this page is about them.

## How to Read a Name

- **Trace name:** Jaeger names a trace after its root span, as `service: span name`, for example `harness: invoke_agent guardian`. While the root span has not arrived yet, Jaeger may show the trace ID instead: a span is sent only when it ends, and the root ends last.
- **Service name:** the process that recorded the span, from `OTEL_SERVICE_NAME` in [docker-compose.yml](../docker-compose.yml). A client span (a call out) belongs to the caller: the `anthropic.messages.create` spans belong to `harness`, because the Anthropic SDK runs inside the harness. Claude sends no spans of its own.
- **Span name:** three names come from our code, all defined in [span_names.py](../src/shared/span_names.py), with the attributes our code sets. All others come from the libraries: the Anthropic SDK, the MCP SDK, and the OpenTelemetry instrumentations for urllib and FastAPI.
- **The naming rule:** OpenTelemetry asks for short, low-cardinality span names: an operation, never an ID. IDs go into attributes. Our three names follow that rule as a verb and an object; no standard defines them.

## The Two Traces

### A Harness Run

One run of `docker compose run --rm harness` is one trace. The model calls and the MCP requests repeat once per round and per tool call. The first MCP request is the MCP SDK's handshake, `server/discover`, which agrees on the protocol version: the harness opens the MCP connection inside the run's span, so the handshake is part of the run's trace.

```text
invoke_agent guardian                               [harness]       our code
├─ MCP send server/discover                         [harness]       MCP SDK
│  └─ server/discover                               [mcp-server]    MCP SDK
├─ MCP send tools/list                              [harness]       MCP SDK
│  └─ tools/list                                    [mcp-server]    MCP SDK
├─ anthropic.messages.create                        [harness]       Anthropic SDK
├─ MCP send tools/call scan_registry                [harness]       MCP SDK
│  └─ tools/call scan_registry                      [mcp-server]    MCP SDK
│     ├─ GET   (sidecar: a token; Graph: reads)     [mcp-server]    urllib
│     ├─ POST  (Identity API)                       [mcp-server]    urllib
│     │  └─ POST /findings                          [identity-api]  FastAPI
│     └─ GET   (Identity API)                       [mcp-server]    urllib
│        └─ GET /agents/{agent_id}/disableRequests  [identity-api]  FastAPI
├─ MCP send tools/call get_agent                    [harness]       MCP SDK
│  └─ tools/call get_agent                          [mcp-server]    MCP SDK
│     └─ GET   (sidecar, Graph)                     [mcp-server]    urllib
└─ MCP send tools/call submit_disable_agent_request [harness]       MCP SDK
   └─ tools/call submit_disable_agent_request       [mcp-server]    MCP SDK
      ├─ GET   (sidecar, Graph)                     [mcp-server]    urllib
      └─ POST  (Identity API)                       [mcp-server]    urllib
         └─ POST /agents/{agent_id}/disableRequests [identity-api]  FastAPI
```

### An Approve CLI Run

One run of `docker compose run --rm --service-ports approve` is one trace. Each decision's span links to the span that created the request, in the harness's trace (Jaeger shows the link under the span's References).

```text
review disable_requests                                       [approve-cli]   our code
├─ GET   (Identity API)                                       [approve-cli]   urllib
│  └─ GET /disableRequests                                    [identity-api]  FastAPI
└─ decide disable_request          link: the request's span   [approve-cli]   our code
   ├─ POST  (Identity API)                                    [approve-cli]   urllib
   │  └─ POST .../disableRequests/{request_id}/decision       [identity-api]  FastAPI
   └─ POST  (executor, after an approval)                     [approve-cli]   urllib
      └─ POST .../disableRequests/{request_id}/delegation     [executor]      FastAPI
         ├─ GET    (sidecar: check Tc)                        [executor]      urllib
         ├─ GET    (Identity API: read the request)           [executor]      urllib
         │  └─ GET .../disableRequests/{request_id}           [identity-api]  FastAPI
         ├─ GET    (sidecar, Graph: check the agent again)    [executor]      urllib
         ├─ POST   (Identity API: the claim)                  [executor]      urllib
         │  └─ POST .../disableRequests/{request_id}/execution [identity-api] FastAPI
         ├─ GET    (sidecar: the OBO exchange)                [executor]      urllib
         ├─ PATCH  (Graph: the disable)                       [executor]      urllib
         ├─ GET    (Graph: read it back)                      [executor]      urllib
         └─ POST   (Identity API: the outcome)                [executor]      urllib
            └─ POST .../disableRequests/{request_id}/outcome  [identity-api]  FastAPI
```

## Every Span

| Span name | Service | Named by | Kind | What it is |
|---|---|---|---|---|
| `invoke_agent guardian` | harness | our code ([span_names.py](../src/shared/span_names.py), used in [harness.py](../src/guardian/harness.py)) | internal | One run of the agent loop: the root of the harness's trace. The name follows the GenAI agent span convention: `invoke_agent {gen_ai.agent.name}` |
| `anthropic.messages.create` | harness | Anthropic SDK | client | One model call |
| `MCP send server/discover`, `MCP send tools/list`, `MCP send tools/call {tool}` | harness | MCP SDK | client | One MCP request, as the harness sends it. The SDK puts the trace context into the request's `_meta` (MCP SEP-414) |
| `server/discover`, `tools/list`, `tools/call {tool}` | mcp-server | MCP SDK | server | The same MCP request, as the MCP server runs it |
| `GET`, `POST`, `PATCH` | mcp-server, executor, approve-cli | urllib instrumentation | client | One outgoing HTTP call: to a sidecar, Microsoft Graph, the Identity API or the executor. The URL is in `http.url`. It adds the `traceparent` header |
| `{METHOD} {route}`, for example `POST /findings` | identity-api, executor | FastAPI instrumentation | server | One incoming HTTP call. It reads the `traceparent` header, so it joins the caller's trace. `/health` is not traced |
| `review disable_requests` | approve-cli | our code ([span_names.py](../src/shared/span_names.py), used in [approve.py](../src/approve_cli/approve.py)) | internal | One run of the CLI: the root of its trace |
| `decide disable_request` | approve-cli | our code ([span_names.py](../src/shared/span_names.py), used in [approve.py](../src/approve_cli/approve.py)) | internal | One decision: approve or deny, and after an approval the delegation to the executor. It starts after the person answers, so it times the calls, not the thinking. A skip makes no span |

## The Attributes We Set

The libraries set their own attributes (the model, the tokens, the HTTP method, the URL and the status). These are the ones our code sets, all defined in [span_names.py](../src/shared/span_names.py).

| Attribute | On span | What it holds | Source |
|---|---|---|---|
| `gen_ai.operation.name` | `invoke_agent guardian` | `invoke_agent` | GenAI semantic conventions |
| `gen_ai.agent.name` | `invoke_agent guardian` | `guardian`: the whole agent, not one container | GenAI semantic conventions |
| `gen_ai.provider.name` | `invoke_agent guardian` | `anthropic` | GenAI semantic conventions |
| `gen_ai.request.model` | `invoke_agent guardian` | The model from the settings | GenAI semantic conventions |
| `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` | `invoke_agent guardian` | The totals over the run's model calls | GenAI semantic conventions |
| `error.type` | `invoke_agent guardian` | Why a run stopped, for example `ModelStopped` | OpenTelemetry general conventions |
| `guardian.rounds` | `invoke_agent guardian` | How many model calls the run made | ours |
| `guardian.disable_requests.shown` | `review disable_requests` | How many pending requests the CLI showed | ours |
| `guardian.disable_request.id` | `decide disable_request` | The request decided | ours |
| `guardian.decision` | `decide disable_request` | `approve` or `deny` | ours |

Our own attributes carry the `guardian.` prefix, so they cannot clash with a standard name.

## How Jaeger Shows GenAI Spans

Jaeger's UI gives a span an icon and a GenAI tab when the span carries the attribute `gen_ai.operation.name`. It reads the attribute's value, not the span's name, so renaming a span changes nothing as long as the attribute stays.

1. **Read:** for each span, the UI reads `gen_ai.operation.name`.
2. **Classify:** it maps the value to a kind of span (the UI's `detect.ts`).
3. **Draw:** each kind gets its own icon in the trace view:
   - **Agent** (`invoke_agent`, set by our code on `invoke_agent guardian`): a robot.
   - **Model call** (`chat`, set by the Anthropic SDK on `anthropic.messages.create`): a graduation cap.
   - **Tool call** (`execute_tool`, set by the MCP SDK on the MCP server's `tools/call {tool}`): a wrench.
4. **Fill the GenAI tab:** on a span with an icon, the GenAI tab shows the other `gen_ai.*` attributes: the agent's name (`gen_ai.agent.name`), the operation, the provider, the model, and the input and output tokens.
5. **Sum the trace:** the trace header shows "GenAI calls" (agents, model calls and tool calls together). Clicking it opens a summary with each count, the recorded tokens and the failed calls.

So a span without `gen_ai.operation.name` is a plain span to Jaeger: no icon and no GenAI tab, even if its name looks like a model call.

**The Conversation view.** On an `anthropic.messages.create` span, the GenAI tab also shows a Conversation: the `system` field (from `gen_ai.system_instructions`), then each message of the request and the answer (from `gen_ai.input.messages` and `gen_ai.output.messages`), each under its role: SYSTEM, USER or ASSISTANT. A message with several parts shows them as "Part 1 of N". Jaeger turns each part into one line of text (`genAiData.ts` in the Jaeger UI source):

- **`→ name(arguments)`:** a `tool_call` part, which is a `tool_use` block in the Messages API. The model asks for a tool, so it sits under ASSISTANT, for example `→ scan_registry({})`.
- **`← result`:** a `tool_call_response` part, which is a `tool_result` block. The harness sends the tool's result back, so it sits under USER, for example `← [{"type":"text", ...}]`.
- **Plain text:** a `text` part, or a `reasoning` part (a `thinking` block). A `reasoning` part is empty in a plain run: the harness asks for a readable summary of the thinking only in the `GUARDIAN_TRACE` modes, so the block carries no text.

So the arrows are Jaeger's, not part of the data: the span holds the parts as JSON, and the arrow only says which way the part goes, to a tool (`→`) or back from one (`←`). How the roles and blocks work is in [The Messages API](messages-api.md#9-in-the-guardian-one-real-run-round-by-round). The GenAI conventions have the status "Development", so a later Jaeger version may map the values differently.

## Notes

- **What spans hold:** never a token: the instrumentations record no headers and no bodies, and the MCP SDK records no tool arguments or results. The `anthropic.messages.create` spans do hold content, because the harness sets `ANTHROPIC_OPEN_TELEMETRY_TRACES_CONTENT_MODE: content` ([docker-compose.yml](../docker-compose.yml)): the messages the model was sent (the system prompt, the task and the tool results) and its answer, up to 750,000 bytes per span. That content is untrusted text (it includes the planted prompt injection) and tenant data. The SDK's other value, `metadata_only`, is its default.
- **The Anthropic SDK's span** also carries the Anthropic organization ID and workspace ID (`anthropic.organization.id`, `anthropic.workspace.id`). They are account identifiers, not secrets, but they leave the machine if the spans go to a hosted viewer.
- **Old HTTP attribute names:** the urllib and FastAPI instrumentations write the older names (`http.method`, `http.url`, `http.status_code`), while the Anthropic SDK writes the stable ones (`http.request.method`, `url.full`). Setting `OTEL_SEMCONV_STABILITY_OPT_IN=http` makes the instrumentations write the stable names; it is not set yet.
- **The sidecars send no spans:** the Entra SDK sidecar image (`auth-sidecar` 1.1.2) has no OpenTelemetry SDK or exporter ([its source](https://github.com/AzureAD/microsoft-identity-web/tree/master/src/Microsoft.Identity.Web.Sidecar)), so there is no `sidecar` service in Jaeger, and `OTEL_*` variables on it would change nothing. A call to a sidecar shows only as the caller's client span (`GET 127.0.0.1:5000` under mcp-server or executor), with its duration; the sidecar's own call to Entra is not visible. Building our own sidecar image with OpenTelemetry would mean changing Microsoft's security component, so it is left out.
- **Status of the GenAI conventions:** "Development", so their names can still change between versions.
- **The trace ID in audit lines:** every audit line carries the trace ID and span ID of the span that wrote it, so `docker compose logs identity-api mcp-server executor | grep <trace ID>` finds a trace's audit lines.
