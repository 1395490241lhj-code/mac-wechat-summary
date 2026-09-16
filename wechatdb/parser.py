"""Reads a **plaintext** WeChat 4.1+ message database into :class:`MessageRecord`.

This is a schema layer and only a schema layer. It is handed an open
:class:`sqlite3.Connection` to a database that is already readable, and it turns
rows into records. What it deliberately does not contain:

* no key, passphrase, salt, cipher parameter, or SQLCipher pragma
* no WeChat filesystem path, container name, or bundle identifier
* no process-memory access, no decryption, no attach, no OCR
* no code that opens a file it was not handed a connection to

Opening a real WeChat container is therefore not something this module can do,
and making it possible is not a change to this module. Everything here is
exercised against synthetic fixtures built by ``wechatdb.tests.fixtures``.

## What changed in 4.1+

The 4.0-era reader in ``core/wechat_db.py`` reads the same
``Msg_<md5(username)>`` table layout but resolves a group sender by splitting a
``wxid:\\n`` prefix off the message text, treats ``local_type`` as a small
integer, and reads ``create_time`` as seconds. Each of those is now only
sometimes true:

* **Sender.** 4.1+ stores an integer ``real_sender_id`` that indexes the
  per-database ``Name2Id`` table. The text prefix still appears in some rows and
  is kept as a fallback, never as the primary answer.
* **``local_type``.** Application messages pack the ``<appmsg><type>`` value
  into the high 32 bits: ``(appmsg_type << 32) | 49``. Read as a small integer
  a quoted reply is a nonsensical 244813135921.
* **``create_time``.** Both seconds and milliseconds occur. Records normalise to
  **seconds**, which is what the rest of this project means by a timestamp.
* **Compression.** ``message_content`` and ``source`` may be zstd frames.

## What a record does not claim

``session_id`` is the conversation's own identifier when the caller supplies a
map from table digest to username, and ``msg_<digest>`` when it does not. The
placeholder is deliberately not a username: a digest cannot be reversed, and
inventing a name for it would make an unresolved conversation look resolved.

``sender_name`` falls back to ``sender_id``. A display name is a fact from a
contact table this module is not given, so it is injected or it is absent.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from .msg_types import (
    APPMSG_MARKER,
    KIND_QUOTE,
    KIND_SYSTEM,
    KIND_TEXT,
    KIND_UNKNOWN,
    classify,
    decode_text,
    maybe_decompress,
    strip_markup,
    unpack_local_type,
    xml_attr,
    xml_tag,
)

#: One conversation per table, named for the md5 digest of its username.
CONVERSATION_TABLE = re.compile(r"^Msg_([0-9a-fA-F]{32})$")

#: A ``create_time`` at or above this is milliseconds. Ten to the eleventh
#: seconds is the year 5138, so no real second-valued timestamp reaches it.
MILLISECOND_THRESHOLD: int = 10**11

#: The ``wxid:\n`` prefix 4.0 used to carry a group sender inside the payload.
_SENDER_PREFIX = re.compile(r"^([A-Za-z0-9_\-@.]{2,64}):\r?\n")

#: A media digest as it appears in ``packed_info_data`` or an ``md5`` attribute.
_MEDIA_DIGEST = re.compile(rb"[0-9a-fA-F]{32}")

#: Columns this layer reads. A database missing one is not fatal: the value is
#: reported absent, because a 4.1 build that drops a column is a coverage gap
#: and not a reason to refuse every row in the table.
_WANTED_COLUMNS = (
    "local_id",
    "server_id",
    "local_type",
    "real_sender_id",
    "create_time",
    "message_content",
    "source",
    "packed_info_data",
)


@dataclass(frozen=True)
class MessageRecord:
    """One message, in the single shape every fixture and schema resolves to.

    ``timestamp`` is Unix **seconds**, whatever the column held.

    ``message_type`` is one of the kinds in :mod:`wechatdb.msg_types`.

    ``content`` is text the message actually carries: the body of a text
    message, the title of a link, file, or quote, the rendering of a system
    event. A message whose payload is purely binary — an image, a voice clip —
    has ``content`` of ``None`` and is identified by ``message_type`` and
    ``media_id``. A placeholder such as ``[图片]`` is display text, and
    inventing one here would put a presentation decision in the parser.

    ``media_id`` is the media digest, when the row carries one.

    ``reply_to`` is the server id of the quoted message, as a string, for a
    quote and nothing else.
    """

    session_id: str
    local_id: int
    timestamp: int
    sender_id: str | None
    sender_name: str | None
    message_type: str
    content: str | None
    media_id: str | None
    reply_to: str | None


def normalise_timestamp(value: object) -> int:
    """Unix seconds, from a column holding either seconds or milliseconds."""
    try:
        raw = int(value)
    except (TypeError, ValueError):
        return 0
    if raw >= MILLISECOND_THRESHOLD:
        return raw // 1000
    return raw


def conversation_tables(connection: sqlite3.Connection) -> list[str]:
    """Every ``Msg_<32 hex>`` table in the database, in a stable order."""
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return [row[0] for row in rows if CONVERSATION_TABLE.match(row[0])]


def load_name2id(connection: sqlite3.Connection) -> dict[int, str]:
    """``Name2Id`` as ``rowid -> username``, or empty when the table is absent.

    ``real_sender_id`` is that rowid. Builds differ on the column name, so both
    spellings seen in the wild are accepted; an absent table is a database
    without group sender resolution, not an error.
    """
    present = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='Name2Id'"
    ).fetchone()
    if present is None:
        return {}
    columns = {row[1] for row in connection.execute("PRAGMA table_info('Name2Id')")}
    for candidate in ("user_name", "username"):
        if candidate in columns:
            column = candidate
            break
    else:
        return {}
    mapping: dict[int, str] = {}
    for rowid, name in connection.execute(
        f"SELECT rowid, {column} FROM Name2Id"  # noqa: S608 - column is from a fixed set
    ):
        if isinstance(name, str) and name:
            mapping[int(rowid)] = name
    return mapping


def parse_database(
    connection: sqlite3.Connection,
    *,
    session_names: Mapping[str, str] | None = None,
    display_names: Mapping[str, str] | None = None,
) -> Iterator[MessageRecord]:
    """Every message in every conversation table, oldest first per conversation.

    ``session_names`` maps a table's md5 digest to the conversation's username.
    ``display_names`` maps a ``sender_id`` to a name to show. Both are injected
    because both live in databases this module is not given.
    """
    name2id = load_name2id(connection)
    for table in conversation_tables(connection):
        yield from parse_conversation(
            connection,
            table,
            name2id=name2id,
            session_names=session_names,
            display_names=display_names,
        )


def parse_conversation(
    connection: sqlite3.Connection,
    table: str,
    *,
    name2id: Mapping[int, str] | None = None,
    session_names: Mapping[str, str] | None = None,
    display_names: Mapping[str, str] | None = None,
) -> Iterator[MessageRecord]:
    """One conversation table, oldest first."""
    match = CONVERSATION_TABLE.match(table)
    if match is None:
        raise ValueError("Not a conversation table name.")
    digest = match.group(1).lower()
    session_id = (session_names or {}).get(digest) or f"msg_{digest}"

    available = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
    columns = [name for name in _WANTED_COLUMNS if name in available]
    if "local_id" not in columns or "create_time" not in columns:
        return
    selection = ", ".join(f'"{name}"' for name in columns)
    rows = connection.execute(
        f'SELECT {selection} FROM "{table}"'  # noqa: S608
    ).fetchall()

    records = [
        _record(
            dict(zip(columns, row)),
            session_id=session_id,
            name2id=name2id or {},
            display_names=display_names or {},
        )
        for row in rows
    ]
    # Ordering happens after normalisation, not in SQL. A table holding both
    # second- and millisecond-valued `create_time` would sort every millisecond
    # row to the end if the raw column decided the order, which is a plausible
    # chronology and a wrong one.
    records.sort(key=lambda record: (record.timestamp, record.local_id))
    yield from records


def _record(
    values: Mapping[str, object],
    *,
    session_id: str,
    name2id: Mapping[int, str],
    display_names: Mapping[str, str],
) -> MessageRecord:
    base_type, appmsg_type = unpack_local_type(values.get("local_type"))
    payload = decode_text(values.get("message_content"))
    prefix_sender, payload = _split_sender_prefix(payload)

    if base_type == APPMSG_MARKER and appmsg_type is None:
        # A bare 49: the type is only in the XML. Reading it there is the
        # difference between a classified message and an unknown one.
        declared = xml_tag(payload, "type")
        if declared and declared.isdigit():
            appmsg_type = int(declared)

    kind = classify(base_type, appmsg_type)
    sender_id = _sender(values, name2id, prefix_sender)

    return MessageRecord(
        session_id=session_id,
        local_id=int(values.get("local_id") or 0),
        timestamp=normalise_timestamp(values.get("create_time")),
        sender_id=sender_id,
        sender_name=display_names.get(sender_id or "", sender_id),
        message_type=kind,
        content=_content(kind, payload),
        media_id=_media_id(values, payload),
        reply_to=xml_tag(payload, "svrid") if kind == KIND_QUOTE else None,
    )


def _split_sender_prefix(payload: str) -> tuple[str | None, str]:
    """Removes the legacy ``wxid:\\n`` group-sender prefix, if present."""
    match = _SENDER_PREFIX.match(payload)
    if match is None:
        return None, payload
    return match.group(1), payload[match.end():]


def _sender(
    values: Mapping[str, object],
    name2id: Mapping[int, str],
    prefix_sender: str | None,
) -> str | None:
    """The sender's identifier, by descending reliability.

    ``real_sender_id`` through ``Name2Id`` is the 4.1+ answer and is preferred.
    The payload prefix is the 4.0 answer and still appears. ``source`` names the
    real chat user for a message relayed through another conversation; it is
    last because it answers a slightly different question.
    """
    raw = values.get("real_sender_id")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        resolved = name2id.get(raw)
        if resolved:
            return resolved
    if prefix_sender:
        return prefix_sender
    source = decode_text(values.get("source"))
    if source:
        return xml_tag(source, "realChatUserName") or xml_tag(source, "fromusr")
    return None


def _content(kind: str, payload: str) -> str | None:
    """The text the message carries, or ``None`` when it carries none."""
    if not payload:
        return None
    if kind == KIND_TEXT:
        return payload.strip() or None
    if kind == KIND_SYSTEM:
        return (
            xml_tag(payload, "replacemsg")
            or xml_tag(payload, "content")
            or strip_markup(payload)
            or None
        )
    if kind == KIND_UNKNOWN and not payload.lstrip().startswith("<"):
        # An unrecognised type whose payload is plain text is still readable.
        return payload.strip() or None
    return xml_tag(payload, "title") or xml_attr(payload, "filename")


def _media_id(values: Mapping[str, object], payload: str) -> str | None:
    """The media digest, from the packed column or from the payload XML."""
    packed = maybe_decompress(values.get("packed_info_data"))
    match = _MEDIA_DIGEST.search(packed)
    if match is not None:
        return match.group(0).decode("ascii").lower()
    attribute = xml_attr(payload, "md5")
    if attribute and _MEDIA_DIGEST.fullmatch(attribute.encode("ascii", "ignore")):
        return attribute.lower()
    return None
