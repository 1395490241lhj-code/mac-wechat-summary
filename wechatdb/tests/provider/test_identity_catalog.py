"""IdentityCatalog converts explicit source evidence into resolver inputs."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
import sqlite3
from pathlib import Path

import pytest

from conversation_identity import conversation_identifier
from wechatdb.provider import (
    ExplicitShardLocator,
    IdentityCatalog,
    NAME_CONTACT_NICKNAME,
    NAME_CONTACT_REMARK,
    NameCandidate,
    ShardEntry,
    ShardedMessageProvider,
)
from wechatdb.provider import identity_catalog as catalog_module

from . import fixtures
from .fixtures import ALPHA, BETA, ROOM, SyntheticMessage as M


SESSION_DIGEST = "bace2842a0707f217e849ac5538eed53"
ALPHA_DIGEST = "c5c1fbfecc5787de56152869a5ed9f02"
BETA_DIGEST = "57767ee4fa19275031bd1ea6e1fc0cb3"


class TrackingConnection(sqlite3.Connection):
    closed = False

    def close(self) -> None:
        self.closed = True
        super().close()


class TrackingOpener:
    def __init__(self) -> None:
        self.entries: list[ShardEntry] = []
        self.connections: list[TrackingConnection] = []

    def open(self, entry: ShardEntry) -> sqlite3.Connection:
        self.entries.append(entry)
        connection = sqlite3.connect(entry.handle, factory=TrackingConnection)
        self.connections.append(connection)
        return connection


def sqlite_file(tmp_path, name: str, statements: tuple[str, ...]):
    path = tmp_path / name
    connection = sqlite3.connect(path)
    try:
        for statement in statements:
            connection.execute(statement)
        connection.commit()
    finally:
        connection.close()
    return path


def session_file(tmp_path, *usernames: object):
    path = sqlite_file(tmp_path, "fixture_session.db", (
        "CREATE TABLE SessionTable (username TEXT)",
    ))
    connection = sqlite3.connect(path)
    try:
        connection.executemany(
            "INSERT INTO SessionTable VALUES (?)", ((username,) for username in usernames))
        connection.commit()
    finally:
        connection.close()
    return path


def contact_file(tmp_path, *rows: tuple[object, object, object]):
    path = sqlite_file(tmp_path, "fixture_contact.db", (
        "CREATE TABLE contact (username TEXT, remark TEXT, nick_name TEXT)",
    ))
    connection = sqlite3.connect(path)
    try:
        connection.executemany("INSERT INTO contact VALUES (?, ?, ?)", rows)
        connection.commit()
    finally:
        connection.close()
    return path


def entry(name: str, path) -> ShardEntry:
    return ShardEntry(name=name, handle=path)


def part_entry(part: fixtures.SyntheticPart) -> ShardEntry:
    return entry(part.name, part.path)


def test_session_username_produces_parser_shaped_identity(tmp_path):
    path = session_file(tmp_path, ROOM)
    opener = TrackingOpener()

    catalog = IdentityCatalog.build(
        opener=opener,
        session=ShardEntry("session", path),
    )

    assert catalog.resolver().resolve().session_names == {
        SESSION_DIGEST: ROOM,
    }
    assert len(opener.connections) == 1 and opener.connections[0].closed


def test_contact_username_contributes_identity_and_exact_display_candidates(tmp_path):
    path = contact_file(tmp_path,
        (ALPHA, "Fixture Alpha Remark", "Fixture Alpha Nickname"),
        (BETA, "", None),
    )

    catalog = IdentityCatalog.build(
        opener=TrackingOpener(), contact=entry("contact", path))
    resolved = catalog.resolver().resolve(room=ROOM)

    assert resolved.session_names == {ALPHA_DIGEST: ALPHA, BETA_DIGEST: BETA}
    assert resolved.display_names == {ALPHA: "Fixture Alpha Remark"}
    assert catalog.candidates == (
        NameCandidate(ALPHA, NAME_CONTACT_NICKNAME, "Fixture Alpha Nickname"),
        NameCandidate(ALPHA, NAME_CONTACT_REMARK, "Fixture Alpha Remark"),
    )


def test_conflicting_contact_remarks_survive_exact_duplicate_deduplication(tmp_path):
    path = contact_file(tmp_path,
        (ALPHA, "Fixture Remark One", "Fixture Nickname"),
        (ALPHA, "Fixture Remark One", "Fixture Nickname"),
        (ALPHA, "Fixture Remark Two", "Fixture Nickname"),
    )

    catalog = IdentityCatalog.build(
        opener=TrackingOpener(), contact=entry("contact", path))
    remarks = tuple(candidate for candidate in catalog.candidates
                    if candidate.kind == NAME_CONTACT_REMARK)
    resolved = catalog.resolver().resolve()

    assert {candidate.name for candidate in remarks} == {
        "Fixture Remark One", "Fixture Remark Two"}
    assert len(remarks) == 2
    assert ALPHA not in resolved.display_names and resolved.unresolved == 1


def test_name2id_values_contribute_identity_and_absence_contributes_nothing(tmp_path):
    with_names = fixtures.readable_part(tmp_path, "message_0.db", ROOM, (
        M(1, 100, ALPHA, "fixture one"),
        M(2, 200, BETA, "fixture two"),
    ))
    without_names = fixtures.readable_part(tmp_path, "message_1.db", ROOM, (
        M(3, 300, ALPHA, "fixture three"),
    ))
    connection = without_names.open()
    try:
        connection.execute("DROP TABLE Name2Id")
        connection.commit()
    finally:
        connection.close()

    catalog = IdentityCatalog.build(
        opener=TrackingOpener(),
        message_parts=(part_entry(with_names), part_entry(without_names)),
    )

    assert catalog.resolver().resolve().session_names == {
        ALPHA_DIGEST: ALPHA, BETA_DIGEST: BETA}


def test_all_three_source_roles_jointly_populate_one_catalog(tmp_path):
    session = session_file(tmp_path, ROOM)
    contact = contact_file(tmp_path, (ALPHA, "Fixture Alpha Remark", ""))
    part = fixtures.readable_part(tmp_path, "message_0.db", ROOM, (
        M(1, 100, BETA, "fixture message"),
    ))
    opener = TrackingOpener()

    catalog = IdentityCatalog.build(
        opener=opener,
        session=entry("session", session),
        contact=entry("contact", contact),
        message_parts=(part_entry(part),),
    )

    assert catalog.session_names == (
        (BETA_DIGEST, BETA),
        (SESSION_DIGEST, ROOM),
        (ALPHA_DIGEST, ALPHA),
    )
    assert catalog.resolver().resolve().display_names == {
        ALPHA: "Fixture Alpha Remark"}
    assert len(opener.entries) == 3 and all(c.closed for c in opener.connections)


def test_different_usernames_with_one_digest_fail_closed_without_content(
    tmp_path, monkeypatch,
):
    secret_one = "fixture_secret_alpha"
    secret_two = "fixture_secret_beta"
    path = session_file(tmp_path, secret_one, secret_two)
    monkeypatch.setattr(catalog_module, "_session_digest", lambda username: "same")

    with pytest.raises(ValueError, match="^session identity collision$") as refusal:
        IdentityCatalog.build(
            opener=TrackingOpener(), session=entry("session", path))

    assert secret_one not in str(refusal.value) and secret_two not in str(refusal.value)


def test_source_strings_are_exact_and_empty_values_are_not_evidence(tmp_path):
    spaced = "  Fixture Mixed  "
    path = session_file(tmp_path, spaced, "FixtureCase", "fixturecase", "", None)

    resolved = IdentityCatalog.build(
        opener=TrackingOpener(), session=entry("session", path)).resolver().resolve()

    assert resolved.session_names == {
        "7bf06407ce6da376be5348fbdd704a0b": spaced,
        "100bc7ecd6dc654ea9f28abbd720668a": "FixtureCase",
        "628aaedd98dc252d66be93997778137e": "fixturecase",
    }


def test_missing_optional_sources_are_allowed_and_open_nothing():
    opener = TrackingOpener()

    catalog = IdentityCatalog.build(opener=opener)

    assert catalog.session_names == () and catalog.candidates == ()
    assert catalog.resolver().resolve().session_names == {}
    assert opener.entries == [] and opener.connections == []


@pytest.mark.parametrize("statements", [
    ("CREATE TABLE fixture_other (username TEXT)",),
    ("CREATE TABLE SessionTable (fixture_id TEXT)",),
])
def test_unsupported_supplied_session_schema_is_fixed_copy_and_closes(
    tmp_path, statements,
):
    secret = "fixture_secret_session.db"
    path = sqlite_file(tmp_path, secret, statements)
    opener = TrackingOpener()

    with pytest.raises(ValueError, match="^identity catalog schema unsupported$") as refusal:
        IdentityCatalog.build(
            opener=opener, session=entry("session", path))

    assert secret not in str(refusal.value)
    assert len(opener.connections) == 1 and opener.connections[0].closed
    with pytest.raises(sqlite3.ProgrammingError):
        opener.connections[0].execute("SELECT 1")


def test_unsupported_supplied_contact_schema_is_fixed_copy_and_closes(tmp_path):
    secret = "fixture_secret_contact.db"
    path = sqlite_file(tmp_path, secret, (
        "CREATE TABLE contact (username TEXT, remark TEXT)",
    ))
    opener = TrackingOpener()

    with pytest.raises(ValueError, match="^identity catalog schema unsupported$") as refusal:
        IdentityCatalog.build(
            opener=opener, contact=entry("contact", path))

    assert secret not in str(refusal.value)
    assert len(opener.connections) == 1 and opener.connections[0].closed


def test_explicit_roles_use_the_injected_opener_without_inspecting_handles(tmp_path):
    paths = (
        session_file(tmp_path, ROOM),
        contact_file(tmp_path, (ALPHA, "Fixture Remark", "")),
        fixtures.readable_part(tmp_path, "message_0.db", ROOM, (
            M(1, 100, BETA, "fixture message"),)).path,
    )
    handles = (object(), object(), object())

    class MappedOpener(TrackingOpener):
        def open(self, supplied: ShardEntry) -> sqlite3.Connection:
            self.entries.append(supplied)
            path = paths[handles.index(supplied.handle)]
            connection = sqlite3.connect(path, factory=TrackingConnection)
            self.connections.append(connection)
            return connection

    supplied = (
        entry("role-is-explicit", handles[0]),
        entry("role-is-explicit", handles[1]),
        entry("not-a-message-path", handles[2]),
    )
    opener = MappedOpener()

    IdentityCatalog.build(
        opener=opener, session=supplied[0], contact=supplied[1],
        message_parts=(supplied[2],))

    assert opener.entries == list(supplied)
    assert all(connection.closed for connection in opener.connections)


def test_catalog_snapshots_canonical_immutable_evidence_and_fresh_resolver_inputs(tmp_path):
    alpha = fixtures.readable_part(tmp_path, "message_0.db", ROOM, (
        M(1, 100, ALPHA, "fixture alpha"),))
    beta = fixtures.readable_part(tmp_path, "message_1.db", ROOM, (
        M(2, 200, BETA, "fixture beta"),))
    parts = [part_entry(alpha)]
    catalog = IdentityCatalog.build(opener=TrackingOpener(), message_parts=parts)
    parts.append(part_entry(beta))

    assert catalog.session_names == ((ALPHA_DIGEST, ALPHA),)
    assert isinstance(catalog.session_names, tuple) and isinstance(catalog.candidates, tuple)
    with pytest.raises(FrozenInstanceError):
        catalog.session_names = ()
    first = catalog.resolver().resolve()
    first.session_names["injected"] = "injected"
    assert catalog.resolver().resolve().session_names == {ALPHA_DIGEST: ALPHA}


def test_reversed_message_part_input_builds_equivalent_catalogs(tmp_path):
    parts = (
        fixtures.readable_part(tmp_path, "message_0.db", ROOM, (
            M(1, 100, ALPHA, "fixture alpha"),)),
        fixtures.readable_part(tmp_path, "message_1.db", ROOM, (
            M(2, 200, BETA, "fixture beta"),)),
    )

    forward = IdentityCatalog.build(
        opener=TrackingOpener(), message_parts=tuple(map(part_entry, parts)))
    backward = IdentityCatalog.build(
        opener=TrackingOpener(), message_parts=tuple(map(part_entry, reversed(parts))))

    assert forward == backward
    assert forward.resolver().resolve() == backward.resolver().resolve()


def test_catalog_resolver_drives_existing_provider_identity_flow(tmp_path):
    session = session_file(tmp_path, ROOM)
    contact = contact_file(tmp_path,
        (ALPHA, "Fixture Alpha Remark", "Fixture Alpha Nickname"),
        (BETA, "", "Fixture Beta Nickname"),
    )
    part = fixtures.readable_part(tmp_path, "message_0.db", ROOM, (
        M(1, 100, ALPHA, "fixture alpha", server_id=101),
        M(2, 200, BETA, "fixture beta", server_id=202),
    ))
    catalog = IdentityCatalog.build(
        opener=TrackingOpener(),
        session=entry("session", session),
        contact=entry("contact", contact),
        message_parts=(part_entry(part),),
    )
    provider = ShardedMessageProvider(
        ExplicitShardLocator((part_entry(part),)), identities=catalog.resolver())

    conversations = provider.list_conversations(10).items
    messages = provider.get_messages(conversation_identifier(ROOM), 10).items

    assert [(conversation.title, conversation.id) for conversation in conversations] == [
        (ROOM, conversation_identifier(ROOM))]
    assert [(message.id, message.sender) for message in messages] == [
        (101, "Fixture Alpha Remark"),
        (202, "Fixture Beta Nickname"),
    ]


def test_identity_catalog_has_no_discovery_acquisition_or_room_member_vocabulary():
    source = Path(catalog_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    roots = set()
    identifiers = set()
    attrs = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            roots.add((node.module or "").split(".")[0])
        elif isinstance(node, (ast.Name, ast.ClassDef, ast.FunctionDef, ast.arg)):
            identifiers.add(getattr(node, "id", getattr(node, "name", getattr(node, "arg", ""))))
        elif isinstance(node, ast.Attribute):
            attrs.add(node.attr)

    assert not roots & {
        "bridge", "core", "glob", "memory", "os", "pathlib", "shadow", "subprocess"}
    assert not identifiers & {
        "ExplicitShardLocator", "NAME_ROOM_MEMBER", "ReadOnlySqliteOpener",
        "ShardDiscovery", "conversation_identifier", "selected_source_name"}
    assert not attrs & {"glob", "iglob", "iterdir", "listdir", "rglob", "scandir", "walk"}
