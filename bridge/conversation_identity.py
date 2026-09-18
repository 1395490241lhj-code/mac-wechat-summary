"""The one construction every source uses for a conversation's identity.

The Reader boundary's conversation ids are integers; a source's own key for a
conversation may be a string. How that string becomes the integer
``NormalizedConversation.id`` and ``NormalizedMessage.conversation_id`` carry is
a property of the Reader **layer**, not of whichever source happened to need it
first — two sources disagreeing about a conversation's identifier would be a
defect, so there is exactly one owner and every source imports it.

This module is technology-neutral: it names no reader, transport, schema,
table, column or path. It lives beside the boundary types rather than inside
them so that the boundary module keeps the import set its contract is sealed
at.
"""

from __future__ import annotations

import hashlib


def conversation_identifier(chat: str) -> int:
    """A stable positive integer for a source's string chat identifier.

    The digest is truncated to 48 bits: stable across runs and processes,
    positive, and comfortably inside the range that survives JSON without
    losing precision.
    """
    digest = hashlib.blake2b(chat.encode("utf-8"), digest_size=6).digest()
    return int.from_bytes(digest, "big")
