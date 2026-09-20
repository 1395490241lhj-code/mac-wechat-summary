"""The provider's own supported-generation envelope.

The provider reads a *surface* of the WeChat schema: a fixed set of columns on
each conversation table. A part whose tables carry that surface is the
generation this provider was built against. A part whose tables have renamed,
dropped or reshaped one of those columns is a different generation, and it is
refused **even though the old reader would still have parsed it** -- that is the
whole point of an envelope rather than a best-effort parse.

Additive columns are tolerated. A generation that adds a column the provider
never reads changes nothing it relies on, and refusing it would make every
future additive build a false alarm.

Nothing here knows where a database lives, how it was decrypted, or who is in
it. It is handed an open read-only connection and a table name the parser
already recognised, and it answers one question about that table's shape.
"""

from __future__ import annotations

import sqlite3

#: The conversation-table columns this provider reads. Every one of them must
#: be present; the table may carry others.
SUPPORTED_CONVERSATION_COLUMNS: frozenset[str] = frozenset({
    "local_id",
    "server_id",
    "local_type",
    "real_sender_id",
    "create_time",
    "message_content",
    "source",
    "packed_info_data",
})


class UnsupportedGeneration(Exception):
    """A readable part is not the generation this provider supports.

    Fixed, content-free copy: the message never names the table, the columns or
    the path, because it is returned to a caller and is safe to log.
    """

    def __init__(self) -> None:
        super().__init__("wechat database generation unsupported")


def require_supported_surface(
    connection: sqlite3.Connection, table: str
) -> None:
    """Refuse a conversation table whose read surface has changed."""
    try:
        present = {
            row[1]
            for row in connection.execute(f'PRAGMA table_info("{table}")')  # noqa: S608
        }
    except sqlite3.Error:
        raise UnsupportedGeneration() from None
    if not SUPPORTED_CONVERSATION_COLUMNS <= present:
        raise UnsupportedGeneration()
