"""ShardDiscovery: an inventory that never drops a part, and never guesses.

Two passes with one honest state for each thing that can be true of a part.
The catalogue opens nothing and can only say KNOWN or UNKNOWN; only the probe,
asking an injected opener, can say READABLE or UNAVAILABLE. Nothing here opens
a real location: every part is a P12 synthetic fixture under tmp_path, and an
opener that must refuse is injected rather than pointed at anything.
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

import wechatdb
from wechatdb.provider import discovery
from wechatdb.provider.discovery import (
    SHARD_KNOWN,
    SHARD_READABLE,
    SHARD_STATES,
    SHARD_UNAVAILABLE,
    SHARD_UNKNOWN,
    ExplicitShardLocator,
    ReadOnlySqliteOpener,
    ShardDiscovery,
    ShardEntry,
    ShardFacts,
    ShardLocator,
    ShardOpener,
    shard_key,
)

from . import fixtures
from .fixtures import ALPHA, BETA, SyntheticMessage

CONVERSATION = ALPHA
MESSAGES = (
    SyntheticMessage(1, 1_756_000_010, ALPHA, "fixture message one"),
    SyntheticMessage(2, 1_756_000_020, BETA, "fixture message two"),
    SyntheticMessage(3, 1_756_000_030, ALPHA, "fixture message three"),
)
PROVIDER = Path(discovery.__file__).resolve().parent


def entry(part: fixtures.SyntheticPart, name: str | None = None) -> ShardEntry:
    """A P12 part as the provider sees it: a name and an opaque handle."""
    return ShardEntry(name=name or part.name, handle=part.path)


class RefusingOpener:
    """Refuses every open with the failure contract Discovery consumes."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def open(self, entry: ShardEntry) -> sqlite3.Connection:
        self.asked.append(entry.name)
        raise sqlite3.OperationalError("synthetic refusal")


class CountingOpener:
    """The real opener, with every request recorded."""

    def __init__(self) -> None:
        self.asked: list[str] = []
        self._real = ReadOnlySqliteOpener()

    def open(self, entry: ShardEntry) -> sqlite3.Connection:
        self.asked.append(entry.name)
        return self._real.open(entry)


class ExplodingOpener:
    def open(self, entry: ShardEntry) -> sqlite3.Connection:
        raise AssertionError("the catalogue pass must not open anything")


def discovered(tmp_path, *parts_and_names, opener=None):
    entries = [entry(p, n) if isinstance(p, fixtures.SyntheticPart) else p
               for p, n in parts_and_names]
    return ShardDiscovery(ExplicitShardLocator(entries), opener or ReadOnlySqliteOpener())


# --- the vocabulary ----------------------------------------------------------

def test_the_four_states_are_provider_internal_and_closed():
    assert SHARD_STATES == {"known", "readable", "unknown", "unavailable"}
    assert (SHARD_KNOWN, SHARD_READABLE, SHARD_UNKNOWN, SHARD_UNAVAILABLE) == (
        "known", "readable", "unknown", "unavailable")
    # Never in the generic boundary.
    import message_source
    for name in ("SHARD_KNOWN", "SHARD_READABLE", "SHARD_UNKNOWN",
                 "SHARD_UNAVAILABLE", "SHARD_STATES", "ShardFacts"):
        assert not hasattr(message_source, name), name


# --- the catalogue pass -------------------------------------------------------

def test_a_recognised_name_is_catalogued_as_known_not_readable(tmp_path):
    part = fixtures.readable_part(tmp_path, "message_0.db", CONVERSATION, MESSAGES)
    inventory = discovered(tmp_path, (part, None)).catalogue()

    [facts] = inventory.values()
    assert facts.state == SHARD_KNOWN
    assert not any(f.state == SHARD_READABLE for f in inventory.values())


@pytest.mark.parametrize("name,expected", [
    ("message_0.db", SHARD_KNOWN),
    ("message_12.db", SHARD_KNOWN),
    ("fixture_a.db", SHARD_UNKNOWN),
    ("fixture.blob", SHARD_UNKNOWN),
    ("message_.db", SHARD_UNKNOWN),
    ("message_0.db.bak", SHARD_UNKNOWN),
    ("contact.db", SHARD_UNKNOWN),
    ("session.db", SHARD_UNKNOWN),
])
def test_only_the_message_part_shape_is_known(tmp_path, name, expected):
    """Message-shard discovery only: no contact, session or bare-.db role."""
    part = fixtures.readable_part(tmp_path, name, CONVERSATION, MESSAGES[:1])
    [facts] = discovered(tmp_path, (part, None), opener=ExplodingOpener()).catalogue().values()
    assert facts.state == expected


