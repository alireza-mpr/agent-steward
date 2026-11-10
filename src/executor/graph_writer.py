"""The one write: disabling an agent identity, after a human approved it (FR7), with the approver's
own rights (FR4). Only the executor holds this code: the MCP server can read Graph, never write.

Two clients, because the OBO token can write but not read. It carries only the guardian's delegated
AgentIdentity.EnableDisable.All (setup/bicep/approver-delegation.bicep), which allows the PATCH
(Graph API docs, "Update agentIdentity") but not reading agent identities: that needs at least
AgentIdentity.Read.All ("List agentIdentity objects"). So the lookup and the confirming read use the
guardian's own app-only, read-only token, and only the PATCH uses the approver's rights.
"""

import json
import time
import urllib.request
from collections.abc import Callable

from graph_reader import GRAPH, GraphClient


class GraphWriter(GraphClient):
    """The reader's client plus PATCH. Only the executor has it: the MCP server's image holds no
    write method at all."""

    def patch(self, path: str, body: dict) -> None:
        request = urllib.request.Request(GRAPH + path, data=json.dumps(body).encode(), method="PATCH",
                                         headers={"Authorization": f"Bearer {self._get_token()}",
                                                  "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=60):
            pass


CONFIRM_TRIES = 6
CONFIRM_EVERY_SECONDS = 5


def disable_agent_identity(reader: GraphClient, writer: GraphWriter, app_id: str,
                           sleep: Callable[[float], None] = time.sleep) -> bool:
    """Set accountEnabled to false on one agent identity, so it can get no new tokens. reader holds
    the guardian's app-only read token, writer the approver's OBO token. Returns whether a read
    showed the change: Graph is eventually consistent, and in this lab a read still showed the old
    value about 10 seconds after the write. The caller must check that app_id is a GUID first."""
    [item] = reader.get_all("/v1.0/servicePrincipals/microsoft.graph.agentIdentity",
                            {"$filter": f"appId eq '{app_id}'", "$select": "id"})
    path = f"/beta/servicePrincipals/{item['id']}/microsoft.graph.agentIdentity"
    writer.patch(path, {"accountEnabled": False})
    for _ in range(CONFIRM_TRIES):
        if reader.get(path, {"$select": "accountEnabled"}).get("accountEnabled") is False:
            return True
        sleep(CONFIRM_EVERY_SECONDS)
    return False
