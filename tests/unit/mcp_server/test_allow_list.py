"""Unit tests for loading the MCP server's tool allow-list (FR6, NFR8).

A wrong file must stop the server at start: it never falls back to a default.
"""

import json
from pathlib import Path

import pytest

from allow_list import AllowListError, load_allow_list

REAL_ALLOW_LIST = Path(__file__).parents[3] / "config" / "tool-allow-list.json"
TOOLS_IN_CODE = {"scan_registry", "get_agent"}


def write(tmp_path: Path, content) -> Path:
    path = tmp_path / "tool-allow-list.json"
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    return path


def test_the_real_allow_list_holds_the_guardians_two_tools():
    assert load_allow_list(REAL_ALLOW_LIST, TOOLS_IN_CODE) == {"scan_registry", "get_agent"}


def test_an_empty_list_is_valid_and_allows_nothing(tmp_path):
    assert load_allow_list(write(tmp_path, {"allowed_tools": []}), TOOLS_IN_CODE) == frozenset()


def test_a_name_that_is_not_a_tool_in_the_code_stops_the_start(tmp_path):
    # A typo, or an entry for a tool that does not exist yet (it would go live, unreviewed, later).
    path = write(tmp_path, {"allowed_tools": ["scan_registry", "disable_agent"]})

    with pytest.raises(AllowListError, match="disable_agent"):
        load_allow_list(path, TOOLS_IN_CODE)


@pytest.mark.parametrize("content", [
    '{"allowed_tools": ["scan_registry"',                         # not valid JSON
    ["scan_registry"],                                            # not one JSON object
    {},                                                           # no allowed_tools
    {"allowed_tools": ["scan_registry"], "allow_all": True},      # an unknown key
    {"allowed_tools": "scan_registry"},                           # a string, not a list
    {"allowed_tools": ["scan_registry", 1]},                      # a name that is not a string
])
def test_a_wrong_file_stops_the_start(tmp_path, content):
    with pytest.raises(AllowListError):
        load_allow_list(write(tmp_path, content), TOOLS_IN_CODE)


def test_a_missing_file_stops_the_start(tmp_path):
    with pytest.raises(AllowListError, match="no allow-list file"):
        load_allow_list(tmp_path / "missing.json", TOOLS_IN_CODE)