def test_an_uncharacterisable_entry_stays_in_the_inventory_as_unknown(tmp_path):
    unknown = fixtures.unknown_name_part(tmp_path, CONVERSATION, MESSAGES[:1])
    known = fixtures.readable_part(tmp_path, "message_0.db", CONVERSATION, MESSAGES[:1])
    inventory = discovered(tmp_path, (unknown, None), (known, None),
                           opener=ExplodingOpener()).catalogue()

    assert len(inventory) == 2
    assert inventory[shard_key(unknown.name)].state == SHARD_UNKNOWN


def test_the_catalogue_pass_opens_nothing(tmp_path):
    """The opener explodes if asked, and the handle is a value nothing can open."""
    untouchable = object()
    d = ShardDiscovery(ExplicitShardLocator([ShardEntry("message_0.db", untouchable),
                                             ShardEntry("fixture.blob", untouchable)]),
                       ExplodingOpener())
    inventory = d.catalogue()
    assert {f.state for f in inventory.values()} == {SHARD_KNOWN, SHARD_UNKNOWN}


def test_nothing_is_claimed_before_probing(tmp_path):
    part = fixtures.readable_part(tmp_path, "message_0.db", CONVERSATION, MESSAGES)
    unknown = fixtures.unknown_name_part(tmp_path, CONVERSATION, MESSAGES[:1])
    for facts in discovered(tmp_path, (part, None), (unknown, None)).catalogue().values():
        assert facts.bounds_established is False
        assert facts.min_timestamp is None and facts.max_timestamp is None
        assert facts.tables == ()
        assert facts.state in {SHARD_KNOWN, SHARD_UNKNOWN}


def test_duplicate_entry_names_are_refused_not_collapsed(tmp_path):
    """Two parts under one key would drop one of them. That is refused."""
    a = fixtures.readable_part(tmp_path, "message_0.db", CONVERSATION, MESSAGES[:1])
    (tmp_path / "other").mkdir()
    b = fixtures.readable_part(tmp_path / "other", "message_0.db", CONVERSATION,
                               MESSAGES[1:2])
    d = discovered(tmp_path, (a, None), (b, None), opener=ExplodingOpener())
    with pytest.raises(ValueError) as refusal:
        d.catalogue()
    assert "message_0" not in str(refusal.value)
    assert str(tmp_path) not in str(refusal.value)


# --- the probe pass -----------------------------------------------------------

def test_a_known_readable_part_becomes_readable(tmp_path):
    part = fixtures.readable_part(tmp_path, "message_0.db", CONVERSATION, MESSAGES)
    d = discovered(tmp_path, (part, None))
    [facts] = d.probe(d.catalogue()).values()

    assert facts.state == SHARD_READABLE
    assert facts.tables == (fixtures.conversation_table(CONVERSATION),)
    assert facts.bounds_established is True
    assert (facts.min_timestamp, facts.max_timestamp) == (1_756_000_010, 1_756_000_030)


def test_a_part_that_will_not_open_is_unavailable_not_absent(tmp_path):
    part = fixtures.readable_part(tmp_path, "message_0.db", CONVERSATION, MESSAGES)
    opener = RefusingOpener()
    d = discovered(tmp_path, (part, None), opener=opener)
    before = d.catalogue()
    after = d.probe(before)

    assert after.keys() == before.keys()
    [facts] = after.values()
    assert facts.state == SHARD_UNAVAILABLE
    assert facts.tables == () and facts.bounds_established is False
    assert opener.asked == ["message_0.db"]


def test_a_part_with_an_unrecognised_schema_is_unavailable(tmp_path):
    part = fixtures.unrecognised_schema_part(tmp_path, name="message_3.db")
    d = discovered(tmp_path, (part, None))
    [facts] = d.probe(d.catalogue()).values()
    assert facts.state == SHARD_UNAVAILABLE
    assert facts.tables == ()


def test_a_valid_empty_message_part_is_readable_without_invented_bounds(tmp_path):
    path = tmp_path / "message_8.db"
    connection = sqlite3.connect(str(path))
    connection.execute("CREATE TABLE TimeStamp (timestamp INTEGER)")
    connection.execute("CREATE TABLE wcdb_builtin_compression_record (id INTEGER)")
    connection.commit(); connection.close()
    part = fixtures.SyntheticPart("message_8.db", path, lambda: None)
    d = discovered(tmp_path, (part, None))

    [facts] = d.probe(d.catalogue()).values()

    assert facts.state == SHARD_READABLE
    assert facts.tables == ()
    assert facts.bounds_established is False
    assert (facts.min_timestamp, facts.max_timestamp) == (None, None)


