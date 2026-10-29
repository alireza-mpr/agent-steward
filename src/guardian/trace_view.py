"""A chat-style view of the agent loop in the terminal (GUARDIAN_TRACE=1).

The same information as harness.Trace, the JSON trace, laid out like a conversation: one panel
per message, blue for what the harness sends to Claude, green for what Claude sends back. It
prints only what the harness builds and receives, never HTTP headers, so the API key never appears.
"""

import json
from typing import Any

from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from harness import SYSTEM_PROMPT, as_plain_data

PREVIEW = 300   # characters of a tool result shown in the panel


def short(identifier: str) -> str:
    """A long ID cut to its start, enough to match a request with its result."""
    return identifier if len(identifier) <= 14 else identifier[:12] + "…"


def arguments(values: dict) -> str:
    return ", ".join(f"{name}={short(str(value))}" for name, value in values.items())


def size(text: str) -> str:
    count = len(text.encode())
    return f"{count} bytes" if count < 1024 else f"{count / 1024:.1f} KB"


class ChatTrace:
    def __init__(self, console: Console | None = None):
        self.console = console or Console()
        self.sent = 0                       # how many messages earlier requests already showed
        self.tool_names: dict[str, str] = {}   # tool_use ID -> tool name, to label each result

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
                        lines.append(self.tool_result(item))
        self.sent = len(messages)
        self.console.print(Panel(Group(*lines), title=f"Harness → Claude · round {round_number}",
                                 title_align="left", border_style="blue"))

    def tool_result(self, item: dict) -> Text:
        content = item["content"] if isinstance(item["content"], str) else json.dumps(item["content"])
        name = self.tool_names.get(item["tool_use_id"], "a tool")
        preview = content if len(content) <= PREVIEW else content[:PREVIEW] + " …"
        label = "failed" if item.get("is_error") else "result"
        text = Text(f"🔧 {name} {label} ({short(item['tool_use_id'])}), {size(content)}:\n",
                    style="red" if item.get("is_error") else "cyan")
        text.append(preview, style="red" if item.get("is_error") else "default")
        return text

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
