"""Schema parsing for plaintext WeChat 4.1+ message databases.

Nothing here decrypts, locates, or opens a WeChat database. See
:mod:`wechatdb.parser` for the boundary this package keeps.
"""

from .msg_types import classify, decode_text, maybe_decompress, unpack_local_type
from .parser import (
    MessageRecord,
    conversation_tables,
    load_name2id,
    normalise_timestamp,
    parse_conversation,
    parse_database,
)

__all__ = [
    "MessageRecord",
    "classify",
    "conversation_tables",
    "decode_text",
    "load_name2id",
    "maybe_decompress",
    "normalise_timestamp",
    "parse_conversation",
    "parse_database",
    "unpack_local_type",
]
