"""The provider's supported-generation envelope, and within-role drift.

Three claims are kept apart here, because keeping them apart is the point:

* a **role** is what a name shape earns, and nothing more;
* a **compatibility outcome** says whether the schema inside is the generation
  this reader understands, decided against the parser's own column contract
  rather than a second copy of it;
* **coverage** is produced elsewhere, by exactly one function, and a gap found
  here reaches it only as the already-existing ``inventory_gap`` boolean.

A recognised name is never a compatible one. ``message_7.db`` earns an ordinary
message role *and then* is probed, and the two answers are allowed to disagree.

The supported surface is derived from ``wechatdb.parser.WANTED_COLUMNS``, so the
probe and the parser cannot drift into disagreeing about what a conversation
table must carry. A table missing a column the parser itself refuses on is
``malformed``; a table missing one only this envelope requires is ``unsupported``,
which is the honest word for a different generation that still parses.

Nothing here knows where a database lives, how it was decrypted, or who is in it.
It is handed an open read-only connection and reads structure only: table names
from ``sqlite_master``, column names from a bound ``pragma_table_info``. Every
statement is a literal and every identifier travels as a bound value, so no
caller string reaches query construction.
"""

from __future__ import annotations

import re
import sqlite3

from wechatdb.parser import CONVERSATION_TABLE, MANDATORY_COLUMNS, WANTED_COLUMNS

#: The conversation-table columns this provider reads. Every one must be present;
#: the table may carry others. Derived, not declared, so it cannot drift from the
#: parser that has to accept the same tables.
SUPPORTED_CONVERSATION_COLUMNS: frozenset[str] = frozenset(WANTED_COLUMNS)


class UnsupportedGeneration(Exception):
    """A read surface this provider was not built for. Carries no name."""

    def __init__(self) -> None:
        super().__init__("wechat database generation unsupported")


# -- roles ----------------------------------------------------------------------
#
# A provider-local mirror of the acquisition role vocabulary, proven equal by
# test rather than shared by import: acquisition owns the decision made while a
# source is still encrypted, this mirror owns the decision made once a part
# opens, and the isolation guard forbids either importing the other. A name with
# no known shape becomes ``unknown``, which is the intended failure mode rather
# than a hole in the list.

ROLE_ORDINARY_MESSAGE = "ordinary_message"
ROLE_BUSINESS_MESSAGE = "business_message"
ROLE_SEARCH_INDEX = "search_index"
ROLE_MEDIA = "media"
ROLE_AUXILIARY = "auxiliary"
ROLE_UNKNOWN = "unknown"
ROLE_UNSUPPORTED_MESSAGE_CANDIDATE = "unsupported_message_candidate"

SHARD_ROLES: frozenset[str] = frozenset({
    ROLE_ORDINARY_MESSAGE,
    ROLE_BUSINESS_MESSAGE,
    ROLE_SEARCH_INDEX,
    ROLE_MEDIA,
    ROLE_AUXILIARY,
    ROLE_UNKNOWN,
    ROLE_UNSUPPORTED_MESSAGE_CANDIDATE,
})

#: The roles this reader must be able to read before an ordinary message read may
#: claim complete. Every other role is optional: it can be incompatible all day
#: without weakening a message claim. This is a statement about the read
#: contract, not about the data.
REQUIRED_MESSAGE_ROLES: frozenset[str] = frozenset({ROLE_ORDINARY_MESSAGE})

