"""The reader-neutral boundary the MCP bridge serves its tools from.

The bridge historically read one thing: the SQLite store the macOS app fills
from the visual capture path. This module names that assumption instead of
hiding it, so a second reader can exist without the tools, the agent runner, or
the skill learning anything about it.

Two rules shape everything here.

**Nothing in this file is specific to any one reader.** No process, no JSON
dialect, no WeChat schema, no vendor name. A source is anything that can answer
four questions: are you ready, which conversations do you have, what is in one
conversation, and what is recent. An implementation lives in its own module.

**Provenance is carried, not projected.** :class:`NormalizedMessage` knows which
source produced it, but :meth:`NormalizedMessage.payload` deliberately omits
that field. The wire shape an agent sees is therefore byte-identical to the one
the bridge has always returned, while the process still knows where a row came
from. Provenance reaches a client on the response envelope instead, where it
describes the whole answer rather than each row. When the reader layer is ready
to be user-visible, that is a projection change here and nowhere else.

Coverage differences between sources are real and must stay visible. A source
that cannot answer a question raises :class:`MessageSourceError`; it never
substitutes a different answer, and no caller silently falls back to another
source. A quiet fallback would make a partial read look like a complete one,
which is the one failure this boundary exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

#: The visual capture path: frames extracted, reconciled, and stored by the
#: macOS app under the user's local-persistence consent.
SOURCE_VISUAL: str = "visual"

#: A local WeChat database read through an external reader implementation.
SOURCE_DATABASE: str = "database"

SOURCE_NAMES: frozenset[str] = frozenset({SOURCE_VISUAL, SOURCE_DATABASE})


class MessageSourceError(Exception):
    """A source cannot answer, and will not guess.

    ``state`` is a fixed, lowercase token describing the category of refusal.
    ``detail`` is a fixed human-readable sentence. Neither may ever carry chat
    content, a sender, a filesystem path, or output captured from another
    program: both are returned to a client and both are safe to log.
    """

    def __init__(self, state: str, detail: str) -> None:
        super().__init__(detail)
        self.state = state
        self.detail = detail


@dataclass(frozen=True)
class SourceStatus:
    """Readiness of one source. Never carries chat content.

    ``conversation_count`` and ``message_count`` are optional because they are
    not universally cheap: a source that would have to scan to answer reports
    ``None`` rather than an estimate or a capped total that reads like a total.
    """

    source: str
    ready: bool
    state: str
    detail: str = ""
    schema_version: int | None = None
    conversation_count: int | None = None
    message_count: int | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "state": self.state,
            "schema_version": self.schema_version,
            "conversation_count": self.conversation_count,
            "message_count": self.message_count,
        }


@dataclass(frozen=True)
class NormalizedConversation:
    """One conversation, in the representation the bridge already publishes.

    ``first_seen_at`` and ``last_seen_at`` are optional. The visual path knows
    both because it watched them arrive; a database read may know only when the
    last message was written. ``None`` says "not known by this source" and is
    not the same claim as a zero.
    """

    id: int
    title: str | None
    first_seen_at: float | None
    last_seen_at: float | None
    source: str

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
        }


@dataclass(frozen=True)
class NormalizedMessage:
    """One message, in the representation the bridge already publishes.

    The field set is the store's, not a new model: adding a parallel shape
    would push the difference between readers all the way out to the skill.

    Two fields mean subtly different things per source, and that difference is
    the reason the envelope names the source.

    ``first_observed_at`` is when the visual path first saw the message on
    screen. A database source has no such moment worth reporting and supplies
    the message's own stored creation time instead, which is strictly better
    ordering information. Both are Unix timestamps and both are safe to filter
    on; only their meaning differs.

    ``visible_time`` is the string WeChat itself displayed, such as a relative
    day label. It is never a timestamp and is never parsed into one. A database
    source leaves it ``None`` rather than inventing a formatted time, because a
    value there would look like something a user saw when it is not.
    """

    id: int
    conversation_id: int
    sequence: int
    sender: str | None
    ownership: str
    visible_time: str | None
    text: str | None
    kind: str
    confidence: float
    first_observed_at: float
    source: str

    def payload(self) -> dict[str, Any]:
        """The wire shape. Provenance is intentionally not included.

        The envelope carries the source for the whole response; per-message
        provenance stays inside the process until there is a decision to make
        it user-visible.
        """
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "sequence": self.sequence,
            "sender": self.sender,
            "ownership": self.ownership,
            "visible_time": self.visible_time,
            "text": self.text,
            "kind": self.kind,
            "confidence": self.confidence,
            "first_observed_at": self.first_observed_at,
        }


@runtime_checkable
class MessageSource(Protocol):
    """What the bridge requires of any reader.

    Four questions, no more. There is deliberately no query method, no path
    argument, and no way to ask a source for arbitrary data: the tools are a
    fixed surface and a source cannot widen it.

    Every method raises :class:`MessageSourceError` when it cannot answer.
    Returning an empty list means the source answered and found nothing, which
    is a different fact from being unable to answer, and callers rely on the
    distinction.
    """

    #: One of :data:`SOURCE_NAMES`.
    name: str

    def status(self) -> SourceStatus:
        """Readiness only. Never raises for an unready source: it reports."""
        ...

    def list_conversations(self, limit: int) -> list[NormalizedConversation]:
        ...

    def get_messages(
        self,
        conversation_id: int,
        limit: int,
        before_sequence: int | None = None,
    ) -> list[NormalizedMessage]:
        ...

    def get_recent_messages(
        self, since_observed_at: float, limit: int
    ) -> list[NormalizedMessage]:
        ...
