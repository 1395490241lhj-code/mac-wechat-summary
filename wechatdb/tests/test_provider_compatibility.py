"""A part that parses is not a part this provider supports.

The compatibility envelope is deliberately narrow: it refuses a conversation
table whose read surface has changed, even when today's reader would still have
found rows in it. An additive column is not a change; a renamed or dropped one
is.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT, ROOT / "bridge"):
    sys.path.insert(0, str(path))

from wechatdb.provider import UnsupportedGeneration, require_supported_surface  # noqa: E402
from wechatdb.provider.compatibility import (  # noqa: E402
    SUPPORTED_CONVERSATION_COLUMNS,
)

TABLE = "Msg_" + "a" * 32


def _table(connection: sqlite3.Connection, columns) -> None:
    connection.execute(
        'CREATE TABLE "' + TABLE + '" (' + ", ".join(sorted(columns)) + ")")


def test_the_supported_surface_is_accepted_even_with_extra_columns():
    connection = sqlite3.connect(":memory:")
    _table(connection, set(SUPPORTED_CONVERSATION_COLUMNS) | {"origin_source"})

    require_supported_surface(connection, TABLE)


@pytest.mark.parametrize(
    "columns",
    [
        set(SUPPORTED_CONVERSATION_COLUMNS) - {"server_id"} | {"svr_id"},
        set(SUPPORTED_CONVERSATION_COLUMNS) - {"packed_info_data"},
        set(SUPPORTED_CONVERSATION_COLUMNS) - {"message_content", "source"},
        {"local_id", "create_time"},
    ],
)
def test_a_changed_but_still_parseable_generation_is_refused(columns):
    connection = sqlite3.connect(":memory:")
    _table(connection, columns)

    with pytest.raises(UnsupportedGeneration) as raised:
        require_supported_surface(connection, TABLE)
    assert TABLE not in str(raised.value)
    assert "server_id" not in str(raised.value)


def test_a_missing_table_is_refused():
    connection = sqlite3.connect(":memory:")

    with pytest.raises(UnsupportedGeneration):
        require_supported_surface(connection, TABLE)

