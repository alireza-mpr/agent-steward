# MCP: the guardian's MCP server

This document is the hands-on reference for the guardian's MCP server: what MCP is, what travels on the wire, what our server offers, and how to call and inspect it. For where the MCP server sits in the agent's design and why it is the only part that can get tokens, see [Agent architecture](agent-architecture.md). For the security controls it enforces, see [Security controls](security-controls.md#the-mcp-server).

## Contents

- [1. MCP in short](#1-mcp-in-short)
- [2. On the wire](#2-on-the-wire)
- [3. The guardian's MCP server](#3-the-guardians-mcp-server)
- [4. Calling it from your machine](#4-calling-it-from-your-machine)
  - [The MCP Inspector](#the-mcp-inspector)
  - [A Python client](#a-python-client)
  - [Raw HTTP with curl](#raw-http-with-curl)
- [5. Behaviour seen in this lab](#5-behaviour-seen-in-this-lab)

---

## 1. MCP in short

**MCP (Model Context Protocol)** is a standard protocol that lets an AI application find and use capabilities on a separate server.

- **Three roles:** The **host** is the AI application, here the guardian's harness. It holds one **client** for each server it talks to. The **server** offers the capabilities, here our MCP server.
- **Three kinds of capability:**
  - **Tools:** functions the *model* decides to call. Our server offers two.
  - **Resources:** data the *application* can read, such as files or records. Our server offers none.
  - **Prompts:** ready-made prompt templates that a *user (a human)* picks. Our server offers none.
- **The model never talks to the server.** It only asks the harness for a tool, and the harness sends the request ([The agent loop](agent-architecture.md#4-the-agent-loop-in-action)).

## 2. On the wire

MCP messages are **JSON-RPC 2.0** messages. Over **Streamable HTTP**, the transport for a server that runs as its own network service, the client sends each message as an HTTP POST to one endpoint (`/mcp`), and the server answers with JSON or streams the answer back on the same response.

There are two eras of the protocol, and our server serves both:

- **Modern (2026-07-28 and later):** There is no handshake, so every request stands alone. It carries `_meta` in its body (the protocol version, who the client is, what it can do) and three HTTP headers: `MCP-Protocol-Version`, `Mcp-Method` (which must match the body's `method`) and, for `tools/call`, `Mcp-Name` (which must match the tool's name). The headers let proxies and firewalls route MCP traffic without reading the body. The guardian's harness and the examples below use this era.
- **Legacy (2025-11-25 and earlier):** A session starts with an `initialize` request, then a `notifications/initialized` message, and later requests belong to that session.

**`_meta`** is a reserved field for information *about* a message, next to its business data (`params` or `result`). Without a handshake, every request introduces itself in `_meta` (the protocol version, the client's name and version in `clientInfo`, and its capabilities), and every answer names the server in `serverInfo`. The keys start with `io.modelcontextprotocol/`, a prefix reserved for the specification. Two different versions appear: the *protocol* version (`2026-07-28`, the version of MCP itself) and the *server* version (the version of our program). All of it is self-declared, like HTTP's `User-Agent` and `Server` headers, so it helps with logs and debugging but must never decide what a caller may do.

MCP Python SDK 2.x is *dual-era*: the same endpoint answers both kinds of client. The message shapes for tools (definitions, results, the two kinds of error) are in [The MCP Contract](agent-architecture.md#the-mcp-contract).

## 3. The guardian's MCP server

| Item | Value |
|---|---|
| Code | [src/mcp_server/server.py](../src/mcp_server/server.py), built with the official MCP Python SDK 2.3.0 (`MCPServer`) |
| Server name and version | `agent-steward-mcp-server`, version `1.0.0`, sent in every answer's `_meta` as `serverInfo` |
| Endpoint | `http://sidecar:8100/mcp` inside Compose. It has no network name of its own, because it shares the sidecar's network namespace ([infra.md](infra.md#local-run-the-sidecar)). |
| Accepted names (`Host` header) | `sidecar:8100` only, as protection against DNS rebinding. The Inspector override adds `localhost:8100` and `127.0.0.1:8100`. |
| `scan_registry()` | Runs the four registry rules on Graph data, sends the findings to the Identity API's audit log itself ([ADR-001](decisions.md#adr-001-the-mcp-server-sends-the-findings-to-the-identity-api-never-the-model)), and returns the findings with the audit's correlation ID. Not read-only: every call writes to the audit log. |
| `get_agent(app_id)` | Returns one agent identity's name, blueprint and description. Read-only. It refuses any `app_id` that is not a GUID, before it calls Graph. |

## 4. Calling it from your machine

Normally the MCP server is reachable only inside the Compose network. To reach it from your machine, start it with the dev-only override, which publishes its port on `127.0.0.1` only ([docker-compose.inspect.yml](../docker-compose.inspect.yml)):

```sh
docker compose -f docker-compose.yml -f docker-compose.inspect.yml up -d sidecar mcp-server
```

While it is open, any program on your machine can call the tools. Close it again when you are done:

```sh
docker compose up -d sidecar mcp-server
```

### The MCP Inspector

The official visual tool for MCP. It runs in your browser, lists the tools with their schemas, lets you call them from a form, and logs every message.

```sh
npx @modelcontextprotocol/inspector@2.12.0
```

Choose the **Streamable HTTP** transport, enter `http://localhost:8100/mcp` and connect.

**Reading its message log.** When it connects, the Inspector uses the legacy era, so its log shows a "LEGACY" label. Read the log from the bottom up: `initialize`, then `notifications/initialized` (a notification, so it has no answer and no time), then `resources/list`, `tools/list`, `prompts/list` and `resources/templates/list`. Our server answers the resource and prompt lists with empty lists. The times are shown in UTC, not in local time.

### A Python client

From the repository's virtual environment:

```sh
.venv/bin/python -c "
import asyncio
from mcp import Client
async def main():
    async with Client('http://localhost:8100/mcp') as c:
        print([t.name for t in (await c.list_tools()).tools])
        r = await c.call_tool('get_agent', {'app_id': 'abc'})
        print(r.is_error, r.content[0].text)
asyncio.run(main())"
```

### Raw HTTP with curl

This shows the modern era exactly as it travels:

```sh
META='"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientInfo":{"name":"curl","version":"1.0"},"io.modelcontextprotocol/clientCapabilities":{}}'

# List the tools
curl -s -X POST http://localhost:8100/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -H "MCP-Protocol-Version: 2026-07-28" -H "Mcp-Method: tools/list" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\",\"params\":{$META}}"

# Call get_agent with an input that is not a GUID: refused
curl -s -X POST http://localhost:8100/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -H "MCP-Protocol-Version: 2026-07-28" -H "Mcp-Method: tools/call" -H "Mcp-Name: get_agent" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"get_agent\",\"arguments\":{\"app_id\":\"abc\"},$META}}"
```

The second call returns a tool execution error, as a normal result:

```json
{"jsonrpc":"2.0","id":2,"result":{"content":[{"text":"Error executing tool get_agent: app_id must be an agent identity's app ID (a GUID)","type":"text"}],"isError":true,"resultType":"complete"}}
```

## 5. Behaviour seen in this lab

These were observed on 28 October 2026 with MCP Python SDK 2.3.0:

| What was sent | What came back | What it shows |
|---|---|---|
| A modern request without `Mcp-Method`, or with one that does not match the body | Error `-32020`: "mcp-method header does not match the request body's method" | The server checks that the headers and the body agree |
| A request with `Host: evil.example:8100` | `421 Misdirected Request` | The DNS rebinding protection works ([C10](security-controls.md#the-mcp-server)) |
| `tools/call` for a tool that does not exist | A normal result with `isError: true` and "Unknown tool" | The SDK reports an unknown tool as a *tool execution error*, although the specification describes it as a *protocol error* (`-32602`). This matters for the tool allow-list. |
| Any call from the Inspector or curl | The MCP server's log shows the caller as `172.18.0.1` | Port publishing hides the real caller behind Docker's gateway, so a network address never proves who is calling (D1) |