def test_message_metadata_without_the_timestamp_column_is_unavailable(tmp_path):
    path = tmp_path / "message_9.db"
    connection = sqlite3.connect(str(path))
    connection.execute("CREATE TABLE TimeStamp (not_timestamp INTEGER)")
    connection.execute("CREATE TABLE wcdb_builtin_compression_record (id INTEGER)")
    connection.commit(); connection.close()
    part = fixtures.SyntheticPart("message_9.db", path, lambda: None)
    d = discovered(tmp_path, (part, None))

    [facts] = d.probe(d.catalogue()).values()

    assert facts.state == SHARD_UNAVAILABLE
    assert facts.tables == ()


def test_a_conversation_table_missing_a_mandatory_column_is_unavailable(tmp_path):
    """Recognised name, recognised table name, but not the parser's contract."""
    path = tmp_path / "message_4.db"
    c = sqlite3.connect(str(path))
    c.execute(f'CREATE TABLE "{fixtures.conversation_table(CONVERSATION)}" '
              "(local_id INTEGER PRIMARY KEY, message_content BLOB)")
    c.commit(); c.close()
    d = discovered(tmp_path, (fixtures.SyntheticPart("message_4.db", path, lambda: None), None))
    [facts] = d.probe(d.catalogue()).values()
    assert facts.state == SHARD_UNAVAILABLE


def test_one_malformed_table_beside_a_good_one_is_not_silently_ignored(tmp_path):
    """READABLE would have to mean every recognised table is readable."""
    part = fixtures.readable_part(tmp_path, "message_5.db", CONVERSATION, MESSAGES)
    c = sqlite3.connect(str(part.path))
    c.execute(f'CREATE TABLE "{fixtures.conversation_table(BETA)}" (local_id INTEGER)')
    c.commit(); c.close()
    d = discovered(tmp_path, (part, None))
    [facts] = d.probe(d.catalogue()).values()
    assert facts.state == SHARD_UNAVAILABLE


def test_an_unknown_entry_is_not_probed(tmp_path):
    unknown = fixtures.unknown_name_part(tmp_path, CONVERSATION, MESSAGES[:1])
    opener = CountingOpener()
    d = discovered(tmp_path, (unknown, None), opener=opener)
    before = d.catalogue(); after = d.probe(before)

    assert opener.asked == []
    assert after == before
    assert after[shard_key(unknown.name)].state == SHARD_UNKNOWN


def test_probing_never_changes_how_many_parts_there_are(tmp_path):
    parts = [
        (fixtures.readable_part(tmp_path, "message_0.db", CONVERSATION, MESSAGES[:1]), None),
        (fixtures.unknown_name_part(tmp_path, CONVERSATION, MESSAGES[:1]), None),
        (fixtures.unrecognised_schema_part(tmp_path, name="message_1.db"), None),
        (fixtures.unbounded_part(tmp_path, CONVERSATION, name="message_2.db"), None),
        (ShardEntry("message_9.db", object()), None),  # will not open
    ]
    d = discovered(tmp_path, *parts)
    before = d.catalogue(); after = d.probe(before)
    assert set(after) == set(before) and len(after) == 5
    assert {after[shard_key("message_9.db")].state} == {SHARD_UNAVAILABLE}


def test_a_readable_empty_part_has_absent_bounds(tmp_path):
    part = fixtures.unbounded_part(tmp_path, CONVERSATION, name="message_2.db")
    d = discovered(tmp_path, (part, None))
    [facts] = d.probe(d.catalogue()).values()
    assert facts.state == SHARD_READABLE
    assert facts.tables != ()
    assert facts.bounds_established is False
    assert (facts.min_timestamp, facts.max_timestamp) == (None, None)


def test_mixed_second_and_millisecond_times_normalise_before_bounding(tmp_path):
    """Each value is normalised, then bounded; not the other way round."""
    mixed = (SyntheticMessage(1, 1_756_000_000, ALPHA, "seconds"),
             SyntheticMessage(2, 1_756_000_010_000, BETA, "milliseconds"))
    part = fixtures.readable_part(tmp_path, "message_6.db", CONVERSATION, mixed)
    d = discovered(tmp_path, (part, None))
    [facts] = d.probe(d.catalogue()).values()
    assert (facts.min_timestamp, facts.max_timestamp) == (1_756_000_000, 1_756_000_010)
    assert facts.max_timestamp < wechatdb.parser.MILLISECOND_THRESHOLD


