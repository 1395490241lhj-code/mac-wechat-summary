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
    classify_message_directory_name,
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

# -- container-domain boundary policy ---------------------------------------
#
# A *domain* is a root-relative directory; a *role* is what a database basename
# is understood to be. They are independent axes and must not collapse into each
# other: a recognised auxiliary basename says nothing about its parent directory,
# and a physical-only parent says nothing about the database inside it.
#
# The class answers one question only -- does this domain lie inside the Reader
# completeness scope -- so physical presence in the container never decides it.

#: The Reader requires ordinary message truth here.
DOMAIN_REQUIRED_MESSAGE = "required_message"

#: The Reader requires identity truth here (session and contact anchors).
DOMAIN_REQUIRED_IDENTITY = "required_identity"

#: Independently proven to be a genuine WeChat store domain that the current v2
#: Reader does not claim as required message/identity truth. Existence is
#: accounted; its contents add no coverage and support no role.
DOMAIN_KNOWN_PHYSICAL_ONLY = "known_physical_only"

#: No independent provenance. The default, and always fail-closed.
DOMAIN_AMBIGUOUS = "ambiguous"

CONTAINER_DOMAIN_CLASSES: frozenset[str] = frozenset({
    DOMAIN_REQUIRED_MESSAGE,
    DOMAIN_REQUIRED_IDENTITY,
    DOMAIN_KNOWN_PHYSICAL_ONLY,
    DOMAIN_AMBIGUOUS,
})

#: Root domains proven physical-container-only, by exact name. Provenance, one
#: row per entry, in ``docs/v2/DB_READER_P4_A10_REAL_CONTAINER_CLASSIFICATION.md``
#: §15 ledger:
#:
#:   ``emoticon`` -- Tier 2, this repository's own committed historical reader.
#:   ``core/wechat_db.py`` reads ``os.path.join("emoticon", "emoticon.db")`` from
#:   a directory its own docstring documents as the WeChat ``db_storage`` root,
#:   and queries ``kNonStoreEmoticonTable`` for an md5 -> CDN sticker mapping.
#:   That is a *root-domain* fact: a first path component relative to the
#:   container root, and an explicit feature purpose that is not message or
#:   identity truth. No v2 production path reads it.
#:
#: Every other entry is Tier 3B/4 public provenance and needs BOTH (1) the exact
#: root-relative directory spelled in at least two independent public sources, and
#: (2) at least two sources that characterise its contents as non-message feature
#: data, with none describing chat/message rows. One row per entry, with source
#: URLs, pinned revisions and licenses, in the A10 classification doc sections
#: 17.3 and 18; this comment only names the evidence class.
#:
#:   ``sns`` (Moments), ``favorite`` (saved items), ``head_image`` (avatars),
#:   ``hardlink`` (attachment link index), ``bizchat`` (business-chat group/user
#:   metadata), ``third_app_icon`` (third-party app icons).
#:
#: Deliberately NOT here: ``chatbot`` (sources describe chatbot *messages*),
#: ``general`` (its documented tables hold message-event records such as recalled
#: message content), and ``solitaire`` (only one source characterises it).
#: ``weclaw.db`` semantics are uncertain, and a basename alone -- ``sns.db``,
#: ``hardlink_*.db``, ``chatbot.db`` -- never becomes a directory-domain entry.
#: There is deliberately no inference helper: no prefix, substring, case-folding
#: or fuzzy matching, because a look-alike name is not the proven domain.
_PHYSICAL_ONLY_DOMAIN_NAMES = frozenset({
    "emoticon", "sns", "favorite", "head_image", "hardlink", "bizchat",
    "third_app_icon",
})

KNOWN_PHYSICAL_ONLY_DIRECTORIES: frozenset[str] = _PHYSICAL_ONLY_DOMAIN_NAMES

