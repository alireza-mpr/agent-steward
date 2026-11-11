"""Every name our code gives a tracer, a span or a span attribute (NFR4), in one place.

The code reads the names from here, so a name changes in one line, and a typo fails at import
instead of creating a second name in Jaeger. Each image copies this file with telemetry.py.

Only the names our code chooses are here. The libraries name their own spans (the Anthropic SDK,
the MCP SDK, and the urllib and FastAPI instrumentations): docs/tracing.md lists every span.

OpenTelemetry's rule for span names: short and low-cardinality, an operation and never an ID. IDs
go into attributes. Our own attributes carry the guardian. prefix, so they cannot clash with a
standard name.
"""

# --- Tracers: the instrumentation scope, which Jaeger shows as otel.scope.name ---

HARNESS_TRACER = "guardian.harness"
APPROVE_CLI_TRACER = "approve_cli"

# --- The harness: one run of the agent loop (src/guardian/harness.py) ---

# The agent's name: the whole guardian, not one container.
AGENT_NAME = "guardian"
# The GenAI agent span convention names an agent run "{gen_ai.operation.name} {gen_ai.agent.name}".
INVOKE_AGENT_OPERATION = "invoke_agent"
INVOKE_AGENT_SPAN = f"{INVOKE_AGENT_OPERATION} {AGENT_NAME}"
PROVIDER = "anthropic"

# --- The approve CLI: one run, and one span per decision (src/approve_cli/approve.py) ---

REVIEW_SPAN = "review disable_requests"
DECIDE_SPAN = "decide disable_request"

# --- Standard attributes: the GenAI semantic conventions (status Development) and error.type ---

GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_AGENT_NAME = "gen_ai.agent.name"
GEN_AI_PROVIDER_NAME = "gen_ai.provider.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
ERROR_TYPE = "error.type"

# --- Our own attributes ---

ROUNDS = "guardian.rounds"                              # the run's model calls
REQUESTS_SHOWN = "guardian.disable_requests.shown"      # the pending requests the CLI showed
REQUEST_ID = "guardian.disable_request.id"              # the request decided
DECISION = "guardian.decision"                          # approve or deny
