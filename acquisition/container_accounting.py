"""Container-level role accounting for one already-selected source root.

P4-A A10 groundwork, synthetic fixtures only. ``database_inventory`` accounts
the children of one selected ``message/`` directory. That is not the same claim
as accounting for a Reader container: the two identity anchors are opened by
name and never inventoried, and a real container also holds
``message/message_fts.db`` and ``emoticon/emoticon.db``. This is the
container-level view D-040 accounting needs.

Four bounds. The root is supplied, never chosen -- no recursion, no scoring, no
search above or beside it. The source is still encrypted, so a name shape is the
only evidence and no database is opened. Nothing is written, so a real container
is never mutated. And *every* directory in the boundary is examined, because
"no .db-bearing directory remains unexamined" is only true when nothing was
skipped: a name or a directory outside the vocabulary becomes a visible gap
rather than a silent omission.

The message-directory classifier is left alone. Session and contact identity
are accounted as required roles here rather than pushed back into a shard
classifier that has no reason to know about anchors. That disagreement between
D-040 and ``classify_database_name`` is recorded, not edited away.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import stat

from .database_inventory import (
    DATABASE_ROLES,
    GAP_BUSINESS_MESSAGE_UNREAD,
    GAP_UNKNOWN_DATABASE,
    GAP_UNSUPPORTED_MESSAGE_CANDIDATE,
    REJECTED_NOT_REGULAR_FILE,
    ROLE_BUSINESS_MESSAGE,
    ROLE_ORDINARY_MESSAGE,
    ROLE_UNKNOWN,
    ROLE_UNSUPPORTED_MESSAGE_CANDIDATE,
    _is_candidate,
    classify_database_name,
)

#: What this module claims about a source: it read names and never contents.
READ_ONLY_CLASSIFICATION = "read_only_structural_classification"

# -- the two roles the directory vocabulary cannot express --------------------

ROLE_SESSION_IDENTITY = "session_identity"
ROLE_CONTACT_IDENTITY = "contact_identity"

CONTAINER_ROLES: frozenset[str] = frozenset(DATABASE_ROLES | {
    ROLE_SESSION_IDENTITY,
    ROLE_CONTACT_IDENTITY,
})

#: The roles D-040 requires before a container may claim it is accounted.
REQUIRED_CONTAINER_ROLES: frozenset[str] = frozenset({
    ROLE_ORDINARY_MESSAGE,
    ROLE_SESSION_IDENTITY,
    ROLE_CONTACT_IDENTITY,
})

# -- where inside the boundary a database was found ----------------------------
#
# Fixed tokens, never a path: the root is operator-owned and its layout names the
# account, so a report carries these and nothing else.

LOCATION_ROOT = "container_root"
LOCATION_MESSAGE_DIRECTORY = "message_directory"
LOCATION_SESSION_DIRECTORY = "session_directory"
LOCATION_CONTACT_DIRECTORY = "contact_directory"
LOCATION_OTHER_DIRECTORY = "other_directory"

CONTAINER_LOCATIONS: frozenset[str] = frozenset({
    LOCATION_ROOT,
    LOCATION_MESSAGE_DIRECTORY,
    LOCATION_SESSION_DIRECTORY,
    LOCATION_CONTACT_DIRECTORY,
    LOCATION_OTHER_DIRECTORY,
})

#: The named directories, spelled to match the reader's own constants so the two
#: cannot drift apart silently.
MESSAGE_DIRECTORY_NAME = "message"
SESSION_DIRECTORY_NAME = "session"
CONTACT_DIRECTORY_NAME = "contact"

_NAMED_DIRECTORIES = {
    MESSAGE_DIRECTORY_NAME: LOCATION_MESSAGE_DIRECTORY,
    SESSION_DIRECTORY_NAME: LOCATION_SESSION_DIRECTORY,
    CONTACT_DIRECTORY_NAME: LOCATION_CONTACT_DIRECTORY,
}

#: Which required role each named directory exists to hold, and the name that
#: carries it. A location that exists but does not hold the role has not lost the
#: role -- it has misplaced it, which is a different fixed condition.
_LOCATION_ROLE = {
    LOCATION_SESSION_DIRECTORY: ROLE_SESSION_IDENTITY,
    LOCATION_CONTACT_DIRECTORY: ROLE_CONTACT_IDENTITY,
}

_LOCATION_ANCHOR = {
    LOCATION_SESSION_DIRECTORY: SESSION_DIRECTORY_NAME + ".db",
    LOCATION_CONTACT_DIRECTORY: CONTACT_DIRECTORY_NAME + ".db",
}

# -- the closed condition vocabulary -------------------------------------------

#: A required role is absent from the boundary.
REQUIRED_ROLE_MISSING = "required_role_missing"

#: A required-role directory was listed and did not hold its role.
REQUIRED_ROLE_UNCLASSIFIED = "required_role_unclassified"

#: Part of the boundary was not examined: an unreadable directory, or a nested
#: directory this module deliberately does not recurse into. Total accounting is
#: a claim about what was looked at, so an unlooked-at part fails it closed.
DIRECTORY_UNEXAMINED = "directory_unexamined"

CONTAINER_REQUIREMENTS: frozenset[str] = frozenset({
    REQUIRED_ROLE_MISSING,
    REQUIRED_ROLE_UNCLASSIFIED,
    DIRECTORY_UNEXAMINED,
})

UNREADABLE_DIRECTORY = "unreadable_directory"
NESTED_DIRECTORY = "nested_directory"

CONTAINER_REJECTION_REASONS: frozenset[str] = frozenset({
    REJECTED_NOT_REGULAR_FILE,
    UNREADABLE_DIRECTORY,
    NESTED_DIRECTORY,
})

#: Which role raises which gap. A recognised optional or auxiliary database is
#: not a gap; a rejection is not a claim at all.
_ROLE_GAPS = {
    ROLE_BUSINESS_MESSAGE: GAP_BUSINESS_MESSAGE_UNREAD,
    ROLE_UNKNOWN: GAP_UNKNOWN_DATABASE,
    ROLE_UNSUPPORTED_MESSAGE_CANDIDATE: GAP_UNSUPPORTED_MESSAGE_CANDIDATE,
}


@dataclass(frozen=True, slots=True)
class ContainerDatabase:
    """One database inside the boundary, and the one role it was given.

    The name is retained so the caller that owns the boundary can ask about its
    own child; it is never rendered. The location is a fixed token, so a report
    built from these rows carries no path.
    """

    location: str
    name: str
    role: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("container database invalid")
        if self.role not in CONTAINER_ROLES:
            raise ValueError("container database invalid")
        if self.location not in CONTAINER_LOCATIONS:
            raise ValueError("container database invalid")


@dataclass(frozen=True, slots=True)
class ContainerAccounting:
    """Every database inside one selected boundary, accounted for exactly once.

    Ordering is by location then name and neither key repeats, so the result
    depends only on which children exist, never on listing order. Duplicate
    accounting is a construction error, not a runtime outcome.
    """

    databases: tuple[ContainerDatabase, ...] = ()
    examined_directories: tuple[str, ...] = ()
    rejections: tuple[tuple[str, str, str], ...] = ()

    def __post_init__(self) -> None:
        databases = tuple(self.databases)
        examined = tuple(self.examined_directories)
        rejections = tuple(self.rejections)
        rows = [(row.location, row.name) for row in databases]
        if (any(not isinstance(row, ContainerDatabase) for row in databases)
                or any(not isinstance(row, tuple) or len(row) != 3
                       for row in rejections)
                or any(reason not in CONTAINER_REJECTION_REASONS
                       for _, _, reason in rejections)
                or any(location not in CONTAINER_LOCATIONS
                       for location, _, _ in rejections)
                or len(set(rows)) != len(rows)
                or len(set(examined)) != len(examined)
                or set(examined) - CONTAINER_LOCATIONS
                or databases != tuple(sorted(
                    databases, key=lambda row: (row.location, row.name)))
                or rejections != tuple(sorted(
                    rejections, key=lambda row: (row[0], row[1], row[2])))
                ):
            raise ValueError("container accounting invalid")
        object.__setattr__(self, "databases", databases)
        object.__setattr__(self, "examined_directories", examined)
        object.__setattr__(self, "rejections", rejections)

    @property
    def role_counts(self) -> dict[str, int]:
        """How many databases carry each role. The report content."""
        counts = {role: 0 for role in CONTAINER_ROLES}
        for row in self.databases:
            counts[row.role] += 1
        return counts

    @property
    def gaps(self) -> tuple[str, ...]:
        """The closed gap vocabulary, in a fixed order.

        The message-directory tokens are reused on purpose: a business message, an
        unknown database and an unsupported message candidate mean the same thing
        at either level, and one vocabulary cannot drift apart.
        """
        present = {row.role for row in self.databases}
        for _, name, reason in self.rejections:
            if reason == UNREADABLE_DIRECTORY or reason == NESTED_DIRECTORY:
                continue
            role = classify_database_name(name)
            if role == ROLE_ORDINARY_MESSAGE:
                # A refused shard-shaped name is a candidate, never a shard: this
                # reader did not and may not open it.
                role = ROLE_UNSUPPORTED_MESSAGE_CANDIDATE
            present.add(role)
        return tuple(gap for role, gap in _ROLE_GAPS.items() if role in present)

    def accounts_for(self, keys) -> bool:
        """True only when these (location, name) keys are each accounted once."""
        expected = set(keys)
        accounted = [(row.location, row.name) for row in self.databases]
        accounted += [(location, name)
                      for location, name, _ in self.rejections]
        return (expected == set(accounted)
                and len(set(accounted)) == len(accounted))

    def meets_requirements(self) -> bool:
        """Whether every P4-A required-role condition holds for this boundary.

        This is the acceptance predicate, in code. Optional and excluded roles
        never appear in it: a present business message or an unreadable search
        index is accounted and visible, and weakens no ordinary message claim.
        """
        return unmet_requirements(self) == ()

    def evidence(self) -> dict[str, object]:
        """A renderable, structural-only summary: counts and closed tokens.

        No name, no path, no identifier, no digest, nothing read out of a
        database. This is the shape a gate document records.
        """
        counts = self.role_counts
        return {
            "classification": READ_ONLY_CLASSIFICATION,
            "database_count": len(self.databases),
            "role_counts": {role: count
                            for role, count in sorted(counts.items()) if count},
            "examined_directory_count": len(self.examined_directories),
            "location_counts": {
                location: sum(1 for row in self.databases
                              if row.location == location)
                for location in sorted(CONTAINER_LOCATIONS)
                if any(row.location == location for row in self.databases)
            },
            "rejections": tuple(reason for _, _, reason in self.rejections),
            "gaps": self.gaps,
            "unmet_requirements": unmet_requirements(self),
        }


def _is_directory(child: Path) -> bool:
    """A real directory, never a symlink to one."""
    try:
        return stat.S_ISDIR(child.lstat().st_mode)
    except OSError:
        return False


def _is_regular_file(child: Path) -> bool:
    """A plain file, never a symlink and never a directory.

    lstat is the whole point: a symlink to a real database would otherwise pass
    as one, and what this names is a path the reader was told about.
    """
    try:
        return stat.S_ISREG(child.lstat().st_mode)
    except OSError:
        return False


def _account_directory(directory: Path, location: str, rejections: list) -> list:
    """One boundary directory's database children, accounted exactly once.

    A directory that cannot be listed is a named rejection, never an empty
    result: an unreadable directory must not read as an absent one. Only the
    identity anchor is re-roled here, so this widens accounting and never changes
    a message read.
    """
    try:
        children = sorted(directory.iterdir(), key=lambda path: path.name)
    except OSError:
        rejections.append((location, directory.name, UNREADABLE_DIRECTORY))
        return []
    role_for = _LOCATION_ROLE.get(location)
    anchor = _LOCATION_ANCHOR.get(location)
    rows = []
    for child in children:
        if not _is_candidate(child):
            # A subdirectory is not a database, but if it holds one then part of
            # the boundary was not walked. Failing closed is the only honest
            # reading of "every database accounted exactly once".
            if not child.name.startswith(".") and _is_directory(child):
                rejections.append((location, child.name, NESTED_DIRECTORY))
            continue
        if not _is_regular_file(child):
            rejections.append((location, child.name, REJECTED_NOT_REGULAR_FILE))
            continue
        role = (role_for if role_for and child.name == anchor
                else classify_database_name(child.name))
        rows.append(ContainerDatabase(location, child.name, role))
    return rows


def account_container(source_root) -> ContainerAccounting:
    """Account for every database inside one already-selected boundary.

    Bounded by construction: one listing of the root, one listing of every
    directory directly inside it, no recursion, no search above or beside it,
    and no attempt to decide which account it is. No file is opened and nothing
    is written, so a real container is read exactly as far as a name shape.
    """
    root = Path(source_root)
    databases: list[ContainerDatabase] = []
    examined: list[str] = []
    rejections: list[tuple[str, str, str]] = []
    if not root.is_dir():
        return ContainerAccounting()

    for child in sorted(root.iterdir(), key=lambda path: path.name):
        if child.name.startswith("."):
            continue
        if _is_directory(child):
            location = _NAMED_DIRECTORIES.get(child.name, LOCATION_OTHER_DIRECTORY)
            examined.append(location)
            databases += _account_directory(child, location, rejections)
        elif _is_candidate(child):
            # A database can sit directly in the root, so the root is listed for
            # its own children rather than only for its directories. Dropping
            # these would make "every database accounted exactly once" false.
            if _is_regular_file(child):
                databases.append(ContainerDatabase(
                    LOCATION_ROOT, child.name, classify_database_name(child.name)))
            else:
                rejections.append((LOCATION_ROOT, child.name,
                                   REJECTED_NOT_REGULAR_FILE))
    return ContainerAccounting(
        tuple(sorted(databases, key=lambda row: (row.location, row.name))),
        tuple(sorted(examined)),
        tuple(sorted(rejections)),
    )


def unmet_requirements(accounting) -> tuple[str, ...]:
    """The fixed requirements this accounting does not meet, in a fixed order.

    Each condition is a claim the accounting either supports or visibly lacks,
    and none is assembled from a name or a path: the caller can log this verbatim
    and disclose nothing. Optional and excluded roles never appear, so a business
    message or a search index cannot fail an ordinary message claim.
    """
    if not isinstance(accounting, ContainerAccounting):
        raise ValueError("container accounting invalid")
    unmet = set()
    counts = accounting.role_counts
    for role in sorted(REQUIRED_CONTAINER_ROLES):
        if counts[role]:
            continue
        unmet.add(REQUIRED_ROLE_MISSING)
        location = next((where for where, carried in _LOCATION_ROLE.items()
                         if carried == role), None)
        if location is not None and location in accounting.examined_directories:
            # The directory that exists to hold this role was listed and did not
            # hold it. That is a different failure from the role never existing,
            # and it is what tells a future capsule where to look.
            unmet.add(REQUIRED_ROLE_UNCLASSIFIED)
    if any(reason in (UNREADABLE_DIRECTORY, NESTED_DIRECTORY)
           for _, _, reason in accounting.rejections):
        unmet.add(DIRECTORY_UNEXAMINED)
    return tuple(token for token in CONTAINER_REQUIREMENTS
                 if token in unmet)


__all__ = [
    "CONTAINER_LOCATIONS",
    "CONTAINER_REJECTION_REASONS",
    "CONTAINER_REQUIREMENTS",
    "CONTAINER_ROLES",
    "CONTACT_DIRECTORY_NAME",
    "GAP_BUSINESS_MESSAGE_UNREAD",
    "GAP_UNKNOWN_DATABASE",
    "GAP_UNSUPPORTED_MESSAGE_CANDIDATE",
    "LOCATION_CONTACT_DIRECTORY",
    "LOCATION_MESSAGE_DIRECTORY",
    "LOCATION_OTHER_DIRECTORY",
    "LOCATION_ROOT",
    "LOCATION_SESSION_DIRECTORY",
    "MESSAGE_DIRECTORY_NAME",
    "READ_ONLY_CLASSIFICATION",
    "REQUIRED_CONTAINER_ROLES",
    "REQUIRED_ROLE_MISSING",
    "REQUIRED_ROLE_UNCLASSIFIED",
    "DIRECTORY_UNEXAMINED",
    "NESTED_DIRECTORY",
    "SESSION_DIRECTORY_NAME",
    "UNREADABLE_DIRECTORY",
    "ContainerAccounting",
    "ContainerDatabase",
    "account_container",
    "unmet_requirements",
]
