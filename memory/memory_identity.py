"""Canonical identity for remembered messages, independent of any reader.

Two different jobs are done here and they must not be confused.

**Identity** answers "is this the same record I already have?". It is what the
primary key is built from, and it decides whether a second ingestion writes a
new row or touches an existing one.

**Fingerprinting** answers "does this look like something I already have?". It
is computed for every message regardless of identity mode, and it is what makes
a duplicate visible instead of silently merged. A fingerprint is never a
primary key: two genuinely distinct messages can share one (a person sending
"好的" twice in the same second), and collapsing them would lose real content.

Both are deterministic: the same input produces the same identifier in every
process, on every run, forever. Nothing here reads a clock, a random source, or
a filesystem.

Source observations, not logical objects
----------------------------------------

Everything derived here identifies a **source observation**: one reader's
account of one WeChat object, stable *within that reader*. The visual store's
row 17 and the database's local id 17 are two observations, and their canonical
ids differ by construction. That difference is not a claim that they are two
different messages; it is the absence of a claim either way.

Whether two observations are the same logical object is a separate fact, held
in the store's ``logical_*`` tables (schema v2) and written only by an explicit
assertion with an accepted basis. Nothing in this module -- not a derived id,
not a fingerprint -- is such an assertion. A fingerprint match means "these
look alike"; the store reports it and refuses to merge on it.

Identity modes
--------------

``IDENTITY_SOURCE``
    The source supplies an identifier that is stable for that source. The
    canonical id is a digest over ``(source, source_message_id)``, so two
    sources may use the same local id without colliding.

``IDENTITY_DERIVED``
    The source has no stable identifier. The canonical id is a digest over the
    message's own content and position. This is a genuine fallback with real
    limits, documented in ``docs/v2/M1_MEMORY_FOUNDATION.md`` and repeated
    here because it is easy to forget:

    * Two identical messages from the same sender in the same conversation at
      the same recorded timestamp collapse into one record. That is a real
      loss, not a deduplication.
    * Any change to the text — a re-extraction that fixes one character, a
      trailing space — produces a *different* canonical id, so the corrected
      message is a new record rather than an update of the old one.
    * A derived id is not comparable with a source-provided id for the same
      underlying message. Switching a source's identity mode re-identifies
      everything it has ever contributed.

Neither mode is inherently better; a source declares which one honestly
describes it, and the store records the declaration per message so a later
reader can tell how much a canonical id is worth.
"""

from __future__ import annotations

import hashlib
import unicodedata

#: The source hands us an identifier that is stable for that source.
IDENTITY_SOURCE: str = "source"

#: The source has no stable identifier and one is derived from content.
IDENTITY_DERIVED: str = "derived"

IDENTITY_MODES: frozenset[str] = frozenset({IDENTITY_SOURCE, IDENTITY_DERIVED})

#: Digest width in bytes. 16 bytes of BLAKE2b is 128 bits, which is far more
#: than a local message store can exhaust, and short enough to stay readable
#: in a citation.
_DIGEST_SIZE: int = 16

#: Separates the parts of a digest input. A byte that cannot occur inside any
#: of the parts, so "a|b" and "a" + "|b" cannot produce the same input.
_SEPARATOR: bytes = b"\x1f"


def _digest(kind: str, parts: list[str | None]) -> str:
    """A namespaced digest over an ordered list of optional strings.

    ``None`` and the empty string are encoded differently on purpose: a
    message with no sender is not the same message as one whose sender is "".
    """
    hasher = hashlib.blake2b(digest_size=_DIGEST_SIZE)
    hasher.update(kind.encode("utf-8"))
    for part in parts:
        hasher.update(_SEPARATOR)
        if part is None:
            hasher.update(b"\x00")
        else:
            hasher.update(b"\x01")
            hasher.update(part.encode("utf-8"))
    return f"{kind}:{hasher.hexdigest()}"


def conversation_canonical_id(source: str, source_conversation_id: str) -> str:
    """Stable identity for one conversation within one source.

    Conversation identity is always source-provided: every source this project
    can describe knows which conversation a message belongs to, even when it
    does not know a message id.
    """
    return _digest("conv", [source, source_conversation_id])


def message_canonical_id_from_source(source: str, source_message_id: str) -> str:
    """Identity for a message whose source supplies a stable identifier."""
    return _digest("msg", [source, source_message_id])


def message_canonical_id_derived(
    source: str,
    conversation_canonical_id_value: str,
    sender: str | None,
    ownership: str,
    kind: str,
    text: str | None,
    timestamp: float,
) -> str:
    """Identity for a message from a source without stable identifiers.

    The timestamp is rounded to whole seconds before it enters the digest.
    Sub-second precision differs between a re-read and the original read for
    reasons that have nothing to do with the message, and letting it into the
    identity would make every re-ingestion produce new records.
    """
    return _digest(
        "msg",
        [
            source,
            conversation_canonical_id_value,
            sender,
            ownership,
            kind,
            normalize_text(text),
            str(int(timestamp)),
        ],
    )


def content_fingerprint(
    conversation_canonical_id_value: str,
    sender: str | None,
    ownership: str,
    kind: str,
    text: str | None,
    timestamp: float,
) -> str:
    """What this message looks like, ignoring which reader produced it.

    Deliberately **not** namespaced by source, so two ingestion runs describing
    the same underlying message are recognisable as such — most importantly a
    store that was deleted and re-read, which renumbers every source id and
    would otherwise duplicate silently. The store *reports* such a match; it
    never merges on it.

    It **is** scoped to a conversation, and conversation identity is still
    per-source, so this does not detect the same message arriving from two
    different readers. Equating one reader's conversation with another's needs
    a conversation-mapping layer that M1 deliberately does not have; inventing
    one here would guess at which chats are the same chat.
    """
    return _digest(
        "fp",
        [
            conversation_canonical_id_value,
            sender,
            ownership,
            kind,
            normalize_text(text),
            str(int(timestamp)),
        ],
    )


def normalize_text(text: str | None) -> str | None:
    """Unicode-normalises and trims text before it enters any digest.

    NFC only, plus surrounding whitespace. Case is left alone: Chinese has no
    case, and lowercasing Latin text would make two genuinely different
    messages identical. This normalisation is for *comparison*; the stored text
    is always the text the source gave us, unchanged.
    """
    if text is None:
        return None
    return unicodedata.normalize("NFC", text).strip()
