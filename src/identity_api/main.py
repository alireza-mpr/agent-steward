"""Identity API (FR3, FR5, FR8).

Receives the guardian's findings and accepts them only from an agent identity that holds
the app role Findings.Submit. Every decision becomes one JSON audit line on stdout.

Run: uvicorn main:app --host 0.0.0.0 --port 8000
"""

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Literal

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from token_validation import ApiSettings, Caller, TokenRejected, verify_token

SETTINGS = ApiSettings(
    tenant_id=os.environ["TENANT_ID"],
    audience=os.environ["IDENTITY_API_APP_ID"],
    required_role="Findings.Submit",
)

# Entra's public signing keys for this tenant. The client caches them, and fetches them
# again when a token names a key it does not know.
SIGNING_KEYS = jwt.PyJWKClient(
    f"https://login.microsoftonline.com/{SETTINGS.tenant_id}/discovery/v2.0/keys"
)

app = FastAPI(title="agent-steward Identity API")


def audit(**fields) -> None:
    """Write one audit line (FR8). Never pass a token in here."""
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), **fields}
    print(json.dumps(record), file=sys.stdout, flush=True)


def authenticate(request: Request) -> Caller:
    """Run the token checks before the endpoint, and before the request body is validated.

    FastAPI reads the body first, so broken JSON gets a 422 before this runs. A body that
    is valid JSON but does not match the schema gets its 422 only after the token passes.
    """
    correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
    request.state.correlation_id = correlation_id
    scheme, _, token = (request.headers.get("Authorization") or "").partition(" ")
    try:
        if scheme.lower() != "bearer" or not token:
            raise TokenRejected(401, "no bearer token")
        try:
            signing_key = SIGNING_KEYS.get_signing_key_from_jwt(token).key
        except jwt.PyJWTError as error:
            raise TokenRejected(401, f"signature: {error}") from error
        return verify_token(token, signing_key, SETTINGS, time.time())
    except TokenRejected as rejection:
        audit(
            event="request_rejected",
            correlation_id=correlation_id,
            path=request.url.path,
            client_app_id=rejection.client,
            outcome="rejected",
            status=rejection.status,
            reason=rejection.reason,
        )
        headers = {"WWW-Authenticate": 'Bearer error="invalid_token"'} if rejection.status == 401 else None
        raise HTTPException(rejection.status, detail=rejection.reason, headers=headers) from None


class Finding(BaseModel):
    agent_app_id: str
    agent_name: str
    check: Literal["orphan", "expiring_credential", "over_permissioned", "dormant"]
    detail: str
    date: str | None = None   # YYYY-MM-DD: when a credential ends, or when the agent last got a token


class FindingsReport(BaseModel):
    findings: list[Finding]


@app.post("/findings", status_code=202)
def submit_findings(report: FindingsReport, request: Request, caller: Caller = Depends(authenticate)) -> dict:
    correlation_id = request.state.correlation_id
    audit(
        event="findings_submitted",
        correlation_id=correlation_id,
        path=request.url.path,
        tool="submit_findings",
        agent_app_id=caller.agent_app_id,
        agent_object_id=caller.agent_object_id,
        blueprint_app_id=caller.blueprint_app_id,
        mode=caller.mode,
        delegating_user=None,   # autonomous mode: no user (a human) in the token
        approver=None,          # read-only findings need no approval (FR7 covers fixes)
        outcome="accepted",
        findings=len(report.findings),
    )
    for finding in report.findings:
        audit(event="finding", correlation_id=correlation_id, **finding.model_dump())
    return {"accepted": len(report.findings), "correlation_id": correlation_id}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
