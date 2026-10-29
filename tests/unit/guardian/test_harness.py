"""Unit tests for the guardian's harness (FR6, NFR8): the agent loop with a scripted model and a
fake MCP server, and the settings file. No network and no model calls.
"""

import asyncio
import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import harness

REPO_SETTINGS = Path(__file__).parents[3] / "config" / "guardian.json"
SETTINGS = harness.HarnessSettings(model="claude-opus-5-5", effort="medium", max_rounds=10,
                                   strict_tool_inputs=False, refusal_fallback=False)


def text(value):
    return SimpleNamespace(type="text", text=value)


def tool_use(request_id, name, arguments):
    return SimpleNamespace(type="tool_use", id=request_id, name=name, input=arguments)


def answer(stop_reason, *content, model="claude-opus-5-5"):
    return SimpleNamespace(model=model, stop_reason=stop_reason, content=list(content))


class ScriptedClaude:
    """Returns the scripted answers in order and keeps every request it was sent."""
    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    async def create(self, **request):
        self.requests.append({**request, "messages": list(request["messages"])})
        return self.answers.pop(0)


SCAN_SCHEMA = {"type": "object", "properties": {}}


class FakeMcpServer:
    def __init__(self, results):
        self.results = results
        self.calls = []

    async def list_tools(self):
        return SimpleNamespace(tools=[SimpleNamespace(name="scan_registry", description="Scan.",
                                                      input_schema=SCAN_SCHEMA)])

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self.results[name]


SCAN_OK = SimpleNamespace(is_error=False, content=[], structured_content={"findings": [], "accepted": 0,
                                                                          "correlation_id": "c-1"})
TOOL_FAILED = SimpleNamespace(is_error=True, content=[text("Unknown tool: delete_everything")],
                              structured_content=None)


def run(claude, mcp_server, settings=SETTINGS, log=lambda line: None):
    return asyncio.run(harness.run_agent(claude, mcp_server, settings, log=log))


# --- The agent loop ---

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


def test_strict_tool_inputs_mark_every_tool_strict_and_close_its_schema():
    claude = ScriptedClaude(answer("end_turn", text("Done.")))

    run(claude, FakeMcpServer({}), dataclasses.replace(SETTINGS, strict_tool_inputs=True))

    assert claude.requests[0]["tools"] == [{"name": "scan_registry", "description": "Scan.", "strict": True,
                                            "input_schema": {"type": "object", "properties": {},
                                                             "additionalProperties": False}}]
    assert SCAN_SCHEMA == {"type": "object", "properties": {}}   # the MCP server's schema is not changed


def test_the_request_uses_the_model_and_effort_from_the_settings():
    claude = ScriptedClaude(answer("end_turn", text("Done.")))

    run(claude, FakeMcpServer({}), dataclasses.replace(SETTINGS, model="claude-test-model", effort="high"))

    assert claude.requests[0]["model"] == "claude-test-model"
    assert claude.requests[0]["output_config"] == {"effort": "high"}


def test_the_refusal_fallback_is_asked_for_only_when_the_settings_turn_it_on():
    with_fallback, without = (ScriptedClaude(answer("end_turn", text("Done."))) for _ in range(2))

    run(with_fallback, FakeMcpServer({}), dataclasses.replace(SETTINGS, refusal_fallback=True))
    run(without, FakeMcpServer({}), dataclasses.replace(SETTINGS, refusal_fallback=False))

    assert with_fallback.requests[0]["fallbacks"] == "default"
    assert with_fallback.requests[0]["betas"] == ["server-side-fallback-2026-07-01"]
    assert "fallbacks" not in without.requests[0] and "betas" not in without.requests[0]


def test_the_log_names_the_model_that_answered_not_the_model_that_was_asked():
    claude = ScriptedClaude(answer("end_turn", text("Done."), model="claude-fallback-model"))
    lines = []

    run(claude, FakeMcpServer({}), log=lines.append)

    assert any("claude-fallback-model stopped with end_turn" in line for line in lines)


def test_a_failed_tool_goes_back_to_the_model_as_an_error_it_can_read():
    claude = ScriptedClaude(answer("tool_use", tool_use("toolu_1", "delete_everything", {})),
                            answer("end_turn", text("I could not do that.")))

    run(claude, FakeMcpServer({"delete_everything": TOOL_FAILED}))

    [tool_result] = claude.requests[1]["messages"][-1]["content"]
    assert tool_result["is_error"] is True
    assert "Unknown tool" in tool_result["content"]


