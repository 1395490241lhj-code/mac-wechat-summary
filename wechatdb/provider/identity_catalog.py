"""Provider-local identity evidence from explicitly supplied database parts.

The catalog knows three source roles because its caller supplies those roles;
it infers nothing from a name or handle.  It opens each supplied entry through
the injected opener, snapshots exact source strings into canonical tuples, and
closes the connection.  The existing resolver remains the only owner of name
precedence and ambiguity refusal.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Sequence

import wechatdb

from .discovery import ShardEntry, ShardOpener
from .identity import (
    NAME_CONTACT_NICKNAME,
    NAME_CONTACT_REMARK,
    IdentityResolver,
    NameCandidate,
)


_SCHEMA_ERROR = "identity catalog schema unsupported"


def _session_digest(username: str) -> str:
    return hashlib.md5(username.encode("utf-8")).hexdigest()


def _require_columns(
    connection: sqlite3.Connection,
    table: str,
    required: frozenset[str],
) -> None:
    present = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if present is None:
        raise ValueError(_SCHEMA_ERROR)
    columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
    if not required <= columns:
        raise ValueError(_SCHEMA_ERROR)


def _valid_string(value: object) -> bool:
    return isinstance(value, str) and bool(value)


@dataclass(frozen=True, slots=True)
class IdentityCatalog:
    session_names: tuple[tuple[str, str], ...]
    candidates: tuple[NameCandidate, ...]

    @classmethod
    def build(
        cls,
        *,
        opener: ShardOpener,
        session: ShardEntry | None = None,
        contact: ShardEntry | None = None,
        message_parts: Sequence[ShardEntry] = (),
    ) -> IdentityCatalog:
        usernames: set[str] = set()
        candidates: set[NameCandidate] = set()

        def read(entry: ShardEntry, role: str) -> None:
            connection = opener.open(entry)
            try:
                if role == "session":
                    _require_columns(connection, "SessionTable", frozenset({"username"}))
                    rows = connection.execute("SELECT username FROM SessionTable")
                    usernames.update(value for (value,) in rows if _valid_string(value))
                elif role == "contact":
                    _require_columns(
                        connection, "contact",
                        frozenset({"username", "remark", "nick_name"}),
                    )
                    for username, remark, nickname in connection.execute(
                        "SELECT username, remark, nick_name FROM contact"
                    ):
                        if not _valid_string(username):
                            continue
                        usernames.add(username)
                        if _valid_string(remark):
                            candidates.add(NameCandidate(
                                username, NAME_CONTACT_REMARK, remark))
                        if _valid_string(nickname):
                            candidates.add(NameCandidate(
                                username, NAME_CONTACT_NICKNAME, nickname))
                else:
                    usernames.update(wechatdb.load_name2id(connection).values())
            except sqlite3.Error:
                raise ValueError(_SCHEMA_ERROR) from None
            finally:
                connection.close()

        if session is not None:
            read(session, "session")
        if contact is not None:
            read(contact, "contact")
        for entry in tuple(message_parts):
            read(entry, "message")

        by_digest: dict[str, str] = {}
        for username in sorted(usernames):
            digest = _session_digest(username)
            previous = by_digest.get(digest)
            if previous is not None and previous != username:
                raise ValueError("session identity collision")
            by_digest[digest] = username

        ordered_candidates = tuple(sorted(
            candidates,
            key=lambda candidate: (
                candidate.identifier,
                candidate.kind,
                candidate.name,
                candidate.room or "",
            ),
        ))
        return cls(tuple(sorted(by_digest.items())), ordered_candidates)

    def resolver(self) -> IdentityResolver:
        return IdentityResolver(
            [*self.candidates], session_names=dict(self.session_names))
