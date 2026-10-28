"""Unit test for the chat-style trace (GUARDIAN_TRACE=1): it renders without a model or a terminal."""

import io
from types import SimpleNamespace

from rich.console import Console

from trace_view import ChatTrace


def test_the_chat_view_labels_each_tool_result_with_the_tool_that_produced_it():
    out = io.StringIO()
    view = ChatTrace(Console(file=out, width=100, color_system=None))
    tools = [{"name": "get_agent", "input_schema": {"properties": {"app_id": {"type": "string"}}}}]

    view.request(1, tools, [{"role": "user", "content": "Run today's check."}])
    view.response(1, SimpleNamespace(stop_reason="tool_use", usage=None, content=[
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
