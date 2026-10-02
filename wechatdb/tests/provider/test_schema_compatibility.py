"""P4 schema-drift accounting: role is not compatibility, compatibility is not coverage.

Three claims are kept apart here, because keeping them apart is the point of
the sub-gate:

* a name shape earns a **role**, and nothing more;
* a **compatibility outcome** says whether the schema inside is the generation
  this reader understands, decided per conversation table against the parser's
  own column contract rather than a second copy of it;
* **coverage** is still produced by exactly one function, and a compatibility
  gap reaches it only as the already-existing ``inventory_gap`` ceiling.

Every database below is synthetic and built in this file: invented names,
invented tables, invented text, temporary paths under pytest's own ``tmp_path``.
The message-bearing relation treated here as *drift* is named by a pattern
invented in this file, because naming a real generation's table is a claim this
project has not made and does not make.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

import message_source as ms
import wechatdb
from wechatdb.parser import MANDATORY_COLUMNS, WANTED_COLUMNS
from wechatdb.provider import (
    CAPABILITY_ABSENT,
    CAPABILITY_UNSUPPORTED,
    COMPATIBILITY_STATES,
    COMPATIBLE,
    EMPTY,
    GAP_SCHEMA_INCOMPATIBLE,
    INCOMPLETE,
    MALFORMED,
    REQUIRED_MESSAGE_ROLES,
    ROLE_AUXILIARY,
    ROLE_BUSINESS_MESSAGE,
    ROLE_MEDIA,
    ROLE_ORDINARY_MESSAGE,
    ROLE_SEARCH_INDEX,
    ROLE_UNKNOWN,
    ROLE_UNSUPPORTED_MESSAGE_CANDIDATE,
    SHARD_ROLES,
    UNASSESSED,
    UNSUPPORTED,
    UnsupportedGeneration,
)
from wechatdb.provider.compatibility import SUPPORTED_CONVERSATION_COLUMNS

from .fixtures import ROOM
from .test_provider_reads import provider_over

TABLE = "Msg_" + "0123456789abcdef0123456789abcdef"
OTHER_TABLE = "Msg_" + "f" * 32


@dataclass(frozen=True)
class Part:
    """A name and a place. Test-only, and deliberately not a production type."""

    name: str
    path: Path


def _write(path: Path, statements) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    try:
        for statement in statements:
            connection.execute(statement)
        connection.commit()
    finally:
        connection.close()
    return path


def _conversation(columns, table: str = TABLE, rows=((1, 100),)):
    """A conversation table carrying exactly the given columns, with invented rows."""
    populated = sorted(set(columns) & set(MANDATORY_COLUMNS))
    definition = ", ".join('"%s" INTEGER' % name for name in sorted(columns))
    ddl = 'CREATE TABLE "%s" (%s)' % (table, definition)
    if not rows or not populated:
        return [ddl]
    targets = ", ".join('"%s"' % name for name in populated)
    return [ddl] + [
        'INSERT INTO "%s" (%s) VALUES (%s)'
        % (table, targets, ", ".join(str(value) for value in row))
        for row in rows
    ]


def shard(tmp_path: Path, name: str, *blocks) -> Part:
    statements: list[str] = []
    for block in blocks:
        statements.extend(block if isinstance(block, list) else [block])
    return Part(name=name, path=_write(tmp_path / name, statements))


def not_a_database(tmp_path: Path, name: str) -> Part:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fixture bytes that are not a sqlite database")
    return Part(name=name, path=path)


def valid_empty_message_part(tmp_path: Path, name: str = "message_0.db") -> Part:
    """The shape committed Discovery already seals as a readable ordinary shard.

    A structurally valid but currently empty part is the generation this reader
    knows, with no conversations in it -- not a different generation. Copied from
    the sealed fixture rather than invented, so the two layers cannot disagree
    about the same file.
    """
    return shard(tmp_path, name,
                 "CREATE TABLE TimeStamp (timestamp INTEGER)",
                 "CREATE TABLE wcdb_builtin_compression_record (id INTEGER)")


def supported(tmp_path: Path, name: str = "message_0.db", **kwargs) -> Part:
    return shard(tmp_path, name,
                 _conversation(set(SUPPORTED_CONVERSATION_COLUMNS), **kwargs))


def outcome_of(part: Part):
    return report_over([part]).outcome_of(part.name)


def report_over(parts):
    """The report one real read produced, refused generation and all.

    Accounting is a by-product of the read that had to open the parts anyway,
    so there is no separate entry point that could report something no read
    ever saw. A refused generation raises out of the read; the accounting it
    produced first is still the answer, which is why the refusal is caught here
    rather than avoided.
    """
    provider = provider_over(parts)
    try:
        provider.get_recent_messages(0.0, 50)
    except UnsupportedGeneration:
        pass
    return provider.compatibility


# -- role, compatibility, and their independence -------------------------------


def test_a_recognised_role_is_not_yet_a_compatible_schema(tmp_path):
    """The invariant of 4.1, stated so a later change cannot quietly undo it."""
    part = supported(tmp_path)
    report = report_over([part])
    assert report.role_of(part.name) == ROLE_ORDINARY_MESSAGE
    assert report.outcome_of(part.name) == COMPATIBLE


def test_a_supported_schema_is_compatible(tmp_path):
    report = report_over([supported(tmp_path)])
    assert report.outcome_of("message_0.db") == COMPATIBLE
    assert report.required_gap is False
    assert report.gaps() == ()


@pytest.mark.parametrize(
    "columns,expected",
    [
        # Still the parser's own contract, so still readable: a different
        # generation, not a broken table.
        (set(MANDATORY_COLUMNS), UNSUPPORTED),
        # A column the parser itself refuses on.
        (set(SUPPORTED_CONVERSATION_COLUMNS) - {"create_time"}, MALFORMED),
        # Readable, and outside the envelope this provider was built for.
        (set(SUPPORTED_CONVERSATION_COLUMNS) - {"message_content"}, UNSUPPORTED),
        # Renamed rather than dropped.
        (set(SUPPORTED_CONVERSATION_COLUMNS) - {"server_id"} | {"svr_id"},
         UNSUPPORTED),
    ],
)
def test_a_changed_generation_is_named_by_its_own_verdict(tmp_path, columns, expected):
    part = shard(tmp_path, "message_0.db", _conversation(columns, rows=()))
    assert outcome_of(part) == expected


def test_an_optional_parser_column_missing_is_unsupported_and_never_malformed(tmp_path):
    """Case 5, as the committed envelope already decides it.

    ``test_provider_compatibility.py`` seals the strict reading: a dropped
    ``source`` column is a different generation and is refused. What must not
    happen is the parser's own optional field being treated as mandatory, so the
    verdict is ``unsupported`` -- the parser still reads the table -- and never
    ``malformed``, which is reserved for the shape the parser itself refuses.
    """
    part = shard(tmp_path, "message_0.db",
                 _conversation(set(SUPPORTED_CONVERSATION_COLUMNS) - {"source"}))
    assert outcome_of(part) == UNSUPPORTED
    connection = sqlite3.connect(str(part.path))
    try:
        records = list(wechatdb.parse_conversation(connection, TABLE))
    finally:
        connection.close()
    assert len(records) == 1


def test_bytes_that_are_not_a_database_stay_unassessed_without_driver_text(tmp_path):
    """Nothing was structurally read, so no verdict is invented for it.

    ``unassessed`` is the honest word here, and the point of the test is that
    it is not quietly promoted to a compatibility claim in either direction.
    """
    part = not_a_database(tmp_path, "message_0.db")
    provider = provider_over([part])
    provider.get_recent_messages(0.0, 50)
    report = provider.compatibility
    assert report.outcome_of(part.name) == UNASSESSED
    assert provider.diagnostics.unavailable == 1
    # A required role that was never read is still a gap, so the read cannot
    # claim to have observed everything.
    assert report.required_gap is True
    rendered = repr(report) + repr(report.gaps()) + repr(report.counts())
    assert "fixture bytes" not in rendered and "not a database" not in rendered
    assert str(part.path) not in rendered


def test_a_valid_empty_message_part_is_compatible_not_a_different_generation(tmp_path):
    """The valid-empty part is the generation this reader knows, with no rows.

    Committed Discovery seals this exact shape as ``SHARD_READABLE``
    (``test_discovery.py``). The probe must not call the same file unsupported,
    which would cost a real shard its completeness claim over having no
    conversations in it right now.
    """
    part = valid_empty_message_part(tmp_path)
    assert outcome_of(part) == EMPTY
    provider = provider_over([part])
    got = provider.get_recent_messages(0.0, 50)
    assert provider.compatibility.required_gap is False
    assert got.items == ()


def test_a_valid_empty_part_does_not_cost_a_good_shard_its_completeness(tmp_path):
    """An empty shard beside a populated one is still the whole generation."""
    provider = provider_over([
        supported(tmp_path, "message_0.db"),
        valid_empty_message_part(tmp_path, "message_1.db")])
    got = provider.get_recent_messages(0.0, 50)
    assert provider.compatibility.required_gap is False
    assert got.coverage.status == ms.COVERAGE_COMPLETE
    assert len(got.items) == 1


def test_a_part_with_nothing_this_reader_reads_is_unsupported(tmp_path):
    """It opened, and there is no conversation table: a different generation."""
    part = shard(tmp_path, "message_0.db",
                 "CREATE TABLE fixture_ledger (id INTEGER, note TEXT)")
    report = report_over([part])
    assert report.outcome_of(part.name) == UNSUPPORTED
    assert report.required_gap is True


def test_a_harmless_extra_table_leaves_an_ordinary_shard_compatible(tmp_path):
    part = shard(tmp_path, "message_0.db",
                 _conversation(set(SUPPORTED_CONVERSATION_COLUMNS)),
                 "CREATE TABLE fixture_ledger (id INTEGER, note TEXT)")
    assert outcome_of(part) == COMPATIBLE


@pytest.mark.parametrize("companion", ["_data", "_idx", "_docsize", "_config"])
def test_an_fts_companion_table_is_not_a_new_message_relation(tmp_path, companion):
    """FTS5 materialises real ``type='table'`` companions beside its content table.

    They are named ``<content-table>_data`` and friends, so a bare ``Msg``
    prefix test reads an index's internals as an unread message relation and
    costs a shard a completeness claim that an identically harmless non-``Msg``
    table does not. The companion carries the conversation table's own digest,
    exactly as FTS5 names it, so this is the real shape rather than a guess.
    """
    part = shard(tmp_path, "message_0.db",
                 _conversation(set(SUPPORTED_CONVERSATION_COLUMNS)),
                 "CREATE TABLE \"Msg_%s%s\" (\"block\" BLOB)"
                 % (TABLE[len("Msg_"):], companion))
    assert outcome_of(part) == COMPATIBLE


def test_a_new_message_bearing_relation_keeps_the_shard_from_claiming_complete(
        tmp_path):
    """Case 7 / 4.5: readable conversations are not the whole shard."""
    part = shard(tmp_path, "message_0.db",
                 _conversation(set(SUPPORTED_CONVERSATION_COLUMNS)),
                 "CREATE TABLE Msg_Archive_0 (local_id INTEGER, create_time INTEGER)")
    report = report_over([part])
    assert report.outcome_of(part.name) == INCOMPLETE
    assert report.required_gap is True
    assert "message_relation_unread" in report.gaps()


def test_a_new_message_relation_in_an_empty_part_also_blocks_completion(tmp_path):
    """The same drift, with no conversations to notice it.

    An empty part is the generation this reader knows, but emptiness is only a
    reason to stay quiet when there is nothing unread beside it. A relation
    holding messages this reader never opens is the fact that matters, and it
    does not stop being that fact because the part happens to be empty.
    """
    part = shard(tmp_path, "message_0.db",
                 "CREATE TABLE TimeStamp (timestamp INTEGER)",
                 "CREATE TABLE wcdb_builtin_compression_record (id INTEGER)",
                 "CREATE TABLE Msg_Archive_0 (local_id INTEGER, create_time INTEGER)")
    report = report_over([part])
    assert report.outcome_of(part.name) == INCOMPLETE
    assert report.required_gap is True
    assert "message_relation_unread" in report.gaps()


def test_a_second_conversation_table_is_another_conversation_not_drift(tmp_path):
    part = shard(tmp_path, "message_0.db",
                 _conversation(set(SUPPORTED_CONVERSATION_COLUMNS)),
                 _conversation(set(SUPPORTED_CONVERSATION_COLUMNS),
                               table=OTHER_TABLE, rows=((2, 200),)))
    assert outcome_of(part) == COMPATIBLE


def test_the_same_name_with_a_changed_schema_changes_the_outcome(tmp_path):
    good = supported(tmp_path, "message_0.db")
    bad = shard(tmp_path / "other", "message_0.db",
                _conversation(set(MANDATORY_COLUMNS), rows=()))
    assert outcome_of(good) == COMPATIBLE
    assert outcome_of(bad) == UNSUPPORTED


def test_role_stays_the_same_while_compatibility_gets_worse(tmp_path):
    """Case 9: recognition is a function of the name, and only the name."""
    part = shard(tmp_path, "message_0.db",
                 _conversation(set(MANDATORY_COLUMNS), rows=()))
    report = report_over([part])
    assert report.role_of(part.name) == ROLE_ORDINARY_MESSAGE
    assert report.outcome_of(part.name) == UNSUPPORTED


def test_inventory_ordering_never_changes_a_report(tmp_path):
    first = supported(tmp_path, "message_0.db")
    second = shard(tmp_path, "message_1.db",
                   _conversation(set(MANDATORY_COLUMNS), rows=()))
    forwards = report_over([first, second])
    backwards = report_over([second, first])
    assert forwards.counts() == backwards.counts()
    assert forwards.outcome_of(first.name) == COMPATIBLE
    assert backwards.outcome_of(second.name) == UNSUPPORTED


def test_one_shard_with_two_generations_states_exactly_one_outcome(tmp_path):
    part = shard(tmp_path, "message_0.db",
                 _conversation(set(SUPPORTED_CONVERSATION_COLUMNS)),
                 _conversation(set(MANDATORY_COLUMNS), table=OTHER_TABLE, rows=()))
    report = report_over([part])
    assert report.outcome_of(part.name) == UNSUPPORTED
    assert report.names() == (part.name,)


# -- optional roles never touch ordinary message coverage ----------------------


@pytest.mark.parametrize(
    "name",
    ["message_fts.db", "media.db", "session.db", "contact.db", "sns.db"],
)
def test_an_optional_role_is_accounted_and_never_probed(tmp_path, name):
    """Accounted for, explicitly not assessed, and never a message gap.

    Discovery opens only message parts, so these are ``unassessed`` for an
    honest reason rather than for want of a verdict: nothing read them, and a
    compatibility claim is a statement about something that was read.
    """
    report = report_over([supported(tmp_path, name)])
    assert report.role_of(name) != ROLE_ORDINARY_MESSAGE
    assert report.outcome_of(name) == UNASSESSED
    assert report.required_gap is False
    assert report.gaps() == ()


def test_optional_role_states_can_never_strengthen_a_message_claim():
    assert ROLE_ORDINARY_MESSAGE in REQUIRED_MESSAGE_ROLES
    assert not REQUIRED_MESSAGE_ROLES & {
        ROLE_SEARCH_INDEX, ROLE_MEDIA, ROLE_AUXILIARY, ROLE_BUSINESS_MESSAGE}


def test_a_business_message_shard_is_never_absorbed_by_the_ordinary_reader(tmp_path):
    """Case 17: the gap is explicit, and a green probe does not close it."""
    part = supported(tmp_path, "biz_message_0.db")
    provider = provider_over([part])
    assert provider.get_recent_messages(0.0, 50).items == ()
    report = provider.compatibility
    assert report.role_of(part.name) == ROLE_BUSINESS_MESSAGE
    assert "business_message_unread" in report.gaps()


@pytest.mark.parametrize(
    "name,role",
    [("zzz_unknown.db", ROLE_UNKNOWN),
     ("message_shard.db", ROLE_UNSUPPORTED_MESSAGE_CANDIDATE)],
)
def test_unknown_and_candidate_roles_keep_their_existing_gap(tmp_path, name, role):
    report = report_over([supported(tmp_path, name)])
    assert report.role_of(name) == role
    assert report.outcome_of(name) == UNASSESSED
    assert report.gaps(), name


def test_the_two_role_vocabularies_have_not_drifted():
    """Acquisition and the provider classify independently; parity is proved."""
    from acquisition.database_inventory import classify_database_name
    from wechatdb.provider.compatibility import classify_shard_name

    for name in ("message_0.db", "message_12.db", "biz_message_0.db",
                 "message_fts.db", "message_fts_1.db", "media.db", "media_2.db",
                 "session.db", "contact.db", "hardlink_3.db", "chatbot.db",
                 "sns.db", "message_shard.db", "biz_message_x.db",
                 "zzz_unknown.db", "Message_0.db", "message_0.DB", "message_.db"):
        assert classify_shard_name(name) == classify_database_name(name), name


# -- coverage composition ------------------------------------------------------


def test_a_required_ordinary_shard_that_is_incompatible_is_never_complete(tmp_path):
    """Cases 12 and 13: the mixed set, which is the one that can be got wrong.

    One compatible shard and one refused one cannot yield a complete claim, and
    the refusal reaches coverage as the existing inventory gap rather than as a
    second notion of completeness.
    """
    good = supported(tmp_path, "message_0.db")
    drift = shard(tmp_path, "message_1.db",
                  _conversation(set(MANDATORY_COLUMNS), rows=()))
    report = report_over([good, drift])
    assert report.outcome_of(drift.name) == UNSUPPORTED
    assert report.required_gap is True
    assert "required_shard_unread" in report.gaps()


def test_a_refused_generation_is_refused_and_not_parsed_on_best_effort(tmp_path):
    """The sealed boundary, restated so the accounting cannot erode it.

    A changed surface raises rather than yielding whatever the old reader could
    still find. The compatibility gap is how the refusal is *reported*; it is
    never a licence to read the part anyway.
    """
    part = shard(tmp_path, "message_0.db", _conversation(set(MANDATORY_COLUMNS)))
    provider = provider_over([part])
    with pytest.raises(UnsupportedGeneration):
        provider.get_recent_messages(0.0, 50)
    # The accounting the refused read produced first is still the answer.
    assert provider.compatibility.outcome_of(part.name) == UNSUPPORTED


def test_a_whole_set_of_compatible_shards_is_complete(tmp_path):
    # Two shards, two invented conversations: two shards carrying the same
    # invented message would be an identity collision, not a coverage claim.
    provider = provider_over([
        supported(tmp_path, "message_0.db", table=TABLE, rows=((1, 100),)),
        supported(tmp_path, "message_1.db", table=OTHER_TABLE, rows=((2, 200),))])
    got = provider.get_recent_messages(0.0, 50)
    assert provider.compatibility.required_gap is False
    assert got.coverage.status == ms.COVERAGE_COMPLETE
    assert len(got.items) == 2


@pytest.mark.parametrize("name", ["message_fts.db", "media.db", "session.db"])
def test_optional_drift_does_not_move_the_message_coverage_verdict(tmp_path, name):
    """Cases 14, 15, 16 and 18: optional capability, one verdict.

    Proven as a difference, because a non-message name in the inventory already
    opens the sealed inventory gap on its own: committed Discovery classifies
    every name that is not a message-numbered one as unknown, and that gap is
    not schema evidence and is not this sub-gate's to change.

    The claim that is this sub-gate's is the narrower one: breaking the optional
    part's schema moves neither the coverage verdict nor the message gaps.
    """
    def verdict_for(directory):
        parts = [supported(directory, "message_0.db"),
                 not_a_database(directory, name)]
        provider = provider_over(parts)
        got = provider.get_recent_messages(0.0, 50)
        return got.coverage.status, got.coverage.reason, provider.compatibility

    # Same inventory and same names, so the only difference is the optional
    # part: readable in one, a file that is not a database at all in the other.
    # That is the strongest optional drift there is.
    readable = verdict_for(tmp_path / "readable")
    drifted = verdict_for(tmp_path / "drifted")
    assert readable[0] == drifted[0] and readable[1] == drifted[1], name
    # And the optional part contributes no message gap in either case.
    assert drifted[2].required_gap is False, name
    assert GAP_SCHEMA_INCOMPATIBLE not in drifted[2].gaps(), name


def test_an_ordinary_read_alone_is_complete(tmp_path):
    """The baseline those optional cases are read against."""
    ordinary = provider_over([supported(tmp_path, "message_0.db")]
                             ).get_recent_messages(0.0, 50)
    assert ordinary.coverage.status == ms.COVERAGE_COMPLETE


def test_a_compatibility_gap_never_overwrites_a_stronger_reason(tmp_path, monkeypatch):
    """F-039: a ceiling never rewrites why a read is already partial."""
    from .test_provider_reads import inject_partial_read

    inject_partial_read(monkeypatch, "message_0.db", keep=1,
                        observed_through=100, complete_through=100)
    got = provider_over([supported(tmp_path, "message_0.db")]
                        ).get_recent_messages(0.0, 50)
    assert got.coverage.reason == ms.REASON_SOURCE_LIMIT
    assert got.coverage.truncated is True


# -- parser parity -------------------------------------------------------------


def test_the_probe_and_the_parser_never_disagree_about_required_columns():
    """One structural truth: the envelope is derived from the parser's own set."""
    assert SUPPORTED_CONVERSATION_COLUMNS == frozenset(WANTED_COLUMNS)
    assert set(MANDATORY_COLUMNS) <= SUPPORTED_CONVERSATION_COLUMNS


