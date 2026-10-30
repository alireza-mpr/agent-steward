"""The MCP server's tool allow-list (FR6): the tools the server offers and runs.

Plain Python with no MCP SDK, so the policy has offline tests. server.py applies it: tools/list
hides every tool that is not on the list, and tools/call refuses it before the tool code runs.
"""

import json
from pathlib import Path


class AllowListError(Exception):
    """The allow-list file is missing, or a value in it is wrong. The MCP server does not start."""


def load_allow_list(path: Path, registered_tools: set[str]) -> frozenset[str]:
    """Read the allow-list file (config/tool-allow-list.json). Fail closed: no defaults, no
    unknown keys, and every name must be a tool in the code. The last check catches a typo, and
    an old entry that would otherwise switch on a tool with that name later, without review."""
    try:
        values = json.loads(path.read_text())
    except FileNotFoundError:
        raise AllowListError(f"no allow-list file at {path}") from None
    except json.JSONDecodeError as error:
        raise AllowListError(f"{path} is not valid JSON: {error}") from None
    if not isinstance(values, dict):
        raise AllowListError(f"{path} must hold one JSON object")

    unknown = [key for key in values if key not in ("description", "allowed_tools")]
    if "allowed_tools" not in values or unknown:
        raise AllowListError(f"{path}: needs the key allowed_tools, unknown keys {unknown}")
    names = values["allowed_tools"]
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise AllowListError("allowed_tools must be a list of tool names")
    not_tools = sorted(set(names) - registered_tools)
    if not_tools:
        raise AllowListError(f"allowed_tools names tools that are not in the code: {not_tools}")
    return frozenset(names)
