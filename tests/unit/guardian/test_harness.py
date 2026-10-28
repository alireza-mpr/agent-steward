"""Unit tests for the guardian's harness (FR6, NFR8): the agent loop with a scripted model and a
fake MCP server. No network and no model calls.
"""

import asyncio
from types import SimpleNamespace

import pytest

import harness


def text(value):
    return SimpleNamespace(type="text", text=value)


def tool_use(request_id, name, arguments):
    return SimpleNamespace(type="tool_use", id=request_id, name=name, input=arguments)


def answer(stop_reason, *content):
    return SimpleNamespace(stop_reason=stop_reason, content=list(content))


class ScriptedClaude:
    """Returns the scripted answers in order and keeps every request it was sent."""
    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    async def create(self, **request):
        self.requests.append({**request, "messages": list(request["messages"])})
        return self.answers.pop(0)


class FakeMcpServer:
    def __init__(self, results):
        self.results = results
        self.calls = []

    async def list_tools(self):
        return SimpleNamespace(tools=[SimpleNamespace(name="scan_registry", description="Scan.",
                                                      input_schema={"type": "object", "properties": {}})])

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self.results[name]


SCAN_OK = SimpleNamespace(is_error=False, content=[], structured_content={"findings": [], "accepted": 0,
                                                                          "correlation_id": "c-1"})
TOOL_FAILED = SimpleNamespace(is_error=True, content=[text("Unknown tool: delete_everything")],
                              structured_content=None)


def run(claude, mcp_server):
    return asyncio.run(harness.run_agent(claude, mcp_server, log=lambda line: None))


def test_a_tool_request_goes_to_the_mcp_server_and_its_result_back_to_the_model():
    claude = ScriptedClaude(answer("tool_use", tool_use("toolu_1", "scan_registry", {})),
                            answer("end_turn", text("No findings. Correlation ID c-1.")))
    mcp_server = FakeMcpServer({"scan_registry": SCAN_OK})

    result = run(claude, mcp_server)

    assert mcp_server.calls == [("scan_registry", {})]
    [tool_result] = claude.requests[1]["messages"][-1]["content"]
    assert tool_result["tool_use_id"] == "toolu_1"
    assert tool_result["is_error"] is False
    assert result.report == "No findings. Correlation ID c-1."
    assert result.correlation_ids == ["c-1"]


def test_the_model_gets_the_mcp_servers_tools_with_their_json_schema():
    claude = ScriptedClaude(answer("end_turn", text("Done.")))

    run(claude, FakeMcpServer({}))

    assert claude.requests[0]["tools"] == [{"name": "scan_registry", "description": "Scan.",
                                            "input_schema": {"type": "object", "properties": {}}}]


def test_a_failed_tool_goes_back_to_the_model_as_an_error_it_can_read():
    claude = ScriptedClaude(answer("tool_use", tool_use("toolu_1", "delete_everything", {})),
                            answer("end_turn", text("I could not do that.")))

    run(claude, FakeMcpServer({"delete_everything": TOOL_FAILED}))

    [tool_result] = claude.requests[1]["messages"][-1]["content"]
    assert tool_result["is_error"] is True
    assert "Unknown tool" in tool_result["content"]


def test_the_loop_stops_at_the_round_limit_when_the_model_never_finishes():
    endless = [answer("tool_use", tool_use(f"toolu_{n}", "scan_registry", {})) for n in range(harness.MAX_ROUNDS + 1)]
    claude = ScriptedClaude(*endless)

    with pytest.raises(harness.RoundLimitReached):
        run(claude, FakeMcpServer({"scan_registry": SCAN_OK}))

    assert len(claude.requests) == harness.MAX_ROUNDS


def test_a_refusal_stops_the_run_without_a_report():
    claude = ScriptedClaude(answer("refusal"))

    with pytest.raises(harness.ModelStopped):
        run(claude, FakeMcpServer({}))


def test_the_trace_is_off_by_default_and_the_model_is_not_asked_for_thinking_summaries():
    claude = ScriptedClaude(answer("end_turn", text("Done.")))

    run(claude, FakeMcpServer({}))

    assert "thinking" not in claude.requests[0]


def test_the_trace_shows_the_prompt_the_tool_requests_and_the_results_sent_back():
    claude = ScriptedClaude(answer("tool_use", tool_use("toolu_1", "scan_registry", {})),
                            answer("end_turn", text("No findings.")))
    lines = []

    asyncio.run(harness.run_agent(claude, FakeMcpServer({"scan_registry": SCAN_OK}),
                                  log=lambda line: None, trace=harness.Trace(lines.append)))

    trace = "\n".join(lines)
    assert harness.SYSTEM_PROMPT.splitlines()[0] in trace
    assert '"id": "toolu_1"' in trace and '"tool_use_id": "toolu_1"' in trace
    assert claude.requests[0]["thinking"] == {"type": "adaptive", "display": "summarized"}
