"""The guardian's run: one pass of the agent loop, from the task to Claude's report (FR6).

Claude decides which tools to ask for; the MCP server runs them. docker compose up runs the
guardian once, and docker compose run --rm guardian runs it again. The container holds only the
Anthropic API key (secrets/anthropic.env) and cannot reach the token sidecar.

To see every message sent to and received from the model:
    docker compose run --rm -e GUARDIAN_TRACE=1 guardian       # as a chat, in colour
    docker compose run --rm -e GUARDIAN_TRACE=json guardian    # as full JSON
The trace holds real tenant data (agent names, IDs), so redact it in any screenshot.
"""

import asyncio
import os
import socket
import sys
import time
import urllib.parse

import anthropic
from mcp import Client

from harness import ModelStopped, RoundLimitReached, Trace, run_agent
from trace_view import ChatTrace

MCP_URL = os.environ.get("MCP_URL", "http://sidecar:8100/mcp")
TRACE = os.environ.get("GUARDIAN_TRACE", "")   # "1": chat view, "json": full JSON, unset: off


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
    wait_until_listening(MCP_URL)
    async with Client(MCP_URL) as mcp_client, anthropic.AsyncAnthropic() as claude:   # the key comes from the environment
        try:
            if TRACE == "json":
                result = await run_agent(claude, mcp_client, trace=Trace())
            elif TRACE:
                # The chat view shows every step, so the one-line progress log is left out.
                result = await run_agent(claude, mcp_client, log=lambda line: None, trace=ChatTrace())
            else:
                result = await run_agent(claude, mcp_client)
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

    if TRACE in ("", "json"):   # the chat view has already shown the report
        print(f"\nThe guardian's report ({result.rounds} model calls):\n\n{result.report}")
    for correlation_id in result.correlation_ids:
        print(f"\nAudit correlation ID from scan_registry: {correlation_id}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
