"""Token checks for the Identity API (FR5).

The order and the reasons follow docs/tokens.md, "What an API must check". They are plain
functions with no network access, so they can be tested offline (NFR8).
"""

from dataclasses import dataclass

import jwt

AGENT_IDENTITY_FACET = "11"   # in xms_act_fct: the actor (the client) is an agent identity
CLOCK_SKEW_SECONDS = 60


class TokenRejected(Exception):
    """A failed check.

    status is 401 when the token is not valid for this API at all, and 403 when it is valid
    but the caller is not allowed. client is the caller's app ID once the signature is
    known to be good, so a rejection can still be attributed in the audit log.
    """

    def __init__(self, status: int, reason: str, client: str | None = None):
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.client = client


@dataclass(frozen=True)
class ApiSettings:
    tenant_id: str
    audience: str         # this API's client ID: v2 tokens carry it in aud
    required_role: str    # the app role a caller must hold, for example Findings.Submit

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"


@dataclass(frozen=True)
class Caller:
    """Who called, for the audit line (FR8)."""

    agent_app_id: str       # azp: the agent identity
    agent_object_id: str    # oid
    blueprint_app_id: str   # xms_par_app_azp: for logs only, never for authorization
    mode: str               # autonomous (idtyp app) or obo (idtyp user)


def verify_token(token: str, signing_key, settings: ApiSettings, now: float) -> Caller:
    """Check 1, the signature, then checks 2 to 6 on the claims."""
    try:
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            # The library checks only the signature here. Checks 2 to 4 run in check_claims,
            # so that each failure has its own reason, in the documented order.
            options={
                "verify_aud": False,
                "verify_iss": False,
                "verify_exp": False,
                "verify_nbf": False,
                "verify_iat": False,
            },
        )
    except jwt.InvalidTokenError as error:
        raise TokenRejected(401, f"signature: {error}") from error
    return check_claims(claims, settings, now)


def check_claims(claims: dict, settings: ApiSettings, now: float) -> Caller:
    """Checks 2 to 6. Raises TokenRejected at the first one that fails."""
    client = claims.get("azp") or claims.get("appid")

    # 2. Issuer and tenant.
    if claims.get("iss") != settings.issuer or claims.get("tid") != settings.tenant_id:
        raise TokenRejected(401, "issuer: not this tenant's v2.0 issuer", client)

    # 3. Audience. A token for another API, such as Microsoft Graph, stops here.
    if claims.get("aud") != settings.audience:
        raise TokenRejected(401, "audience: token is not for this API", client)

    # 4. Lifetime.
    nbf, exp = claims.get("nbf"), claims.get("exp")
    if not isinstance(nbf, (int, float)) or not isinstance(exp, (int, float)):
        raise TokenRejected(401, "lifetime: nbf or exp missing", client)
    if now < nbf - CLOCK_SKEW_SECONDS:
        raise TokenRejected(401, "lifetime: token not valid yet", client)
    if now >= exp + CLOCK_SKEW_SECONDS:
        raise TokenRejected(401, "lifetime: token expired", client)

    # 5. Agent. The actor facets are a space-separated list in any order, so look for 11
    # among the values. Never compare the whole string.
    if AGENT_IDENTITY_FACET not in str(claims.get("xms_act_fct", "")).split():
        raise TokenRejected(403, "agent: caller is not an agent identity", client)

    # 6. Permission. App-only tokens carry application permissions in roles, never in scp.
    roles = claims.get("roles")
    if not isinstance(roles, list) or settings.required_role not in roles:
        raise TokenRejected(403, f"permission: missing app role {settings.required_role}", client)

    return Caller(
        agent_app_id=client or "",
        agent_object_id=claims.get("oid", ""),
        blueprint_app_id=claims.get("xms_par_app_azp", ""),
        mode={"app": "autonomous", "user": "obo"}.get(claims.get("idtyp"), "unknown"),
    )
