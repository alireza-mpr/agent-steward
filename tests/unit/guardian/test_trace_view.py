"""Unit test for the chat-style trace (GUARDIAN_TRACE=1): it renders without a model or a terminal."""

import io
from types import SimpleNamespace

from mcp.types import CallToolResult, Tool
from rich.console import Console

from trace_view import ChatTrace


def test_the_chat_view_labels_each_tool_result_with_the_tool_that_produced_it():
    out = io.StringIO()
    view = ChatTrace(Console(file=out, width=100, color_system=None))
    tools = [{"name": "get_agent", "input_schema": {"properties": {"app_id": {"type": "string"}}}}]

    view.request(1, tools, [{"role": "user", "content": "Run today's check."}])
    view.response(1, SimpleNamespace(model="claude-opus-5-5", stop_reason="tool_use", usage=None, content=[
        SimpleNamespace(type="thinking", thinking="I should read the agent."),
        SimpleNamespace(type="tool_use", id="toolu_123", name="get_agent", input={"app_id": "a-1"})]))
    view.request(2, tools, [{"role": "user", "content": "Run today's check."},
                            {"role": "assistant", "content": []},
                            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_123",
                                                          "content": "app_id must be a GUID", "is_error": True}]}])

    shown = out.getvalue()
    assert "get_agent(app_id)" in shown                    # the tools offered
    assert "I should read the agent." in shown              # the thinking summary
    assert "get_agent(app_id=a-1)" in shown                 # the tool request
    assert "get_agent failed" in shown and "app_id must be a GUID" in shown   # the result, labelled
    assert "claude-opus-5-5 · tool_use" in shown            # the model that answered


def test_the_chat_view_shows_the_mcp_conversation_in_its_own_panels():
    out = io.StringIO()
    view = ChatTrace(Console(file=out, width=100, color_system=None), mcp_endpoint="POST /mcp")

    view.mcp_tools_listed([Tool.model_validate({"name": "get_agent", "inputSchema": {
        "type": "object", "properties": {"app_id": {"type": "string"}}}})])
    view.mcp_call(1, "get_agent", {"app_id": "a-1"})
    view.mcp_result(1, "get_agent", CallToolResult.model_validate(
        {"content": [{"type": "text", "text": "app_id must be a GUID"}], "isError": True}))

    shown = out.getvalue()
    assert "Harness → MCP server · start · POST /mcp" in shown and "MCP server → Harness · start" in shown
    assert "Harness → MCP server · round 1 · POST /mcp" in shown and "tools/call 🔧 get_agent(app_id=a-1)" in shown
    assert "MCP server → Harness · round 1" in shown and "get_agent failed" in shown
    assert "Claude" not in shown                            # nothing here is part of the model conversation
