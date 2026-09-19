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

import json
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any

from conversation_identity import conversation_identifier
from message_source import (
    COVERAGE_COMPLETE,
    COVERAGE_PARTIAL,
    REASON_CALLER_LIMIT,
    REASON_EMPTY_WINDOW,
    REASON_FULL_WINDOW_OBSERVED,
    REASON_SOURCE_LIMIT,
    REASON_UPSTREAM_MORE,
    SOURCE_DATABASE,
    MessageSourceError,
    NormalizedConversation,
    NormalizedMessage,
    ReadCoverage,
    ReadFreshness,
    ReadResult,
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
        #: The reader's own next page offset from the last ``history`` reply.
        #: Adapter paging state: it describes where this reader would resume,
        #: which is a fact about the reader and not about coverage, so it never
        #: enters a ``ReadCoverage``, an item, a log line or an error.
        self._next_offset: int | None = None

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
    def _pagination(data: dict[str, Any]) -> tuple[bool, int | None]:
        """The ``query`` object ``history`` replies carry, validated.

        Only ``history`` sends one; ``sessions`` sends none, and measures its
        own truncation with a sentinel row instead. Malformed evidence is
        refused rather than replaced with an assumption: guessing here would
        manufacture exactly the false completeness this envelope exists to
        remove, out of a reply that could not be read.
        """
        query = data.get("query")
        if not isinstance(query, dict):
            raise MessageSourceError(
                "reader_malformed_response",
                "The external reader returned a response without page state.",
            )
        has_more = query.get("has_more")
        if not isinstance(has_more, bool):
            raise MessageSourceError(
                "reader_malformed_response",
                "The external reader returned an unreadable page signal.",
            )
        offset = query.get("next_offset")
        if offset is not None and (
                isinstance(offset, bool) or not isinstance(offset, int)):
            raise MessageSourceError(
                "reader_malformed_response",
                "The external reader returned an unreadable page offset.",
            )
        return has_more, offset

    @staticmethod
    def _newest(moments: Any) -> float | None:
        """The newest moment actually reached, or ``None`` if none is known."""
        known = [moment for moment in moments if moment is not None]
        return max(known) if known else None

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

    def _conversation(self, row: dict[str, Any]) -> NormalizedConversation:
        """One reader session row, normalised and cached under its derived id."""
        chat = row.get("username")
        if not isinstance(chat, str) or not chat:
            raise MessageSourceError(
                "reader_malformed_response",
                "The external reader returned a conversation without an identifier.",
            )
        identifier = conversation_identifier(chat)
        self._chat_by_id[identifier] = chat
        title = row.get("display_name") or chat
        return NormalizedConversation(
            id=identifier,
            title=title if isinstance(title, str) else chat,
            # A database read knows when the conversation was last written,
            # not when this machine first became aware of it.
            first_seen_at=None,
            last_seen_at=self._number(row.get("last_timestamp")),
            source=self.name,
        )

    def list_conversations(self, limit: int) -> ReadResult[NormalizedConversation]:
        """Conversations, with truncation measured rather than inferred.

        ``sessions`` returns no page state -- unlike ``history`` it sends no
        ``query`` object at all -- so this asks for one row beyond the caller's
        limit. Whether that sentinel comes back is the source's own answer
        about whether more exist, which is why a short reply here means
        something that a merely short reply never could.

        The sentinel is evidence and nothing else. It is not normalised, not
        cached, not counted and not reachable: caching it would quietly widen
        the reachable set by one conversation past the bound every caller was
        told about.
        """
        capped = int(self._limit(limit))
        data = self._invoke(["sessions", "--limit", str(capped + 1)])
        rows = self._rows(data, "sessions")
        truncated = len(rows) > capped
        conversations = tuple(self._conversation(row) for row in rows[:capped])
        observed = self._newest(item.last_seen_at for item in conversations)
        if truncated:
            coverage = ReadCoverage(
                status=COVERAGE_PARTIAL, reason=REASON_CALLER_LIMIT,
                requested_start=None, requested_end=None,
                observed_through=observed, complete_through=None,
                freshness=ReadFreshness.UNKNOWN,
                truncated=True, item_count=len(conversations),
            )
        else:
            coverage = ReadCoverage(
                status=COVERAGE_COMPLETE,
                reason=(REASON_FULL_WINDOW_OBSERVED if conversations
                        else REASON_EMPTY_WINDOW),
                requested_start=None, requested_end=None,
                observed_through=observed, complete_through=observed,
                freshness=ReadFreshness.UNKNOWN,
                truncated=False, item_count=len(conversations),
            )
        return ReadResult(items=conversations, coverage=coverage)

    def get_messages(
        self,
        conversation_id: int,
        limit: int,
        before_sequence: int | None = None,
    ) -> ReadResult[NormalizedMessage]:
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
        # The reader's own page signal, which this adapter used to discard.
        has_more, self._next_offset = self._pagination(data)
        messages = tuple(
            self._message(row, int(conversation_id))
            for row in self._rows(data, "messages")
        )
        observed = self._newest(item.first_observed_at for item in messages)
        if has_more:
            # The reader said a page remains. That is its statement, not the
            # caller's limit, and it is never restated as one.
            coverage = ReadCoverage(
                status=COVERAGE_PARTIAL, reason=REASON_UPSTREAM_MORE,
                requested_start=None, requested_end=None,
                observed_through=observed, complete_through=None,
                freshness=ReadFreshness.UNKNOWN,
                truncated=True, item_count=len(messages),
            )
        else:
            coverage = ReadCoverage(
                status=COVERAGE_COMPLETE,
                reason=(REASON_FULL_WINDOW_OBSERVED if messages
                        else REASON_EMPTY_WINDOW),
                requested_start=None, requested_end=None,
                observed_through=observed, complete_through=observed,
                freshness=ReadFreshness.UNKNOWN,
                truncated=False, item_count=len(messages),
            )
        return ReadResult(items=messages, coverage=coverage)

    def get_recent_messages(
        self, since_observed_at: float, limit: int
    ) -> ReadResult[NormalizedMessage]:
        """Messages at or after a timestamp, swept over recent conversations.

        The reader exposes no single cross-conversation query this adapter can
        express faithfully, so it visits a bounded set of conversations and
        filters their messages itself. That bound is a real coverage limit and
        is now reported as one: the caller asked for messages, never for fifty
        conversations, so a sweep that hit its cap is the *source's* limit and
        is named ``source_limit`` rather than dressed up as the caller's.

        The children are read for their coverage as well as their items. A
        conversation whose own history the reader said was incomplete truncates
        this answer just as surely as the sweep bound does, and neither may be
        hidden behind the fact that few messages happened to match.
        """
        since = float(since_observed_at)
        capped = int(self._limit(limit))
        conversations = self.list_conversations(RECENT_CONVERSATION_SCAN_LIMIT)
        # The sentinel proved there is a conversation this sweep will not visit.
        bounded = conversations.coverage.truncated
        collected: list[NormalizedMessage] = []
        for conversation in conversations.items:
            child = self.get_messages(conversation.id, capped)
            if child.coverage.truncated:
                bounded = True
            for message in child.items:
                if message.first_observed_at >= since:
                    collected.append(message)
        collected.sort(
            key=lambda item: (item.first_observed_at, item.conversation_id, item.sequence)
        )
        messages = tuple(collected[:capped])
        observed = self._newest(item.first_observed_at for item in messages)
        if bounded:
            # An internal bound outranks the caller's, because it is the one
            # the caller had no way of knowing about.
            coverage = ReadCoverage(
                status=COVERAGE_PARTIAL, reason=REASON_SOURCE_LIMIT,
                requested_start=since, requested_end=None,
                observed_through=observed, complete_through=None,
                freshness=ReadFreshness.UNKNOWN,
                truncated=True, item_count=len(messages),
            )
        elif len(collected) > capped:
            # Measured, not guessed: more matching messages were in hand than
            # this answer carries. A count that merely equals the limit while
            # every source read was exhausted proves nothing and says nothing.
            coverage = ReadCoverage(
                status=COVERAGE_PARTIAL, reason=REASON_CALLER_LIMIT,
                requested_start=since, requested_end=None,
                observed_through=observed, complete_through=None,
                freshness=ReadFreshness.UNKNOWN,
                truncated=True, item_count=len(messages),
            )
        else:
            coverage = ReadCoverage(
                status=COVERAGE_COMPLETE,
                reason=(REASON_FULL_WINDOW_OBSERVED if messages
                        else REASON_EMPTY_WINDOW),
                requested_start=since, requested_end=None,
                observed_through=observed, complete_through=observed,
                freshness=ReadFreshness.UNKNOWN,
                truncated=False, item_count=len(messages),
            )
        return ReadResult(items=messages, coverage=coverage)

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
