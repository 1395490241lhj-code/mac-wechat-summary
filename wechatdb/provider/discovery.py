"""Shard discovery: an inventory of a composite source that never drops a part.

Two passes, kept apart because they cost different things and can honestly
say different things. The catalogue looks only at each entry's name and opens
nothing, so it can say **known** (the name shape is recognised, readability
untested) or **unknown** (not characterisable) and nothing more. The probe asks
an injected opener for a read-only connection to each known entry and can then
say **readable** (schema recognised, bounds taken or honestly absent) or
**unavailable** (the open was refused, or what opened is not a message part).
An unknown entry is never opened merely to guess what it might be.

Nothing is ever dropped: probing changes what is known about a part, never how
many parts there are, and two entries that would share one key are refused
rather than collapsed.

This module cannot find a database. The locator lists exactly what it was
given; the opener turns an explicitly supplied handle into a read-only
connection and searches for nothing; Discovery itself never opens anything.
A part is identified internally by an opaque digest of its name, never by the
name and never by a path, and no name, handle or path is ever copied into the
facts recorded about it.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable
from urllib.parse import quote

import wechatdb
from wechatdb.parser import CONVERSATION_TABLE, MANDATORY_COLUMNS

# -- states, provider-internal only -------------------------------------------

SHARD_KNOWN = "known"
SHARD_READABLE = "readable"
SHARD_UNKNOWN = "unknown"
SHARD_UNAVAILABLE = "unavailable"

SHARD_STATES = frozenset({
    SHARD_KNOWN,
    SHARD_READABLE,
    SHARD_UNKNOWN,
    SHARD_UNAVAILABLE,
})

#: The one message-part name shape this project has documented evidence for.
#: Message-shard discovery only: no contact, session or identity role, and no
#: recognition of an arbitrary database name.
_MESSAGE_PART = re.compile(r"^message_[0-9]+[.]db$")


# -- entries and how they are found -------------------------------------------

@dataclass(frozen=True, slots=True)
class ShardEntry:
    """One part of a composite source, as handed to the provider.

    ``name`` is used for classification and for the opaque key, and for
    nothing else. ``handle`` is opaque here: only the opener knows what to do
    with it, and Discovery never inspects, resolves or derives anything from
    it. Neither field is copied into :class:`ShardFacts`.
    """

    name: str
    handle: object


@runtime_checkable
class ShardLocator(Protocol):
    def entries(self) -> tuple[ShardEntry, ...]: ...


class ExplicitShardLocator:
    """Lists exactly the entries it was constructed with. Searches nothing."""

    def __init__(self, entries: Sequence[ShardEntry]) -> None:
        self._entries = tuple(entries)

    def entries(self) -> tuple[ShardEntry, ...]:
        return self._entries


def shard_key(name: str) -> str:
    """A stable, opaque digest of an entry name.

    Deterministic across processes, never the name itself, and carrying no
    path: safe to use as an identifier inside provider orchestration.
    """
    # Not blake2b: that digest is reserved, by an architecture guard, for the
    # one canonical conversation-identity construction in the Reader boundary,
    # and a second blake2b anywhere under wechatdb would read as a copy of it.
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]


# -- what is known about a part ------------------------------------------------

@dataclass(frozen=True, slots=True)
class ShardFacts:
    """Everything Discovery is entitled to say about one part, and no more.

    Only a readable part carries evidence. A structurally valid empty message
    part is readable with no conversation tables and no bounds. Known, unknown
    and unavailable parts all carry none, for three different reasons: not
    opened yet, not characterisable, probed and not readable. Bounds are
    established with both endpoints or absent with neither; one endpoint is
    never guessed from the other. No name, handle or path lives here.
    """

    key: str
    state: str
    bounds_established: bool
    min_timestamp: int | None
    max_timestamp: int | None
    tables: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.state not in SHARD_STATES:
            raise ValueError("shard state is not in the closed vocabulary")
        have_min = self.min_timestamp is not None
        have_max = self.max_timestamp is not None
        if self.bounds_established:
            if not (have_min and have_max):
                raise ValueError("established bounds need both endpoints")
            if self.min_timestamp > self.max_timestamp:
                raise ValueError("bounds are inverted")
        elif have_min or have_max:
            raise ValueError("absent bounds carry no endpoint")
        if self.state == SHARD_READABLE:
            # The parser owns the conversation-table contract; a readable part
            # may not cite a name the parser would never read.
            if any(not isinstance(table, str)
                   or CONVERSATION_TABLE.fullmatch(table) is None
                   for table in self.tables):
                raise ValueError("a readable part carries only conversation tables")
        elif self.tables or self.bounds_established:
            raise ValueError("a part that was not read carries no evidence")


def _unavailable(key: str) -> ShardFacts:
    return ShardFacts(key=key, state=SHARD_UNAVAILABLE, bounds_established=False,
                      min_timestamp=None, max_timestamp=None)


# -- opening, which Discovery never does itself ------------------------------

@runtime_checkable
class ShardOpener(Protocol):
    """The only thing that turns an entry into a connection.

    A refusal is a :class:`sqlite3.Error`; Discovery consumes that and nothing
    broader, so a programming error is never mistaken for an unreadable part.
    """

    def open(self, entry: ShardEntry) -> sqlite3.Connection: ...


class ReadOnlySqliteOpener:
    """An explicitly supplied path-like handle, opened read-only, and no more.

    The handle is taken exactly as given: not resolved, not expanded, not
    searched around, its neighbours never listed. The connection is a
    ``mode=ro`` URI, and never the optimisation that makes SQLite ignore the
    write-ahead log, which would silently drop not-yet-checkpointed rows. A
    handle that is not path-like, or a file that cannot be opened, is a refusal
    in the opener's failure contract, and the refusal carries no path.
    """

    def open(self, entry: ShardEntry) -> sqlite3.Connection:
        try:
            raw = os.fspath(entry.handle)
        except TypeError:
            raise sqlite3.OperationalError("the handle is not path-like") from None
        if isinstance(raw, bytes):
            raw = os.fsdecode(raw)
        uri = f"file:{quote(raw)}?mode=ro"
        return sqlite3.connect(uri, uri=True)


# -- discovery ----------------------------------------------------------------

class ShardDiscovery:
    """Catalogue by name, then probe through the injected opener.

    The entries are kept privately by opaque key between the two passes so
    the probe can ask the opener for them; that mapping never appears in any
    :class:`ShardFacts`.
    """

    def __init__(self, locator: ShardLocator, opener: ShardOpener) -> None:
        self._locator = locator
        self._opener = opener
        self._entries: dict[str, ShardEntry] = {}

    def catalogue(self) -> dict[str, ShardFacts]:
        """Pass one. Classifies by name shape and opens nothing.

        Every listed entry appears exactly once, as known or unknown. Two
        entries that would share a key are refused rather than one being lost.
        """
        keyed: dict[str, ShardEntry] = {}
        inventory: dict[str, ShardFacts] = {}
        for entry in self._locator.entries():
            key = shard_key(entry.name)
            if key in keyed:
                raise ValueError("two parts share one name")
            keyed[key] = entry
            state = SHARD_KNOWN if _MESSAGE_PART.match(entry.name) else SHARD_UNKNOWN
            inventory[key] = ShardFacts(key=key, state=state, bounds_established=False,
                                        min_timestamp=None, max_timestamp=None)
        self._entries = keyed
        return inventory

    def probe(self, inventory: dict[str, ShardFacts]) -> dict[str, ShardFacts]:
        """Pass two. Opens each known part read-only and reclassifies it.

        The returned key set equals the input's. Unknown parts are left exactly
        as they were and never opened.
        """
        result: dict[str, ShardFacts] = {}
        for key, facts in inventory.items():
            if facts.state != SHARD_KNOWN:
                result[key] = facts
                continue
            entry = self._entries.get(key)
            if entry is None:
                raise ValueError("inventory was not produced by this discovery")
            result[key] = self._probe_one(key, entry)
        return result

    def _probe_one(self, key: str, entry: ShardEntry) -> ShardFacts:
        try:
            connection = self._opener.open(entry)
        except sqlite3.Error:
            return _unavailable(key)
        try:
            tables = tuple(wechatdb.conversation_tables(connection))
            if not tables and not _is_valid_empty_message_part(connection):
                return _unavailable(key)
            for table in tables:
                present = {row[1] for row in
                           connection.execute(f'PRAGMA table_info("{table}")')}
                if not all(column in present for column in MANDATORY_COLUMNS):
                    # One malformed table among good ones is not ignored.
                    return _unavailable(key)
            # Each value is normalised, then the extremes are taken, so a table
            # mixing second- and millisecond-valued times cannot report a
            # maximum in the far future.
            values: list[int] = []
            for table in tables:
                rows = connection.execute(
                    f'SELECT "create_time" FROM "{table}"').fetchall()
                values.extend(wechatdb.normalise_timestamp(row[0]) for row in rows)
        except sqlite3.Error:
            return _unavailable(key)
        finally:
            connection.close()
        if values:
            return ShardFacts(key=key, state=SHARD_READABLE, bounds_established=True,
                              min_timestamp=min(values), max_timestamp=max(values),
                              tables=tables)
        return ShardFacts(key=key, state=SHARD_READABLE, bounds_established=False,
                          min_timestamp=None, max_timestamp=None, tables=tables)


def _is_valid_empty_message_part(connection: sqlite3.Connection) -> bool:
    names = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"TimeStamp", "wcdb_builtin_compression_record"} <= names:
        return False
    columns = {row[1] for row in connection.execute("PRAGMA table_info('TimeStamp')")}
    return "timestamp" in columns
