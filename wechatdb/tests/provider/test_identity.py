"""IdentityResolver: exact lookup, fixed precedence, ambiguity refused.

Names and an unresolved count are the whole output. Nothing here touches
coverage, routing, discovery, a database (except the one parser-integration
test, which uses a P12 synthetic part) or any string operation that could turn
a lookup into a guess. Exact strings are evidence.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Mapping

import pytest

import wechatdb
from wechatdb.provider import identity
from wechatdb.provider.identity import (
    NAME_CONTACT_NICKNAME,
    NAME_CONTACT_REMARK,
    NAME_PRECEDENCE,
    NAME_ROOM_MEMBER,
    IdentityResolver,
    NameCandidate,
    ResolvedIdentities,
)

from . import fixtures
from .fixtures import ALPHA, BETA, SyntheticMessage

SOURCE = Path(identity.__file__).resolve()
X = "wxid_fixture_x"
ROOM_A = "fixture_room_a@chatroom"
ROOM_B = "fixture_room_b@chatroom"


def member(identifier, name, room):
    return NameCandidate(identifier=identifier, kind=NAME_ROOM_MEMBER, name=name, room=room)


def remark(identifier, name):
    return NameCandidate(identifier=identifier, kind=NAME_CONTACT_REMARK, name=name)


def nickname(identifier, name):
    return NameCandidate(identifier=identifier, kind=NAME_CONTACT_NICKNAME, name=name)


def resolve(*candidates, room=None, session_names=None):
    return IdentityResolver(candidates, session_names=session_names).resolve(room=room)


# --- vocabulary and shape ---------------------------------------------------

def test_the_vocabulary_and_precedence_are_exactly_as_specified():
    assert (NAME_ROOM_MEMBER, NAME_CONTACT_REMARK, NAME_CONTACT_NICKNAME) == (
        "room_member", "contact_remark", "contact_nickname")
    assert NAME_PRECEDENCE == (NAME_ROOM_MEMBER, NAME_CONTACT_REMARK, NAME_CONTACT_NICKNAME)


def test_resolved_identities_carries_exactly_three_things():
    import dataclasses
    assert [f.name for f in dataclasses.fields(ResolvedIdentities)] == [
        "session_names", "display_names", "unresolved"]
    result = resolve(remark(X, "Fixture Remark"))
    assert isinstance(result, ResolvedIdentities)
    assert isinstance(result.session_names, Mapping)
    assert isinstance(result.display_names, Mapping)
    assert isinstance(result.unresolved, int)
    with pytest.raises((AttributeError, TypeError)):
        result.unresolved = 5  # frozen


# --- precedence and scope -----------------------------------------------------

def test_a_remark_beats_a_nickname():
    result = resolve(nickname(X, "Fixture Nickname"), remark(X, "Fixture Remark"))
    assert result.display_names == {X: "Fixture Remark"}
    assert result.unresolved == 0


def test_a_room_nickname_applies_inside_that_room_and_not_outside_it():
    candidates = (member(X, "Fixture Room Name", ROOM_A), remark(X, "Fixture Global Remark"))
    assert resolve(*candidates, room=ROOM_A).display_names == {X: "Fixture Room Name"}
    assert resolve(*candidates, room=ROOM_B).display_names == {X: "Fixture Global Remark"}
    assert resolve(*candidates).display_names == {X: "Fixture Global Remark"}
    for room in (ROOM_A, ROOM_B, None):
        assert resolve(*candidates, room=room).unresolved == 0


def test_another_rooms_member_name_is_out_of_scope_not_unresolved():
    result = resolve(member(X, "Fixture Room Name", ROOM_A), room=ROOM_B)
    assert X not in result.display_names
    assert result.display_names == {}
    assert result.unresolved == 0
    # And with no room requested, a member name is simply not applicable.
    result = resolve(member(X, "Fixture Room Name", ROOM_A))
    assert result.display_names == {} and result.unresolved == 0


# --- ambiguity ----------------------------------------------------------------

def test_two_conflicting_same_kind_names_resolve_to_no_name():
    result = resolve(remark(X, "Fixture Remark One"), remark(X, "Fixture Remark Two"))
    assert X not in result.display_names
    assert result.unresolved == 1
    everything = " ".join(result.display_names.values())
    assert "Fixture Remark One" not in everything and "Fixture Remark Two" not in everything


def test_duplicate_same_kind_same_name_is_not_ambiguous():
    result = resolve(remark(X, "Fixture Remark"), remark(X, "Fixture Remark"),
                     remark(X, "Fixture Remark"))
    assert result.display_names == {X: "Fixture Remark"}
    assert result.unresolved == 0


def test_an_ambiguous_stronger_kind_does_not_fall_back_to_a_weaker_name():
    result = resolve(member(X, "Room Alice", ROOM_A), member(X, "Room Alicia", ROOM_A),
                     remark(X, "Alice Remark"), room=ROOM_A)
    assert X not in result.display_names
    assert result.unresolved == 1
    assert "Alice Remark" not in result.display_names.values()
    # The same evidence in another room: the members are out of scope and the
    # remark, now the strongest applicable kind, decides.
    elsewhere = resolve(member(X, "Room Alice", ROOM_A), member(X, "Room Alicia", ROOM_A),
                        remark(X, "Alice Remark"), room=ROOM_B)
    assert elsewhere.display_names == {X: "Alice Remark"} and elsewhere.unresolved == 0


def test_exact_strings_are_not_normalised_before_ambiguity():
    result = resolve(remark(X, "Fixture Name"), remark(X, "fixture name"))
    assert X not in result.display_names
    assert result.unresolved == 1
    # Whitespace is also evidence, in both directions.
    spaced = resolve(remark(X, "Fixture Name"), remark(X, " Fixture Name"))
    assert spaced.unresolved == 1
    kept = resolve(remark(X, "  Fixture Name  "))
    assert kept.display_names == {X: "  Fixture Name  "}


def test_unresolved_is_per_resolution_call_not_accumulated():
    resolver = IdentityResolver((member(X, "Room One", ROOM_A), member(X, "Room Two", ROOM_A),
                                 remark(X, "Global X")))
    assert resolver.resolve(room=ROOM_A).unresolved == 1
    assert resolver.resolve(room=ROOM_B).unresolved == 0
    assert resolver.resolve(room=ROOM_B).display_names == {X: "Global X"}
    assert resolver.resolve(room=ROOM_A).unresolved == 1          # not 2
    assert resolver.resolve(room=ROOM_A).unresolved == 1          # not 3


def test_an_unresolved_identity_raises_nothing_and_is_counted():
    result = resolve(nickname(X, "Fixture Nick One"), nickname(X, "Fixture Nick Two"))
    assert result.unresolved == 1
    assert X not in result.display_names
    assert result.display_names == {}


def test_unresolved_counts_distinct_identifiers_once_each():
    y = "wxid_fixture_y"
    result = resolve(remark(X, "One"), remark(X, "Two"), remark(X, "Three"),
                     remark(y, "Alpha"), remark(y, "Beta"),
                     remark("wxid_fixture_z", "Fine"))
    assert result.unresolved == 2
    assert result.display_names == {"wxid_fixture_z": "Fine"}


def test_candidate_order_does_not_affect_the_result():
    candidates = [member(X, "Room Name", ROOM_A), remark(X, "Remark"), nickname(X, "Nick"),
                  remark("wxid_fixture_y", "Y One"), remark("wxid_fixture_y", "Y Two")]
    forward = resolve(*candidates, room=ROOM_A)
    backward = resolve(*reversed(candidates), room=ROOM_A)
    assert forward == backward
    assert forward.display_names == {X: "Room Name"} and forward.unresolved == 1


# --- construction -------------------------------------------------------------

def test_name_candidates_are_sealed_at_construction():
    NameCandidate(identifier=X, kind=NAME_ROOM_MEMBER, name="n", room=ROOM_A)
    NameCandidate(identifier=X, kind=NAME_CONTACT_REMARK, name="n")
    NameCandidate(identifier=" ", kind=NAME_CONTACT_REMARK, name=" ")   # whitespace is a string
    secret = "fixture_secret_value"
    for bad in (
        dict(identifier="", kind=NAME_CONTACT_REMARK, name=secret),
        dict(identifier=42, kind=NAME_CONTACT_REMARK, name=secret),
        dict(identifier=secret, kind="alias", name="n"),
        dict(identifier=secret, kind=NAME_CONTACT_REMARK, name=""),
        dict(identifier=secret, kind=NAME_CONTACT_REMARK, name=42),
        dict(identifier=secret, kind=NAME_ROOM_MEMBER, name="n", room=None),
        dict(identifier=secret, kind=NAME_ROOM_MEMBER, name="n", room=""),
        dict(identifier=secret, kind=NAME_ROOM_MEMBER, name="n", room=7),
        dict(identifier=X, kind=NAME_CONTACT_REMARK, name="n", room=secret),
        dict(identifier=X, kind=NAME_CONTACT_NICKNAME, name="n", room=secret),
    ):
        with pytest.raises(ValueError) as refusal:
            NameCandidate(**bad)
        assert secret not in str(refusal.value)
        assert "alias" not in str(refusal.value)


# --- copies, not aliases ------------------------------------------------------

def test_session_names_are_preserved_not_derived():
    digest = "0123456789abcdef" * 2
    given = {digest: "fixture_alpha_username"}
    resolver = IdentityResolver((), session_names=given)
    given[digest] = "tampered"
    given["extra"] = "tampered"

    first = resolver.resolve()
    assert first.session_names == {digest: "fixture_alpha_username"}
    assert first.session_names is not given
    second = resolver.resolve()
    assert second.session_names == first.session_names
    assert second.session_names is not first.session_names

    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    roots, names, attrs, texts = set(), set(), set(), []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0] if node.level == 0 else "<relative>")
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            attrs.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            texts.append(node.value)
    assert not any("Msg_" in t for t in texts)
    assert "hashlib" not in roots and "re" not in roots and "<relative>" not in roots
    assert "wechatdb" not in roots
    assert attrs.isdisjoint({"hexdigest", "digest", "md5", "sha256", "blake2b", "compile", "match", "fullmatch"})
    assert "hash" not in names and "ShardFacts" not in names and "CONVERSATION_TABLE" not in names


def test_display_name_results_are_defensive_copies():
    resolver = IdentityResolver((remark(X, "Fixture Remark"),))
    first = resolver.resolve()
    assert first.display_names == {X: "Fixture Remark"}
    if isinstance(first.display_names, dict):
        first.display_names["injected"] = "x"
        first.display_names[X] = "overwritten"
    else:
        with pytest.raises(TypeError):
            first.display_names["injected"] = "x"      # type: ignore[index]
    second = resolver.resolve()
    assert second.display_names == {X: "Fixture Remark"}
    assert second.display_names is not first.display_names

    # Caller-owned candidate containers are snapshotted at construction.
    candidates = [remark(X, "Fixture Remark")]
    resolver = IdentityResolver(candidates)
    candidates.append(remark(X, "Fixture Other"))
    assert resolver.resolve().display_names == {X: "Fixture Remark"}
    assert resolver.resolve().unresolved == 0


# --- guards -------------------------------------------------------------------

def test_resolution_is_never_a_guess():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    roots, names, attrs = set(), set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0])
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            attrs.add(node.attr)
    assert roots <= {"__future__", "dataclasses", "typing", "collections"}, roots
    assert "difflib" not in roots and "SequenceMatcher" not in names
    # No string operation that could turn exact equality into a match.
    assert attrs.isdisjoint({"casefold", "lower", "upper", "strip", "lstrip", "rstrip",
                             "startswith", "endswith", "find", "split", "replace",
                             "normalize"}), attrs
    assert "unicodedata" not in roots


def test_identity_resolution_knows_nothing_about_coverage():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    roots, identifiers = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0])
            identifiers.update(a.name for a in node.names)
        elif isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            identifiers.add(node.name)
        elif isinstance(node, ast.arg):
            identifiers.add(node.arg)
    for forbidden in ("message_source", "bridge", "memory", "shadow", "ai", "core", "wechatdb"):
        assert forbidden not in roots, forbidden
    for exact in ("ReadCoverage", "ReadFreshness", "ReadResult",
                  "COVERAGE_COMPLETE", "COVERAGE_PARTIAL"):
        assert exact not in identifiers, exact
    offenders = sorted(i for i in identifiers if "coverage" in i.lower())
    assert offenders == [], offenders


# --- the parser integration ---------------------------------------------------

def test_the_resolver_produces_exactly_what_the_parser_accepts(tmp_path):
    """The real parser, the full table name, and a mapping that changes the answer."""
    part = fixtures.readable_part(tmp_path, "message_0.db", ALPHA, (
        SyntheticMessage(1, 1_756_000_010, ALPHA, "fixture message one"),
        SyntheticMessage(2, 1_756_000_020, BETA, "fixture message two"),
    ))
    full_msg_table_name = fixtures.conversation_table(ALPHA)
    assert full_msg_table_name.startswith("Msg_")
    # The TEST builds the bare digest for its synthetic input; identity.py never does.
    digest = full_msg_table_name[len("Msg_"):].lower()
    username = "fixture_alpha_username"

    resolved = IdentityResolver((remark(ALPHA, "Fixture Alpha Remark"),),
                                session_names={digest: username}).resolve()

    def parse(**kwargs):
        connection = part.open()
        try:
            name2id = wechatdb.load_name2id(connection)
            return list(wechatdb.parse_conversation(
                connection, full_msg_table_name, name2id=name2id, **kwargs))
        finally:
            connection.close()

    bare = parse()
    assert [r.content for r in bare] == ["fixture message one", "fixture message two"]
    assert {r.session_id for r in bare} == {f"msg_{digest}"}          # the parser's fallback
    assert [r.sender_name for r in bare] == [ALPHA, BETA]              # falls back to the id

    named = parse(session_names=resolved.session_names,
                  display_names=resolved.display_names)
    assert [r.content for r in named] == ["fixture message one", "fixture message two"]
    assert {r.session_id for r in named} == {username}                 # the mapping changed it
    assert [r.sender_name for r in named] == ["Fixture Alpha Remark", BETA]
    assert [r.sender_id for r in named] == [ALPHA, BETA]               # identity never rewrites the id