#: Which boundary class each reportable location class always sits in. A location
#: class is a fixed token; only the physical-only row needs the domain name, so
#: that is the only place a name is consulted.
_LOCATION_DOMAIN_CLASS = {
    LOCATION_MESSAGE_DIRECTORY: DOMAIN_REQUIRED_MESSAGE,
    LOCATION_SESSION_DIRECTORY: DOMAIN_REQUIRED_IDENTITY,
    LOCATION_CONTACT_DIRECTORY: DOMAIN_REQUIRED_IDENTITY,
    LOCATION_ROOT: DOMAIN_AMBIGUOUS,
    LOCATION_OTHER_DIRECTORY: DOMAIN_AMBIGUOUS,
}


def domain_boundary_class(directory_name: str, location: str) -> str:
    """The boundary class of one domain, from the closed policy only.

    ``directory_name`` is the root-relative basename; the root itself is the
    empty string. Matching is exact-set membership, so a similar-looking name is
    not the proven domain. Any unknown input is ambiguous by default.
    """
    if location not in _LOCATION_DOMAIN_CLASS:
        raise ValueError("container directory invalid")
    if location == LOCATION_OTHER_DIRECTORY and directory_name in _PHYSICAL_ONLY_DOMAIN_NAMES:
        return DOMAIN_KNOWN_PHYSICAL_ONLY
    return _LOCATION_DOMAIN_CLASS[location]


def _is_outside_reader_boundary(directory_identity: str, location: str) -> bool:
    """Whether this row's parent domain is proven outside Reader completeness.

    Only a domain with independent provenance qualifies. An ambiguous parent is
    inside scope by default, so nothing here can be reached by guessing.
    """
    return domain_boundary_class(directory_identity, location) == DOMAIN_KNOWN_PHYSICAL_ONLY

#: Which required role each named directory exists to hold, and the name that
#: carries it. A location that exists but does not hold the role has not lost the
#: role -- it has misplaced it, which is a different fixed condition.
_LOCATION_ROLE = {
    LOCATION_SESSION_DIRECTORY: ROLE_SESSION_IDENTITY,
    LOCATION_CONTACT_DIRECTORY: ROLE_CONTACT_IDENTITY,
}

#: The same routing read backwards. Identity is not transferable between
#: domains, and the exported constructor enforces that below.
_LOCATION_ROLE_BY_ROLE = {role: location
                          for location, role in _LOCATION_ROLE.items()}

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

#: The same routing, narrowed to the conditions acceptance may actually raise.
#: Kept beside ``_ROLE_GAPS`` on purpose: one table says which role maps to which
#: visible gap, the other says which of those gaps can block a Reader claim.
_REQUIREMENT_ROLE_GAPS = {
    role: gap for role, gap in _ROLE_GAPS.items() if gap in CONTAINER_REQUIREMENTS
}


