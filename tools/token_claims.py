"""Ask the sidecar for a token as the guardian's agent identity and print its claims.

Usage (inside the sidecar's network namespace): python token_claims.py <api>
<api> is required: the name of a downstream API in the sidecar's config, for example graph.

Prints only the decoded claims, never the token itself, so the output cannot be
replayed. The claims hold real tenant and object IDs: redact them in any screenshot.
"""

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def decode_claims(token: str) -> dict:
    """Decode a JWT's payload without checking the signature. For display only."""
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def main() -> int:
    parser = argparse.ArgumentParser(description="Print the claims of the guardian's token for one API.")
    parser.add_argument("api", help="name of a downstream API in the sidecar's config, for example graph")
    api = parser.parse_args().api
    sidecar = os.environ.get("SIDECAR_URL", "http://127.0.0.1:5000")
    agent = os.environ["GUARDIAN_AGENT_APP_ID"]

    query = urllib.parse.urlencode({"AgentIdentity": agent})
    url = f"{sidecar}/AuthorizationHeaderUnauthenticated/{urllib.parse.quote(api)}?{query}"
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            header = json.load(response)["authorizationHeader"]
    except urllib.error.HTTPError as error:
        print(f"Sidecar returned {error.code}: {error.read().decode(errors='replace')}", file=sys.stderr)
        return 1

    scheme, _, token = header.partition(" ")
    print(f"Scheme: {scheme}")
    print(json.dumps(decode_claims(token), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