def test_bounds_are_established_or_absent_never_guessed():
    ok = dict(key="k", state=SHARD_READABLE, tables=("Msg_" + "0" * 32,))
    ShardFacts(bounds_established=True, min_timestamp=1, max_timestamp=2, **ok)
    ShardFacts(bounds_established=False, min_timestamp=None, max_timestamp=None, **ok)
    for bad in (dict(bounds_established=True, min_timestamp=1, max_timestamp=None),
                dict(bounds_established=True, min_timestamp=None, max_timestamp=2),
                dict(bounds_established=False, min_timestamp=1, max_timestamp=None),
                dict(bounds_established=False, min_timestamp=1, max_timestamp=2),
                dict(bounds_established=True, min_timestamp=None, max_timestamp=None),
                dict(bounds_established=True, min_timestamp=3, max_timestamp=2)):
        with pytest.raises(ValueError):
            ShardFacts(**ok, **bad)


def test_state_invariants_are_enforced_at_construction():
    quiet = dict(bounds_established=False, min_timestamp=None, max_timestamp=None)
    for state in (SHARD_KNOWN, SHARD_UNKNOWN, SHARD_UNAVAILABLE):
        ShardFacts(key="k", state=state, **quiet)
        with pytest.raises(ValueError):
            ShardFacts(key="k", state=state, tables=("Msg_" + "0" * 32,), **quiet)
        with pytest.raises(ValueError):
            ShardFacts(key="k", state=state, bounds_established=True,
                       min_timestamp=1, max_timestamp=1)
    ShardFacts(key="k", state=SHARD_READABLE, **quiet)  # valid empty message part
    with pytest.raises(ValueError):
        ShardFacts(key="k", state="mystery", **quiet)



def test_readable_facts_refuse_non_conversation_tables():
    """READABLE carries recognised conversation tables, or it is not READABLE.

    The parser owns the `Msg_<32 hex>` contract; the type must not be able to
    express a readable part whose evidence is a name the parser would never
    read. P14 consumes `tables` as routing evidence, so the claim is sealed at
    construction rather than trusted to whoever populated it.
    """
    quiet = dict(key="k", state=SHARD_READABLE, bounds_established=False,
                 min_timestamp=None, max_timestamp=None)
    good = "Msg_" + "0123456789abcdef" * 2          # exactly 32 hex characters
    ShardFacts(tables=(good,), **quiet)
    ShardFacts(tables=(good, "Msg_" + "F" * 32), **quiet)   # upper-case hex is hex

    for bad in ("not_a_conversation_table", "Msg_deadbeef", "Msg_" + "0" * 31,
                "Msg_" + "0" * 33, "Msg_" + "g" * 32, "msg_" + "0" * 32, ""):
        with pytest.raises(ValueError) as refusal:
            ShardFacts(tables=(bad,), **quiet)
        assert bad == "" or bad not in str(refusal.value)

    with pytest.raises(ValueError):
        ShardFacts(tables=(good, "not_a_conversation_table"), **quiet)  # one bad among good
    with pytest.raises(ValueError):
        ShardFacts(tables=(42,), **quiet)  # not even a string


def test_a_part_is_identified_by_an_opaque_digest_never_by_its_name(tmp_path):
    part = fixtures.readable_part(tmp_path, "message_0.db", CONVERSATION, MESSAGES)
    key = shard_key("message_0.db")
    assert key == shard_key("message_0.db") and key != shard_key("message_1.db")
    assert key != "message_0.db" and len(key) == 12 and int(key, 16) >= 0

    d = discovered(tmp_path, (part, None))
    for facts in d.probe(d.catalogue()).values():
        rendered = repr(facts)
        assert "message_0" not in rendered
        assert str(part.path) not in rendered and str(tmp_path) not in rendered
        assert "handle" not in ShardFacts.__slots__


def test_shard_key_is_stable_across_processes():
    import subprocess, sys
    probe = ("import sys; sys.path.insert(0, %r); sys.path.insert(0, %r)\n"
             "from wechatdb.provider.discovery import shard_key\n"
             "print(shard_key('message_0.db'))" % (str(PROVIDER.parents[1]),
                                                    str(PROVIDER.parents[1] / "bridge")))
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                         text=True, check=True).stdout.strip()
    assert out == shard_key("message_0.db")


# --- failures during the probe ----------------------------------------------

