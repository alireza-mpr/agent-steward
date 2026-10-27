"""Offline tests for the Identity API's token checks (FR5, NFR8).

Tokens are signed with a throwaway key. The claims copy a real agent token from this lab
(docs/tokens.md), with made-up IDs.
"""

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from token_validation import ApiSettings, TokenRejected, check_claims, verify_token

TENANT = "00000000-0000-0000-0000-0000000000aa"
API = "00000000-0000-0000-0000-0000000000a1"
AGENT = "00000000-0000-0000-0000-0000000000a2"
BLUEPRINT = "00000000-0000-0000-0000-0000000000b1"
GRAPH = "00000003-0000-0000-c000-000000000000"
SETTINGS = ApiSettings(tenant_id=TENANT, audience=API, required_role="Findings.Submit")
NOW = 1_800_000_000


def agent_claims(**changes) -> dict:
    """The guardian's token for the Identity API. A change to None removes that claim."""
    claims = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "tid": TENANT,
        "aud": API,
        "azp": AGENT,
        "oid": AGENT,
        "sub": AGENT,
        "idtyp": "app",
        "roles": ["Findings.Submit"],
        "xms_act_fct": "11 9 3",
        "xms_sub_fct": "9 11 3",
        "xms_par_app_azp": BLUEPRINT,
        "iat": NOW - 60,
        "nbf": NOW - 60,
        "exp": NOW + 3600,
    }
    claims.update(changes)
    return {name: value for name, value in claims.items() if value is not None}


def rejected(claims: dict) -> TokenRejected:
    with pytest.raises(TokenRejected) as failure:
        check_claims(claims, SETTINGS, NOW)
    return failure.value


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


# Accepted

def test_real_agent_token_is_accepted(key):
    token = jwt.encode(agent_claims(), key, algorithm="RS256")
    caller = verify_token(token, key.public_key(), SETTINGS, NOW)
    assert caller.agent_app_id == AGENT
    assert caller.blueprint_app_id == BLUEPRINT
    assert caller.mode == "autonomous"


@pytest.mark.parametrize("facets", ["11 9 3", "3 11 9", "9 3 11", "11"])
def test_actor_facets_are_accepted_in_any_order(facets):
    assert check_claims(agent_claims(xms_act_fct=facets), SETTINGS, NOW).agent_app_id == AGENT


# Check 1: signature

def test_token_signed_with_another_key_is_rejected(key):
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(agent_claims(), other_key, algorithm="RS256")
    with pytest.raises(TokenRejected) as failure:
        verify_token(token, key.public_key(), SETTINGS, NOW)
    assert (failure.value.status, failure.value.reason.split(":")[0]) == (401, "signature")


def test_unsigned_token_is_rejected(key):
    token = jwt.encode(agent_claims(), None, algorithm="none")
    with pytest.raises(TokenRejected) as failure:
        verify_token(token, key.public_key(), SETTINGS, NOW)
    assert failure.value.status == 401


# Check 2: issuer and tenant

@pytest.mark.parametrize("issuer", [
    "https://login.microsoftonline.com/00000000-0000-0000-0000-0000000000ff/v2.0",   # another tenant
    f"https://sts.windows.net/{TENANT}/",                                            # a v1.0 token
])
def test_token_from_another_issuer_is_rejected(issuer):
    failure = rejected(agent_claims(iss=issuer))
    assert (failure.status, failure.reason.split(":")[0]) == (401, "issuer")


def test_token_from_another_tenant_id_is_rejected():
    assert rejected(agent_claims(tid="00000000-0000-0000-0000-0000000000ff")).status == 401


# Check 3: audience

def test_token_for_another_api_is_rejected():
    failure = rejected(agent_claims(aud=GRAPH))
    assert (failure.status, failure.reason.split(":")[0]) == (401, "audience")


# Check 4: lifetime

def test_expired_token_is_rejected():
    failure = rejected(agent_claims(exp=NOW - 120))
    assert (failure.status, failure.reason) == (401, "lifetime: token expired")


def test_token_not_valid_yet_is_rejected():
    failure = rejected(agent_claims(nbf=NOW + 120))
    assert (failure.status, failure.reason) == (401, "lifetime: token not valid yet")


# Check 5: agent

def test_blueprint_own_token_is_rejected():
    # The real token the sidecar returned when its agent identity override was off.
    blueprint_token = agent_claims(
        azp=BLUEPRINT, oid=BLUEPRINT, sub=BLUEPRINT,
        roles=["AgentIdentity.CreateAsManager"],
        xms_act_fct="3 9", xms_sub_fct="3 9", xms_par_app_azp=None,
    )
    failure = rejected(blueprint_token)
    assert (failure.status, failure.reason.split(":")[0], failure.client) == (403, "agent", BLUEPRINT)


def test_token_without_agent_claims_is_rejected():
    # What every token looked like before the API's app registration asked for the optional claims.
    failure = rejected(agent_claims(xms_act_fct=None, xms_sub_fct=None, xms_par_app_azp=None, idtyp=None))
    assert (failure.status, failure.reason.split(":")[0]) == (403, "agent")


def test_agent_user_facet_alone_is_not_an_agent_identity():
    assert rejected(agent_claims(xms_act_fct="13")).status == 403


# Check 6: permission

@pytest.mark.parametrize("roles", [None, [], ["Other.Role"], "Findings.Submit"])
def test_agent_without_the_app_role_is_rejected(roles):
    # A string is not a list: "in" would match a substring, so it must fail too.
    failure = rejected(agent_claims(roles=roles))
    assert (failure.status, failure.reason) == (403, "permission: missing app role Findings.Submit")


# Order

def test_checks_run_in_the_documented_order():
    # A Graph token from the blueprint fails two checks. The audience check (3) comes first.
    failure = rejected(agent_claims(aud=GRAPH, xms_act_fct="3 9"))
    assert failure.reason.split(":")[0] == "audience"
