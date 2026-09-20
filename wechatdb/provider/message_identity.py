"""Provider-local public message identity and ordering."""

from __future__ import annotations

import hashlib

from ..parser import MessageRecord

_MAX_SIGNED_64 = (1 << 63) - 1


def message_sequence(record: MessageRecord) -> int:
    """The provider's approved ``(timestamp, local_id)`` ordering as int64."""
    timestamp = record.timestamp
    local_id = record.local_id
    if (isinstance(timestamp, bool) or not isinstance(timestamp, int)
            or not 0 <= timestamp < 1 << 32
            or isinstance(local_id, bool) or not isinstance(local_id, int)
            or not 0 <= local_id < 1 << 31):
        raise ValueError("message sequence input is invalid")
    sequence = (timestamp << 31) | local_id
    if sequence > _MAX_SIGNED_64:
        raise ValueError("message sequence input is invalid")
    return sequence


def message_id(record: MessageRecord) -> int:
    """Source id when present; otherwise a stable negative structural id."""
    if record.server_id is not None:
        if (isinstance(record.server_id, bool) or not isinstance(record.server_id, int)
                or not 0 < record.server_id <= _MAX_SIGNED_64):
            raise ValueError("message id input is invalid")
        return record.server_id
    if not isinstance(record.session_id, str):
        raise ValueError("message id input is invalid")
    session = record.session_id.encode("utf-8")
    structural = (
        len(session).to_bytes(4, "big") + session
        + message_sequence(record).to_bytes(8, "big")
    )
    magnitude = int.from_bytes(hashlib.sha256(structural).digest()[:8], "big") & _MAX_SIGNED_64
    return -(magnitude or 1)
