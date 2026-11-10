# Built from src/ (docker-compose.yml), because the executor reuses three of the MCP server's
# modules: the Graph client and the checks (the same rule in both places), and the approval
# service client. It does not get server.py, the MCP SDK or the tools.
FROM python:3.13-slim

WORKDIR /app
COPY executor/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY mcp_server/approval.py mcp_server/disable_checks.py mcp_server/graph_reader.py mcp_server/rules.py ./
COPY executor/graph_writer.py executor/executor.py ./

USER nobody
EXPOSE 8200
CMD ["uvicorn", "executor:app", "--host", "0.0.0.0", "--port", "8200"]