def test_every_structure_called_compatible_is_accepted_by_the_parser(tmp_path):
    part = supported(tmp_path)
    assert outcome_of(part) == COMPATIBLE
    connection = sqlite3.connect(str(part.path))
    try:
        assert len(list(wechatdb.parse_conversation(connection, TABLE))) == 1
    finally:
        connection.close()


@pytest.mark.parametrize("column", sorted(SUPPORTED_CONVERSATION_COLUMNS))
def test_no_parser_read_column_may_be_silently_absent(tmp_path, column):
    part = shard(tmp_path, "message_0.db",
                 _conversation(set(SUPPORTED_CONVERSATION_COLUMNS) - {column},
                               rows=()))
    assert outcome_of(part) != COMPATIBLE


@pytest.mark.parametrize("column", sorted(MANDATORY_COLUMNS))
def test_a_parser_mandatory_column_missing_is_malformed_and_also_refused(
        tmp_path, column):
    part = shard(tmp_path, "message_0.db",
                 _conversation(set(SUPPORTED_CONVERSATION_COLUMNS) - {column},
                               rows=()))
    assert outcome_of(part) == MALFORMED
    connection = sqlite3.connect(str(part.path))
    try:
        with pytest.raises(wechatdb.MessageSchemaError):
            list(wechatdb.parse_conversation(connection, TABLE))
    finally:
        connection.close()