def _container_role(location: str, name: str) -> str:
    """Match production routing without changing message-directory inventory.

    An ordinary-shaped database outside message/ is not consumed by bootstrap
    or refresh. Keep it as message-bearing risk, never required-role evidence.
    No unnamed directory is independently proven outside the Reader boundary.
    """
    if name == _LOCATION_ANCHOR.get(location):
        return _LOCATION_ROLE[location]
    # `message/message_resource.db` is the one publicly proven resource store, at
    # that exact location. Elsewhere the same basename is still message-shaped
    # risk: keep the exemption exactly as wide as its proof, and let the inventory
    # and this layer share one definition of it.
    if location == LOCATION_MESSAGE_DIRECTORY:
        return classify_message_directory_name(name)
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
                    and self.location != LOCATION_MESSAGE_DIRECTORY)
                # Identity is not transferable between domains: enumeration only
                # ever puts these roles in their own anchored directory, and the
                # exported constructor must not be a way around that. Without it
                # a required identity could be satisfied out of the ambiguous
                # root or a proven physical-only domain.
                or self.role in _LOCATION_ROLE_BY_ROLE
                    and self.location != _LOCATION_ROLE_BY_ROLE[self.role]):
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

    @property
    def domain_summary(self) -> dict[str, dict[str, object]]:
        """Aggregate counts by boundary class. No name, no path, no identity.

        The reporting axis for the next real gate: how many databases, unknowns,
        message-shaped candidates and unexamined structures sit in each boundary
        class. It answers "are the remaining blockers inside Reader-relevant
        domains?" without ever naming a directory or a file.
        """
        summary = {boundary: {
            "database_count": 0,
            "unknown_count": 0,
            "candidate_count": 0,
            "nested_unexamined_count": 0,
            "unreadable_count": 0,
            "blocking_unknown_count": 0,
            "blocking_candidate_count": 0,
            "blocking_nested_count": 0,
            "blocking_unreadable_count": 0,
            "role_counts": {},
        } for boundary in sorted(CONTAINER_DOMAIN_CLASSES)}
        for row in self.databases:
            boundary = domain_boundary_class(row.directory_identity, row.location)
            entry = summary[boundary]
            entry["database_count"] += 1
            if row.role == ROLE_UNKNOWN:
                entry["unknown_count"] += 1
            elif row.role == ROLE_UNSUPPORTED_MESSAGE_CANDIDATE:
                entry["candidate_count"] += 1
            counts = entry["role_counts"]
            counts[row.role] = counts.get(row.role, 0) + 1
        for row in self.rejections:
            boundary = domain_boundary_class(row.directory_identity, row.location)
            entry = summary[boundary]
            if row.reason == UNREADABLE_DIRECTORY:
                # A directory that could not be listed is unexamined everywhere,
                # including inside a proven physical-only domain. Do not merge it
                # into the nested count: the two say different things.
                entry["unreadable_count"] += 1
            elif row.reason == NESTED_DIRECTORY or row.is_directory:
                entry["nested_unexamined_count"] += 1
        # What actually blocks acceptance, from the same pass that decides it. The
        # plain counts above say what is present; these say what the verdict
        # counted, so an observation outside the identity claim or a proven
        # physical-only domain is distinguishable without naming anything.
        for boundary, kind in _blocking_observations(self):
            summary[boundary]["blocking_%s_count" % kind] += 1
        return summary

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
            "domain_summary": self.domain_summary,
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
            if _is_directory(child):
                rejections.append(ContainerRejection(
                    location, child.name, NESTED_DIRECTORY, directory.name))
            elif not _is_regular_file(child):
                # A symlink is neither a plain file nor a real directory. Ignoring
                # it would let it alias content this pass never reads, under a name
                # it never classifies. A hidden regular file is sidecar noise.
                rejections.append(ContainerRejection(
                    location, child.name, REJECTED_NOT_REGULAR_FILE, directory.name))
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
        if _is_directory(child):
            # A hidden directory is accounted like any other: the root is the
            # strictest location, so an unentered subtree there is fail-closed
            # too. Only hidden *files* stay ignorable as sidecar noise.
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
        elif not _is_regular_file(child):
            rejections.append(ContainerRejection(
                LOCATION_ROOT, child.name, REJECTED_NOT_REGULAR_FILE))
    return ContainerAccounting(
        tuple(sorted(databases, key=lambda row: (
            row.location, row.directory_identity, row.name))),
        tuple(sorted(examined, key=lambda row: row.identity)),
        tuple(sorted(rejections, key=lambda row: (
            row.location, row.directory_identity, row.name, row.reason))),
    )


#: The row-level blocking kinds and the one requirement token each raises. The
#: aggregate report counts by kind; acceptance raises the token. Same pass, so
#: the two cannot disagree.
_BLOCKING_KIND_REQUIREMENT = {
    "unknown": GAP_UNKNOWN_DATABASE,
    "candidate": GAP_UNSUPPORTED_MESSAGE_CANDIDATE,
    "nested": DIRECTORY_UNEXAMINED,
    "unreadable": DIRECTORY_UNEXAMINED,
}

_ROLE_BLOCKING_KIND = {
    ROLE_UNKNOWN: "unknown",
    ROLE_UNSUPPORTED_MESSAGE_CANDIDATE: "candidate",
}