_ORDINARY_SHARD = re.compile(r"^message_[0-9]+[.]db$")
_BUSINESS_SHARD = re.compile(r"^biz_message_[0-9]+[.]db$")
_SEARCH_INDEX = re.compile(r"^message_fts(_[0-9]+)?[.]db$")
_MEDIA = re.compile(r"^media(_[0-9]+)?[.]db$")
_AUXILIARY = re.compile(r"^(session|contact|hardlink_[0-9]+|chatbot|sns)[.]db$")
_MESSAGE_LIKE = re.compile(r"^(biz_)?message[_0-9a-z]*[.]db$")
#: A relation the reader does not read that is nonetheless message-shaped. A bare
#: ``Msg`` prefix is not enough: an FTS5 index materialises real
#: ``type='table'`` companions named ``Msg_<hex>_data``, ``_idx``, ``_docsize``
#: and ``_config``, and reading an index's internals as an unread message
#: relation would cost a shard a completeness claim that an identically
#: harmless non-Msg table does not cost. So a companion is exempted by name,
#: and every other Msg-prefixed table counts. That keeps a message-shaped
#: name this project has not seen honest rather than invisible, while exempting
#: only the suffixes an index actually materialises.
_FTS_COMPANION = re.compile(
    r"^Msg_[0-9a-fA-F]{32}_(data|idx|docsize|content|config)$")
_MESSAGE_RELATION = re.compile(r"^Msg")


def classify_shard_name(name: str) -> str:
    """The role one part's name carries. Closed, and total."""
    if not isinstance(name, str):
        raise ValueError("database name invalid")
    if _ORDINARY_SHARD.match(name):
        return ROLE_ORDINARY_MESSAGE
    if _BUSINESS_SHARD.match(name):
        return ROLE_BUSINESS_MESSAGE
    if _SEARCH_INDEX.match(name):
        return ROLE_SEARCH_INDEX
    if _MEDIA.match(name):
        return ROLE_MEDIA
    if _AUXILIARY.match(name):
        return ROLE_AUXILIARY
    if _MESSAGE_LIKE.match(name):
        return ROLE_UNSUPPORTED_MESSAGE_CANDIDATE
    return ROLE_UNKNOWN


# -- the closed compatibility vocabulary ----------------------------------------

COMPATIBLE = "compatible"
#: Opened, and of the generation this reader knows, but holding no conversation
#: table at all. Not a different generation: committed Discovery already seals
#: this exact shape as readable, and calling it unsupported would cost a real
#: shard its completeness claim merely for having no conversations in it now.
EMPTY = "empty"
UNSUPPORTED = "unsupported"
MALFORMED = "malformed"
INCOMPLETE = "incomplete"
#: Named rather than left absent: this part was never structurally read, so the
#: inventory carries one outcome per member instead of a fact a caller must
#: reconstruct from an absence. It is deliberately *not* a claim of compatibility.
UNASSESSED = "unassessed"

COMPATIBILITY_STATES: frozenset[str] = frozenset({
    COMPATIBLE,
    EMPTY,
    UNSUPPORTED,
    MALFORMED,
    INCOMPLETE,
    UNASSESSED,
})

#: The outcomes that withhold a compatible claim. ``incomplete`` and
#: ``unassessed`` are not here: neither is evidence of a different generation,
#: and the incomplete case says so through its own message-bearing-relation gap.
INCOMPATIBLE_OUTCOMES: frozenset[str] = frozenset({UNSUPPORTED, MALFORMED})


def table_columns(connection: sqlite3.Connection, table: str) -> frozenset[str]:
    """Column names of one table, read through a bound pragma.

    The table name is a value, never string-interpolated, so no caller-supplied
    identifier can reach query construction. A name SQLite does not know yields
    an empty set rather than an exception, which the caller reads as malformed.
    """
    try:
        return frozenset(row[0] for row in connection.execute(
            "SELECT name FROM pragma_table_info(?)", (table,)))
    except sqlite3.Error:
        return frozenset()


def is_valid_empty_message_part(connection: sqlite3.Connection) -> bool:
    """Whether this part is a valid message part that simply has no rows.

    The one definition of that shape in this package. Discovery asks it when it
    decides a part is readable, and the compatibility probe asks the same
    function, so the two layers cannot disagree about the same file.
    """
    names = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"TimeStamp", "wcdb_builtin_compression_record"} <= names:
        return False
    return "timestamp" in table_columns(connection, "TimeStamp")


