"""Unit tests for the guardian's tagged log lines (harness.setup_logging). No network and no model calls."""

import logging

import pytest

import harness


@pytest.fixture
def restore_logging():
    yield
    guardian = logging.getLogger("guardian")
    guardian.handlers, guardian.propagate = [], True
    for name in ("guardian", "guardian.model", "guardian.mcp"):
        logging.getLogger(name).setLevel(logging.NOTSET)


def emit_one_line_per_source():
    logging.getLogger("guardian.harness").info("settings: model=claude-opus-5-5")
    logging.getLogger("guardian.mcp").info("tools/list: scan_registry")
    logging.getLogger("guardian.model").info("round 1: claude-opus-5-5 stopped with end_turn")
    logging.getLogger("guardian.harness").error("run stopped: the model stopped with refusal in round 1")


def test_every_line_starts_with_the_tag_of_its_source_and_errors_go_to_stderr(capsys, restore_logging):
    harness.setup_logging("")
    emit_one_line_per_source()

    out, err = capsys.readouterr()
    assert out.splitlines() == ["[harness] settings: model=claude-opus-5-5",
                                "[mcp]     tools/list: scan_registry",
                                "[model]   round 1: claude-opus-5-5 stopped with end_turn"]
    assert err.splitlines() == ["[harness] run stopped: the model stopped with refusal in round 1"]


def test_in_a_trace_mode_only_the_harness_lines_are_printed(capsys, restore_logging):
    harness.setup_logging("json")
    emit_one_line_per_source()

    out, err = capsys.readouterr()
    assert out.splitlines() == ["[harness] settings: model=claude-opus-5-5"]
    assert err.splitlines() == ["[harness] run stopped: the model stopped with refusal in round 1"]