class _FailsOnBounds:
    """A connection that opens and inspects, then fails reading create_time."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real, self.closed = real, False

    def execute(self, sql, *a):
        if "create_time" in sql and sql.lstrip().upper().startswith("SELECT"):
            raise sqlite3.OperationalError("synthetic read failure")
        return self._real.execute(sql, *a)

    def close(self):
        self.closed = True; self._real.close()


def test_a_failure_while_reading_bounds_is_unavailable_and_still_closed(tmp_path):
    part = fixtures.readable_part(tmp_path, "message_0.db", CONVERSATION, MESSAGES)
    handed: list[_FailsOnBounds] = []

    class Opener:
        def open(self, e: ShardEntry):
            c = _FailsOnBounds(sqlite3.connect(f"file:{e.handle}?mode=ro", uri=True))
            handed.append(c); return c

    d = discovered(tmp_path, (part, None), opener=Opener())
    before = d.catalogue(); after = d.probe(before)
    assert after.keys() == before.keys()
    [facts] = after.values()
    assert facts.state == SHARD_UNAVAILABLE and facts.tables == ()
    assert handed[0].closed is True


def test_a_programming_error_is_not_swallowed(tmp_path):
    part = fixtures.readable_part(tmp_path, "message_0.db", CONVERSATION, MESSAGES)

    class Broken:
        def open(self, e):
            raise TypeError("a bug, not a refusal")

    d = discovered(tmp_path, (part, None), opener=Broken())
    with pytest.raises(TypeError):
        d.probe(d.catalogue())


# --- the read-only boundary ---------------------------------------------------

def test_discovery_opens_only_through_the_injected_opener(tmp_path):
    tree = ast.parse((PROVIDER / "discovery.py").read_text(encoding="utf-8"))
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "ShardDiscovery")
    for node in ast.walk(cls):
        if isinstance(node, ast.Attribute) and node.attr == "connect":
            raise AssertionError("ShardDiscovery must not call sqlite3.connect")

    known = fixtures.readable_part(tmp_path, "message_0.db", CONVERSATION, MESSAGES[:1])
    other = fixtures.readable_part(tmp_path, "message_1.db", CONVERSATION, MESSAGES[1:])
    unknown = fixtures.unknown_name_part(tmp_path, CONVERSATION, MESSAGES[:1])
    opener = CountingOpener()
    d = discovered(tmp_path, (known, None), (other, None), (unknown, None), opener=opener)
    d.probe(d.catalogue())
    assert sorted(opener.asked) == ["message_0.db", "message_1.db"]
    assert isinstance(opener, ShardOpener) and isinstance(
        ExplicitShardLocator([]), ShardLocator)


def test_the_read_only_opener_is_mode_ro_and_never_immutable(tmp_path, monkeypatch):
    seen: dict = {}
    real = sqlite3.connect

    def spy(database, *a, **kw):
        seen["database"], seen["kw"] = database, kw
        return real(database, *a, **kw)

    monkeypatch.setattr(sqlite3, "connect", spy)
    part = fixtures.readable_part(tmp_path, "message_0.db", CONVERSATION, MESSAGES[:1])
    ReadOnlySqliteOpener().open(entry(part)).close()

    assert seen["kw"].get("uri") is True
    assert seen["database"].startswith("file:")
    assert "mode=ro" in seen["database"]
    assert "immutable" not in seen["database"]
    source = (PROVIDER / "discovery.py").read_text(encoding="utf-8")
    assert "immutable" not in source.lower()


def test_a_write_through_the_opener_is_refused(tmp_path):
    part = fixtures.readable_part(tmp_path, "message_0.db", CONVERSATION, MESSAGES)
    before = part.path.read_bytes()
    connection = ReadOnlySqliteOpener().open(entry(part))
    try:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute(
                f'INSERT INTO "{fixtures.conversation_table(CONVERSATION)}"'
                " (local_id, create_time) VALUES (99, 1)")
    finally:
        connection.close()
    assert part.path.read_bytes() == before


def test_a_handle_that_is_not_path_like_is_a_refusal_without_a_leak(tmp_path):
    with pytest.raises(sqlite3.OperationalError) as refusal:
        ReadOnlySqliteOpener().open(ShardEntry("message_0.db", object()))
    assert "object at" not in str(refusal.value)
    with pytest.raises(sqlite3.OperationalError) as refusal:
        ReadOnlySqliteOpener().open(ShardEntry("message_0.db", tmp_path / "fixture_absent.db"))
    assert "fixture_absent" not in str(refusal.value)


def test_no_provider_component_searches_for_a_path():
    """The discovery-side twin of P12's acquisition guard; neither is weakened."""
    names = {"glob", "iglob", "rglob", "iterdir", "listdir", "scandir", "walk",
             "home", "expanduser", "resolve", "absolute", "parent", "parents"}
    offenders = []
    for path in sorted(PROVIDER.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in names:
                offenders.append(f"{path.name}: .{node.attr}")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "/" in node.value or "\\" in node.value:
                    offenders.append(f"{path.name}: {node.value!r}")
    assert offenders == [], offenders
