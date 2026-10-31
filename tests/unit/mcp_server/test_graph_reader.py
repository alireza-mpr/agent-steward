"""Unit tests for turning Graph's JSON into the rules' shapes (FR3, NFR8).

The sample JSON copies the shape of real Graph answers from this lab, with made-up IDs.
"""

from datetime import datetime, timezone

from graph_reader import NO_ROLE, disable_agent_identity, parse_agent_details, parse_agents, parse_blueprints, parse_last_sign_ins, permission_names

GRAPH_SP = "sp-graph"
USER_READ_ALL = "role-user-read-all"
ROLE_VALUES = {GRAPH_SP: {USER_READ_ALL: "User.Read.All"}}


def test_blueprint_credentials_become_end_times():
    [blueprint] = parse_blueprints([{
        "appId": "bp-1", "displayName": "agent-steward-fixture-expiring",
        "passwordCredentials": [{"endDateTime": "2026-11-02T07:58:00Z"}],
        "keyCredentials": [{"endDateTime": "2026-12-10T05:04:19Z"}],
    }])
    assert blueprint.credential_end_times == (datetime(2026, 11, 2, 7, 58, tzinfo=timezone.utc),
                                              datetime(2026, 12, 10, 5, 4, 19, tzinfo=timezone.utc))


def test_a_blueprint_without_credentials_has_no_end_times():
    [blueprint] = parse_blueprints([{"appId": "bp-2", "displayName": "agent-steward-fixture",
                                     "passwordCredentials": [], "keyCredentials": []}])
    assert blueprint.credential_end_times == ()


def test_agents_get_their_sponsor_count_and_permissions():
    agents = parse_agents([
        {"id": "o-1", "appId": "a-1", "displayName": "agent-steward-fixture-overperm-01",
         "agentIdentityBlueprintId": "bp-2", "sponsors": [{"id": "u-1"}]},
        {"id": "o-2", "appId": "a-2", "displayName": "agent-steward-fixture-orphan-01",
         "agentIdentityBlueprintId": "bp-2", "sponsors": []},
    ], {"o-1": frozenset({"Microsoft Graph/User.Read.All"})})
    assert [(agent.name, agent.sponsor_count, agent.permissions) for agent in agents] == [
        ("agent-steward-fixture-overperm-01", 1, frozenset({"Microsoft Graph/User.Read.All"})),
        ("agent-steward-fixture-orphan-01", 0, frozenset()),
    ]


def test_role_ids_become_api_and_role_names():
    assignments = [{"resourceId": GRAPH_SP, "resourceDisplayName": "Microsoft Graph", "appRoleId": USER_READ_ALL}]
    assert permission_names(assignments, ROLE_VALUES) == frozenset({"Microsoft Graph/User.Read.All"})


def test_an_assignment_without_a_role_is_ignored():
    assignments = [{"resourceId": GRAPH_SP, "resourceDisplayName": "Microsoft Graph", "appRoleId": NO_ROLE}]
    assert permission_names(assignments, ROLE_VALUES) == frozenset()


def test_a_role_that_cannot_be_named_keeps_its_id():
    # Still reported, so the over-permissioned rule cannot miss it.
    assignments = [{"resourceId": "sp-unknown", "resourceDisplayName": "Some API", "appRoleId": "role-x"}]
    assert permission_names(assignments, ROLE_VALUES) == frozenset({"Some API/role-x"})


def test_only_the_agents_own_sign_ins_count_and_the_newest_wins():
    events = [
        {"appId": "a-1", "createdDateTime": "2026-10-26T08:45:18Z", "agent": {"agentType": "agenticAppInstance"}},
        {"appId": "a-1", "createdDateTime": "2026-10-26T06:49:17Z", "agent": {"agentType": "agenticAppInstance"}},
        # The blueprint principal's sign-in for the same token (request 1) does not count.
        {"appId": "bp-1", "createdDateTime": "2026-10-26T08:45:18Z", "agent": {"agentType": "agentIdentityBlueprintPrincipal"}},
        # A sign-in by an app that is not an agent at all.
        {"appId": "app-1", "createdDateTime": "2026-10-26T09:00:00Z", "agent": None},
    ]
    assert parse_last_sign_ins(events) == {"a-1": datetime(2026, 10, 26, 8, 45, 18, tzinfo=timezone.utc)}


def test_agent_details_keep_the_description_and_an_empty_answer_means_no_agent():
    [item] = [{"appId": "a-1", "displayName": "agent-steward-fixture-orphan-01",
               "agentIdentityBlueprintId": "bp-1", "description": None}]

    assert parse_agent_details([item]).description == ""
    assert parse_agent_details([]) is None


def test_a_failed_sign_in_is_not_activity():
    # Real case from this lab: a parent-check test logged a failed request 2 for the dormant fixture.
    events = [{"appId": "a-dormant", "createdDateTime": "2026-10-27T06:17:40Z",
               "agent": {"agentType": "agenticAppInstance"}, "status": {"errorCode": 7002134}},
              {"appId": "a-1", "createdDateTime": "2026-10-26T08:45:18Z",
               "agent": {"agentType": "agenticAppInstance"}, "status": {"errorCode": 0}}]
    assert parse_last_sign_ins(events) == {"a-1": datetime(2026, 10, 26, 8, 45, 18, tzinfo=timezone.utc)}


# The one write (FR7)

class FakeGraph:
    """Answers reads with the next accountEnabled value. The last one repeats."""

    def __init__(self, *enabled_values):
        self.enabled_values = list(enabled_values)
        self.patches = []

    def get_all(self, path, params=None):
        return [{"id": "object-id"}]

    def patch(self, path, body):
        self.patches.append((path, body))

    def get(self, path, params=None):
        value = self.enabled_values.pop(0) if len(self.enabled_values) > 1 else self.enabled_values[0]
        return {"accountEnabled": value}


def test_disable_agent_identity_writes_accountenabled_false_and_confirms_with_a_read():
    graph = FakeGraph(True, True, False)   # the first reads still show the old value

    assert disable_agent_identity(graph, "app-id", sleep=lambda seconds: None) is True
    assert graph.patches == [("/beta/servicePrincipals/object-id/microsoft.graph.agentIdentity", {"accountEnabled": False})]


def test_disable_agent_identity_says_so_when_no_read_shows_the_change():
    assert disable_agent_identity(FakeGraph(True), "app-id", sleep=lambda seconds: None) is False
