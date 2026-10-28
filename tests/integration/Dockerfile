FROM python:3.13-slim

RUN pip install --no-cache-dir pytest==9.1.1 mcp==2.3.0   # mcp: the end-to-end test is an MCP client
WORKDIR /tests

USER nobody
# The test folder is mounted read-only, so pytest keeps no cache.
CMD ["pytest", "-v", "-m", "integration", "-p", "no:cacheprovider", "/tests"]
