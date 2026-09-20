"""Synthetic multi-part message sources, built in code, for the provider tests.

Everything in this module is fabricated. There is no capture of a real
database, no sample from a device, no key, and no path into any installation:
each part is a temporary SQLite file under pytest's own ``tmp_path``, shaped
from the leaf parser's documented column contract and filled with invented
names and invented text. Nothing here is copied from any external reader
project.

The abstraction is deliberately small and test-only. A :class:`SyntheticPart`
is a name, a place, and a way to open it. It is *not* a shard entry, a
locator, a router input or any other production type: later tasks translate
these parts into their own types, and this module must never become the
architecture it is meant to exercise.

Every shape the synthetic matrix needs is representable here:

* a **readable** part, parseable by :func:`wechatdb.parse_conversation`;
* an **unknown-name** part, whose logical name matches no message-part
  convention -- kept in the set, never dropped; classification is later work;
* a part that **will not open**, whose opener raises a fixed synthetic failure
  rather than pointing at any real inaccessible location;
* an **unrecognised-schema** part, a valid database with no message table;
* a part with **no establishable bounds**, message-shaped but empty, for which
  no honest min/max timestamp exists and none is invented;
* **one conversation split across several parts**, the same table digest in
  independent databases with no part holding the whole sequence.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

# -- the parser's dialect, written from its contract ---------------------------
#
# `wechatdb.parser` requires `local_id` and `create_time` and reads six more
# columns when present. This table carries exactly those, and nothing WeChat-
# specific beyond what the parser itself documents.

MESSAGE_TABLE_SQL = """
CREATE TABLE "{table}" (
    local_id         INTEGER PRIMARY KEY,
    server_id        INTEGER,
    local_type       INTEGER,
    real_sender_id   INTEGER,
    create_time      INTEGER,
    message_content  BLOB,
    source           BLOB,
    packed_info_data BLOB
)
"""

NAME2ID_SQL = "CREATE TABLE Name2Id (user_name TEXT)"

#: Plain text, in the parser's `local_type` vocabulary.
TEXT_TYPE = 1

#: Obviously synthetic identities. Every `wxid_` here carries the fixture marker.
ALPHA = "wxid_fixture_alpha"
BETA = "wxid_fixture_beta"
ROOM = "fixture_room_0001@chatroom"


class SyntheticOpenFailure(RuntimeError):
    """The fixed failure a part that will not open raises. Carries no path."""


@dataclass(frozen=True)
class SyntheticMessage:
    """One invented message: who said what, when, under which local id."""

    local_id: int
    create_time: int
    sender: str
    text: str
    server_id: int | None = None


@dataclass(frozen=True)
class SyntheticPart:
    """A name, a place, and a way to open it. Test-only.

    ``path`` is ``None`` for a part that has no file because it will not open.
    ``open`` returns a connection or raises; it is the only way a test or a
    later component should reach the part's contents, so that "will not open"
    is a behaviour of the fixture and not a property of some real location.
    """

    name: str
    path: Path | None
    open: Callable[[], sqlite3.Connection]


def conversation_table(conversation: str) -> str:
    """``Msg_<32 hex>`` for a conversation, per the parser's documented shape."""
    return "Msg_" + hashlib.md5(conversation.encode("utf-8")).hexdigest()


def _opener(path: Path) -> Callable[[], sqlite3.Connection]:
    def open_part() -> sqlite3.Connection:
        return sqlite3.connect(str(path))

    return open_part


def _write_messages(
    connection: sqlite3.Connection,
    table: str,
    messages: Sequence[SyntheticMessage],
) -> None:
    connection.execute(NAME2ID_SQL)
    ids: dict[str, int] = {}
    for message in messages:
        if message.sender not in ids:
            cursor = connection.execute(
                "INSERT INTO Name2Id (user_name) VALUES (?)", (message.sender,))
            ids[message.sender] = int(cursor.lastrowid)
    connection.execute(MESSAGE_TABLE_SQL.format(table=table))
    for message in messages:
        connection.execute(
            f'INSERT INTO "{table}" (local_id, server_id, local_type, real_sender_id,'
            " create_time, message_content) VALUES (?, ?, ?, ?, ?, ?)",
            (message.local_id, message.server_id, TEXT_TYPE, ids[message.sender],
             message.create_time, message.text.encode("utf-8")),
        )
    connection.commit()


# -- builders ------------------------------------------------------------------

def readable_part(
    tmp_path: Path, name: str, conversation: str,
    messages: Sequence[SyntheticMessage],
) -> SyntheticPart:
    """A valid database holding one conversation table with these messages."""
    path = tmp_path / name
    connection = sqlite3.connect(str(path))
    try:
        _write_messages(connection, conversation_table(conversation), messages)
    finally:
        connection.close()
    return SyntheticPart(name=name, path=path, open=_opener(path))


def unknown_name_part(tmp_path: Path, conversation: str,
                      messages: Sequence[SyntheticMessage]) -> SyntheticPart:
    """Readable inside, but named like nothing a message part is called.

    It stays in every fixture set it is put in. Whether and how it is
    classified is not this module's decision.
    """
    return readable_part(tmp_path, "fixture_unclassified.blob", conversation, messages)


def unopenable_part(name: str = "fixture_will_not_open.db") -> SyntheticPart:
    """A part whose opener raises. No file, no path, no real location."""

    def refuse() -> sqlite3.Connection:
        raise SyntheticOpenFailure("synthetic part refuses to open")

    return SyntheticPart(name=name, path=None, open=refuse)


def unrecognised_schema_part(tmp_path: Path,
                             name: str = "fixture_not_messages.db") -> SyntheticPart:
    """Opens fine, and holds nothing a message layer could recognise."""
    path = tmp_path / name
    connection = sqlite3.connect(str(path))
    try:
        connection.execute("CREATE TABLE fixture_ledger (id INTEGER, note TEXT)")
        connection.execute("INSERT INTO fixture_ledger VALUES (1, 'fixture note')")
        connection.commit()
    finally:
        connection.close()
    return SyntheticPart(name=name, path=path, open=_opener(path))


def unbounded_part(tmp_path: Path, conversation: str,
                   name: str = "fixture_no_bounds.db") -> SyntheticPart:
    """Message-shaped and empty: no honest min or max timestamp exists."""
    return readable_part(tmp_path, name, conversation, messages=())


def split_conversation(
    tmp_path: Path, conversation: str, messages: Sequence[SyntheticMessage],
    *, parts: int = 2, stem: str = "fixture_part",
) -> list[SyntheticPart]:
    """One conversation across several independent databases.

    Messages are dealt round-robin, so every part holds a strict subset and
    the full sequence exists only in their union. Asking for fewer than two
    parts, or for more parts than messages, is a fixture mistake and refused.
    """
    if parts < 2:
        raise ValueError("a split conversation needs at least two parts")
    if len(messages) < parts:
        raise ValueError("every part must receive at least one message")
    buckets: list[list[SyntheticMessage]] = [[] for _ in range(parts)]
    for index, message in enumerate(messages):
        buckets[index % parts].append(message)
    return [
        readable_part(tmp_path, f"{stem}_{index}.db", conversation, bucket)
        for index, bucket in enumerate(buckets)
    ]


def parse_part(part: SyntheticPart, conversation: str):
    """What the leaf parser makes of one part, oldest first. Test-only."""
    import wechatdb

    connection = part.open()
    try:
        name2id = wechatdb.load_name2id(connection)
        return list(wechatdb.parse_conversation(
            connection, conversation_table(conversation), name2id=name2id))
    finally:
        connection.close()