def test_the_loop_stops_at_the_round_limit_from_the_settings():
    endless = [answer("tool_use", tool_use(f"toolu_{n}", "scan_registry", {})) for n in range(4)]
    claude = ScriptedClaude(*endless)

    with pytest.raises(harness.RoundLimitReached):
        run(claude, FakeMcpServer({"scan_registry": SCAN_OK}), dataclasses.replace(SETTINGS, max_rounds=3))

    assert len(claude.requests) == 3


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens", "model_context_window_exceeded",
                                         "pause_turn", "stop_sequence", "compaction", "a_value_added_later"])
def test_only_end_turn_is_a_report_any_other_stop_reason_stops_the_run(stop_reason):
    claude = ScriptedClaude(answer(stop_reason, text("A report that may be cut off.")))

    with pytest.raises(harness.ModelStopped, match=stop_reason):
        run(claude, FakeMcpServer({}))


def test_the_trace_is_off_by_default_and_the_model_is_not_asked_for_thinking_summaries():
    claude = ScriptedClaude(answer("end_turn", text("Done.")))

    run(claude, FakeMcpServer({}))

    assert "thinking" not in claude.requests[0]


def test_the_trace_shows_the_prompt_the_tool_requests_and_the_results_sent_back():
    claude = ScriptedClaude(answer("tool_use", tool_use("toolu_1", "scan_registry", {})),
                            answer("end_turn", text("No findings.")))
    lines = []

    asyncio.run(harness.run_agent(claude, FakeMcpServer({"scan_registry": SCAN_OK}), SETTINGS,
                                  log=lambda line: None, trace=harness.Trace(lines.append)))

    trace = "\n".join(lines)
    assert harness.SYSTEM_PROMPT.splitlines()[0] in trace
    assert '"id": "toolu_1"' in trace and '"tool_use_id": "toolu_1"' in trace
    assert "model: claude-opus-5-5" in trace
    assert claude.requests[0]["thinking"] == {"type": "adaptive", "display": "summarized"}


# --- The settings file (fail closed: a missing or wrong value stops the run) ---

def settings_file(tmp_path, **changes):
    values = {"model": "claude-opus-5-5", "effort": "medium", "max_rounds": 10,
              "strict_tool_inputs": True, "refusal_fallback": True, **changes}
    path = tmp_path / "guardian.json"
    path.write_text(json.dumps({name: value for name, value in values.items() if value is not None}))
    return path


def test_the_repositorys_settings_file_loads():
    settings = harness.load_settings(REPO_SETTINGS)

    assert 1 <= settings.max_rounds <= harness.MAX_ROUNDS_CAP
    assert settings.effort in harness.EFFORT_LEVELS


def test_a_valid_settings_file_loads_every_value(tmp_path):
    settings = harness.load_settings(settings_file(tmp_path, effort="high", max_rounds=5, refusal_fallback=False))

    assert settings == harness.HarnessSettings(model="claude-opus-5-5", effort="high", max_rounds=5,
                                               strict_tool_inputs=True, refusal_fallback=False)


def test_a_missing_settings_file_stops_the_run(tmp_path):
    with pytest.raises(harness.SettingsError, match="no settings file"):
        harness.load_settings(tmp_path / "missing.json")


def test_a_settings_file_that_is_not_json_stops_the_run(tmp_path):
    path = tmp_path / "guardian.json"
    path.write_text("max_rounds = 10")

    with pytest.raises(harness.SettingsError, match="not valid JSON"):
        harness.load_settings(path)


def test_a_missing_key_stops_the_run_instead_of_falling_back_to_a_default(tmp_path):
    with pytest.raises(harness.SettingsError, match="missing keys \\['max_rounds'\\]"):
        harness.load_settings(settings_file(tmp_path, max_rounds=None))


def test_an_unknown_key_stops_the_run(tmp_path):
    with pytest.raises(harness.SettingsError, match="unknown keys \\['temperature'\\]"):
        harness.load_settings(settings_file(tmp_path, temperature=0))


@pytest.mark.parametrize("changes, message", [
    ({"model": ""}, "model"),
    ({"model": 5}, "model"),
    ({"effort": "extreme"}, "effort"),
    ({"max_rounds": 0}, "max_rounds"),
    ({"max_rounds": 26}, "max_rounds"),
    ({"max_rounds": "10"}, "max_rounds"),
    ({"max_rounds": 2.5}, "max_rounds"),
    ({"max_rounds": True}, "max_rounds"),
    ({"strict_tool_inputs": "true"}, "strict_tool_inputs"),
    ({"refusal_fallback": 1}, "refusal_fallback"),
])
def test_a_wrong_value_stops_the_run(tmp_path, changes, message):
    with pytest.raises(harness.SettingsError, match=message):
        harness.load_settings(settings_file(tmp_path, **changes))
