"""Token checks for the Identity API (FR5).

The order and the reasons follow docs/tokens.md, "What an API must check". They are plain
functions with no network access, so they can be tested offline (NFR8).
"""

from dataclasses import dataclass

import jwt

AGENT_IDENTITY_FACET = "11"   # in xms_act_fct: the actor (the client) is an agent identity
AGENT_USER_FACET = "13"       # in xms_sub_fct: the subject is an agent's user account
DECIDE_SCOPE = "AgentDisable.Decide"
APPROVE_ROLE = "AgentDisable.Approve"
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
    audience: str               # this API's client ID: v2 tokens carry it in aud
    approver_client_id: str     # the only client app an approver may use: the approve command's

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"


@dataclass(frozen=True)
class Caller:
    """Which agent called, for the audit line (FR8)."""

    agent_app_id: str       # azp: the agent identity
    agent_object_id: str    # oid
    blueprint_app_id: str   # xms_par_app_azp: for logs only, never for authorization
    mode: str               # autonomous (idtyp app) or obo (idtyp user)


@dataclass(frozen=True)
class Approver:
    """Which person decided, for the audit line (FR7). Only from the token, never from a request body."""

    object_id: str          # oid
    name: str
    client_app_id: str      # azp: the app the person used


def check_signature(token: str, signing_key) -> dict:
    """Check 1. Returns the claims."""
    try:
        return jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            # The library checks only the signature here. Checks 2 to 4 run in check_shared,
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


def client_of(claims: dict) -> str | None:
    return claims.get("azp") or claims.get("appid")


def check_shared(claims: dict, settings: ApiSettings, now: float) -> None:
    """Checks 2 to 4, for every caller. Raises TokenRejected at the first one that fails."""
    client = client_of(claims)

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


def holds_role(claims: dict, role: str) -> bool:
    # A string is not a list: "in" would match a substring, so it must fail too.
    roles = claims.get("roles")
    return isinstance(roles, list) and role in roles


def check_agent(claims: dict, settings: ApiSettings, role: str, now: float) -> Caller:
    """Checks 2 to 6 for an agent that must hold this app role."""
    check_shared(claims, settings, now)
    client = client_of(claims)

    # 5. Agent. The actor facets are a space-separated list in any order, so look for 11
    # among the values. Never compare the whole string.
    if AGENT_IDENTITY_FACET not in str(claims.get("xms_act_fct", "")).split():
        raise TokenRejected(403, "agent: caller is not an agent identity", client)

    # 6. Permission. App-only tokens carry application permissions in roles, never in scp.
    if not holds_role(claims, role):
        raise TokenRejected(403, f"permission: missing app role {role}", client)

    return Caller(
        agent_app_id=client or "",
        agent_object_id=claims.get("oid", ""),
        blueprint_app_id=claims.get("xms_par_app_azp", ""),
        mode={"app": "autonomous", "user": "obo"}.get(claims.get("idtyp"), "unknown"),
    )


def check_approver(claims: dict, settings: ApiSettings, now: float) -> Approver:
    """Checks 2 to 4, then that a human uses the approve command and may approve (FR7). A missing
    claim fails: an agent must never be able to approve, not even its own request."""
    check_shared(claims, settings, now)
    client = client_of(claims)

    # A person, signed in: not an app, not an agent, not an agent's user account.
    if claims.get("idtyp") != "user":
        raise TokenRejected(403, "approver: idtyp claim is not user", client)
    if AGENT_IDENTITY_FACET in str(claims.get("xms_act_fct", "")).split():
        raise TokenRejected(403, "approver: the client is an agent identity", client)
    if AGENT_USER_FACET in str(claims.get("xms_sub_fct", "")).split():
        raise TokenRejected(403, "approver: the user is an agent's user account", client)

    # The right app. The scope already limits who can get this token, and this still holds if
    # someone grants that scope to another app by mistake.
    if not settings.approver_client_id or client != settings.approver_client_id:
        raise TokenRejected(403, "approver: not the approve command's client app", client)

    # The app may act for the person (scp), and the person may approve (roles).
    if DECIDE_SCOPE not in str(claims.get("scp", "")).split():
        raise TokenRejected(403, f"permission: missing scope {DECIDE_SCOPE}", client)
    if not holds_role(claims, APPROVE_ROLE):
        raise TokenRejected(403, f"permission: missing app role {APPROVE_ROLE}", client)

    object_id = claims.get("oid")
    if not object_id:
        raise TokenRejected(403, "approver: no oid, so the decision could not be recorded", client)
    return Approver(object_id, claims.get("name") or claims.get("preferred_username") or "", client)


def verify_agent(token: str, signing_key, settings: ApiSettings, role: str, now: float) -> Caller:
    return check_agent(check_signature(token, signing_key), settings, role, now)


def verify_approver(token: str, signing_key, settings: ApiSettings, now: float) -> Approver:
    return check_approver(check_signature(token, signing_key), settings, now)
