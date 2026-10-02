"""The native-search spike: an index proposes, an ordinary shard disposes.

Every test here is one rule. A hit from a native search index is a *candidate*
and nothing more: it may propose a row, it may not state one. The row that
reaches a caller is re-read through the ordinary shard and projected by the
provider's own result owner, so identity, content, chronology and coverage keep
exactly the owners they already had.

The fixtures are synthetic and self-authored (see fixtures_fts). No real
database, path, or content appears anywhere in this module.
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

import message_source as ms
import wechatdb
from conversation_identity import conversation_identifier
from wechatdb.provider import (
    CAPABILITY_ABSENT,
    CAPABILITY_AVAILABLE,
    CAPABILITY_MALFORMED,
    CAPABILITY_UNSUPPORTED,
    ExplicitShardLocator,
    IdentityResolver,
    NameCandidate,
    NAME_ROOM_MEMBER,
    NativeSearchDiagnostics,
    NativeSearchIndex,
    NativeSearchResult,
    ShardEntry,
    ShardedMessageProvider,
    UnsupportedGeneration,
    require_supported_surface,
)
from wechatdb.provider.message_identity import message_id

from . import fixtures, fixtures_fts
from .fixtures import ALPHA, BETA, ROOM, SyntheticMessage as M

ROOT_MESSAGES = (
    M(11, 1_000, ALPHA, "fixture text one", server_id=4_001),
    M(12, 2_000, BETA, "fixture text two", server_id=4_002),
    M(13, 3_000, ALPHA, "fixture text three", server_id=4_003),
)
#: One invented term every synthetic body contains, and no real content does.
TERM = "fixture"

PROVIDER = Path(wechatdb.__file__).resolve().parent / "provider"


def table() -> str:
    return fixtures.conversation_table(ROOM)


def digest_of(conversation: str) -> str:
    return fixtures.conversation_table(conversation)[len("Msg_"):]


def entry(part) -> ShardEntry:
    return ShardEntry(name=part.name, handle=part.path)


def identities():
    return IdentityResolver([], session_names={digest_of(ROOM): ROOM})


def ordinary(parts):
    return ShardedMessageProvider(
        ExplicitShardLocator([entry(part) for part in parts]), identities=identities())


def search_index(parts, index_path):
    return NativeSearchIndex(
        ShardEntry(name=index_path.name, handle=index_path) if index_path else None,
        entries=[entry(part) for part in parts], identities=identities())


def named_search_index(parts, index_path, candidates):
    """The same index, with display-name candidates the ordinary path has too."""
    return NativeSearchIndex(
        ShardEntry(name=index_path.name, handle=index_path) if index_path else None,
        entries=[entry(part) for part in parts],
        identities=IdentityResolver(
            candidates, session_names={digest_of(ROOM): ROOM}))


def part(tmp_path, name="message_0.db", conversation=ROOM, messages=ROOT_MESSAGES):
    return fixtures.readable_part(tmp_path, name, conversation, messages)


def hits(part, messages=ROOT_MESSAGES):
    return [fixtures_fts.hit(
        shard_name=part.name, conversation_table=table(), local_id=message.local_id)
        for message in messages]


def ordinary_read(parts, limit=10):
    return ordinary(parts).get_messages(conversation_identifier(ROOM), limit)


# -- capability ----------------------------------------------------------------

def test_an_absent_index_is_unavailable_and_leaves_the_ordinary_reader_alone(tmp_path):
    parts = [part(tmp_path)]
    result = search_index(parts, None).search(TERM, 10)

    assert isinstance(result, NativeSearchResult)
    assert result.capability == CAPABILITY_ABSENT
    assert result.used_native_index is False
    assert result.messages == ()
    assert result.truncated is False
    assert result.diagnostics == NativeSearchDiagnostics()

    read = ordinary_read(parts)
    assert read.coverage.status == ms.COVERAGE_COMPLETE
    assert len(read.items) == len(ROOT_MESSAGES)


def test_a_compatible_index_is_available(tmp_path):
    parts = [part(tmp_path)]
    index = fixtures_fts.compatible_source(tmp_path, hits(parts[0]))
    result = search_index(parts, index).search(TERM, 10)

    assert result.capability == CAPABILITY_AVAILABLE
    assert result.used_native_index is True
    assert len(result.messages) == len(ROOT_MESSAGES)


@pytest.mark.parametrize("missing", fixtures_fts.LINKAGE_COLUMNS)
def test_a_schema_without_the_linkage_is_unsupported_never_a_guess(tmp_path, missing):
    parts = [part(tmp_path)]
    index = fixtures_fts.unsupported_source(tmp_path, missing=missing)
    result = search_index(parts, index).search(TERM, 10)

    assert result.capability == CAPABILITY_UNSUPPORTED
    assert result.used_native_index is False
    assert result.messages == ()
    assert result.diagnostics == NativeSearchDiagnostics()

    # The ordinary reader is untouched by an index it cannot use.
    read = ordinary_read(parts)
    assert read.coverage.status == ms.COVERAGE_COMPLETE
    assert len(read.items) == len(ROOT_MESSAGES)


@pytest.mark.parametrize("state", ["corrupt", "malformed"])
def test_an_unusable_index_yields_a_fixed_diagnostic_and_leaks_nothing(tmp_path, state):
    parts = [part(tmp_path)]
    index = (fixtures_fts.corrupt_source(tmp_path, hits(parts[0])) if state == "corrupt"
             else fixtures_fts.malformed_source(tmp_path))
    result = search_index(parts, index).search(TERM, 10)

    assert result.capability == CAPABILITY_MALFORMED
    assert result.used_native_index is False
    assert result.messages == ()
    assert result.diagnostics == NativeSearchDiagnostics(query_failed=1)

    # The capability token is the answer, so it is public by design. What must
    # never appear is SQLite's own wording, or anything that names the source.
    said = repr(result).replace(result.capability, "")
    for leak in ("sqlite", "corrupt", "disk image", "not a database",
                 str(tmp_path), "message_fts", fixtures_fts.SEARCH_CONTENT_TABLE,
                 TERM, "SELECT", "Msg_", "DROP", "vtable"):
        assert leak not in said, leak

    read = ordinary_read(parts)
    assert read.coverage.status == ms.COVERAGE_COMPLETE


def test_a_plain_table_wearing_the_content_name_is_not_a_capability(tmp_path):
    """A matching name is not a capability claim."""
    parts = [part(tmp_path)]
    index = fixtures_fts.unsupported_source(tmp_path)
    connection = sqlite3.connect(str(index))
    try:
        assert connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = ?",
            (fixtures_fts.SEARCH_CONTENT_TABLE,)).fetchone()[0].upper().startswith(
                "CREATE TABLE")
    finally:
        connection.close()

    result = search_index(parts, index).search(TERM, 10)
    assert result.capability == CAPABILITY_UNSUPPORTED


# -- candidate resolution ------------------------------------------------------

def test_a_candidate_resolves_to_the_authoritative_ordinary_message(tmp_path):
    parts = [part(tmp_path)]
    # The index's own non-authoritative columns say something else entirely.
    index = fixtures_fts.compatible_source(tmp_path, [
        dict(fixtures_fts.hit(
            shard_name=parts[0].name, conversation_table=table(),
            local_id=ROOT_MESSAGES[1].local_id,
            sender_id="wxid_fixture_invented", create_time=999_999,
            body="fixture invented index body"))])
    result = search_index(parts, index).search(TERM, 10)

    assert len(result.messages) == 1
    message = result.messages[0]
    authoritative = fixtures.parse_part(parts[0], ROOM)[1]
    assert message.text == authoritative.content
    assert message.sender == authoritative.sender_name
    assert message.first_observed_at == authoritative.timestamp
    assert message.conversation_id == conversation_identifier(ROOM)
    assert result.diagnostics.resolved == 1


def test_the_index_never_supplies_the_projected_values(tmp_path):
    parts = [part(tmp_path)]
    index = fixtures_fts.compatible_source(tmp_path, [
        dict(fixtures_fts.hit(
            shard_name=parts[0].name, conversation_table=table(),
            local_id=ROOT_MESSAGES[0].local_id,
            sender_id="wxid_fixture_invented", create_time=999_999,
            body="fixture invented index body"))])
    message = search_index(parts, index).search(TERM, 10).messages[0]
    expected = ordinary_read(parts).items[0]

    assert (message.id, message.text, message.sender, message.first_observed_at,
            message.sequence, message.conversation_id) == (
                expected.id, expected.text, expected.sender, expected.first_observed_at,
                expected.sequence, expected.conversation_id)


def test_a_candidate_projects_the_ordinary_paths_display_name(tmp_path):
    """Resolution parity: the same resolver, the same room, the same sender name.

    The ordinary provider resolves names per room and re-parses with the result.
    A search result that skipped that step would answer with a raw sender id for a
    message the ordinary reader shows with a display name -- a second, weaker
    projection of the same authoritative row.
    """
    parts = [part(tmp_path)]
    index = fixtures_fts.compatible_source(tmp_path, hits(parts[0], ROOT_MESSAGES[:1]))
    candidate = NameCandidate(identifier=ALPHA, kind=NAME_ROOM_MEMBER,
                              name="Fixture Alpha", room=ROOM)
    resolver = IdentityResolver([candidate], session_names={digest_of(ROOM): ROOM})

    ordinary_provider = ShardedMessageProvider(
        ExplicitShardLocator([entry(parts[0])]), identities=resolver)
    authoritative = ordinary_provider.get_messages(
        conversation_identifier(ROOM), 10).items[0]

    result = named_search_index(parts, index, [candidate]).search(TERM, 10)
    assert len(result.messages) == 1

    message = result.messages[0]
    assert message.sender == authoritative.sender == "Fixture Alpha"
    assert (message.id, message.text, message.first_observed_at,
            message.sequence, message.conversation_id) == (
        authoritative.id, authoritative.text, authoritative.first_observed_at,
        authoritative.sequence, authoritative.conversation_id)


def test_a_candidate_the_ordinary_shard_does_not_hold_is_never_returned(tmp_path):
    parts = [part(tmp_path)]
    index = fixtures_fts.compatible_source(tmp_path, [
        dict(fixtures_fts.hit(shard_name=parts[0].name, conversation_table=table(),
                              local_id=987_654))])
    result = search_index(parts, index).search(TERM, 10)

    assert result.messages == ()
    assert result.diagnostics.resolved == 0
    assert result.diagnostics.dropped == 1


def test_a_stale_candidate_never_resolves_to_a_coincidentally_similar_message(tmp_path):
    """The row the index points at is gone; a *different* row exists.

    A reader that fell back to "this conversation has messages" would answer
    here. Exact linkage must not.
    """
    parts = [part(tmp_path)]
    index = fixtures_fts.compatible_source(tmp_path, [
        dict(fixtures_fts.hit(shard_name=parts[0].name, conversation_table=table(),
                              local_id=ROOT_MESSAGES[0].local_id + 1_000))])
    result = search_index(parts, index).search(TERM, 10)

    assert result.messages == ()
    assert result.diagnostics.dropped == 1


@pytest.mark.parametrize("wrong", ["shard_name", "conversation_table", "local_id"])
def test_a_candidate_addressing_anything_but_its_own_row_is_dropped(tmp_path, wrong):
    other_room = ROOM + "_other"
    parts = [part(tmp_path), part(tmp_path, "message_1.db", other_room, ROOT_MESSAGES[:1])]
    candidate = dict(hits(parts[0], ROOT_MESSAGES[:1])[0])
    if wrong == "shard_name":
        candidate["shard_name"] = "message_9.db"          # a part nobody supplied
    elif wrong == "conversation_table":
        candidate["conversation_table"] = "not a table name"
    else:
        candidate["local_id"] = "not an integer"
    result = search_index(
        parts, fixtures_fts.compatible_source(tmp_path, [candidate])).search(TERM, 10)

    assert result.messages == ()
    assert result.diagnostics.dropped == 1
    assert result.diagnostics.resolved == 0


def test_an_unreadable_row_coordinate_never_becomes_another_rows_id(tmp_path):
    """A local id the index could not state must not be read as "id zero".

    Coercing a non-numeric coordinate to an integer would silently rewrite an
    unanswerable candidate into a real reference, and row zero is a real row.
    """
    zero = M(0, 1_000, ALPHA, "fixture row zero", server_id=6_001)
    parts = [part(tmp_path, "message_0.db", ROOM, (zero,))]
    index = fixtures_fts.compatible_source(tmp_path, [dict(
        fixtures_fts.hit(shard_name=parts[0].name, conversation_table=table(),
                         local_id="not an integer"))])

    result = search_index(parts, index).search(TERM, 10)
    assert result.messages == ()
    assert result.diagnostics.dropped == 1


def test_a_part_that_will_not_open_drops_its_candidates_quietly(tmp_path):
    """A missing or unreadable ordinary part is a dropped candidate, not a crash.

    The opener's refusal carries SQLite's own wording, including the path. If it
    escaped, a search over a partly-readable source would answer with a path.
    """
    parts = [part(tmp_path)]
    index = fixtures_fts.compatible_source(tmp_path, hits(parts[0]))
    parts[0].path.unlink()

    result = search_index(parts, index).search(TERM, 10)
    assert result.messages == ()
    assert result.diagnostics.resolved == 0
    # The part is gone for every candidate naming it, so all three drop.
    assert result.diagnostics.dropped == len(ROOT_MESSAGES)

    said = repr(result)
    for leak in ("sqlite", "unable to open", "no such file", str(tmp_path),
                 "message_0.db"):
        assert leak not in said, leak


def test_a_part_whose_bytes_are_not_a_database_drops_its_candidates_quietly(tmp_path):
    parts = [part(tmp_path)]
    index = fixtures_fts.compatible_source(tmp_path, hits(parts[0]))
    parts[0].path.write_bytes(b"fixture bytes that are not a sqlite database")

    result = search_index(parts, index).search(TERM, 10)
    assert result.messages == ()
    # The part is gone for every candidate naming it, so all three drop.
    assert result.diagnostics.dropped == len(ROOT_MESSAGES)
    assert "not a database" not in repr(result)


def test_a_candidate_naming_another_parts_conversation_cannot_reach_this_rows(tmp_path):
    """Two parts; the hit names one part and the other part's conversation.

    The part is part of the reference, so this cannot resolve to the row of the
    same shape that lives in the other part.
    """
    other_room = ROOM + "_other"
    parts = [part(tmp_path, "message_0.db", other_room, ROOT_MESSAGES),
             part(tmp_path, "message_1.db", ROOM, ROOT_MESSAGES)]
    candidate = dict(hits(parts[0])[0])
    candidate["conversation_table"] = table()
    candidate["shard_name"] = parts[1].name
    result = search_index(
        parts, fixtures_fts.compatible_source(tmp_path, [candidate])).search(TERM, 10)

    # This one does resolve -- to the row in the part it names, which holds that
    # exact conversation and that exact local id. The refusal it proves is the
    # one below it.
    assert len(result.messages) == 1

    # Now name the *other* part's conversation while pointing at this part.
    crossed = dict(hits(parts[0])[0])
    crossed["conversation_table"] = fixtures.conversation_table(other_room)
    crossed["shard_name"] = parts[1].name
    result = search_index(
        parts, fixtures_fts.compatible_source(tmp_path, [crossed],
                                              name="message_fts2.db")).search(TERM, 10)
    assert result.messages == ()
    assert result.diagnostics.dropped == 1


def test_duplicate_candidates_collapse_once_by_authoritative_identity(tmp_path):
    parts = [part(tmp_path)]
    index = fixtures_fts.compatible_source(tmp_path, hits(parts[0], ROOT_MESSAGES[:1]) * 3)
    result = search_index(parts, index).search(TERM, 10)

    assert len(result.messages) == 1
    assert result.diagnostics.candidates == 3
    assert result.diagnostics.resolved == 1
    assert result.diagnostics.duplicates == 2


def test_two_authoritative_messages_with_identical_text_stay_two_messages(tmp_path):
    twin = (M(21, 1_000, ALPHA, "fixture identical text", server_id=5_001),
            M(22, 2_000, BETA, "fixture identical text", server_id=5_002))
    parts = [part(tmp_path, "message_0.db", ROOM, twin)]
    index = fixtures_fts.compatible_source(tmp_path, hits(parts[0], twin))
    result = search_index(parts, index).search(TERM, 10)

    assert len(result.messages) == 2
    assert result.messages[0].id != result.messages[1].id
    assert result.messages[0].text == result.messages[1].text
    assert result.diagnostics.duplicates == 0


# -- identity ------------------------------------------------------------------

def test_the_final_identity_is_the_providers_own_and_the_rowid_is_not(tmp_path):
    parts = [part(tmp_path)]
    index = fixtures_fts.compatible_source(tmp_path, hits(parts[0]))
    result = search_index(parts, index).search(TERM, 10)

    records = fixtures.parse_part(parts[0], ROOM)
    assert [message.id for message in result.messages] == \
        [message_id(record) for record in records]

    connection = sqlite3.connect(str(index))
    try:
        rowids = {row[0] for row in connection.execute(
            f'SELECT rowid FROM "{fixtures_fts.SEARCH_CONTENT_TABLE}"')}
    finally:
        connection.close()
    assert rowids == {1, 2, 3}
    assert not rowids & {message.id for message in result.messages}


def test_conversation_identity_cannot_be_invented_by_the_index(tmp_path):
    parts = [part(tmp_path)]
    index = fixtures_fts.compatible_source(tmp_path, [
        dict(fixtures_fts.hit(shard_name=parts[0].name,
                              conversation_table="Msg_" + "f" * 32,
                              local_id=ROOT_MESSAGES[0].local_id,
                              body="fixture invented conversation"))])
    result = search_index(parts, index).search(TERM, 10)

    assert result.messages == ()
    assert result.diagnostics.dropped == 1


# -- coverage ------------------------------------------------------------------

def test_a_search_result_carries_no_message_coverage(tmp_path):
    parts = [part(tmp_path)]
    index = fixtures_fts.compatible_source(tmp_path, [])
    result = search_index(parts, index).search(TERM, 10)

    assert result.capability == CAPABILITY_AVAILABLE
    assert result.used_native_index is True
    assert result.messages == ()
    assert result.truncated is False
    assert not isinstance(result, ms.ReadResult)
    assert not hasattr(result, "coverage")
    assert not isinstance(result.diagnostics, ms.ReadCoverage)
    assert "coverage" not in NativeSearchDiagnostics.__dataclass_fields__

    # An empty index result is an empty result, and the ordinary reader still
    # sees every message it always saw.
    read = ordinary_read(parts)
    assert read.coverage.status == ms.COVERAGE_COMPLETE
    assert len(read.items) == len(ROOT_MESSAGES)


@pytest.mark.parametrize("state", ["absent", "compatible", "unsupported",
                                   "corrupt", "malformed"])
def test_no_index_state_changes_what_the_ordinary_reader_covers(tmp_path, state):
    parts = [part(tmp_path)]
    builders = {
        "compatible": lambda: fixtures_fts.compatible_source(tmp_path, hits(parts[0])),
        "unsupported": lambda: fixtures_fts.unsupported_source(tmp_path),
        "corrupt": lambda: fixtures_fts.corrupt_source(tmp_path, hits(parts[0])),
        "malformed": lambda: fixtures_fts.malformed_source(tmp_path),
    }
    index = builders.get(state)
    search_index(parts, index() if index else None).search(TERM, 10)

    read = ordinary_read(parts)
    assert read.coverage.status == ms.COVERAGE_COMPLETE
    assert read.coverage.reason == ms.REASON_FULL_WINDOW_OBSERVED
    assert read.coverage.item_count == len(ROOT_MESSAGES)


def test_orphan_candidates_do_not_weaken_or_strengthen_ordinary_coverage(tmp_path):
    parts = [part(tmp_path)]
    index = fixtures_fts.compatible_source(tmp_path, [
        dict(fixtures_fts.hit(shard_name=parts[0].name, conversation_table=table(),
                              local_id=999_999)),
        *hits(parts[0], ROOT_MESSAGES[:1])])
    result = search_index(parts, index).search(TERM, 10)
    assert result.diagnostics.dropped == 1
    assert len(result.messages) == 1

    read = ordinary_read(parts)
    assert read.coverage.status == ms.COVERAGE_COMPLETE
    assert read.coverage.item_count == len(ROOT_MESSAGES)


# -- ordering ------------------------------------------------------------------

def test_ordering_is_the_providers_own_and_deterministic(tmp_path):
    parts = [part(tmp_path)]
    index = fixtures_fts.compatible_source(tmp_path, hits(parts[0]))
    result = search_index(parts, index).search(TERM, 10)

    assert [message.first_observed_at for message in result.messages] ==         [1_000, 2_000, 3_000]
    assert search_index(parts, index).search(TERM, 10).messages == result.messages


def test_the_newest_limit_keeps_the_newest_authoritative_messages(tmp_path):
    parts = [part(tmp_path)]
    index = fixtures_fts.compatible_source(tmp_path, hits(parts[0]))
    result = search_index(parts, index).search(TERM, 2)

    assert [message.first_observed_at for message in result.messages] == [2_000, 3_000]


# -- boundaries ----------------------------------------------------------------

@pytest.mark.parametrize("query", [
    "", "   ", "fixture OR dinner", '"fixture', "fixture*", "a:b", "NEAR(a b)",
    "fixture; DROP TABLE search_index_content", "fixture AND (", "x" * 4_096,
    None, 7, b"fixture", ["fixture"], "fixture\nbody",
])
def test_the_query_is_one_bounded_term_or_nothing_at_all(tmp_path, query):
    parts = [part(tmp_path)]
    index = fixtures_fts.compatible_source(tmp_path, hits(parts[0]))
    with pytest.raises(ValueError):
        search_index(parts, index).search(query, 10)


@pytest.mark.parametrize("limit", [0, -1, True, 10_000, None, "3", 1.5])
def test_the_limit_is_a_bounded_positive_integer(tmp_path, limit):
    parts = [part(tmp_path)]
    index = fixtures_fts.compatible_source(tmp_path, hits(parts[0]))
    with pytest.raises(ValueError):
        search_index(parts, index).search(TERM, limit)


def test_the_search_is_bounded_and_reports_its_own_truncation(tmp_path):
    parts = [part(tmp_path)]
    index = fixtures_fts.compatible_source(tmp_path, hits(parts[0]))
    result = search_index(parts, index).search(TERM, 1)

    assert len(result.messages) == 1
    assert result.truncated is True
    assert result.diagnostics.candidates == 2


def test_every_statement_is_a_literal_and_every_input_a_parameter():
    """No caller string reaches SQL construction.

    Read from the module's own syntax tree rather than from its behaviour, so a
    later statement cannot quietly acquire interpolation.
    """
    source = (PROVIDER / "native_search.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    executed = [node for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute"]
    assert executed, "the index must query something"
    for node in executed:
        assert isinstance(node.args[0], ast.Name), ast.unparse(node)
        statement = node.args[0].id
        assert statement in {"_SCHEMA_SQL", "_CANDIDATE_SQL", "_COLUMN_SQL"}, statement

    # And it never opens a database itself: the shared read-only opener does.
    assert "sqlite3.connect" not in source
    assert "ReadOnlySqliteOpener" in source


def test_the_module_reads_no_generation_it_cannot_parse(tmp_path):
    """A part whose read surface changed yields no message, even with a hit."""
    parts = [part(tmp_path)]
    connection = sqlite3.connect(str(parts[0].path))
    try:
        connection.execute(f'ALTER TABLE "{table()}" DROP COLUMN server_id')
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(UnsupportedGeneration):
        require_supported_surface(sqlite3.connect(str(parts[0].path)), table())

    result = search_index(parts, fixtures_fts.compatible_source(
        tmp_path, hits(parts[0], ROOT_MESSAGES[:1]))).search(TERM, 10)
    assert result.messages == ()
    assert result.diagnostics.dropped == 1


def test_the_index_is_not_reachable_from_the_product_layer():
    """Not wired, and the guard that keeps it that way is the one that runs."""
    from .test_isolation import provider_modules

    assert (PROVIDER / "native_search.py") in provider_modules()

    # No generic or product module imports it at any depth.
    for root in ("bridge", "memory", "shadow"):
        for path in Path(wechatdb.__file__).resolve().parents[1].joinpath(root).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            assert "native_search" not in path.read_text(encoding="utf-8"), path