def assess_schema(connection: sqlite3.Connection) -> str:
    """The one structural verdict for one already-open part.

    Read-only, structural, deterministic, and it reads no row. Precedence is
    fixed, so several tables that disagree still produce one answer: a table the
    parser itself refuses on outranks one merely outside this envelope, and a
    message-bearing relation the reader does not cover comes last, because it
    weakens a claim about a generation that is otherwise understood.
    """
    malformed = unsupported = conversation = foreign = False
    for (table,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        if CONVERSATION_TABLE.match(table):
            conversation = True
            present = table_columns(connection, table)
            if not set(MANDATORY_COLUMNS) <= present:
                malformed = True
            elif not SUPPORTED_CONVERSATION_COLUMNS <= present:
                unsupported = True
        elif (_MESSAGE_RELATION.match(table)
              and not _FTS_COMPANION.match(table)):
            # Message-shaped, and not the shape the parser reads: it may hold
            # messages this reader never sees, so the part cannot be called whole.
            foreign = True
    if malformed:
        return MALFORMED
    if unsupported:
        return UNSUPPORTED
    if conversation:
        return INCOMPLETE if foreign else COMPATIBLE
    if is_valid_empty_message_part(connection):
        # No conversations, but the shape Discovery already accepts as a
        # readable empty part. Asking Discovery here rather than restating its
        # rule keeps one definition of "valid and empty" in this codebase.
        # Same precedence as above: emptiness speaks only about conversations,
        # and a relation holding messages this reader never opens is still one
        # of them. Otherwise a shard whose conversations were all renamed away
        # would certify completeness over rows nobody here can see.
        return INCOMPLETE if foreign else EMPTY
    # It opened, and there is nothing here this reader reads. A different
    # generation, not a broken one: nothing about it is malformed.
    return UNSUPPORTED


def require_supported_surface(
    connection: sqlite3.Connection, table: str
) -> None:
    """Refuse a conversation table whose read surface has changed.

    Fail-closed per table, and kept for the read path, which refuses before it
    takes a single row. The name is validated against the parser's own contract
    and read through a bound pragma, so this entry point is not a way to build a
    statement out of a caller's string.
    """
    if not isinstance(table, str) or CONVERSATION_TABLE.fullmatch(table) is None:
        raise UnsupportedGeneration()
    if not SUPPORTED_CONVERSATION_COLUMNS <= table_columns(connection, table):
        raise UnsupportedGeneration()


# -- gaps, and the one report ---------------------------------------------------

GAP_SCHEMA_INCOMPATIBLE = "schema_incompatible"
GAP_MESSAGE_RELATION_UNREAD = "message_relation_unread"
GAP_REQUIRED_SHARD_UNREAD = "required_shard_unread"
GAP_MESSAGE_SHARD_ABSENT = "message_shard_absent"

#: Reused from acquisition rather than renamed: the business, unknown and
#: candidate gaps are one fact about one role, and this layer restates it
#: instead of inventing a second spelling it would then have to reconcile.
GAP_BUSINESS_MESSAGE_UNREAD = "business_message_unread"
GAP_UNKNOWN_DATABASE = "unknown_database"
GAP_UNSUPPORTED_MESSAGE_CANDIDATE = "unsupported_message_candidate"

#: Roles this reader does not read at all. Their presence is always a visible
#: gap whatever the schema inside says -- a business shard that probes perfectly
#: is still a shard this reader never opens, and saying otherwise would let a
#: green probe quietly stand in for a message source.
UNREAD_ROLES: dict[str, str] = {
    ROLE_BUSINESS_MESSAGE: GAP_BUSINESS_MESSAGE_UNREAD,
    ROLE_UNKNOWN: GAP_UNKNOWN_DATABASE,
    ROLE_UNSUPPORTED_MESSAGE_CANDIDATE: GAP_UNSUPPORTED_MESSAGE_CANDIDATE,
}

COMPATIBILITY_GAPS: frozenset[str] = frozenset({
    GAP_SCHEMA_INCOMPATIBLE,
    GAP_MESSAGE_RELATION_UNREAD,
    GAP_REQUIRED_SHARD_UNREAD,
    GAP_MESSAGE_SHARD_ABSENT,
}) | frozenset(UNREAD_ROLES.values())


def gaps_for(role: str, outcome: str) -> tuple[str, ...]:
    """The fixed gaps one role-and-outcome pair raises. No name, no path."""
    if role not in SHARD_ROLES or outcome not in COMPATIBILITY_STATES:
        raise ValueError("role or outcome is not in the closed vocabulary")
    raised: list[str] = []
    if role in UNREAD_ROLES:
        raised.append(UNREAD_ROLES[role])
    if outcome in (UNSUPPORTED, MALFORMED):
        raised.append(GAP_SCHEMA_INCOMPATIBLE)
        if role in REQUIRED_MESSAGE_ROLES:
            raised.append(GAP_REQUIRED_SHARD_UNREAD)
    if outcome == INCOMPLETE and role in REQUIRED_MESSAGE_ROLES:
        raised.append(GAP_MESSAGE_RELATION_UNREAD)
    if outcome == UNASSESSED and role in REQUIRED_MESSAGE_ROLES:
        # A required role that was never structurally read is a missing source,
        # not a compatibility verdict: nothing was ever opened to disagree with.
        raised.append(GAP_MESSAGE_SHARD_ABSENT)
    return tuple(raised)


class SchemaCompatibilityReport:
    """One read's schema accounting, per part and in total.

    A part is accounted for exactly once: a role, and one outcome from the closed
    vocabulary. Rendering shows counts only -- no name, handle, path, table or
    content -- so a report is safe to log and safe to put in a failure message.
    Lookups exist because the caller that owns a name has to be able to ask about
    its own part; they hand back a role or an outcome, never anything read out of
    the database.
    """

    __slots__ = ("_roles", "_outcomes")

    def __init__(self, roles: dict[str, str], outcomes: dict[str, str]) -> None:
        if set(roles) != set(outcomes):
            raise ValueError("a role without an outcome is not accounted for")
        if (any(role not in SHARD_ROLES for role in roles.values())
                or any(outcome not in COMPATIBILITY_STATES
                       for outcome in outcomes.values())):
            raise ValueError("report holds a state outside the closed vocabulary")
        self._roles = dict(roles)
        self._outcomes = dict(outcomes)

    def names(self) -> tuple[str, ...]:
        """Every part this report accounts for, in name order."""
        return tuple(sorted(self._outcomes))

    def role_of(self, name: str) -> str:
        return self._roles[name]

    def outcome_of(self, name: str) -> str:
        return self._outcomes[name]

    @property
    def required_gap(self) -> bool:
        """Whether a required message role is anything but a plain ``compatible``.

        The one boolean that reaches coverage, and it reaches it as the existing
        ``inventory_gap`` -- never as a second notion of completeness.
        """
        return any(gaps_for(self._roles[name], outcome) for name, outcome
                   in self._outcomes.items()
                   if self._roles[name] in REQUIRED_MESSAGE_ROLES)

    def gaps(self) -> tuple[str, ...]:
        found = {gap for name, outcome in self._outcomes.items()
                 for gap in gaps_for(self._roles[name], outcome)}
        return tuple(gap for gap in sorted(COMPATIBILITY_GAPS) if gap in found)

    def counts(self) -> dict[str, int]:
        tally: dict[str, int] = {}
        for name, outcome in self._outcomes.items():
            cell = self._roles[name] + ":" + outcome
            tally[cell] = tally.get(cell, 0) + 1
        return dict(sorted(tally.items()))

    def __repr__(self) -> str:
        return f"SchemaCompatibilityReport({self.counts()})"