# -- the boundary of the probe -------------------------------------------------


def test_no_caller_string_reaches_sql_through_the_compatibility_path(tmp_path):
    """Case 28: a hostile name is classified and never interpolated."""
    hostile = 'message_0.db" ; DROP TABLE Msg_ --.db'
    report = report_over([supported(tmp_path, hostile)])
    assert report.outcome_of(hostile) == UNASSESSED
    assert report.role_of(hostile) == ROLE_UNKNOWN


def test_a_report_carries_no_path_no_table_and_no_message_content(tmp_path):
    """Cases 22, 23 and 25: rendering is counts only, from a closed vocabulary."""
    part = shard(tmp_path, "message_0.db",
                 _conversation(set(SUPPORTED_CONVERSATION_COLUMNS), rows=((7, 700),)))
    report = report_over([part])
    rendered = repr(report) + repr(report.counts()) + repr(report.gaps())
    for forbidden in (str(part.path), TABLE, OTHER_TABLE, ROOM, "message_0.db"):
        assert forbidden not in rendered, forbidden


def test_every_recognised_database_has_exactly_one_outcome(tmp_path):
    """Cases 29 and 30: total, and never contradictory."""
    parts = [supported(tmp_path, "message_0.db"),
             shard(tmp_path, "message_1.db",
                   _conversation(set(MANDATORY_COLUMNS), rows=())),
             not_a_database(tmp_path, "message_2.db"),
             supported(tmp_path, "media.db"),
             supported(tmp_path, "biz_message_0.db"),
             supported(tmp_path, "zzz_unknown.db")]
    report = report_over(parts)
    assert report.names() == tuple(sorted(part.name for part in parts))
    assert len(report.names()) == len(parts)
    for name in report.names():
        assert report.role_of(name) in SHARD_ROLES
        assert report.outcome_of(name) in COMPATIBILITY_STATES


def test_a_recognised_shard_that_will_not_open_still_leaves_a_gap(tmp_path):
    """Case 31."""
    part = not_a_database(tmp_path, "message_0.db")
    provider = provider_over([part])
    got = provider.get_recent_messages(0.0, 50)
    assert provider.compatibility.outcome_of(part.name) == UNASSESSED
    assert provider.compatibility.required_gap is True
    assert got.coverage.reason == ms.REASON_PARTIAL_INVENTORY
    assert got.items == ()


def test_the_search_capability_vocabulary_is_untouched_by_message_accounting(tmp_path):
    """Cases 12 and 15: FTS states stay FTS states, and are not coverage."""
    from wechatdb.provider import CAPABILITY_STATES

    assert {CAPABILITY_ABSENT, CAPABILITY_UNSUPPORTED} <= CAPABILITY_STATES
    got = provider_over([supported(tmp_path, "message_0.db")]
                        ).get_recent_messages(0.0, 50)
    assert got.coverage.status == ms.COVERAGE_COMPLETE