def _blocking_observations(accounting):
    """Every observation that blocks acceptance, as ``(boundary class, kind)``.

    This is the one place a row or a refusal is judged. ``unmet_requirements``
    reads it for the verdict and ``domain_summary`` reads it for the aggregate
    report, so there is no second policy engine to drift from.

    Boundary-class aware. An unexamined structure inside a domain independently
    proven outside the Reader boundary is visible physical structure, not a gap in
    Reader completeness: nothing inside it was looked at, nothing inside it counts
    as truth, and no recursion is implied either way. An unreadable directory
    stays blocking everywhere, because a directory that could not be listed is not
    the same claim as one that was listed and held nothing needed.

    Visible gaps and acceptance blockers are still different: business is excluded
    by D-040, and unknown/candidate risk blocks only where its parent domain is
    required or ambiguous. The role is never relabelled -- it stays visible and
    unsupported either way.

    Identity truth is the exact anchor, not the parent directory. Production opens
    session/session.db and contact/contact.db by name and never enumerates their
    siblings, so once a directory's own anchor is proven, an unrecognised
    regular-file entry beside it is outside the identity claim: still visible,
    still unsupported, but not a Reader-completeness blocker. Everything that stops
    the anchor being proven -- missing, refused, unlistable parent -- keeps the
    directory in scope, and a message-bearing candidate blocks regardless.
    """
    established_anchors = {
        row.directory_identity for row in accounting.databases
        if row.role in _LOCATION_ROLE_BY_ROLE
    }
    for row in accounting.databases:
        kind = _ROLE_BLOCKING_KIND.get(row.role)
        if kind is None:
            continue
        if _is_outside_reader_boundary(row.directory_identity, row.location):
            continue
        # A candidate is message-bearing risk, not merely an unrecognised name:
        # it stays a blocker wherever it was found, including beside a valid anchor.
        if (kind == "unknown"
                and row.location in _LOCATION_ROLE
                and row.directory_identity in established_anchors):
            continue
        yield domain_boundary_class(row.directory_identity, row.location), kind
    for row in accounting.rejections:
        boundary = domain_boundary_class(row.directory_identity, row.location)
        outside = boundary == DOMAIN_KNOWN_PHYSICAL_ONLY
        if row.reason == UNREADABLE_DIRECTORY:
            yield boundary, "unreadable"
            continue
        if row.reason == NESTED_DIRECTORY or row.is_directory:
            # Nothing inside an unentered directory was looked at, so it cannot be
            # shown to hold no message-bearing risk. Anchor scope covers what sits
            # beside the anchor, not structures this pass never entered.
            if not outside:
                yield boundary, "nested"
            continue
        role = _container_role(row.location, row.name)
        if role == ROLE_ORDINARY_MESSAGE:
            # A refused shard-shaped name is a candidate, never a shard: this
            # reader did not and may not open it.
            role = ROLE_UNSUPPORTED_MESSAGE_CANDIDATE
        # Only the two conditions in CONTAINER_REQUIREMENTS may come from here.
        # A refused business-shaped name is classified and reported as a gap, but
        # D-040 keeps business message excluded from required truth, so it can
        # never become an acceptance blocker.
        if role in _REQUIREMENT_ROLE_GAPS and not outside:
            yield boundary, _ROLE_BLOCKING_KIND[role]


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
    for _boundary, kind in _blocking_observations(accounting):
        unmet.add(_BLOCKING_KIND_REQUIREMENT[kind])
    return tuple(sorted(unmet))


__all__ = [
    "CONTAINER_DOMAIN_CLASSES",
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
    "DOMAIN_AMBIGUOUS",
    "DOMAIN_KNOWN_PHYSICAL_ONLY",
    "DOMAIN_REQUIRED_IDENTITY",
    "DOMAIN_REQUIRED_MESSAGE",
    "KNOWN_PHYSICAL_ONLY_DIRECTORIES",
    "NESTED_DIRECTORY",
    "SESSION_DIRECTORY_NAME",
    "UNREADABLE_DIRECTORY",
    "ContainerAccounting",
    "ContainerDatabase",
    "ContainerRejection",
    "ExaminedDirectory",
    "account_container",
    "domain_boundary_class",
    "unmet_requirements",
]
