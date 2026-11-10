"""Unit tests for the one write: disabling an agent identity (FR7, FR4, NFR8)."""

import pytest

from graph_writer import disable_agent_identity


class FakeReader:
    """The guardian's app-only read client. Answers reads with the next accountEnabled value; the
    last one repeats. It has no patch, so a write through it fails the test."""

    def __init__(self, *enabled_values):
        self.enabled_values = list(enabled_values)
        self.reads = 0

    def get_all(self, path, params=None):
        self.reads += 1
        return [{"id": "object-id"}]

    def get(self, path, params=None):
        self.reads += 1
        value = self.enabled_values.pop(0) if len(self.enabled_values) > 1 else self.enabled_values[0]
        return {"accountEnabled": value}


class FakeWriter:
    """The approver's OBO client. It may only PATCH: the OBO token cannot read agent identities."""

    def __init__(self):
        self.patches = []

    def patch(self, path, body):
        self.patches.append((path, body))

    def get(self, *arguments):
        raise AssertionError("the OBO token cannot read agent identities: reads go through the reader")

    get_all = get


def test_disable_agent_identity_writes_accountenabled_false_and_confirms_with_a_read():
    reader, writer = FakeReader(True, True, False), FakeWriter()   # the first reads still show the old value

    assert disable_agent_identity(reader, writer, "app-id", sleep=lambda seconds: None) is True
    assert writer.patches == [("/beta/servicePrincipals/object-id/microsoft.graph.agentIdentity", {"accountEnabled": False})]


def test_only_the_patch_uses_the_obo_client_and_every_read_uses_the_reader():
    reader, writer = FakeReader(False), FakeWriter()

    disable_agent_identity(reader, writer, "app-id", sleep=lambda seconds: None)

    assert len(writer.patches) == 1 and reader.reads == 2   # the lookup and one confirming read


def test_the_reader_cannot_write():
    with pytest.raises(AttributeError):
        disable_agent_identity(FakeReader(True), FakeReader(True), "app-id", sleep=lambda seconds: None)


def test_disable_agent_identity_says_so_when_no_read_shows_the_change():
    assert disable_agent_identity(FakeReader(True), FakeWriter(), "app-id", sleep=lambda seconds: None) is False
