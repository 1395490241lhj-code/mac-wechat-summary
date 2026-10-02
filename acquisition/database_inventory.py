"""Database-role accounting for one already-selected message directory.

P4 compatibility groundwork, synthetic fixtures only. The refresher used to
bind every direct child matching "*.db" to the message-role descriptor, so a
directory that also held a business-message, a search, a media, an auxiliary or
an unrecognised database fed all of them to the reader as if they were ordinary
message shards. This module replaces that assumption with a closed role
vocabulary and total accounting.

Two bounds hold it together. First, the directory is the one an operator
already selected; nothing here widens, searches, recurses or chooses an
account. Second, a source is still *encrypted* at this point, so a name shape
is the only evidence available -- classification is structural, deliberately,
and opens no database.

Accounting is total. Every child of the selected directory lands in exactly
one of: a recognised role, explicitly unknown, an explicitly unsupported
message candidate, or a rejection with one fixed content-free reason. Nothing
is silently dropped, and a file whose name is unfamiliar is never allowed to
pass as an ordinary shard.

Auxiliary presence is never message coverage. Search, media and auxiliary
databases are recognised so they stop looking anomalous, and they contribute
nothing to what a message read can claim. Only ROLE_ORDINARY_MESSAGE reaches
the reader, and only the unreadable and unrecognised roles raise a
compatibility gap.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import stat

# -- the closed role vocabulary ------------------------------------------------

ROLE_ORDINARY_MESSAGE = "ordinary_message"
ROLE_BUSINESS_MESSAGE = "business_message"
ROLE_SEARCH_INDEX = "search_index"
ROLE_MEDIA = "media"
ROLE_AUXILIARY = "auxiliary"
ROLE_UNKNOWN = "unknown"
ROLE_UNSUPPORTED_MESSAGE_CANDIDATE = "unsupported_message_candidate"

DATABASE_ROLES: frozenset[str] = frozenset({
    ROLE_ORDINARY_MESSAGE,
    ROLE_BUSINESS_MESSAGE,
    ROLE_SEARCH_INDEX,
    ROLE_MEDIA,
    ROLE_AUXILIARY,
    ROLE_UNKNOWN,
    ROLE_UNSUPPORTED_MESSAGE_CANDIDATE,
})

#: Only this role is a message source. Every other role either adds no coverage
#: or is a visible gap; none of them may ever be read as a shard.
SUPPORTED_MESSAGE_ROLE: str = ROLE_ORDINARY_MESSAGE

# -- the closed gap vocabulary -------------------------------------------------

GAP_BUSINESS_MESSAGE_UNREAD = "business_message_unread"
GAP_UNKNOWN_DATABASE = "unknown_database"
GAP_UNSUPPORTED_MESSAGE_CANDIDATE = "unsupported_message_candidate"

INVENTORY_GAPS: frozenset[str] = frozenset({
    GAP_BUSINESS_MESSAGE_UNREAD,
    GAP_UNKNOWN_DATABASE,
    GAP_UNSUPPORTED_MESSAGE_CANDIDATE,
})

#: The one rejection reason. A candidate that is not a regular file -- a
#: symlink, a directory, a socket -- is not a database this reader can name, and
#: refusing it is not a compatibility claim about anything.
REJECTED_NOT_REGULAR_FILE = "not_a_regular_file"

REJECTION_REASONS: frozenset[str] = frozenset({REJECTED_NOT_REGULAR_FILE})

# -- name shapes ---------------------------------------------------------------
#
# Provenance per shape, stated so a later session does not have to re-derive
# which of these this project has actually observed:
#
#   message_<n>        our own documented shard shape; the real G2 gate read
#                       seven such parts
#   biz_message_<n>    already in this repository's v1 reader (core/wechat_db.py)
#   message_fts        already in this repository's v1 reader (core/wechat_db.py)
#   media, session,
#   contact            conventional WeChat store names; recognising them only
#                       stops them looking anomalous. `message/message_resource.db`
#                       is Tier 4 public provenance -- GreenBubbles' database
#                       reference records it as rows connecting a message to media
#                       metadata, ids, hashes or packed information, and
#                       raclen/wechat-suite reads the same packed_info blob for
#                       image md5 lookup -- but only for that exact location, so
#                       `classify_message_directory_name` narrows it there
#                       instead of editing this location-free classifier.
#   hardlink, chatbot,
#   sns                named in docs/v2/GREENBUBBLES_ASSIMILATION_AUDIT.md as
#                       stores a comparable reader recognises. Behavioural
#                       evidence only -- no code, SQL, fixture or identifier
#                       scheme was copied.
#
# A name this list does not know becomes `unknown`, which is visible. That is
# the intended failure mode, not a gap in the list.

_ORDINARY_SHARD = re.compile(r"\Amessage_[0-9]+\.db\Z")
_MESSAGE_LIKE = re.compile(r"\A(biz_)?message[_0-9a-z]*\.db\Z")
_BUSINESS_SHARD = re.compile(r"\Abiz_message_[0-9]+\.db\Z")
_SEARCH_INDEX = re.compile(r"\Amessage_fts(_[0-9]+)?\.db\Z")
_MEDIA = re.compile(r"\Amedia(_[0-9]+)?\.db\Z")
_AUXILIARY = re.compile(r"\A(session|contact|hardlink_[0-9]+|chatbot|sns)\.db\Z")


def classify_database_name(name: str) -> str:
    """The role one directory-child name carries. Closed, and total.

    The documented ordinary shard shape is checked first, so a message-shaped
    name that is *not* it becomes a visible candidate rather than a shard.
    Recognition is by shape only: the file is still encrypted here, and a role
    is never inferred from anything this function cannot see.
    """
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


#: The one exact name, inside ``message/`` only, that public provenance documents
#: as message-attachment resource metadata (ledger: the A10 classification doc).
#: Exact match: an indexed variant has no public proof and stays a candidate.
MESSAGE_RESOURCE_STORE_NAME = "message_resource.db"


def classify_message_directory_name(name: str) -> str:
    """The role a direct child of ``message/`` carries.

    ``classify_database_name`` stays location-free on purpose: the same basename
    elsewhere is still message-shaped risk. This is the narrow view for the one
    location the public evidence names, so the message-directory inventory and
    container accounting cannot disagree about it.
    """
    if name == MESSAGE_RESOURCE_STORE_NAME:
        return ROLE_MEDIA
    return classify_database_name(name)


#: Which role raises which gap. Deliberately small: a recognised auxiliary
#: database is not a gap, and a rejection is not a claim at all.
_ROLE_GAPS: dict[str, str] = {
    ROLE_BUSINESS_MESSAGE: GAP_BUSINESS_MESSAGE_UNREAD,
    ROLE_UNKNOWN: GAP_UNKNOWN_DATABASE,
    ROLE_UNSUPPORTED_MESSAGE_CANDIDATE: GAP_UNSUPPORTED_MESSAGE_CANDIDATE,
}

@dataclass(frozen=True, slots=True)
class DatabaseInventoryEntry:
    """One candidate database, and the one role it was given."""

    name: str
    role: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("database inventory entry invalid")
        if self.role not in DATABASE_ROLES:
            raise ValueError("database inventory entry invalid")


@dataclass(frozen=True, slots=True)
class DatabaseInventory:
    """Every child of one selected directory, accounted for exactly once.

    Ordering is by name and no name appears twice, so the result depends only
    on which children exist, never on the order the filesystem listed them in.
    """

    entries: tuple[DatabaseInventoryEntry, ...] = ()
    rejections: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        entries = tuple(self.entries)
        rejections = tuple(self.rejections)
        names = [entry.name for entry in entries]
        rejected = [name for name, _ in rejections]
        if (any(not isinstance(entry, DatabaseInventoryEntry) for entry in entries)
                or any(not isinstance(pair, tuple) or len(pair) != 2 for pair in rejections)
                or any(reason not in REJECTION_REASONS for _, reason in rejections)
                or len(set(names)) != len(names)
                or len(set(rejected)) != len(rejected)
                or set(names) & set(rejected)
                or entries != tuple(sorted(entries, key=lambda entry: entry.name))
                or rejections != tuple(sorted(rejections, key=lambda pair: pair[0]))):
            raise ValueError("database inventory invalid")
        object.__setattr__(self, "entries", entries)
        object.__setattr__(self, "rejections", rejections)

    def by_role(self, role: str) -> tuple[str, ...]:
        """The names carrying one role, in name order."""
        if role not in DATABASE_ROLES:
            raise ValueError("database role is not in the closed vocabulary")
        return tuple(entry.name for entry in self.entries if entry.role == role)

    @property
    def message_shards(self) -> tuple[str, ...]:
        """The only names that may become message-role sources."""
        return self.by_role(SUPPORTED_MESSAGE_ROLE)

    @property
    def supports_message_read(self) -> bool:
        """Whether the directory holds a supported message source at all."""
        return bool(self.message_shards)

    @property
    def gaps(self) -> tuple[str, ...]:
        """The compatibility gaps, closed vocabulary, in a fixed order.

        A rejected child counts. It was refused, so it is not a shard and not a
        claim about what it contains, but it is still a database this directory
        holds and the read did not cover -- and a supported or message-shaped
        name means the read cannot honestly say it saw everything.
        """
        present = {entry.role for entry in self.entries}
        for name, _ in self.rejections:
            role = classify_message_directory_name(name)
            if role == ROLE_ORDINARY_MESSAGE:
                # A refused shard-shaped name is a candidate, never a shard:
                # this reader did not and may not open it. A refused media or
                # auxiliary name carries no message claim and raises nothing.
                role = ROLE_UNSUPPORTED_MESSAGE_CANDIDATE
            present.add(role)
        return tuple(
            gap for role, gap in _ROLE_GAPS.items() if role in present)

    def accounts_for(self, names) -> bool:
        """True only when these names are accounted for exactly once each."""
        expected = set(names)
        accounted = [entry.name for entry in self.entries]
        accounted += [name for name, _ in self.rejections]
        return (expected == set(accounted)
                and len(set(accounted)) == len(accounted))


def _is_candidate(child: Path) -> bool:
    """A direct child worth naming: visible, and a ".db" file.

    The suffix is what excludes the -wal and -shm sidecars, which belong to a
    database rather than standing for one.
    """
    return not child.name.startswith(".") and child.name.endswith(".db")


def _is_regular_file(child: Path) -> bool:
    """A plain file, never a symlink and never a directory.

    lstat is the whole point: a symlink to a real database would otherwise pass
    as one, and what this names is a path the reader was told to snapshot.
    """
    try:
        return stat.S_ISREG(child.lstat().st_mode)
    except OSError:
        return False


def inventory_message_directory(message_dir: Path) -> DatabaseInventory:
    """Account for every direct child of one already-selected directory.

    Bounded by construction: one directory listing, no recursion, no search
    above or beside it, and no attempt to decide which account it is.
    """
    directory = Path(message_dir)
    if not directory.is_dir():
        return DatabaseInventory()
    entries: list[DatabaseInventoryEntry] = []
    rejections: list[tuple[str, str]] = []
    for child in sorted(directory.iterdir(), key=lambda path: path.name):
        if not _is_candidate(child):
            continue
        if not _is_regular_file(child):
            rejections.append((child.name, REJECTED_NOT_REGULAR_FILE))
            continue
        entries.append(
            DatabaseInventoryEntry(
                child.name, classify_message_directory_name(child.name)))
    return DatabaseInventory(tuple(entries), tuple(rejections))
