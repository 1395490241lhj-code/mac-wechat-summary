"""Container-level role accounting for one already-selected source root.

P4-A A10 groundwork, synthetic fixtures only. ``database_inventory`` accounts
the children of one selected ``message/`` directory. That is not the same claim
as accounting for a Reader container: the two identity anchors are opened by
name and never inventoried, and a real container also holds
``message/message_fts.db`` and ``emoticon/emoticon.db``. This is the
container-level view D-040 accounting needs. Physical observation is broader
than required Reader consumption; unnamed domains have no proven exclusion,
so unknown/candidate and unexamined risks remain blocking. Recognized optional
and excluded stores remain nonrequired.

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

from dataclasses import dataclass, field
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
    GAP_UNKNOWN_DATABASE,
    GAP_UNSUPPORTED_MESSAGE_CANDIDATE,
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


def _container_role(location: str, name: str) -> str:
    """Match production routing without changing message-directory inventory.

    An ordinary-shaped database outside message/ is not consumed by bootstrap
    or refresh. Keep it as message-bearing risk, never required-role evidence.
    No unnamed directory is independently proven outside the Reader boundary.
    """
    if name == _LOCATION_ANCHOR.get(location):
        return _LOCATION_ROLE[location]
    role = classify_database_name(name)
    if role == ROLE_ORDINARY_MESSAGE and location != LOCATION_MESSAGE_DIRECTORY:
        return ROLE_UNSUPPORTED_MESSAGE_CANDIDATE
    return role


def _valid_directory_identity(identity: str, location: str) -> bool:
    if location == LOCATION_ROOT:
        return identity == ""
    return (isinstance(identity, str) and bool(identity)
            and identity not in (".", "..") and "/" not in identity
            and "\x00" not in identity)


@dataclass(frozen=True, slots=True)
class ContainerDatabase:
    """One database inside the boundary, and the one role it was given.

    The name is retained so the caller that owns the boundary can ask about its
    own child; it is never rendered. The location is a fixed token, so a report
    built from these rows carries no path.
    """

    location: str
    name: str = field(repr=False)
    role: str
    directory_identity: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("container database invalid")
        if (self.role not in CONTAINER_ROLES
                or (self.role == ROLE_ORDINARY_MESSAGE
                    and self.location != LOCATION_MESSAGE_DIRECTORY)):
            raise ValueError("container database invalid")
        if (self.location not in CONTAINER_LOCATIONS
                or not _valid_directory_identity(self.directory_identity, self.location)):
            raise ValueError("container database invalid")


@dataclass(frozen=True, slots=True)
class ExaminedDirectory:
    """Internal direct-entry identity, separate from its reportable class.

    Identity is the root-relative basename, unique within this operation only.
    It is not a source/product identity and must not be persisted or reported.
    """

    identity: str = field(repr=False)
    classification: str

    def __post_init__(self) -> None:
        if (not isinstance(self.identity, str) or not self.identity
                or self.identity in (".", "..") or "/" in self.identity
                or "\x00" in self.identity
                or not isinstance(self.classification, str)
                or self.classification not in CONTAINER_LOCATIONS - {LOCATION_ROOT}):
            raise ValueError("container directory invalid")


@dataclass(frozen=True, slots=True)
class ContainerRejection:
    """One refused entry, keyed by its concrete directory rather than class."""

    location: str
    name: str = field(repr=False)
    reason: str
    directory_identity: str = field(default="", repr=False)
    is_directory: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        if (not isinstance(self.name, str) or not self.name
                or self.location not in CONTAINER_LOCATIONS
                or self.reason not in CONTAINER_REJECTION_REASONS
                or not _valid_directory_identity(self.directory_identity, self.location)
                or type(self.is_directory) is not bool):
            raise ValueError("container rejection invalid")


@dataclass(frozen=True, slots=True)
class ContainerAccounting:
    """Every database inside one selected boundary, accounted for exactly once.

    Ordering is by class, directory identity then name, independent of listing
    order. Database and rejection keys use concrete directory identity plus
    basename, and no key repeats across either set. Directory identities are
    distinct from classes and ordered by root-relative basename.
    """

    databases: tuple[ContainerDatabase, ...] = ()
    examined_directories: tuple[ExaminedDirectory, ...] = field(default=(), repr=False)
    rejections: tuple[ContainerRejection, ...] = ()

    def __post_init__(self) -> None:
        databases = tuple(self.databases)
        examined = tuple(self.examined_directories)
        rejections = tuple(self.rejections)
        if (any(not isinstance(row, ContainerDatabase) for row in databases)
                or any(not isinstance(row, ContainerRejection) for row in rejections)
                or any(not isinstance(row, ExaminedDirectory) for row in examined)):
            raise ValueError("container accounting invalid")
        keys = [(row.directory_identity, row.name) for row in databases + rejections]
        if (len(set(keys)) != len(keys)
                or len({row.identity for row in examined}) != len(examined)
                or examined != tuple(sorted(examined, key=lambda row: row.identity))
                or databases != tuple(sorted(databases, key=lambda row: (
                    row.location, row.directory_identity, row.name)))
                or rejections != tuple(sorted(rejections, key=lambda row: (
                    row.location, row.directory_identity, row.name, row.reason)))):
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
        for row in self.rejections:
            if row.reason in (UNREADABLE_DIRECTORY, NESTED_DIRECTORY):
                continue
            role = _container_role(row.location, row.name)
            if role == ROLE_ORDINARY_MESSAGE:
                # A refused shard-shaped name is a candidate, never a shard: this
                # reader did not and may not open it.
                role = ROLE_UNSUPPORTED_MESSAGE_CANDIDATE
            present.add(role)
        return tuple(gap for role, gap in _ROLE_GAPS.items() if role in present)

    def accounts_for(self, keys) -> bool:
        """True only when these (directory identity, name) keys occur once.

        The root identity is the empty string; other identities are internal
        direct-entry basenames. Neither key is a reportable location class.
        """
        expected = set(keys)
        accounted = [(row.directory_identity, row.name)
                     for row in self.databases + self.rejections]
        return (expected == set(accounted)
                and len(set(accounted)) == len(accounted))

    def meets_requirements(self) -> bool:
        """The sole structural A10 acceptance predicate for this boundary.

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
            "rejections": tuple(row.reason for row in self.rejections),
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
    result: an unreadable directory must not read as an absent one. Container
    policy recognizes identity anchors and refuses ordinary shard shapes outside
    message routing; it never changes a production message read.
    """
    try:
        children = sorted(directory.iterdir(), key=lambda path: path.name)
    except OSError:
        rejections.append(ContainerRejection(
            location, directory.name, UNREADABLE_DIRECTORY, directory.name))
        return []
    rows = []
    for child in children:
        if not _is_candidate(child):
            # A subdirectory is not a database, but if it holds one then part of
            # the boundary was not walked. Failing closed is the only honest
            # reading of "every database accounted exactly once".
            if not child.name.startswith(".") and _is_directory(child):
                rejections.append(ContainerRejection(
                    location, child.name, NESTED_DIRECTORY, directory.name))
            continue
        if not _is_regular_file(child):
            rejections.append(ContainerRejection(
                location, child.name, REJECTED_NOT_REGULAR_FILE, directory.name,
                is_directory=_is_directory(child)))
            continue
        role = _container_role(location, child.name)
        rows.append(ContainerDatabase(location, child.name, role, directory.name))
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
    examined: list[ExaminedDirectory] = []
    rejections: list[ContainerRejection] = []
    if not root.is_dir():
        return ContainerAccounting()

    try:
        children = sorted(root.iterdir(), key=lambda path: path.name)
    except OSError:
        raise ValueError("container accounting invalid") from None
    for child in children:
        if child.name.startswith("."):
            continue
        if _is_directory(child):
            location = _NAMED_DIRECTORIES.get(child.name, LOCATION_OTHER_DIRECTORY)
            examined.append(ExaminedDirectory(child.name, location))
            databases += _account_directory(child, location, rejections)
        elif _is_candidate(child):
            # A database can sit directly in the root, so the root is listed for
            # its own children rather than only for its directories. Dropping
            # these would make "every database accounted exactly once" false.
            if _is_regular_file(child):
                databases.append(ContainerDatabase(
                    LOCATION_ROOT, child.name, _container_role(LOCATION_ROOT, child.name)))
            else:
                rejections.append(ContainerRejection(
                    LOCATION_ROOT, child.name, REJECTED_NOT_REGULAR_FILE))
    return ContainerAccounting(
        tuple(sorted(databases, key=lambda row: (
            row.location, row.directory_identity, row.name))),
        tuple(sorted(examined, key=lambda row: row.identity)),
        tuple(sorted(rejections, key=lambda row: (
            row.location, row.directory_identity, row.name, row.reason))),
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
        if location is not None and any(
                row.classification == location for row in accounting.examined_directories):
            # The directory that exists to hold this role was listed and did not
            # hold it. That is a different failure from the role never existing,
            # and it is what tells a future capsule where to look.
            unmet.add(REQUIRED_ROLE_UNCLASSIFIED)
    if any(row.reason in (UNREADABLE_DIRECTORY, NESTED_DIRECTORY) or row.is_directory
           for row in accounting.rejections):
        unmet.add(DIRECTORY_UNEXAMINED)
    # Visible gaps and acceptance blockers are different: business is excluded
    # by D-040, while unknown/candidate risk has no proven location exemption.
    unmet.update(gap for gap in accounting.gaps if gap in {
        GAP_UNKNOWN_DATABASE, GAP_UNSUPPORTED_MESSAGE_CANDIDATE})
    return tuple(sorted(unmet))


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
    "ContainerRejection",
    "ExaminedDirectory",
    "account_container",
    "unmet_requirements",
]
