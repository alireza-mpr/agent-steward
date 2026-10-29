"""The guardian's run: one pass of the agent loop, from the task to Claude's report (FR6).

Claude decides which tools to ask for; the MCP server runs them. docker compose up runs the
guardian once, and docker compose run --rm guardian runs it again. The container holds only the
Anthropic API key (secrets/anthropic.env) and cannot reach the token sidecar.

To see every message between the harness and its two peers, the model and the MCP server,
each conversation in its own sections:
    docker compose run --rm -e GUARDIAN_TRACE=1 guardian       # as a chat, in colour, shortened
    docker compose run --rm -e GUARDIAN_TRACE=json guardian    # as JSON, each model message once, no settings
    docker compose run --rm -e GUARDIAN_TRACE=full guardian    # the exact request and response bodies, in full
The trace holds real tenant data (agent names, IDs), so keep it out of git (run*.txt is ignored)
and redact it in any screenshot.
"""

import asyncio
import os
import socket
import sys
import time
import urllib.parse
from dataclasses import asdict
from pathlib import Path

import anthropic
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from harness import BodyLog, ModelStopped, RoundLimitReached, SettingsError, Trace, load_settings, run_agent
from trace_view import ChatTrace

MCP_URL = os.environ.get("MCP_URL", "http://sidecar:8100/mcp")
# "1": chat view, "json": messages as JSON, "full": the exact request and response bodies, unset: off
TRACE = os.environ.get("GUARDIAN_TRACE", "")
# The harness's settings: model, effort, round limit and more (config/guardian.json, mounted read-only).
SETTINGS_PATH = Path(os.environ.get("GUARDIAN_CONFIG_PATH", "/config/guardian.json"))


def wait_until_listening(url: str, timeout_seconds: int = 60) -> None:
    """docker compose up starts everything at once, so the guardian can be faster than the MCP server."""
    address = urllib.parse.urlsplit(url)
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            socket.create_connection((address.hostname, address.port), timeout=5).close()
            return
        except OSError:
            if time.monotonic() > deadline:
                raise SystemExit(f"Not listening after {timeout_seconds} s: {url}")
            time.sleep(2)


async def main() -> int:
    try:
        settings = load_settings(SETTINGS_PATH)
    except SettingsError as error:
        print(f"Guardian run not started: {error}", file=sys.stderr)
        return 1
    # Every run records the settings it used, in every trace mode.
    print("Guardian settings: " + ", ".join(f"{name}={value}" for name, value in asdict(settings).items()))

    wait_until_listening(MCP_URL)
    # full: two body logs, one in each SDK's HTTP client, so each sees every body exactly as sent:
    # the model's (Anthropic's API) and the MCP server's. Same timeouts as the MCP SDK's default client.
    full = TRACE == "full"
    model_http = anthropic.DefaultAsyncHttpxClient(event_hooks=BodyLog("model").hooks()) if full else None
    mcp_http = httpx2.AsyncClient(timeout=httpx2.Timeout(30, read=300),
                                  event_hooks=BodyLog("MCP server", show_endpoint=True).hooks() if full else None)
    # Streamable HTTP: the MCP client sends every message as a POST to this one path. The full log
    # prints each request's real method and path; the other traces print this.
    mcp_endpoint = f"POST {urllib.parse.urlsplit(MCP_URL).path}"
    async with mcp_http, Client(streamable_http_client(MCP_URL, http_client=mcp_http)) as mcp_client, \
            anthropic.AsyncAnthropic(http_client=model_http) as claude:   # the key comes from the environment
        try:
            # Every trace shows each step in its own section, so the one-line progress log is left out.
            quiet = lambda line: None   # noqa: E731
            if full:
                result = await run_agent(claude, mcp_client, settings, log=quiet, show_thinking=True)
            elif TRACE == "json":
                result = await run_agent(claude, mcp_client, settings, log=quiet,
                                         trace=Trace(mcp_endpoint=mcp_endpoint), show_thinking=True)
            elif TRACE:
                result = await run_agent(claude, mcp_client, settings, log=quiet,
                                         trace=ChatTrace(mcp_endpoint=mcp_endpoint), show_thinking=True)
            else:
                result = await run_agent(claude, mcp_client, settings)
        except (ModelStopped, RoundLimitReached) as error:
            print(f"Guardian run stopped: {error}", file=sys.stderr)
            return 1
        except anthropic.APIStatusError as error:   # for example a bad key, no credit, or a rate limit
            print(f"Guardian run stopped: the model provider answered {error.status_code}: {error.message}",
                  file=sys.stderr)
            return 1
        except anthropic.APIConnectionError as error:
            print(f"Guardian run stopped: the model provider could not be reached: {error}", file=sys.stderr)
            return 1

    if TRACE in ("", "json", "full"):   # the chat view has already shown the report
        print(f"\nThe guardian's report ({result.rounds} model calls):\n\n{result.report}")
    for correlation_id in result.correlation_ids:
        print(f"\nAudit correlation ID from scan_registry: {correlation_id}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
