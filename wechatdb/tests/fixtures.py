"""Synthetic WeChat 4.1+ message databases, built in code.

Every fixture in this module is fabricated. There is no capture of a real
database here, no sample lifted from a device, no key, and no path into a
WeChat installation: the schema is recreated from its published shape and
filled with invented names and invented text.

That is not only a privacy property. A fixture built in code can be read: the
column layout, the compression, and the exact integer a quoted reply carries
are all visible in this file rather than buried in a binary nobody can diff.
"""

from __future__ import annotations

import hashlib
import sqlite3

try:
    import zstandard as _zstd

    _COMPRESSOR = _zstd.ZstdCompressor()
except ImportError:  # pragma: no cover
    _COMPRESSOR = None

#: The 4.1+ per-conversation message table, as this layer reads it.
MESSAGE_TABLE_SQL = """
CREATE TABLE "{table}" (
    local_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id           INTEGER,
    local_type          INTEGER,
    sort_seq            INTEGER,
    real_sender_id      INTEGER,
    create_time         INTEGER,
    status              INTEGER,
    upload_status       INTEGER,
    download_status     INTEGER,
    server_seq          INTEGER,
    origin_source       INTEGER,
    source              BLOB,
    message_content     BLOB,
    compress_content    BLOB,
    packed_info_data    BLOB,
    WCDB_CT_message_content INTEGER,
    WCDB_CT_source      INTEGER
)
"""

NAME2ID_SQL = """
CREATE TABLE Name2Id (
    user_name TEXT
)
"""


def compress(text: str) -> bytes:
    """A zstd frame, as WeChat 4.1+ stores a compressed payload column."""
    if _COMPRESSOR is None:  # pragma: no cover
        raise RuntimeError("zstandard is required to build a compressed fixture.")
    return _COMPRESSOR.compress(text.encode("utf-8"))


def table_name(username: str) -> str:
    """``Msg_<md5(username)>``, the 4.x per-conversation table name."""
    return "Msg_" + hashlib.md5(username.encode("utf-8")).hexdigest()


def digest(username: str) -> str:
    return hashlib.md5(username.encode("utf-8")).hexdigest()


def new_database(path) -> sqlite3.Connection:
    """An empty message database with a ``Name2Id`` table and nothing else."""
    connection = sqlite3.connect(str(path))
    connection.execute(NAME2ID_SQL)
    return connection


def add_names(connection: sqlite3.Connection, usernames) -> dict[str, int]:
    """Populates ``Name2Id`` and returns ``username -> real_sender_id``."""
    ids: dict[str, int] = {}
    for username in usernames:
        cursor = connection.execute(
            "INSERT INTO Name2Id (user_name) VALUES (?)", (username,)
        )
        ids[username] = int(cursor.lastrowid)
    return ids


def add_conversation(connection: sqlite3.Connection, username: str) -> str:
    table = table_name(username)
    connection.execute(MESSAGE_TABLE_SQL.format(table=table))
    return table


def insert(connection: sqlite3.Connection, table: str, **fields) -> int:
    """Inserts one message row; unset columns stay NULL."""
    keys = list(fields)
    placeholders = ", ".join("?" for _ in keys)
    columns = ", ".join(f'"{key}"' for key in keys)
    cursor = connection.execute(
        f'INSERT INTO "{table}" ({columns}) VALUES ({placeholders})',  # noqa: S608
        [fields[key] for key in keys],
    )
    return int(cursor.lastrowid)


# -- payloads ------------------------------------------------------------------
#
# Invented XML in the shape WeChat writes. Names and text are fictional.

def quote_xml(*, title: str, quoted_server_id: int, quoted_text: str) -> str:
    return (
        '<?xml version="1.0"?><msg><appmsg appid="" sdkver="0">'
        f"<title>{title}</title><type>57</type>"
        f"<refermsg><type>1</type><svrid>{quoted_server_id}</svrid>"
        "<fromusr>7700000001@chatroom</fromusr><chatusr>wxid_fixture_b</chatusr>"
        f"<displayname>Fixture B</displayname><content>{quoted_text}</content>"
        "</refermsg></appmsg></msg>"
    )


def image_xml(*, media_digest: str) -> str:
    return (
        '<?xml version="1.0"?><msg><img aeskey="0f0f0f0f" '
        f'md5="{media_digest}" length="20480" '
        'cdnthumburl="fixture" hdlength="40960" /></msg>'
    )


def link_xml(*, title: str) -> str:
    return (
        '<?xml version="1.0"?><msg><appmsg appid="" sdkver="0">'
        f"<title>{title}</title><type>5</type>"
        "<url>https://example.invalid/fixture</url></appmsg></msg>"
    )


def file_xml(*, title: str) -> str:
    return (
        '<?xml version="1.0"?><msg><appmsg appid="" sdkver="0">'
        f"<title>{title}</title><type>6</type>"
        "<appattach><totallen>1024</totallen>"
        "<fileext>pdf</fileext></appattach></appmsg></msg>"
    )


def voice_xml() -> str:
    return (
        '<?xml version="1.0"?><msg><voicemsg endflag="1" length="3200" '
        'voicelength="2400" clientmsgid="fixture" /></msg>'
    )


def video_xml(*, media_digest: str) -> str:
    return (
        '<?xml version="1.0"?><msg><videomsg aeskey="0e0e0e0e" '
        f'md5="{media_digest}" length="204800" playlength="7" /></msg>'
    )


def revoke_xml(*, who: str) -> str:
    return (
        '<sysmsg type="revokemsg"><revokemsg><session>7700000001@chatroom</session>'
        "<msgid>99</msgid>"
        f"<replacemsg><![CDATA[{who} recalled a message]]></replacemsg>"
        "</revokemsg></sysmsg>"
    )


def source_xml(*, real_chat_user: str) -> str:
    return (
        "<msgsource><sequence_id>1</sequence_id>"
        f"<realChatUserName>{real_chat_user}</realChatUserName></msgsource>"
    )


def packed_info(media_digest: str) -> bytes:
    """``packed_info_data`` as a length-prefixed blob carrying a media digest."""
    body = media_digest.encode("ascii")
    return b"\x0a\x20" + body + b"\x10\x80\x02"
