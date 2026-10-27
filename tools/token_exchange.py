"""Dev tool: get T1 and T2 the way the sidecar does, and print both tokens' claims.

It makes the two requests of the agent sign-in itself (docs/agent-id.md, "How an agent
signs in"), so that T1, which the sidecar keeps to itself, can be seen.

Dev-only exception: it reads the guardian blueprint's private key from secrets/. In normal
runs only the token sidecar holds that key. Run this only on demand, on your own machine.

Usage, from the repo root:
    .venv/bin/python tools/token_exchange.py [--api identity-api|graph] [--mask]

It prints decoded headers and claims, never a raw token. --mask keeps only the first and
last 4 characters of IDs and opaque values, for screenshots and docs.
"""

import argparse
import base64
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import pkcs12

REPO = Path(__file__).resolve().parent.parent
BLUEPRINT_PFX = REPO / "secrets" / "agent-steward-guardian.pfx"
BLUEPRINT_PFX_PASSWORD = REPO / "secrets" / "agent-steward-guardian.sidecar.env"

GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
# T1's sub is an FMI path, /eid1/c/pub/t/<tenant>/a/<blueprint>/<agent>, where the tenant and the
# blueprint are GUIDs written as 22 characters of base64url.
BASE64_GUID_SEGMENT = re.compile(r"(?<=/)[A-Za-z0-9_-]{22}(?=/|$)")
OPAQUE_CLAIMS = {"aio", "rh", "uti", "xms_ftd", "xms_rd"}   # per-token values, no meaning to read


def read_env_file(path: Path) -> dict:
    pairs = (line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and not line.startswith("#"))
    return {name.strip(): value.strip() for name, value in pairs}


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def decode_part(token: str, index: int) -> dict:
    part = token.split(".")[index]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def shorten(value: str) -> str:
    return value if len(value) <= 10 else f"{value[:4]}…{value[-4:]}"


def mask(fields: dict) -> dict:
    def scrub(value):
        if isinstance(value, str):
            value = GUID.sub(lambda match: shorten(match.group(0)), value)
            return BASE64_GUID_SEGMENT.sub(lambda match: shorten(match.group(0)), value)
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    return {name: shorten(value) if name in OPAQUE_CLAIMS and isinstance(value, str) else scrub(value)
            for name, value in fields.items()}


def request_token(token_url: str, form: dict) -> str:
    body = urllib.parse.urlencode(form).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(token_url, data=body), timeout=60) as response:
            return json.load(response)["access_token"]
    except urllib.error.HTTPError as error:
        sys.exit(f"Entra returned {error.code}: {json.load(error).get('error_description', '')}")


def blueprint_assertion(token_url: str, blueprint_app_id: str) -> str:
    """The blueprint's proof for request 1: a short JWT signed with its private key."""
    password = read_env_file(BLUEPRINT_PFX_PASSWORD)["AzureAd__ClientCredentials__0__CertificatePassword"]
    key, certificate, _ = pkcs12.load_key_and_certificates(BLUEPRINT_PFX.read_bytes(), password.encode())
    now = int(time.time())
    return jwt.encode(
        {"aud": token_url, "iss": blueprint_app_id, "sub": blueprint_app_id,
         "jti": str(uuid.uuid4()), "nbf": now, "iat": now, "exp": now + 300},
        key,
        algorithm="RS256",
        headers={"x5t": b64url(certificate.fingerprint(hashes.SHA1()))},   # tells Entra which certificate
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Get T1 and T2 for the guardian and print their claims.")
    parser.add_argument("--api", choices=["identity-api", "graph"], default="identity-api", help="the API T2 is for")
    parser.add_argument("--mask", action="store_true", help="keep only the first and last 4 characters of IDs")
    args = parser.parse_args()

    env = read_env_file(REPO / ".env")
    token_url = f"https://login.microsoftonline.com/{env['TENANT_ID']}/oauth2/v2.0/token"
    scope = {"identity-api": f"{env['IDENTITY_API_APP_ID']}/.default",
             "graph": "https://graph.microsoft.com/.default"}[args.api]
    assertion_type = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"

    # Request 1: the client is the blueprint, proving itself with its key, for one agent identity.
    t1 = request_token(token_url, {
        "client_id": env["GUARDIAN_BLUEPRINT_APP_ID"],
        "scope": "api://AzureADTokenExchange/.default",
        "fmi_path": env["GUARDIAN_AGENT_APP_ID"],
        "client_assertion_type": assertion_type,
        "client_assertion": blueprint_assertion(token_url, env["GUARDIAN_BLUEPRINT_APP_ID"]),
        "grant_type": "client_credentials",
    })

    # Request 2: the client is the agent identity, and T1 is its proof.
    t2 = request_token(token_url, {
        "client_id": env["GUARDIAN_AGENT_APP_ID"],
        "scope": scope,
        "client_assertion_type": assertion_type,
        "client_assertion": t1,
        "grant_type": "client_credentials",
    })

    show = mask if args.mask else (lambda fields: fields)
    for title, token in (("T1, from request 1 (client: the blueprint)", t1),
                         (f"T2, from request 2 (client: the agent identity, API: {args.api})", t2)):
        print(f"=== {title}")
        print("header:", json.dumps(show(decode_part(token, 0)), indent=2, sort_keys=True, ensure_ascii=False))
        print("claims:", json.dumps(show(decode_part(token, 1)), indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
