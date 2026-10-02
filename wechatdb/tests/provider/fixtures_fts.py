"""Synthetic native-search sources, built in code, for the spike tests.

Everything here is fabricated, and the schema below is *this file's own* answer
to one behavioural question: what is the least a native search index has to
expose for a hit to be mapped back to an authoritative ordinary message?
Nothing is copied from any external reader project, and no real WeChat
database was opened to find out what its tables or columns are called.

The answer this file encodes is that a hit is usable only if it carries the
*structural coordinates* of a row in an ordinary message shard -- which part,
which conversation table, which local id -- rather than a pointer that only
some other table in the same index happens to agree with. The conversation
coordinate is the parser's own table name, because that is the only
conversation spelling the ordinary reader parses, and a digest that some other
table shares is not the same reference.

Four shapes, so the verdicts stay distinguishable:

* **compatible** -- a full-text content table carrying the part binding and
  both row coordinates, plus columns that carry nothing this reader may use;
* **unsupported** -- the right table name, with one linkage column removed, so
  no hit can name one row in one ordinary part;
* **corrupt** -- structurally recognised, but the query fails at runtime
  rather than at schema time;
* **malformed** -- bytes that are not a database at all.

Every string in here is invented. No real message, sender, conversation or
identifier appears, and no real content is used as durable evidence.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Sequence

#: The one table this spike's schema recognises. The name is the spike's own,
#: written from the behavioural question above.
SEARCH_CONTENT_TABLE = "search_index_content"

#: The columns that carry the exact reference. All three, or the index is
#: unsupported: a hit without them cannot name one row in one ordinary part.
LINKAGE_COLUMNS: tuple[str, ...] = ("shard_name", "conversation_table", "local_id")

#: Columns that carry *nothing* this reader may use. They exist so a test can
#: show that a hit which is otherwise perfect is still never returned on the
#: index's own word about who said it, when, or what.
NON_AUTHORITATIVE_COLUMNS: tuple[str, ...] = ("sender_id", "create_time", "body")

ALL_COLUMNS: tuple[str, ...] = LINKAGE_COLUMNS + NON_AUTHORITATIVE_COLUMNS


def _quoted(columns: Sequence[str]) -> str:
    return ", ".join(f'"{name}"' for name in columns)


def _rows(hits: Sequence[dict], columns: Sequence[str]) -> tuple[tuple, ...]:
    """Each column keeps the type the test gave it; absent stays empty."""
    return tuple(tuple(item.get(column, "") for column in columns)
                 for item in hits)


def hit(*, shard_name: str, conversation_table: str, local_id: int,
        sender_id: str = "", create_time: int = 0,
        body: str = "fixture body") -> dict:
    """One invented hit, addressed the way an ordinary shard addresses a row.

    The last three are the index's own untrustworthy account of the message.
    They exist so a test can prove they are never the reason a message comes
    back, or what it says.
    """
    return {"shard_name": shard_name, "conversation_table": conversation_table,
            "local_id": local_id, "sender_id": sender_id,
            "create_time": create_time, "body": body}


# -- builders ------------------------------------------------------------------

def compatible_source(tmp_path: Path, hits: Sequence[dict] = (),
                      name: str = "message_fts.db") -> Path:
    """A native search index whose hits address ordinary rows exactly."""
    path = tmp_path / name
    connection = sqlite3.connect(str(path))
    try:
        connection.execute(
            f'CREATE VIRTUAL TABLE "{SEARCH_CONTENT_TABLE}"'
            f" USING fts5({_quoted(ALL_COLUMNS)})")
        connection.executemany(
            f'INSERT INTO "{SEARCH_CONTENT_TABLE}" ({_quoted(ALL_COLUMNS)})'
            f" VALUES ({', '.join('?' for _ in ALL_COLUMNS)})",
            _rows(hits, ALL_COLUMNS))
        connection.commit()
    finally:
        connection.close()
    return path


def unsupported_source(tmp_path: Path, *, missing: str = "shard_name",
                       hits: Sequence[dict] = (),
                       name: str = "message_fts.db") -> Path:
    """The right table name, without the linkage a hit needs to be resolved.

    Dropping the part binding leaves nothing to say which ordinary source a hit
    belongs to; dropping either row coordinate leaves nothing to name a row
    inside it. Every one of those is unsupported, never "probably close
    enough".
    """
    columns = tuple(column for column in ALL_COLUMNS if column != missing)
    path = tmp_path / name
    connection = sqlite3.connect(str(path))
    try:
        connection.execute(
            f'CREATE TABLE "{SEARCH_CONTENT_TABLE}" ({_quoted(columns)})')
        connection.executemany(
            f'INSERT INTO "{SEARCH_CONTENT_TABLE}" ({_quoted(columns)})'
            f" VALUES ({', '.join('?' for _ in columns)})",
            _rows(hits, columns))
        connection.commit()
    finally:
        connection.close()
    return path


def corrupt_source(tmp_path: Path, hits: Sequence[dict] = (),
                   name: str = "message_fts.db") -> Path:
    """Recognised and structurally valid, with an index that cannot be read.

    The table is there and carries the right columns, so the schema check
    passes; its stored blocks are then removed, so the full-text query fails at
    runtime. This is the case whose SQLite text must never cross a public
    boundary.
    """
    path = compatible_source(tmp_path, hits, name)
    connection = sqlite3.connect(str(path))
    try:
        connection.execute(f'DROP TABLE "{SEARCH_CONTENT_TABLE}_data"')
        connection.commit()
    finally:
        connection.close()
    return path


def malformed_source(tmp_path: Path, name: str = "message_fts.db") -> Path:
    """Bytes that are not a database. A runtime fact, not a schema opinion."""
    path = tmp_path / name
    path.write_bytes(b"fixture bytes that are not a sqlite database")
    return path
