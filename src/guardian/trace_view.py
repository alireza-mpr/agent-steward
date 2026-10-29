"""A chat-style view of the agent loop in the terminal (GUARDIAN_TRACE=1).

A readable view, laid out like a conversation, one panel per message. The harness talks to two
peers, and each has its own colours:
- the model: blue for what the harness sends to Claude, green for what Claude sends back;
- the MCP server: magenta for what the harness sends to it, yellow for what it sends back.
It shortens long results and IDs and does not repeat earlier messages, so it is not the exact
request: for that, use GUARDIAN_TRACE=full (harness.BodyLog). It never prints HTTP headers, so the
API key never appears.
"""

import json
from typing import Any

from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from harness import SYSTEM_PROMPT, as_plain_data, result_text

PREVIEW = 300   # characters of a tool result shown in the panel


def short(identifier: str) -> str:
    """A long ID cut to its start, enough to match a request with its result."""
    return identifier if len(identifier) <= 14 else identifier[:12] + "…"


def arguments(values: dict) -> str:
    return ", ".join(f"{name}={short(str(value))}" for name, value in values.items())


def size(text: str) -> str:
    count = len(text.encode())
    return f"{count} bytes" if count < 1024 else f"{count / 1024:.1f} KB"


def result_preview(label: str, content: str, failed: bool) -> Text:
    """A tool result: its label and size, then its first characters."""
    text = Text(f"{label}, {size(content)}:\n", style="red" if failed else "cyan")
    text.append(content if len(content) <= PREVIEW else content[:PREVIEW] + " …", style="red" if failed else "default")
    return text


class ChatTrace:
    def __init__(self, console: Console | None = None, mcp_endpoint: str = ""):
        self.console = console or Console()
        self.sent = 0                       # how many messages earlier requests already showed
        self.tool_names: dict[str, str] = {}   # tool_use ID -> tool name, to label each result
        # The HTTP method and path of every call to the MCP server, such as "POST /mcp".
        self.endpoint = f" · {mcp_endpoint}" if mcp_endpoint else ""

    def request(self, round_number: int, tools: list[dict], messages: list[dict]) -> None:
        lines: list[Any] = []
        if round_number == 1:
            lines.append(Text("System prompt", style="bold"))
            lines.append(Text(SYSTEM_PROMPT, style="dim"))
            lines.append(Text("\nTools offered", style="bold"))
            for tool in tools:
                inputs = ", ".join(tool["input_schema"].get("properties", {})) or "no inputs"
                lines.append(Text(f"🔧 {tool['name']}({inputs})", style="cyan"))
            lines.append(Text(""))
        else:
            earlier = f"{self.sent} earlier message" + ("" if self.sent == 1 else "s")
            lines.append(Text(f"(also sends again: the system prompt, the tools and the {earlier})", style="dim"))

        for message in messages[self.sent:]:
            if message["role"] == "assistant":
                lines.append(Text("↩ Claude's previous answer, sent back unchanged", style="dim"))
            elif isinstance(message["content"], str):
                lines.append(Text(f"👤 {message['content']}"))
            else:
                for item in message["content"]:
                    if item.get("type") == "tool_result":
                        lines.append(self.result_line(item))
        self.sent = len(messages)
        self.console.print(Panel(Group(*lines), title=f"Harness → Claude · round {round_number}",
                                 title_align="left", border_style="blue"))

    def result_line(self, item: dict) -> Text:
        content = item["content"] if isinstance(item["content"], str) else json.dumps(item["content"])
        name = self.tool_names.get(item["tool_use_id"], "a tool")
        return result_preview(f"🔧 {name} {'failed' if item.get('is_error') else 'result'} "
                              f"({short(item['tool_use_id'])})", content, bool(item.get("is_error")))

    def mcp_tools_listed(self, tools: list[Any]) -> None:
        self.console.print(Panel(Text("tools/list"), title=f"Harness → MCP server · start{self.endpoint}",
                                 title_align="left", border_style="magenta"))
        lines = [Text(f"🔧 {tool.name}({', '.join(tool.input_schema.get('properties', {})) or 'no inputs'})",
                      style="cyan") for tool in tools]
        self.console.print(Panel(Group(*lines) if lines else Text("(no tools)"), title="MCP server → Harness · start",
                                 title_align="left", border_style="yellow"))

    def mcp_call(self, round_number: int, name: str, values: dict) -> None:
        self.console.print(Panel(Text(f"tools/call 🔧 {name}({arguments(values)})", style="bold cyan"),
                                 title=f"Harness → MCP server · round {round_number}{self.endpoint}",
                                 title_align="left", border_style="magenta"))

    def mcp_result(self, round_number: int, name: str, outcome: Any) -> None:
        line = result_preview(f"🔧 {name} {'failed' if outcome.is_error else 'result'}",
                              result_text(outcome), bool(outcome.is_error))
        self.console.print(Panel(line, title=f"MCP server → Harness · round {round_number}",
                                 title_align="left", border_style="yellow"))

    def response(self, round_number: int, response: Any) -> None:
        lines: list[Any] = []
        for block in response.content:
            data = as_plain_data(block)
            if data["type"] == "thinking":
                if data.get("thinking"):
                    lines.append(Text(f"💭 {data['thinking'].strip()}", style="italic dim"))
            elif data["type"] == "text":
                lines.append(Markdown(data["text"]))
            elif data["type"] == "tool_use":
                self.tool_names[data["id"]] = data["name"]
                lines.append(Text(f"🔧 {data['name']}({arguments(data['input'])})  [{short(data['id'])}]",
                                  style="bold cyan"))
            else:
                lines.append(Text(f"[{data['type']} block]", style="dim"))
        usage = getattr(response, "usage", None)
        subtitle = f"{response.model} · {response.stop_reason}"
        if usage:
            subtitle = f"{usage.input_tokens:,} in · {usage.output_tokens:,} out · {subtitle}"
        self.console.print(Panel(Group(*lines) if lines else Text("(empty)"),
                                 title=f"Claude → Harness · round {round_number}", title_align="left",
                                 subtitle=subtitle, subtitle_align="right", border_style="green"))
