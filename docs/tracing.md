# Trace and Span Names

This page lists every span you see in Jaeger for this project: which service records it, who chose its name (our code or a library), and what it stands for. Use it to match a trace in Jaeger to the code. How to start Jaeger and run a trace is in [infra.md](infra.md#tracing-opentelemetry-and-jaeger); the setup code is [src/shared/telemetry.py](../src/shared/telemetry.py).

## Contents

- [How to Read a Name](#how-to-read-a-name)
- [The Two Traces](#the-two-traces)
  - [A Harness Run](#a-harness-run)
  - [An Approve CLI Run](#an-approve-cli-run)
- [Every Span](#every-span)
- [The Attributes We Set](#the-attributes-we-set)
- [Notes](#notes)

## How to Read a Name

- **Trace name:** Jaeger names a trace after its root span, as `service: span name`, for example `harness: invoke_agent guardian`. While the root span has not arrived yet, Jaeger may show the trace ID instead: a span is sent only when it ends, and the root ends last.
- **Service name:** the process that recorded the span, from `OTEL_SERVICE_NAME` in [docker-compose.yml](../docker-compose.yml). A client span (a call out) belongs to the caller: the `anthropic.messages.create` spans belong to `harness`, because the Anthropic SDK runs inside the harness. Claude sends no spans of its own.
- **Span name:** three names come from our code, all defined in [span_names.py](../src/shared/span_names.py), with the attributes our code sets. All others come from the libraries: the Anthropic SDK, the MCP SDK, and the OpenTelemetry instrumentations for urllib and FastAPI.
- **The naming rule:** OpenTelemetry asks for short, low-cardinality span names: an operation, never an ID. IDs go into attributes. Our three names follow that rule as a verb and an object; no standard defines them.

## The Two Traces

### A Harness Run

One run of `docker compose run --rm harness` is one trace. The model calls and the MCP requests repeat once per round and per tool call.

```text
invoke_agent guardian                               [harness]       our code
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
| `MCP send tools/list`, `MCP send tools/call {tool}` | harness | MCP SDK | client | One MCP request, as the harness sends it. The SDK puts the trace context into the request's `_meta` (MCP SEP-414) |
| `tools/list`, `tools/call {tool}` | mcp-server | MCP SDK | server | The same MCP request, as the MCP server runs it |
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

## Notes

- **What spans hold:** never a token: the instrumentations record no headers and no bodies, and the MCP SDK records no tool arguments or results. The `anthropic.messages.create` spans do hold content, because the harness sets `ANTHROPIC_OPEN_TELEMETRY_TRACES_CONTENT_MODE: content` ([docker-compose.yml](../docker-compose.yml)): the messages the model was sent (the system prompt, the task and the tool results) and its answer, up to 750,000 bytes per span. That content is untrusted text (it includes the planted prompt injection) and tenant data. The SDK's other value, `metadata_only`, is its default.
- **The Anthropic SDK's span** also carries the Anthropic organization ID and workspace ID (`anthropic.organization.id`, `anthropic.workspace.id`). They are account identifiers, not secrets, but they leave the machine if the spans go to a hosted viewer.
- **Old HTTP attribute names:** the urllib and FastAPI instrumentations write the older names (`http.method`, `http.url`, `http.status_code`), while the Anthropic SDK writes the stable ones (`http.request.method`, `url.full`). Setting `OTEL_SEMCONV_STABILITY_OPT_IN=http` makes the instrumentations write the stable names; it is not set yet.
- **Status of the GenAI conventions:** "Development", so their names can still change between versions.
- **The trace ID in audit lines:** every audit line carries the trace ID and span ID of the span that wrote it, so `docker compose logs identity-api mcp-server executor | grep <trace ID>` finds a trace's audit lines.
