"""A :class:`MessageSource` backed by an external, independently installed reader.

This adapter speaks to a separate reader process over its documented JSON
command-line contract. It exists so a local WeChat database can be read without
this repository containing anything that reads one.

**What this module deliberately is not.** It vendors no reader source, links no
reader library, imports nothing from a reader, and copies no reader
implementation. It contains no key handling, no cipher parameter, no WeChat
schema, no process-memory access, and no code that could modify an installed
application. It cannot install, download, or locate a reader: the executable
path is injected by the caller and nothing else is ever executed. If that path
is absent, this source reports itself unavailable and the visual path continues
to work untouched.

**Licensing.** The reader is an optional external program the operator installs
themselves, under its own licence. Running it in a separate process with a
documented data contract *reduces* coupling risk compared with vendoring or
linking it. That is an engineering statement, not a legal conclusion, and it
does not by itself resolve any licensing obligation.

**Failure is always closed.** A missing executable, a timeout, a non-JSON reply,
a reply that is JSON but not the expected envelope, a reply the reader itself
marked unsuccessful, or a request this adapter cannot express faithfully all
raise :class:`MessageSourceError`. Nothing is partially ingested, no answer is
approximated, and this adapter never hands the question to another source.

**Nothing from the reader is echoed.** Reader stdout and stderr may contain
chat content or filesystem paths, so neither is ever placed in an exception, a
log line, or a return value. Only the reader's own short error code survives,
and only after it matches a strict token pattern.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any

from message_source import (
    SOURCE_DATABASE,
    MessageSourceError,
    NormalizedConversation,
    NormalizedMessage,
    SourceStatus,
)

#: Reader error codes are short lowercase tokens. Anything else is treated as
#: untrusted text and discarded rather than returned to a client.
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

#: Reader message kinds mapped onto the kinds the store already publishes.
#: Anything unlisted becomes ``unknown`` instead of inventing a new kind.
_KIND_MAP: dict[str, str] = {
    "text": "text",
    "image": "image",
    "video": "file",
    "file": "file",
    "voice": "voice",
    "audio": "voice",
    "link": "link",
    "sticker": "image",
    "emoji": "image",
    "system": "system",
    "location": "unknown",
    "transfer": "unknown",
    "red_packet": "unknown",
}

#: How many conversations a recent-messages sweep will visit. The reader has no
#: single cross-conversation query this adapter can express faithfully, so the
#: sweep is bounded and the bound is reported rather than presented as a total.
RECENT_CONVERSATION_SCAN_LIMIT: int = 50


def conversation_identifier(chat: str) -> int:
    """A stable positive integer for a reader's string chat identifier.

    The bridge's conversation ids are integers and the reader's are strings, so
    one has to be derived from the other. The digest is truncated to 48 bits:
    stable across runs and processes, positive, and comfortably inside the
    range that survives JSON without losing precision.
    """
    digest = hashlib.blake2b(chat.encode("utf-8"), digest_size=6).digest()
    return int.from_bytes(digest, "big")


@dataclass(frozen=True)
class RionReaderConfig:
    """Everything about the external reader, supplied from outside.

    No field has a discovered default. ``executable`` is a path the operator
    chose; this adapter never searches for one.
    """

    executable: str
    config_path: str | None = None
    timeout_seconds: float = 30.0
    #: Extra global arguments placed before the subcommand, if the operator
    #: needs them. Never derived from a client request.
    extra_args: tuple[str, ...] = ()
    #: Optional replacement environment for the reader process.
    env: dict[str, str] | None = field(default=None)


class RionReaderAdapter:
    """Reads through an external reader process. Read-only by construction.

    Every subcommand this adapter issues is a read. There is no argument here
    that a client controls except a numeric limit and a conversation id, both
    of which are validated before they reach an argument vector.
    """

    name = SOURCE_DATABASE

    def __init__(self, config: RionReaderConfig) -> None:
        self._config = config
        # Maps a derived integer id back to the reader's own chat identifier.
        # Populated by listing; a lookup that misses re-lists once rather than
        # guessing an identifier it has never seen.
        self._chat_by_id: dict[int, str] = {}

    # -- process boundary ----------------------------------------------------

    def _argv(self, arguments: list[str]) -> list[str]:
        argv = [self._config.executable]
        if self._config.config_path:
            argv += ["--config", self._config.config_path]
        argv += list(self._config.extra_args)
        return argv + arguments

    def _invoke(self, arguments: list[str]) -> dict[str, Any]:
        """Runs one reader subcommand and returns its ``data`` object.

        No shell is involved: the argument vector is passed directly.
        """
        try:
            completed = subprocess.run(
                self._argv(arguments),
                capture_output=True,
                text=True,
                timeout=self._config.timeout_seconds,
                check=False,
                env=self._config.env,
            )
        except FileNotFoundError as error:
            raise MessageSourceError(
                "reader_unavailable",
                "The external reader executable was not found.",
            ) from error
        except PermissionError as error:
            raise MessageSourceError(
                "reader_unavailable",
                "The external reader executable is not executable.",
            ) from error
        except OSError as error:
            raise MessageSourceError(
                "reader_unavailable", "The external reader could not be started."
            ) from error
        except subprocess.TimeoutExpired as error:
            raise MessageSourceError(
                "reader_timeout", "The external reader did not answer in time."
            ) from error
        return self._parse(completed.stdout, completed.returncode)

    def _parse(self, stdout: str, returncode: int) -> dict[str, Any]:
        """Turns one reply into a ``data`` object, or fails closed.

        A reply is accepted only if it is JSON, is an object, carries an
        explicit successful ``ok``, and carries an object ``data``. Every other
        shape is a malformed response, including a partially valid one: there
        is no path here that ingests part of a reply.
        """
        text = (stdout or "").strip()
        if not text:
            raise MessageSourceError(
                "reader_malformed_response", "The external reader returned no output."
            )
        try:
            payload = json.loads(text)
        except (ValueError, TypeError) as error:
            raise MessageSourceError(
                "reader_malformed_response",
                "The external reader returned output that is not valid JSON.",
            ) from error
        if not isinstance(payload, dict) or "ok" not in payload:
            raise MessageSourceError(
                "reader_malformed_response",
                "The external reader returned an unrecognised response shape.",
            )
        if payload.get("ok") is not True:
            raise MessageSourceError(
                self._error_state(payload),
                "The external reader reported that it could not answer.",
            )
        if returncode != 0:
            raise MessageSourceError(
                "reader_error",
                "The external reader reported success but exited with a failure.",
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise MessageSourceError(
                "reader_malformed_response",
                "The external reader returned a response without a data object.",
            )
        return data

    @staticmethod
    def _error_state(payload: dict[str, Any]) -> str:
        """The reader's own error code, but only if it is a safe token."""
        error = payload.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        if isinstance(code, str) and _SAFE_CODE.match(code):
            return f"reader_{code}" if not code.startswith("reader_") else code
        return "reader_error"

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _rows(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
        rows = data.get(key)
        if not isinstance(rows, list):
            raise MessageSourceError(
                "reader_malformed_response",
                "The external reader returned a response without the expected rows.",
            )
        for row in rows:
            if not isinstance(row, dict):
                raise MessageSourceError(
                    "reader_malformed_response",
                    "The external reader returned a row that is not an object.",
                )
        return rows

    @staticmethod
    def _number(value: Any) -> float | None:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return None

    @staticmethod
    def _limit(value: int) -> str:
        capped = int(value)
        if capped < 1:
            raise MessageSourceError(
                "invalid_argument", "A limit must be a positive integer."
            )
        return str(capped)

    def _chat_for(self, conversation_id: int) -> str:
        """Resolves a derived id back to the reader's chat identifier."""
        known = self._chat_by_id.get(conversation_id)
        if known is not None:
            return known
        # One refresh, then give up. Never fabricate an identifier.
        self.list_conversations(RECENT_CONVERSATION_SCAN_LIMIT)
        known = self._chat_by_id.get(conversation_id)
        if known is None:
            raise MessageSourceError(
                "conversation_unknown",
                "That conversation is not present in the external reader.",
            )
        return known

    # -- MessageSource -------------------------------------------------------

    def status(self) -> SourceStatus:
        """Reports readiness. An unready reader is reported, never raised."""
        try:
            data = self._invoke(["doctor"])
        except MessageSourceError as error:
            return SourceStatus(
                source=self.name, ready=False, state=error.state, detail=error.detail
            )
        summary = data.get("summary")
        ready = summary == "ready"
        return SourceStatus(
            source=self.name,
            ready=ready,
            state="ready" if ready else "reader_not_ready",
            detail="" if ready else "The external reader is not ready to read.",
            # A database read has no store schema to stamp, and counting every
            # conversation and message would mean a scan. Unknown is reported
            # as unknown rather than as a capped number that reads like a total.
            schema_version=None,
            conversation_count=None,
            message_count=None,
        )

    def list_conversations(self, limit: int) -> list[NormalizedConversation]:
        data = self._invoke(["sessions", "--limit", self._limit(limit)])
        conversations: list[NormalizedConversation] = []
        for row in self._rows(data, "sessions"):
            chat = row.get("username")
            if not isinstance(chat, str) or not chat:
                raise MessageSourceError(
                    "reader_malformed_response",
                    "The external reader returned a conversation without an identifier.",
                )
            identifier = conversation_identifier(chat)
            self._chat_by_id[identifier] = chat
            title = row.get("display_name") or chat
            conversations.append(
                NormalizedConversation(
                    id=identifier,
                    title=title if isinstance(title, str) else chat,
                    # A database read knows when the conversation was last
                    # written, not when this machine first became aware of it.
                    first_seen_at=None,
                    last_seen_at=self._number(row.get("last_timestamp")),
                    source=self.name,
                )
            )
        return conversations

    def get_messages(
        self,
        conversation_id: int,
        limit: int,
        before_sequence: int | None = None,
    ) -> list[NormalizedMessage]:
        if before_sequence is not None:
            # The reader pages on its own message identifiers, which are not
            # the ordering key published as `sequence`. Paging on a key that
            # only resembles the right one would return a plausible, wrong
            # window, so this is refused rather than approximated.
            raise MessageSourceError(
                "unsupported_paging",
                "This source cannot page backwards by sequence.",
            )
        chat = self._chat_for(int(conversation_id))
        data = self._invoke(
            ["history", chat, "--limit", self._limit(limit), "--display-order", "asc"]
        )
        return [
            self._message(row, int(conversation_id))
            for row in self._rows(data, "messages")
        ]

    def get_recent_messages(
        self, since_observed_at: float, limit: int
    ) -> list[NormalizedMessage]:
        """Messages at or after a timestamp, swept over recent conversations.

        The reader exposes no single cross-conversation query this adapter can
        express faithfully, so it visits a bounded set of conversations and
        filters their messages itself. The bound is a real coverage limit, not
        a total, and is documented rather than smoothed over.
        """
        since = float(since_observed_at)
        capped = int(self._limit(limit))
        collected: list[NormalizedMessage] = []
        for conversation in self.list_conversations(RECENT_CONVERSATION_SCAN_LIMIT):
            for message in self.get_messages(conversation.id, capped):
                if message.first_observed_at >= since:
                    collected.append(message)
        collected.sort(
            key=lambda item: (item.first_observed_at, item.conversation_id, item.sequence)
        )
        return collected[:capped]

    # -- normalisation -------------------------------------------------------

    def _message(self, row: dict[str, Any], conversation_id: int) -> NormalizedMessage:
        """One reader row in the representation the bridge already publishes."""
        identifier = row.get("local_id")
        if not isinstance(identifier, int) or isinstance(identifier, bool):
            raise MessageSourceError(
                "reader_malformed_response",
                "The external reader returned a message without an identifier.",
            )
        sequence = row.get("sort_seq")
        if not isinstance(sequence, int) or isinstance(sequence, bool):
            sequence = identifier
        created = self._number(row.get("create_time"))
        if created is None:
            raise MessageSourceError(
                "reader_malformed_response",
                "The external reader returned a message without a creation time.",
            )
        text = row.get("text")
        if not isinstance(text, str):
            content = row.get("content")
            text = content if isinstance(content, str) else None
        sender = row.get("sender")
        from_me = row.get("from_me")
        if from_me is True:
            ownership = "own"
        elif from_me is False:
            ownership = "other"
        else:
            ownership = "unknown"
        kind_name = row.get("kind_name")
        kind = _KIND_MAP.get(kind_name, "unknown") if isinstance(kind_name, str) else "unknown"
        return NormalizedMessage(
            id=identifier,
            conversation_id=conversation_id,
            sequence=sequence,
            sender=sender if isinstance(sender, str) else None,
            ownership=ownership,
            # The reader reports a real timestamp, not the label WeChat drew on
            # screen. Putting a formatted time here would look like something
            # the user saw, so it stays empty for this source.
            visible_time=None,
            text=text,
            kind=kind,
            # A decoded database row is exact. There is no estimator in this
            # path, so there is no estimate to report.
            confidence=1.0,
            first_observed_at=created,
            source=self.name,
        )
