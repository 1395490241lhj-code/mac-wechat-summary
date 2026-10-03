"""Container-level role accounting for one already-selected source root.

P4-A A10 groundwork, synthetic fixtures only. The message-directory inventory
accounts the children of one message/ directory, but a Reader container is
bigger than that: the identity anchors are opened by name and never
inventoried, and the real container also holds message/message_fts.db and
emoticon/emoticon.db. This module is the container-level view.

Nothing here opens a database, writes, searches above or beside the supplied
root, or chooses an account. Classification is structural, on names only,
because the source is still encrypted at this point. The message-directory
classifier is deliberately left untouched: session and contact identity are
accounted here rather than pushed back into a directory-shard classifier.
"""

from __future__ import annotations

from dataclasses import fields, replace
from pathlib import Path

import pytest

import acquisition.container_accounting as container

from acquisition.container_accounting import (
    CONTAINER_REQUIREMENTS,
    CONTAINER_LOCATIONS,
    CONTAINER_REJECTION_REASONS,
    CONTAINER_ROLES,
    DIRECTORY_UNEXAMINED,
    GAP_BUSINESS_MESSAGE_UNREAD,
    GAP_UNKNOWN_DATABASE,
    GAP_UNSUPPORTED_MESSAGE_CANDIDATE,
    LOCATION_CONTACT_DIRECTORY,
    LOCATION_MESSAGE_DIRECTORY,
    LOCATION_OTHER_DIRECTORY,
    LOCATION_ROOT,
    LOCATION_SESSION_DIRECTORY,
    READ_ONLY_CLASSIFICATION,
    REQUIRED_CONTAINER_ROLES,
    REQUIRED_ROLE_MISSING,
    REQUIRED_ROLE_UNCLASSIFIED,
    ROLE_BUSINESS_MESSAGE,
    ROLE_CONTACT_IDENTITY,
    ROLE_ORDINARY_MESSAGE,
    ROLE_SESSION_IDENTITY,
    ROLE_UNKNOWN,
    ROLE_UNSUPPORTED_MESSAGE_CANDIDATE,
    REJECTED_NOT_REGULAR_FILE,
    UNREADABLE_DIRECTORY,
    PROVEN_STORE_NAMES,
    account_container,
    unmet_requirements,
)
from acquisition.database_inventory import (
    ROLE_AUXILIARY,
    ROLE_MEDIA,
    ROLE_SEARCH_INDEX,
    classify_database_name,
)


def _root(tmp_path, *, message=("message_0.db", "message_1.db"), session=True,
          contact=True, directories=(), root_files=()):
    """A synthetic container. Names only; no real file is ever represented."""
    root = tmp_path / "container"
    (root / "message").mkdir(parents=True)
    for name in message:
        (root / "message" / name).write_bytes(b"synthetic")
    if session:
        (root / "session").mkdir()
        (root / "session" / "session.db").write_bytes(b"synthetic")
    if contact:
        (root / "contact").mkdir()
        (root / "contact" / "contact.db").write_bytes(b"synthetic")
    for name in directories:
        (root / name).mkdir()
        (root / name / "a.db").write_bytes(b"synthetic")
    for name in root_files:
        (root / name).write_bytes(b"synthetic")
    return root


# -- 1: the three required roles are accounted across the whole boundary ------


def test_a_complete_container_accounts_every_required_role(tmp_path):
    accounting = account_container(_root(tmp_path))

    assert accounting.role_counts[ROLE_ORDINARY_MESSAGE] == 2
    assert accounting.role_counts[ROLE_SESSION_IDENTITY] == 1
    assert accounting.role_counts[ROLE_CONTACT_IDENTITY] == 1
    assert unmet_requirements(accounting) == ()


def test_the_message_directory_classifier_is_not_widened_by_the_container_view():
    # session.db/contact.db are identity roles *here*; the message-directory
    # classifier still calls the same names auxiliary. D-040 recorded this
    # divergence; neither side is quietly edited to match the other.
    assert classify_database_name("session.db") == ROLE_AUXILIARY
    assert classify_database_name("contact.db") == ROLE_AUXILIARY


# -- 2: accounting is total over every database in the boundary --------------


def test_every_boundary_database_is_classified_exactly_once(tmp_path):
    root = _root(tmp_path, directories=("hardlink",), root_files=("stray.db",))

    accounting = account_container(root)

    # Keyed by (directory identity, name): the same name in two directories is two
    # databases, and one database in two roles would be a double count.
    assert accounting.accounts_for((
        ("", "stray.db"),
        ("message", "message_0.db"),
        ("message", "message_1.db"),
        ("session", "session.db"),
        ("contact", "contact.db"),
        ("hardlink", "a.db"),
    ))
    assert accounting.role_counts[ROLE_UNKNOWN] == 2


def test_a_directory_outside_the_named_three_is_examined_not_merely_counted(tmp_path):
    # emoticon/emoticon.db is real container layout this project has read.
    accounting = account_container(_root(tmp_path, directories=("emoticon",)))

    assert LOCATION_OTHER_DIRECTORY in {
        row.classification for row in accounting.examined_directories}
    # An unrecognised name in an examined directory is a visible gap, so an
    # unnamed directory can never hide a database from accounting.
    assert GAP_UNKNOWN_DATABASE in accounting.gaps


def test_no_db_bearing_directory_is_left_unexamined(tmp_path):
    # A nested directory is not walked, so its presence is a fail-closed
    # condition rather than a silent omission.
    root = _root(tmp_path)
    (root / "some_domain" / "shards").mkdir(parents=True)
    (root / "some_domain" / "shards" / "x.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert DIRECTORY_UNEXAMINED in unmet_requirements(accounting)


def test_an_unreadable_boundary_directory_is_named_not_silently_empty(tmp_path):
    root = _root(tmp_path, contact=False)
    (root / "contact").mkdir()
    (root / "contact" / "contact.db").write_bytes(b"synthetic")
    (root / "contact").chmod(0o000)
    try:
        accounting = account_container(root)
    finally:
        (root / "contact").chmod(0o700)

    assert any(row.reason == UNREADABLE_DIRECTORY for row in accounting.rejections)
    assert DIRECTORY_UNEXAMINED in unmet_requirements(accounting)
    assert REQUIRED_ROLE_MISSING in unmet_requirements(accounting)


def test_an_absent_root_is_empty_accounting_and_not_a_crash(tmp_path):
    accounting = account_container(tmp_path / "nothing-here")

    assert accounting.databases == ()
    assert REQUIRED_ROLE_MISSING in unmet_requirements(accounting)


# -- 3: optional and excluded roles stay explicit ----------------------------


def test_a_present_business_message_is_explicit_unsupported_and_not_required(tmp_path):
    accounting = account_container(
        _root(tmp_path, message=("message_0.db", "biz_message_0.db")))

    assert accounting.role_counts[ROLE_BUSINESS_MESSAGE] == 1
    assert GAP_BUSINESS_MESSAGE_UNREAD in accounting.gaps
    assert ROLE_BUSINESS_MESSAGE not in REQUIRED_CONTAINER_ROLES
    # Present, explicit and unsupported does not fail ordinary P4-A.
    assert unmet_requirements(accounting) == ()


def test_optional_search_and_media_roles_are_accounted_and_raise_no_gap(tmp_path):
    accounting = account_container(
        _root(tmp_path, message=("message_0.db", "message_fts.db", "media.db")))

    assert accounting.role_counts[ROLE_SEARCH_INDEX] == 1
    assert accounting.role_counts[ROLE_MEDIA] == 1
    assert accounting.gaps == ()


def test_an_unknown_root_database_is_a_visible_gap_and_blocker(tmp_path):
    accounting = account_container(_root(tmp_path, root_files=("zzz.db",)))

    assert GAP_UNKNOWN_DATABASE in accounting.gaps
    assert unmet_requirements(accounting) == (GAP_UNKNOWN_DATABASE,)


def test_an_unclassified_message_like_name_is_a_visible_candidate_gap(tmp_path):
    accounting = account_container(
        _root(tmp_path, root_files=("message_backup.db",)))

    assert accounting.role_counts[ROLE_UNSUPPORTED_MESSAGE_CANDIDATE] == 1
    assert GAP_UNSUPPORTED_MESSAGE_CANDIDATE in accounting.gaps


def test_no_message_bearing_shape_can_be_silently_absent_from_the_gaps(tmp_path):
    # Every name either matches a known role or raises unknown/candidate, so an
    # unfamiliar message-bearing database is visible rather than dropped.
    accounting = account_container(
        _root(tmp_path, root_files=("zzz.db", "message_backup.db",
                                    "biz_message_9.db")))

    assert accounting.gaps == (GAP_BUSINESS_MESSAGE_UNREAD,
                               GAP_UNKNOWN_DATABASE,
                               GAP_UNSUPPORTED_MESSAGE_CANDIDATE)


# -- 4: a required role that is absent or mislaid fails closed ----------------


@pytest.mark.parametrize("omit", ["session", "contact", "message"])
def test_an_absent_required_role_is_a_missing_required_role(tmp_path, omit):
    accounting = account_container(_root(
        tmp_path, session=omit != "session", contact=omit != "contact",
        message=() if omit == "message" else ("message_0.db",)))

    assert REQUIRED_ROLE_MISSING in unmet_requirements(accounting)


def test_a_database_where_an_identity_anchor_belongs_is_unclassified(tmp_path):
    root = _root(tmp_path)
    (root / "session" / "session.db").unlink()
    (root / "session" / "surprise.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert accounting.role_counts[ROLE_UNKNOWN] == 1
    unmet = unmet_requirements(accounting)
    assert REQUIRED_ROLE_MISSING in unmet
    assert REQUIRED_ROLE_UNCLASSIFIED in unmet


def test_a_symlinked_anchor_is_refused_rather_than_read(tmp_path):
    root = _root(tmp_path)
    anchor = root / "session" / "session.db"
    anchor.unlink()
    anchor.symlink_to(root / "message" / "message_0.db")

    accounting = account_container(root)

    assert container.ContainerRejection(LOCATION_SESSION_DIRECTORY, "session.db",
            "not_a_regular_file", "session") in accounting.rejections
    assert REQUIRED_ROLE_MISSING in unmet_requirements(accounting)


# -- 5: the closed vocabularies stay closed ----------------------------------


def test_every_accounted_role_and_location_is_in_the_closed_vocabulary(tmp_path):
    accounting = account_container(
        _root(tmp_path, directories=("hardlink",), root_files=("stray.db",)))

    assert set(accounting.role_counts) <= CONTAINER_ROLES
    assert {row.location for row in accounting.databases} <= CONTAINER_LOCATIONS
    assert {row.classification for row in accounting.examined_directories} <= CONTAINER_LOCATIONS
    assert {row.reason for row in accounting.rejections} <= CONTAINER_REJECTION_REASONS


def test_the_required_role_set_is_exactly_the_three_reader_contracts():
    assert REQUIRED_CONTAINER_ROLES == frozenset({
        ROLE_ORDINARY_MESSAGE, ROLE_SESSION_IDENTITY, ROLE_CONTACT_IDENTITY,
    })


def test_the_evidence_summary_carries_no_name_or_path(tmp_path):
    evidence = account_container(
        _root(tmp_path, directories=("emoticon",), root_files=("stray.db",))
    ).evidence()

    rendered = repr(evidence)
    for leak in ("message_0", "message_1", "session.db", "contact.db",
                 "stray", "emoticon", "a.db", "/"):
        assert leak not in rendered
    assert evidence["database_count"] == 6
    assert evidence["unmet_requirements"] == (GAP_UNKNOWN_DATABASE,)


def test_reading_a_container_is_read_only_and_classification_only(tmp_path):
    # The gate may classify a real container, so the module must expose that
    # as its one claim and never widen into discovery, selection or reading.
    root = _root(tmp_path, directories=("emoticon",))
    before = {path: path.stat().st_mtime_ns for path in sorted(root.rglob("*"))
              if path.is_file()}

    account_container(root)

    after = {path: path.stat().st_mtime_ns for path in sorted(root.rglob("*"))
             if path.is_file()}
    assert before == after
    assert READ_ONLY_CLASSIFICATION


# -- corrective capsule: concrete directory identity is not its class --------


@pytest.mark.parametrize("count", [0, 1, 3])
def test_same_class_directories_are_each_examined_once(tmp_path, count):
    root = tmp_path / "synthetic"
    root.mkdir()
    names = ("extra_a", "extra_b", "extra_c")[:count]
    for name in names:
        (root / name).mkdir()
    accounting = account_container(root)

    assert len(accounting.examined_directories) == count
    assert [row.identity for row in accounting.examined_directories] == list(names)
    assert [row.classification for row in accounting.examined_directories] == [
        LOCATION_OTHER_DIRECTORY] * count
    assert DIRECTORY_UNEXAMINED not in unmet_requirements(accounting)
    assert accounting.evidence()["examined_directory_count"] == count


def test_different_directory_identities_with_same_class_are_valid():
    rows = tuple(container.ExaminedDirectory(name, LOCATION_OTHER_DIRECTORY)
                 for name in ("extra_a", "extra_b"))
    accounting = container.ContainerAccounting(examined_directories=rows)
    assert accounting.examined_directories == rows


def test_duplicate_concrete_directory_identity_is_rejected():
    row = container.ExaminedDirectory("extra_a", LOCATION_OTHER_DIRECTORY)
    with pytest.raises(ValueError, match="^container accounting invalid$"):
        container.ContainerAccounting(examined_directories=(row, row))
    # A different class cannot disguise the same identity as a second directory.
    with pytest.raises(ValueError, match="^container accounting invalid$"):
        container.ContainerAccounting(examined_directories=(
            row, replace(row, classification=LOCATION_MESSAGE_DIRECTORY)))


def test_class_tokens_cannot_substitute_for_directory_records():
    with pytest.raises(ValueError, match="^container accounting invalid$"):
        container.ContainerAccounting(examined_directories=(LOCATION_OTHER_DIRECTORY,))
    assert {field.name for field in fields(container.ExaminedDirectory)} == {
        "identity", "classification"}


def test_multiple_other_directories_preserve_required_roles_and_database_accounting(tmp_path):
    root = _root(tmp_path, root_files=("root_extra.db",))
    for index, name in enumerate(("extra_a", "extra_b", "extra_c")):
        (root / name).mkdir()
        (root / name / f"neutral_{index}.db").write_bytes(b"synthetic")
    accounting = account_container(root)

    assert len(accounting.examined_directories) == 6
    assert not accounting.meets_requirements()
    assert accounting.role_counts[ROLE_UNKNOWN] == 4
    assert accounting.gaps == (GAP_UNKNOWN_DATABASE,)
    assert accounting.accounts_for([
        (row.directory_identity, row.name) for row in accounting.databases])
    assert len(accounting.databases) == 8
    assert accounting.evidence()["location_counts"][LOCATION_ROOT] == 1
    duplicate = accounting.databases[0]
    with pytest.raises(ValueError, match="^container accounting invalid$"):
        replace(accounting, databases=(duplicate, duplicate))


def test_multiple_other_directories_do_not_accept_nested_directories(tmp_path):
    root = _root(tmp_path)
    for name in ("extra_a", "extra_b", "extra_c"):
        (root / name).mkdir()
    nested = root / "extra_b" / "deeper"
    nested.mkdir()
    (nested / "message_99.db").write_bytes(b"synthetic")
    accounting = account_container(root)

    assert container.ContainerRejection(LOCATION_OTHER_DIRECTORY, "deeper",
            container.NESTED_DIRECTORY, "extra_b") in accounting.rejections
    assert DIRECTORY_UNEXAMINED in unmet_requirements(accounting)
    assert not accounting.meets_requirements()
    assert accounting.role_counts[ROLE_ORDINARY_MESSAGE] == 2


def test_directory_identity_order_does_not_depend_on_enumeration(tmp_path, monkeypatch):
    root = _root(tmp_path)
    for name in ("extra_c", "extra_a", "extra_b"):
        (root / name).mkdir()
    expected = account_container(root)
    original = Path.iterdir
    monkeypatch.setattr(Path, "iterdir", lambda path: iter(reversed(list(original(path)))))

    assert account_container(root) == expected
    identities = [row.identity for row in expected.examined_directories]
    assert identities == sorted(identities)
    with pytest.raises(ValueError, match="^container accounting invalid$"):
        replace(expected, examined_directories=tuple(reversed(expected.examined_directories)))


def test_directory_identity_stays_internal_and_diagnostics_are_sanitized(tmp_path):
    root = _root(tmp_path)
    for name in ("extra_a", "extra_b", "extra_c"):
        (root / name).mkdir()
    accounting = account_container(root)
    evidence = accounting.evidence()
    assert set(evidence) == {
        "classification", "database_count", "role_counts", "examined_directory_count",
        "location_counts", "rejections", "gaps", "domain_summary",
        "companion_count", "unmet_requirements"}
    rendered = repr(accounting) + repr(evidence) + repr(accounting.examined_directories)
    for private in (str(root), "extra_a", "extra_b", "extra_c"):
        assert private not in rendered
    for invalid in (str(root), "../extra_a", "", ".", ".."):
        with pytest.raises(ValueError, match="^container directory invalid$") as error:
            container.ExaminedDirectory(invalid, LOCATION_OTHER_DIRECTORY)
        assert str(root) not in str(error.value)


def test_repeated_listing_of_one_concrete_directory_is_rejected(tmp_path, monkeypatch):
    root = tmp_path / "synthetic"
    root.mkdir()
    child = root / "extra_a"
    child.mkdir()
    original = Path.iterdir
    monkeypatch.setattr(Path, "iterdir", lambda path: iter((child, child))
                        if path == root else original(path))
    with pytest.raises(ValueError, match="^container accounting invalid$"):
        account_container(root)


# -- independent-review corrective RED regressions --------------------------


def test_same_database_basename_in_distinct_directories_is_not_a_duplicate(tmp_path):
    root = _root(tmp_path)
    for name in ("extra_a", "extra_b", "extra_c"):
        (root / name).mkdir()
        (root / name / "shared.db").write_bytes(b"synthetic")
    accounting = account_container(root)
    assert accounting.role_counts[ROLE_UNKNOWN] == 3
    assert accounting.accounts_for([
        (row.directory_identity, row.name) for row in accounting.databases])
    assert {(row.directory_identity, row.name) for row in accounting.databases
            if row.location == LOCATION_OTHER_DIRECTORY} == {
        (name, "shared.db") for name in ("extra_a", "extra_b", "extra_c")}
    with pytest.raises(ValueError, match="^container accounting invalid$"):
        replace(accounting, databases=(accounting.databases[0],) * 2)


@pytest.mark.parametrize("first_regular", [False, True])
def test_same_basename_rejections_in_distinct_directories_are_accounted_once(tmp_path, first_regular):
    root = _root(tmp_path)
    for index, name in enumerate(("extra_a", "extra_b", "extra_c")):
        (root / name).mkdir()
        child = root / name / "shared.db"
        if first_regular and index == 0:
            child.write_bytes(b"synthetic")
        else:
            child.symlink_to(root / "message" / "message_0.db")
    accounting = account_container(root)
    keys = [(row.directory_identity, row.name) for row in accounting.databases]
    keys += [(row.directory_identity, row.name) for row in accounting.rejections]
    assert len(keys) == len(set(keys)) == 7
    assert accounting.accounts_for(keys)
    assert GAP_UNKNOWN_DATABASE in accounting.gaps


def test_duplicate_rejection_identity_and_database_overlap_are_rejected():
    row = container.ContainerRejection(LOCATION_OTHER_DIRECTORY, "shared.db",
                                      "not_a_regular_file", "extra_a")
    with pytest.raises(ValueError, match="^container accounting invalid$"):
        container.ContainerAccounting(rejections=(row, row))
    database = container.ContainerDatabase(LOCATION_OTHER_DIRECTORY, "shared.db",
                                          ROLE_UNKNOWN, "extra_a")
    with pytest.raises(ValueError, match="^container accounting invalid$"):
        container.ContainerAccounting(databases=(database,), rejections=(row,))


def test_nested_db_directory_still_means_unexamined_and_keeps_name_gap(tmp_path):
    root = _root(tmp_path)
    nested = root / "extra_a" / "nested.db"
    nested.mkdir(parents=True)
    (nested / "message_99.db").write_bytes(b"synthetic")
    accounting = account_container(root)
    assert DIRECTORY_UNEXAMINED in unmet_requirements(accounting)
    assert not accounting.meets_requirements()
    assert GAP_UNKNOWN_DATABASE in accounting.gaps
    assert accounting.role_counts[ROLE_ORDINARY_MESSAGE] == 2


def test_unreadable_directory_identity_is_hidden_in_rejection_repr(tmp_path, monkeypatch):
    root = _root(tmp_path)
    private = root / "synthetic_private_directory"
    private.mkdir()
    original = Path.iterdir
    def listing(path):
        if path == private:
            raise PermissionError(13, "Permission denied", str(private))
        return original(path)
    monkeypatch.setattr(Path, "iterdir", listing)
    accounting = account_container(root)
    rendered = repr(accounting) + repr(accounting.rejections) + repr(accounting.evidence())
    assert private.name not in rendered
    assert str(root) not in rendered
    assert DIRECTORY_UNEXAMINED in unmet_requirements(accounting)


def test_root_listing_error_is_sanitized(tmp_path, monkeypatch):
    root = _root(tmp_path)
    original = Path.iterdir
    def listing(path):
        if path == root:
            raise PermissionError(13, "Permission denied", str(root))
        return original(path)
    monkeypatch.setattr(Path, "iterdir", listing)
    with pytest.raises(ValueError, match="^container accounting invalid$") as error:
        account_container(root)
    assert str(root) not in str(error.value)
    assert error.value.__suppress_context__


# -- boundary/gap semantics: one machine acceptance truth --------------------

# Identity parents are absent on purpose: their unknown siblings are visible but
# outside the anchor-scoped identity claim, and the candidate case there is
# covered by the anchor-scope group below. Both remain blocking in every domain
# that *is* ambiguous.
@pytest.mark.parametrize("directory", [None, "message", "extra_domain"])
@pytest.mark.parametrize("name,gap", [
    ("neutral_store.db", GAP_UNKNOWN_DATABASE),
    ("message_future.db", GAP_UNSUPPORTED_MESSAGE_CANDIDATE),
])
def test_ambiguous_database_blocks_in_every_unexcluded_location(tmp_path, directory, name, gap):
    root = _root(tmp_path)
    parent = root if directory is None else root / directory
    parent.mkdir(exist_ok=True)
    (parent / name).write_bytes(b"synthetic")
    accounting = account_container(root)
    assert gap in accounting.gaps
    assert unmet_requirements(accounting) == (gap,)
    assert accounting.evidence()["unmet_requirements"] == (gap,)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("directory", [None, "session", "contact", "extra_domain"])
def test_ordinary_shape_outside_message_domain_cannot_supply_required_truth(tmp_path, directory):
    root = _root(tmp_path, message=())
    parent = root if directory is None else root / directory
    parent.mkdir(exist_ok=True)
    (parent / "message_8.db").write_bytes(b"synthetic")
    accounting = account_container(root)
    assert accounting.role_counts[ROLE_ORDINARY_MESSAGE] == 0
    assert GAP_UNSUPPORTED_MESSAGE_CANDIDATE in accounting.gaps
    assert REQUIRED_ROLE_MISSING in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


def test_mixed_recognized_nonrequired_stores_do_not_block_or_add_message_truth(tmp_path):
    root = _root(tmp_path, message=("message_0.db", "biz_message_0.db", "message_fts.db", "media.db"))
    (root / "extra_domain").mkdir()
    (root / "extra_domain" / "sns.db").write_bytes(b"synthetic")
    accounting = account_container(root)
    assert accounting.role_counts[ROLE_ORDINARY_MESSAGE] == 1
    assert accounting.role_counts[ROLE_AUXILIARY] == 1
    assert accounting.gaps == (GAP_BUSINESS_MESSAGE_UNREAD,)
    assert accounting.evidence()["unmet_requirements"] == unmet_requirements(accounting) == ()
    assert accounting.meets_requirements()


@pytest.mark.parametrize("directory", ["message", "extra_domain"])
def test_nested_domain_has_no_proven_physical_only_exemption(tmp_path, directory):
    root = _root(tmp_path)
    (root / directory / "unexamined").mkdir(parents=True)
    accounting = account_container(root)
    assert DIRECTORY_UNEXAMINED in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("name,gap", [
    ("neutral_store.db", GAP_UNKNOWN_DATABASE),
    ("message_future.db", GAP_UNSUPPORTED_MESSAGE_CANDIDATE),
    ("message_8.db", GAP_UNSUPPORTED_MESSAGE_CANDIDATE),
])
def test_refused_database_shape_keeps_its_acceptance_blocker(tmp_path, name, gap):
    root = _root(tmp_path)
    (root / "message" / name).symlink_to(root / "contact" / "contact.db")
    accounting = account_container(root)
    assert accounting.gaps == (gap,)
    assert unmet_requirements(accounting) == (gap,)
    assert not accounting.meets_requirements()


def test_refused_optional_database_does_not_become_a_message_blocker(tmp_path):
    root = _root(tmp_path)
    (root / "message" / "media.db").symlink_to(root / "contact" / "contact.db")
    accounting = account_container(root)
    assert accounting.rejections
    assert accounting.gaps == ()
    assert accounting.meets_requirements()


@pytest.mark.parametrize('directory', [None, 'session', 'contact', 'extra_domain'])
def test_exported_database_api_cannot_relabel_misplaced_shard_as_required(tmp_path, directory):
    root = _root(tmp_path, message=())
    parent = root if directory is None else root / directory
    parent.mkdir(exist_ok=True)
    (parent / 'message_8.db').write_bytes(b'synthetic')
    accounting = account_container(root)
    row = next(row for row in accounting.databases
               if row.role == ROLE_UNSUPPORTED_MESSAGE_CANDIDATE)
    with pytest.raises(ValueError, match='^container database invalid$'):
        replace(row, role=ROLE_ORDINARY_MESSAGE)
    with pytest.raises(ValueError, match='^container database invalid$'):
        container.ContainerDatabase(row.location, row.name, ROLE_ORDINARY_MESSAGE,
                                    row.directory_identity)


# -- provenance-based container-domain reconciliation -------------------------
#
# One domain has independent provenance in this repository's own committed
# historical reader: core/wechat_db.py reads os.path.join("emoticon",
# "emoticon.db") from a directory it documents as the WeChat db_storage root,
# for a sticker md5 -> CDN lookup table. No v2 production path references it.
# That is a root-directory fact, not a basename fact, so it is the only shape
# that may become a domain boundary entry. Everything else stays ambiguous.

BOUNDARY_REQUIRED_MESSAGE = "required_message"
BOUNDARY_REQUIRED_IDENTITY = "required_identity"
BOUNDARY_KNOWN_PHYSICAL_ONLY = "known_physical_only"
BOUNDARY_AMBIGUOUS = "ambiguous"


def test_domain_boundary_vocabulary_is_closed_and_default_is_ambiguous():
    assert container.CONTAINER_DOMAIN_CLASSES == frozenset({
        BOUNDARY_REQUIRED_MESSAGE,
        BOUNDARY_REQUIRED_IDENTITY,
        BOUNDARY_KNOWN_PHYSICAL_ONLY,
        BOUNDARY_AMBIGUOUS,
    })
    # No inference helper exists: only the closed mapping decides.
    assert not hasattr(container, "looks_like_domain")


@pytest.mark.parametrize("directory,expected", [
    ("message", BOUNDARY_REQUIRED_MESSAGE),
    ("session", BOUNDARY_REQUIRED_IDENTITY),
    ("contact", BOUNDARY_REQUIRED_IDENTITY),
    ("emoticon", BOUNDARY_KNOWN_PHYSICAL_ONLY),
    ("anything_else", BOUNDARY_AMBIGUOUS),
    ("extra_domain", BOUNDARY_AMBIGUOUS),
    ("", BOUNDARY_AMBIGUOUS),
])
def test_directory_boundary_class_comes_only_from_the_closed_policy(directory, expected):
    classification = container._NAMED_DIRECTORIES.get(directory, LOCATION_OTHER_DIRECTORY)
    assert container.domain_boundary_class(directory, classification) == expected


# required domains: visible and blocking

@pytest.mark.parametrize("name,role,gap", [
    ("neutral_store.db", ROLE_UNKNOWN, GAP_UNKNOWN_DATABASE),
    ("message_future.db", ROLE_UNSUPPORTED_MESSAGE_CANDIDATE, GAP_UNSUPPORTED_MESSAGE_CANDIDATE),
])
def test_required_message_domain_unknown_and_candidate_stay_visible_and_blocking(tmp_path, name, role, gap):
    accounting = account_container(_root(tmp_path, message=("message_0.db", name)))

    assert accounting.role_counts[role] == 1
    assert gap in accounting.gaps
    assert unmet_requirements(accounting) == (gap,)
    assert not accounting.meets_requirements()


def test_required_message_domain_ordinary_shard_satisfies_the_ordinary_role(tmp_path):
    accounting = account_container(_root(tmp_path))

    assert accounting.role_counts[ROLE_ORDINARY_MESSAGE] == 2
    assert accounting.meets_requirements()


def test_identity_anchors_satisfy_only_their_own_required_identities(tmp_path):
    accounting = account_container(_root(tmp_path))

    assert accounting.role_counts[ROLE_SESSION_IDENTITY] == 1
    assert accounting.role_counts[ROLE_CONTACT_IDENTITY] == 1
    assert ROLE_SESSION_IDENTITY not in {
        row.role for row in accounting.databases
        if row.location != LOCATION_SESSION_DIRECTORY}
    assert ROLE_CONTACT_IDENTITY not in {
        row.role for row in accounting.databases
        if row.location != LOCATION_CONTACT_DIRECTORY}


def test_a_misplaced_ordinary_shard_cannot_satisfy_the_ordinary_role(tmp_path):
    root = _root(tmp_path, message=())
    (root / "session" / "message_8.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert accounting.role_counts[ROLE_ORDINARY_MESSAGE] == 0
    assert GAP_UNSUPPORTED_MESSAGE_CANDIDATE in accounting.gaps
    assert REQUIRED_ROLE_MISSING in unmet_requirements(accounting)


# proven physical-only domain: accounted and visible, never blocking

def test_proven_physical_only_domain_accounts_and_keeps_an_unknown_visible(tmp_path):
    root = _root(tmp_path, directories=("emoticon",))
    placeholder = root / "emoticon" / "a.db"
    placeholder.unlink()
    placeholder.with_name("whatever_this_is.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    # Visible, and still exactly the honest role. Not relabelled, not supported.
    assert accounting.role_counts[ROLE_UNKNOWN] == 1
    # Accounted exactly once.
    assert accounting.accounts_for([(row.directory_identity, row.name)
                                    for row in accounting.databases])
    # Visible and honestly unknown, but a proven domain is not a proven store: an
    # unrecognised basename keeps ROLE_UNKNOWN and still blocks.
    assert unmet_requirements(accounting) == (GAP_UNKNOWN_DATABASE,)
    assert not accounting.meets_requirements()
    # But the role-level observation is still reported, so nothing is hidden.
    assert accounting.gaps


def test_proven_physical_only_domain_cannot_supply_any_required_truth(tmp_path):
    root = _root(tmp_path, message=(), directories=("emoticon",))
    (root / "emoticon" / "message_0.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert accounting.role_counts[ROLE_ORDINARY_MESSAGE] == 0
    assert accounting.role_counts[ROLE_UNSUPPORTED_MESSAGE_CANDIDATE] == 1
    assert REQUIRED_ROLE_MISSING in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("directory,location,role", [
    ("emoticon", LOCATION_OTHER_DIRECTORY, ROLE_SESSION_IDENTITY),
    ("emoticon", LOCATION_OTHER_DIRECTORY, ROLE_CONTACT_IDENTITY),
    ("", LOCATION_ROOT, ROLE_SESSION_IDENTITY),
    ("", LOCATION_ROOT, ROLE_CONTACT_IDENTITY),
    ("some_domain", LOCATION_OTHER_DIRECTORY, ROLE_SESSION_IDENTITY),
    ("message", LOCATION_MESSAGE_DIRECTORY, ROLE_SESSION_IDENTITY),
])
def test_exported_database_api_cannot_inject_a_required_identity_outside_its_domain(
        directory, location, role):
    # Enumeration routes anchors correctly, but the exported dataclass is also a
    # construction path. Without a location guard a caller -- or a future
    # miscounting caller inside this module -- could satisfy a required identity
    # from a proven physical-only domain or from the ambiguous root.
    with pytest.raises(ValueError, match='^container database invalid$'):
        container.ContainerDatabase(location, "anchor.db", role, directory)


def test_proven_physical_only_nested_directory_is_visible_and_blocking(tmp_path):
    root = _root(tmp_path, directories=("emoticon",))
    nested = root / "emoticon" / "shards"
    nested.mkdir(parents=True)
    (nested / "whatever.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert container.ContainerRejection(LOCATION_OTHER_DIRECTORY, "shards",
                                       container.NESTED_DIRECTORY, "emoticon") in accounting.rejections
    # Visible as physical structure, and no recursion was implied.
    assert accounting.evidence()["rejections"].count(container.NESTED_DIRECTORY) == 1
    # An unentered subtree is not proven by its parent's provenance.
    assert DIRECTORY_UNEXAMINED in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


def test_an_unreadable_physical_only_directory_still_fails_closed(tmp_path):
    root = _root(tmp_path, directories=("emoticon",))
    (root / "emoticon").chmod(0o000)
    try:
        accounting = account_container(root)
    finally:
        (root / "emoticon").chmod(0o700)

    assert DIRECTORY_UNEXAMINED in unmet_requirements(accounting)
    summary = accounting.evidence()["domain_summary"]
    # Reported as its own thing: an unlisted directory is not the same claim as a
    # listed one holding a structure that was not entered.
    assert summary[BOUNDARY_KNOWN_PHYSICAL_ONLY]["unreadable_count"] == 1
    assert summary[BOUNDARY_KNOWN_PHYSICAL_ONLY]["nested_unexamined_count"] == 0


# ambiguous default: fail-closed everywhere

@pytest.mark.parametrize("name,gap", [
    ("neutral_store.db", GAP_UNKNOWN_DATABASE),
    ("message_future.db", GAP_UNSUPPORTED_MESSAGE_CANDIDATE),
])
def test_ambiguous_domain_unknown_and_candidate_are_blockers(tmp_path, name, gap):
    root = _root(tmp_path)
    (root / "some_domain").mkdir()
    (root / "some_domain" / name).write_bytes(b"synthetic")

    accounting = account_container(root)

    assert gap in accounting.gaps
    assert unmet_requirements(accounting) == (gap,)
    assert not accounting.meets_requirements()


def test_ambiguous_domain_nested_directory_is_a_blocker(tmp_path):
    root = _root(tmp_path)
    (root / "some_domain" / "deeper").mkdir(parents=True)

    accounting = account_container(root)

    assert DIRECTORY_UNEXAMINED in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


# the two axes cannot be collapsed into each other

def test_a_recognised_auxiliary_basename_does_not_promote_an_ambiguous_parent(tmp_path):
    # sns.db is a proven auxiliary *basename* with no proven parent domain.
    root = _root(tmp_path)
    (root / "some_domain").mkdir()
    (root / "some_domain" / "sns.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert accounting.role_counts[ROLE_AUXILIARY] == 1
    assert container.domain_boundary_class(
        "some_domain", LOCATION_OTHER_DIRECTORY) == BOUNDARY_AMBIGUOUS
    assert accounting.meets_requirements()


def test_a_recognised_auxiliary_basename_does_not_excuse_an_unknown_beside_it(tmp_path):
    root = _root(tmp_path)
    (root / "some_domain").mkdir()
    (root / "some_domain" / "sns.db").write_bytes(b"synthetic")
    (root / "some_domain" / "neutral_store.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert unmet_requirements(accounting) == (GAP_UNKNOWN_DATABASE,)


@pytest.mark.parametrize("spoofed", [
    "emoticon2", "emoticon_", "emoticons", "Emoticon", "xemoticon", "emot",
])
def test_a_similar_looking_directory_name_stays_ambiguous(tmp_path, spoofed):
    root = _root(tmp_path)
    (root / spoofed).mkdir()
    (root / spoofed / "neutral_store.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert container.domain_boundary_class(
        spoofed, LOCATION_OTHER_DIRECTORY) == BOUNDARY_AMBIGUOUS
    assert unmet_requirements(accounting) == (GAP_UNKNOWN_DATABASE,)
    assert not accounting.meets_requirements()


def test_the_policy_mapping_is_exact_matching_only():
    assert container.KNOWN_PHYSICAL_ONLY_DIRECTORIES == frozenset(
        container._PHYSICAL_ONLY_DOMAIN_NAMES)


# evidence rendering: aggregate by boundary class, never a name

def test_evidence_aggregates_by_boundary_class_without_leaking_names(tmp_path):
    root = _root(tmp_path, message=("message_0.db", "biz_message_0.db"),
                 directories=("emoticon",), root_files=("stray.db",))
    (root / "some_domain").mkdir()
    (root / "some_domain" / "message_future.db").write_bytes(b"synthetic")
    (root / "emoticon" / "deeper").mkdir()

    evidence = account_container(root).evidence()

    summary = evidence["domain_summary"]
    assert set(summary) == {
        BOUNDARY_REQUIRED_MESSAGE, BOUNDARY_REQUIRED_IDENTITY,
        BOUNDARY_KNOWN_PHYSICAL_ONLY, BOUNDARY_AMBIGUOUS}
    assert summary[BOUNDARY_KNOWN_PHYSICAL_ONLY]["database_count"] == 1
    assert summary[BOUNDARY_KNOWN_PHYSICAL_ONLY]["unknown_count"] == 1
    assert summary[BOUNDARY_KNOWN_PHYSICAL_ONLY]["nested_unexamined_count"] == 1
    assert summary[BOUNDARY_AMBIGUOUS]["database_count"] == 2
    assert summary[BOUNDARY_AMBIGUOUS]["candidate_count"] == 1
    assert summary[BOUNDARY_REQUIRED_MESSAGE]["database_count"] == 2
    assert summary[BOUNDARY_REQUIRED_IDENTITY]["database_count"] == 2
    # Optional/excluded capability stays visible on the role axis.
    assert summary[BOUNDARY_REQUIRED_MESSAGE]["role_counts"].get(ROLE_BUSINESS_MESSAGE)
    rendered = repr(evidence)
    for leak in ("emoticon", "some_domain", "deeper", "stray", "message_future", "/"):
        assert leak not in rendered


# a refusal is classified, not just dropped: it must not smuggle in a condition
# that is outside the closed requirement vocabulary

def test_a_refused_name_can_never_add_a_condition_outside_the_closed_vocabulary(tmp_path):
    # D-040 excludes business message from required truth. A business-shaped name
    # that was refused (a symlink is never opened) is still business-shaped and
    # still excluded -- so it may appear in gaps but never in unmet requirements.
    root = _root(tmp_path)
    (root / "message" / "biz_message_0.db").symlink_to(root / "message" / "message_0.db")

    accounting = account_container(root)

    assert (container.ContainerRejection(
        LOCATION_MESSAGE_DIRECTORY, "biz_message_0.db", REJECTED_NOT_REGULAR_FILE,
        "message") in accounting.rejections)
    assert GAP_BUSINESS_MESSAGE_UNREAD in accounting.gaps
    assert GAP_BUSINESS_MESSAGE_UNREAD not in CONTAINER_REQUIREMENTS
    assert unmet_requirements(accounting) == ()
    assert accounting.meets_requirements()


def test_a_refused_non_regular_entry_inside_a_proven_domain_still_blocks(tmp_path):
    # A refusal is not a role. `sns.db` is auxiliary as a *name*, but an entry
    # under an other-directory carries no proven store row, and the exemption is
    # keyed on a regular file's exact name -- a symlink is never one. So this
    # stays ROLE_UNKNOWN and blocks: the cheapest false-pass path the store
    # predicate has to refuse.
    root = _root(tmp_path, directories=("emoticon",))
    (root / "emoticon" / "sns.db").symlink_to(root / "emoticon")

    accounting = account_container(root)

    assert accounting.rejections
    assert GAP_UNKNOWN_DATABASE in accounting.gaps
    assert unmet_requirements(accounting) == (GAP_UNKNOWN_DATABASE,)
    assert not accounting.meets_requirements()


# -- Workstream A: identity truth is anchor-scoped, not directory-scoped ------
#
# Production opens session/session.db and contact/contact.db by exact name and
# never enumerates their siblings (bridge/acquired_database_source.py,
# acquisition/source_refresher.py, wechatdb/provider/identity_catalog.py). So the
# required identity claim is the anchor, not the whole parent directory. These
# tests hold that line: an unknown sibling beside a proven anchor is visible and
# unsupported but outside the identity claim, while every way of failing to
# *prove* the anchor keeps blocking.

@pytest.mark.parametrize("directory", ["session", "contact"])
def test_an_unknown_sibling_beside_a_proven_anchor_is_visible_but_not_a_blocker(tmp_path, directory):
    root = _root(tmp_path)
    (root / directory / "neutral_store.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    # Visible and honestly labelled: nothing is relabelled or dropped.
    assert accounting.role_counts[ROLE_UNKNOWN] == 1
    assert GAP_UNKNOWN_DATABASE in accounting.gaps
    # The anchor alone carries the identity role, and it is still proven.
    assert accounting.role_counts[ROLE_SESSION_IDENTITY] == 1
    assert accounting.role_counts[ROLE_CONTACT_IDENTITY] == 1
    # But an unrecognised basename beside the anchor does not make the Reader's
    # identity claim untrue.
    assert unmet_requirements(accounting) == ()
    assert accounting.evidence()["unmet_requirements"] == ()
    assert accounting.meets_requirements()


@pytest.mark.parametrize("directory", ["session", "contact"])
@pytest.mark.parametrize("nested_names", [
    ("neutral_store.db",),
    ("message_0.db", "message_1.db", "biz_message_9.db"),
])
def test_a_sibling_directory_inside_an_identity_parent_still_blocks(tmp_path, directory, nested_names):
    # Anchor scope only covers what sits *beside* the anchor and is a regular
    # file the listing already named. A directory is a structure this accounting
    # never entered, so it cannot be shown to hold no message-bearing risk -- and
    # claiming otherwise would let session/archive/message_*.db pass unexamined.
    root = _root(tmp_path)
    nested = root / directory / "deeper"
    nested.mkdir(parents=True)
    for name in nested_names:
        (nested / name).write_bytes(b"synthetic")

    accounting = account_container(root)

    # Visible as unentered physical structure; no recursion was implied.
    assert accounting.evidence()["rejections"].count(container.NESTED_DIRECTORY) == 1
    assert DIRECTORY_UNEXAMINED in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("directory", ["session", "contact"])
def test_a_refused_unknown_sibling_beside_a_proven_anchor_still_blocks(tmp_path, directory):
    # The exemption is for an *unknown regular-file* sibling: one this listing
    # named and classified. A refused entry is not that, so it stays blocking.
    root = _root(tmp_path)
    sibling = root / directory / "neutral_store.db"
    sibling.symlink_to(root / "message" / "message_0.db")

    accounting = account_container(root)

    assert GAP_UNKNOWN_DATABASE in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("directory", ["session", "contact"])
def test_identity_anchor_scope_still_fails_when_the_exact_anchor_is_missing(tmp_path, directory):
    root = _root(tmp_path)
    (root / directory / f"{directory}.db").unlink()
    (root / directory / "neutral_store.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert accounting.role_counts[ROLE_UNKNOWN] == 1
    assert REQUIRED_ROLE_MISSING in unmet_requirements(accounting)
    assert REQUIRED_ROLE_UNCLASSIFIED in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("directory", ["session", "contact"])
def test_identity_anchor_scope_still_fails_when_the_exact_anchor_is_a_symlink(tmp_path, directory):
    root = _root(tmp_path)
    anchor = root / directory / f"{directory}.db"
    anchor.unlink()
    anchor.symlink_to(root / "message" / "message_0.db")

    accounting = account_container(root)

    assert REQUIRED_ROLE_MISSING in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("directory", ["session", "contact"])
def test_identity_anchor_scope_still_fails_when_the_exact_anchor_is_a_directory(tmp_path, directory):
    root = _root(tmp_path)
    anchor = root / directory / f"{directory}.db"
    anchor.unlink()
    anchor.mkdir()

    accounting = account_container(root)

    assert REQUIRED_ROLE_MISSING in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("directory", ["session", "contact"])
def test_identity_anchor_scope_still_fails_when_the_parent_cannot_be_listed(tmp_path, directory):
    root = _root(tmp_path)
    (root / directory).chmod(0o000)
    try:
        accounting = account_container(root)
    finally:
        (root / directory).chmod(0o700)

    assert DIRECTORY_UNEXAMINED in unmet_requirements(accounting)
    assert REQUIRED_ROLE_MISSING in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("directory", ["session", "contact"])
@pytest.mark.parametrize("name", ["message_future.db", "message_8.db"])
def test_identity_anchor_scope_does_not_excuse_a_message_bearing_candidate(tmp_path, directory, name):
    # A sibling whose own shape says it may carry ordinary message truth is a
    # cross-domain message risk. D-040 keeps it an explicit gap, so it blocks.
    root = _root(tmp_path)
    (root / directory / name).write_bytes(b"synthetic")

    accounting = account_container(root)

    assert accounting.role_counts[ROLE_UNSUPPORTED_MESSAGE_CANDIDATE] == 1
    assert GAP_UNSUPPORTED_MESSAGE_CANDIDATE in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


def test_a_misplaced_ordinary_shard_beside_a_valid_anchor_stays_a_candidate(tmp_path):
    root = _root(tmp_path)
    (root / "session" / "message_8.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert accounting.role_counts[ROLE_UNSUPPORTED_MESSAGE_CANDIDATE] == 1
    assert GAP_UNSUPPORTED_MESSAGE_CANDIDATE in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


def test_a_misplaced_identity_basename_never_satisfies_an_identity(tmp_path):
    root = _root(tmp_path)
    (root / "session" / "contact.db").write_bytes(b"synthetic")
    (root / "contact" / "session.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert accounting.role_counts[ROLE_SESSION_IDENTITY] == 1
    assert accounting.role_counts[ROLE_CONTACT_IDENTITY] == 1
    assert accounting.role_counts[ROLE_AUXILIARY] == 2
    assert accounting.meets_requirements()


def test_identity_anchor_scope_leaves_the_message_domain_fail_closed(tmp_path):
    # Anchor scope is about identity only. message/ keeps its whole-directory
    # claim: an unknown beside the shards is still a completeness blocker.
    root = _root(tmp_path)
    (root / "message" / "neutral_store.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert unmet_requirements(accounting) == (GAP_UNKNOWN_DATABASE,)
    assert not accounting.meets_requirements()


# -- Workstream B: root domains and message shapes with public provenance ----
#
# A domain entry needs BOTH (1) the exact root-relative first path component under
# db_storage in at least two independent public sources, and (2) at least two
# sources that characterise its contents as non-message feature data, with no
# source describing it as holding chat/message rows. Ledger and revisions: the A10
# classification doc, sections 17.3 and 18.
#
# chatbot fails (2): the sources describe chatbot *messages*. general fails (2):
# its documented tables hold message-event records (recalled-message content,
# red-envelope and transfer rows tied to a message id, friend-request content).
# solitaire fails (2): only one source characterises its contents; the other gives
# a feature name, not a description of the rows. All three stay ambiguous.

PROVEN_PHYSICAL_ONLY = frozenset({
    "emoticon", "sns", "favorite", "head_image", "hardlink", "bizchat",
    "third_app_icon",
})
NEW_PHYSICAL_ONLY = sorted(PROVEN_PHYSICAL_ONLY - {"emoticon"})
STILL_AMBIGUOUS = ("chatbot", "solitaire", "general", "weclaw", "MMKV", "unproven_store")


def test_the_proven_physical_only_set_is_exactly_the_ledger():
    assert container.KNOWN_PHYSICAL_ONLY_DIRECTORIES == PROVEN_PHYSICAL_ONLY


@pytest.mark.parametrize("domain", NEW_PHYSICAL_ONLY)
def test_a_proven_domain_direct_regular_unknown_is_visible_and_still_blocks(tmp_path, domain):
    root = _root(tmp_path, directories=(domain,))
    placeholder = root / domain / "a.db"
    placeholder.unlink()
    placeholder.with_name("neutral_store.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert container.domain_boundary_class(
        domain, LOCATION_OTHER_DIRECTORY) == BOUNDARY_KNOWN_PHYSICAL_ONLY
    # Still exactly the honest role: not relabelled, not supported, not dropped.
    assert accounting.role_counts[ROLE_UNKNOWN] == 1
    assert GAP_UNKNOWN_DATABASE in accounting.gaps
    assert accounting.accounts_for([(row.directory_identity, row.name)
                                    for row in accounting.databases])
    # It can never become required truth from a physical-only domain.
    assert accounting.role_counts[ROLE_SESSION_IDENTITY] == 1
    assert accounting.role_counts[ROLE_ORDINARY_MESSAGE] == 2
    assert unmet_requirements(accounting) == (GAP_UNKNOWN_DATABASE,)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("domain", NEW_PHYSICAL_ONLY)
def test_a_proven_domain_cannot_supply_required_truth_or_coverage(tmp_path, domain):
    root = _root(tmp_path, message=(), session=False, contact=False, directories=(domain,))
    (root / domain / "message_0.db").write_bytes(b"synthetic")
    (root / domain / "session.db").write_bytes(b"synthetic")
    (root / domain / "contact.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert accounting.role_counts[ROLE_ORDINARY_MESSAGE] == 0
    assert accounting.role_counts[ROLE_SESSION_IDENTITY] == 0
    assert accounting.role_counts[ROLE_CONTACT_IDENTITY] == 0
    assert REQUIRED_ROLE_MISSING in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("domain", NEW_PHYSICAL_ONLY)
def test_an_unreadable_proven_domain_still_fails_closed(tmp_path, domain):
    root = _root(tmp_path, directories=(domain,))
    (root / domain).chmod(0o000)
    try:
        accounting = account_container(root)
    finally:
        (root / domain).chmod(0o700)

    assert DIRECTORY_UNEXAMINED in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("domain", NEW_PHYSICAL_ONLY)
def test_a_proven_domain_cannot_inject_a_required_identity(domain):
    for role in (ROLE_SESSION_IDENTITY, ROLE_CONTACT_IDENTITY):
        with pytest.raises(ValueError, match='^container database invalid$'):
            container.ContainerDatabase(LOCATION_OTHER_DIRECTORY, "anchor.db", role, domain)


@pytest.mark.parametrize("domain", STILL_AMBIGUOUS)
def test_a_domain_without_sufficient_provenance_stays_ambiguous_and_blocking(tmp_path, domain):
    root = _root(tmp_path)
    (root / domain).mkdir()
    (root / domain / "neutral_store.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert container.domain_boundary_class(
        domain, LOCATION_OTHER_DIRECTORY) == BOUNDARY_AMBIGUOUS
    assert unmet_requirements(accounting) == (GAP_UNKNOWN_DATABASE,)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("spoofed", [
    "Favorite", "FAVORITE", "favorite2", "favorites", "favorite_", "xfavorite", "fav",
    "head_images", "headimage", "Head_Image", "hardlink_0", "hardlinks",
    "bizchat2", "BizChat", "biz", "third_app_icons", "third_app", "SNS", "sns_",
])
def test_a_similar_looking_proven_domain_name_stays_ambiguous(tmp_path, spoofed):
    root = _root(tmp_path)
    (root / spoofed).mkdir()
    (root / spoofed / "neutral_store.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert container.domain_boundary_class(
        spoofed, LOCATION_OTHER_DIRECTORY) == BOUNDARY_AMBIGUOUS
    assert unmet_requirements(accounting) == (GAP_UNKNOWN_DATABASE,)
    assert not accounting.meets_requirements()


def test_a_proven_domain_name_is_not_proven_at_the_container_root_or_as_a_basename(tmp_path):
    # A root-level database named like a domain, and a basename inside another
    # domain, are not the proven directory.
    root = _root(tmp_path, root_files=("favorite.db", "general.db"))
    (root / "some_domain").mkdir()
    (root / "some_domain" / "favorite.db").write_bytes(b"synthetic")
    (root / "some_domain" / "neutral_store.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert GAP_UNKNOWN_DATABASE in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


def test_the_contact_search_index_beside_the_anchor_is_visible_and_non_blocking(tmp_path):
    # contact/contact_fts.db is a publicly documented identity-parent sibling. It
    # needs no role of its own: anchor scope already leaves it visible, unsupported
    # and outside the identity claim, and it can never satisfy a required role.
    root = _root(tmp_path)
    (root / "contact" / "contact_fts.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert accounting.role_counts[ROLE_CONTACT_IDENTITY] == 1
    assert accounting.role_counts[ROLE_UNKNOWN] == 1
    assert unmet_requirements(accounting) == ()


# message-directory shapes: message_resource.db is attachment/resource
# metadata in both independent sources, so it is media -- not an unread
# message-shaped candidate, and never ordinary message truth.

@pytest.mark.parametrize("name,role", [
    ("media.db", ROLE_MEDIA),
    ("media_0.db", ROLE_MEDIA),
    ("message_fts.db", ROLE_SEARCH_INDEX),
    ("biz_message_0.db", ROLE_BUSINESS_MESSAGE),
    ("message_0.db", ROLE_ORDINARY_MESSAGE),
])
def test_known_message_directory_shapes_keep_their_role(name, role):
    assert classify_database_name(name) == role


def test_message_resource_is_recognised_media_and_adds_no_message_truth(tmp_path):
    root = _root(tmp_path, message=("message_0.db", "message_resource.db"))

    accounting = account_container(root)

    assert accounting.role_counts[ROLE_MEDIA] == 1
    assert accounting.role_counts[ROLE_ORDINARY_MESSAGE] == 1
    assert accounting.gaps == ()
    assert unmet_requirements(accounting) == ()
    assert accounting.meets_requirements()


def test_a_weclaw_shaped_name_stays_unknown(tmp_path):
    # One source calls it WeChat internal state with no usable content tables.
    # Uncertain semantics stay unknown: mapping it to auxiliary merely because it
    # is not a message store would be the inference this policy refuses.
    root = _root(tmp_path, message=("message_0.db", "weclaw.db"))

    accounting = account_container(root)

    assert accounting.role_counts[ROLE_UNKNOWN] == 1
    assert unmet_requirements(accounting) == (GAP_UNKNOWN_DATABASE,)
    assert not accounting.meets_requirements()


# -- review corrections: the exemptions must be exactly as wide as the evidence

@pytest.mark.parametrize("place", ["message", "session", "contact", None])
def test_the_resource_shape_is_recognised_only_inside_the_message_directory(tmp_path, place):
    # Both cited sources document message/message_resource.db. Nothing documents
    # that name in the container root or an identity parent, so there the honest
    # reading is still message-shaped risk beside or under an anchor.
    root = _root(tmp_path)
    target = root if place is None else root / place
    (target / "message_resource.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    if place == "message":
        assert accounting.role_counts[ROLE_MEDIA] == 1
        assert accounting.meets_requirements()
    else:
        assert accounting.role_counts[ROLE_MEDIA] == 0
        assert GAP_UNSUPPORTED_MESSAGE_CANDIDATE in unmet_requirements(accounting)
        assert not accounting.meets_requirements()


@pytest.mark.parametrize("directory", ["session", "contact", "extra_domain"])
def test_a_hidden_directory_holding_shards_still_blocks(tmp_path, directory):
    # A dot prefix is how sidecar noise is excluded, but it must not let an
    # unentered directory pass: nothing inside it was looked at either way.
    root = _root(tmp_path, directories=(directory,) if directory == "extra_domain" else ())
    nested = root / directory / ".archive"
    nested.mkdir(parents=True)
    (nested / "message_0.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert accounting.evidence()["rejections"].count(container.NESTED_DIRECTORY) == 1
    assert DIRECTORY_UNEXAMINED in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


def test_a_hidden_anchor_is_reported_missing_rather_than_silently_absent(tmp_path):
    root = _root(tmp_path)
    (root / "session" / "session.db").unlink()
    (root / "session" / ".session.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert REQUIRED_ROLE_MISSING in unmet_requirements(accounting)
    assert REQUIRED_ROLE_UNCLASSIFIED in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("name", [".archive", ".message", ".session"])
def test_a_hidden_directory_at_the_root_is_not_skipped(tmp_path, name):
    # The root is the strictest location -- its boundary class is ambiguous --
    # so a hidden directory there cannot be the one place an unentered subtree
    # goes unaccounted. It is accounted like any other root domain, so its
    # message-shaped child is classified and blocks. A hidden *file* is ignored.
    root = _root(tmp_path)
    nested = root / name
    nested.mkdir()
    (nested / "message_0.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert name in {row.identity for row in accounting.examined_directories}
    assert accounting.role_counts[ROLE_UNSUPPORTED_MESSAGE_CANDIDATE] == 1
    assert GAP_UNSUPPORTED_MESSAGE_CANDIDATE in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("where", ["root", "session", "message"])
def test_a_symlink_named_like_nothing_in_particular_is_still_refused(tmp_path, where):
    # A symlink is neither a plain file nor a real directory. Ignoring it would
    # let it alias message-bearing content under a name this pass never reads.
    root = _root(tmp_path)
    target = root / "message" if where == "root" else root
    (target / "link").symlink_to(root / "message")

    accounting = account_container(root)

    assert REJECTED_NOT_REGULAR_FILE in accounting.evidence()["rejections"]
    assert GAP_UNKNOWN_DATABASE in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("where", ["root", "session"])
def test_a_hidden_sidecar_file_is_still_ignored(tmp_path, where):
    root = _root(tmp_path)
    target = root if where == "root" else root / "session"
    (target / ".DS_Store").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert accounting.evidence()["rejections"] == ()
    assert accounting.meets_requirements()


@pytest.mark.parametrize("where", ["root", "session", "message"])
def test_a_hidden_symlink_to_a_directory_is_still_refused(tmp_path, where):
    # Hidden is not the same as sidecar. A dot-prefixed *file* is noise, but a
    # dot-prefixed symlink can still alias message content under a name this
    # pass never classifies, so hiding it must not buy it a pass.
    root = _root(tmp_path)
    target = root if where == "root" else root / where
    (target / ".link").symlink_to(root / "message")

    accounting = account_container(root)

    assert REJECTED_NOT_REGULAR_FILE in accounting.evidence()["rejections"]
    assert GAP_UNKNOWN_DATABASE in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


# -- aggregate blocking evidence: derived from the one acceptance policy ------
#
# The next real gate must tell a visible-but-outside-the-claim observation from a
# blocking one without naming anything. Those counts are read from the same pass
# that decides acceptance; there is no second verdict implementation.

BLOCKING_KEYS = ("blocking_unknown_count", "blocking_candidate_count",
                 "blocking_nested_count", "blocking_unreadable_count")


def _blocking(summary, boundary):
    return {key: summary[boundary][key] for key in BLOCKING_KEYS}


def _no_blocking():
    return {key: 0 for key in BLOCKING_KEYS}


def test_an_unknown_beside_a_proven_anchor_is_counted_but_not_blocking(tmp_path):
    root = _root(tmp_path)
    (root / "session" / "neutral_store.db").write_bytes(b"synthetic")

    summary = account_container(root).evidence()["domain_summary"]

    assert summary[BOUNDARY_REQUIRED_IDENTITY]["unknown_count"] == 1
    assert _blocking(summary, BOUNDARY_REQUIRED_IDENTITY) == _no_blocking()


def test_an_unknown_in_an_identity_directory_without_its_anchor_is_blocking(tmp_path):
    root = _root(tmp_path, session=False)
    (root / "session").mkdir()
    (root / "session" / "neutral_store.db").write_bytes(b"synthetic")

    summary = account_container(root).evidence()["domain_summary"]

    assert _blocking(summary, BOUNDARY_REQUIRED_IDENTITY)["blocking_unknown_count"] == 1


def test_blocking_counts_split_by_condition_and_boundary_class(tmp_path):
    root = _root(tmp_path, message=("message_0.db", "neutral_store.db", "message_future.db"),
                 directories=("emoticon",))
    (root / "some_domain" / "deeper").mkdir(parents=True)
    (root / "some_domain" / "neutral_store.db").write_bytes(b"synthetic")
    (root / "emoticon" / "neutral_store.db").write_bytes(b"synthetic")
    (root / "emoticon" / "deeper").mkdir()
    (root / "contact" / "deeper").mkdir()

    summary = account_container(root).evidence()["domain_summary"]

    assert _blocking(summary, BOUNDARY_REQUIRED_MESSAGE) == {
        "blocking_unknown_count": 1, "blocking_candidate_count": 1,
        "blocking_nested_count": 0, "blocking_unreadable_count": 0}
    assert _blocking(summary, BOUNDARY_AMBIGUOUS) == {
        "blocking_unknown_count": 1, "blocking_candidate_count": 0,
        "blocking_nested_count": 1, "blocking_unreadable_count": 0}
    assert _blocking(summary, BOUNDARY_REQUIRED_IDENTITY) == {
        "blocking_unknown_count": 0, "blocking_candidate_count": 0,
        "blocking_nested_count": 1, "blocking_unreadable_count": 0}
    # a.db (the fixture's own) and neutral_store.db: direct regular-file unknowns,
    # counted and visible, and -- since neither basename is a proven store -- also
    # blocking. A proven domain is not a proven directory. The unentered nested
    # directory blocks too: parent provenance cannot prove an unentered subtree.
    assert _blocking(summary, BOUNDARY_KNOWN_PHYSICAL_ONLY) == {
        "blocking_unknown_count": 2, "blocking_candidate_count": 0,
        "blocking_nested_count": 1, "blocking_unreadable_count": 0}
    assert summary[BOUNDARY_KNOWN_PHYSICAL_ONLY]["unknown_count"] == 2
    assert summary[BOUNDARY_KNOWN_PHYSICAL_ONLY]["nested_unexamined_count"] == 1


def test_an_unreadable_directory_blocks_even_inside_a_physical_only_domain(tmp_path):
    root = _root(tmp_path, directories=("emoticon",))
    (root / "emoticon").chmod(0o000)
    try:
        summary = account_container(root).evidence()["domain_summary"]
    finally:
        (root / "emoticon").chmod(0o700)

    assert _blocking(summary, BOUNDARY_KNOWN_PHYSICAL_ONLY)["blocking_unreadable_count"] == 1


def test_a_refused_unknown_sibling_beside_an_anchor_is_a_blocking_unknown(tmp_path):
    root = _root(tmp_path)
    (root / "session" / "linked.db").symlink_to(root / "message" / "message_0.db")

    summary = account_container(root).evidence()["domain_summary"]

    assert _blocking(summary, BOUNDARY_REQUIRED_IDENTITY)["blocking_unknown_count"] == 1


@pytest.mark.parametrize("build", [
    lambda root: None,
    lambda root: (root / "message" / "neutral_store.db").write_bytes(b"x"),
    lambda root: (root / "message" / "message_future.db").write_bytes(b"x"),
    lambda root: (root / "session" / "neutral_store.db").write_bytes(b"x"),
    lambda root: (root / "contact" / "message_9.db").write_bytes(b"x"),
    lambda root: (root / "session" / "deeper").mkdir(),
    lambda root: (root / "some_domain").mkdir() or (root / "some_domain" / "x.db").write_bytes(b"x"),
    lambda root: (root / "favorite").mkdir() or (root / "favorite" / "x.db").write_bytes(b"x"),
    lambda root: (root / "favorite").mkdir() or (root / "favorite" / "deeper").mkdir(),
    lambda root: (root / "stray.db").write_bytes(b"x"),
    lambda root: (root / "message" / "biz_message_0.db").write_bytes(b"x"),
    lambda root: (root / "favorite").mkdir() or (root / "favorite" / "message_future.db").write_bytes(b"x"),
    lambda root: (root / "bizchat").mkdir() or (root / "bizchat" / "message_0.db").write_bytes(b"x"),
    lambda root: (root / "emoticon").mkdir() or (root / "emoticon" / "neutral_store.db").symlink_to(root / "message" / "message_0.db"),
    lambda root: (root / "emoticon").mkdir() or (root / "emoticon" / "message_future.db").symlink_to(root / "message" / "message_0.db"),
    lambda root: (root / "sns").mkdir() or (root / "sns" / ".hidden_dir").mkdir(),
    lambda root: (root / "sns").mkdir() or (root / "sns" / "neutral_store.db").write_bytes(b"x"),
])
def test_the_summary_blocking_counts_agree_with_the_single_acceptance_predicate(tmp_path, build):
    root = _root(tmp_path)
    build(root)

    accounting = account_container(root)
    summary = accounting.evidence()["domain_summary"]
    totals = {key: sum(summary[b][key] for b in summary) for key in BLOCKING_KEYS}
    derived = set()
    if totals["blocking_unknown_count"]:
        derived.add(GAP_UNKNOWN_DATABASE)
    if totals["blocking_candidate_count"]:
        derived.add(GAP_UNSUPPORTED_MESSAGE_CANDIDATE)
    if totals["blocking_nested_count"] or totals["blocking_unreadable_count"]:
        derived.add(DIRECTORY_UNEXAMINED)

    # The role-level conditions are not row observations; everything else is.
    row_conditions = set(unmet_requirements(accounting)) - {
        REQUIRED_ROLE_MISSING, REQUIRED_ROLE_UNCLASSIFIED}
    assert derived == row_conditions
    assert accounting.meets_requirements() == (unmet_requirements(accounting) == ())


# -- the physical-only exemption is one proven store wide ---------------------
#
# Domain knowledge and store knowledge are two different facts. A
# known_physical_only domain says only that this exact root directory is proven
# outside required message/identity truth; it does NOT say that any file below it
# is safe. A store exemption needs its own exact-name proof, so the exemption is
# keyed on (domain, basename) and a future or unproven name inside a proven
# domain keeps ROLE_UNKNOWN, stays visible and still blocks. Candidates, unentered
# nested directories, refused/non-regular entries and unreadable directories block
# regardless. Domain class and row role stay separate axes: nothing is relabelled.

NARROWING_DOMAINS = ["emoticon", "favorite", "bizchat"]


def _domain_root(tmp_path, domain):
    root = _root(tmp_path, directories=(domain,))
    (root / domain / "a.db").unlink()
    return root


@pytest.mark.parametrize("domain", sorted(PROVEN_STORE_NAMES))
def test_every_proven_store_is_non_blocking_in_its_proven_domain(tmp_path, domain):
    # The exemption is keyed on the store, not the directory: the exact
    # basename the provenance actually spells out is the only thing exempt.
    root = _domain_root(tmp_path, domain)
    for name in sorted(PROVEN_STORE_NAMES[domain]):
        (root / domain / name).write_bytes(b"synthetic")

    accounting = account_container(root)

    assert unmet_requirements(accounting) == ()
    assert accounting.meets_requirements()


@pytest.mark.parametrize("domain", sorted(PROVEN_STORE_NAMES))
def test_an_unproven_store_in_a_proven_domain_stays_blocking(tmp_path, domain):
    # THE key regression: a domain may be proven while the file inside it is not.
    # An unproven name keeps ROLE_UNKNOWN, stays visible, and still blocks.
    root = _domain_root(tmp_path, domain)
    (root / domain / "random_future.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert accounting.role_counts[ROLE_UNKNOWN] == 1
    assert GAP_UNKNOWN_DATABASE in accounting.gaps
    assert unmet_requirements(accounting) == (GAP_UNKNOWN_DATABASE,)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("domain,name", [
    (domain, name)
    for domain in sorted(PROVEN_STORE_NAMES)
    for name in ("favorite2.db", "Favorite.db", "favorite_1.db",
                 "emoticon2.db", "sns_0.db")
])
def test_a_look_alike_of_a_proven_store_is_not_exempt(tmp_path, domain, name):
    # Exact matching only. No prefix, no suffix, no case folding, no inferred
    # numeric variant -- unless a source spelled that shape itself.
    root = _domain_root(tmp_path, domain)
    (root / domain / name).write_bytes(b"synthetic")

    accounting = account_container(root)

    assert accounting.role_counts[ROLE_UNKNOWN] == 1
    assert unmet_requirements(accounting) == (GAP_UNKNOWN_DATABASE,)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("domain", sorted(PROVEN_STORE_NAMES))
def test_a_proven_store_name_cannot_supply_required_truth(tmp_path, domain):
    # Store exemption is a coverage decision, never a relabelling: a proven
    # physical-only store can never satisfy message or identity truth.
    root = _root(tmp_path, message=(), session=False, contact=False,
                 directories=(domain,))
    for name in sorted(PROVEN_STORE_NAMES[domain]):
        (root / domain / name).write_bytes(b"synthetic")

    accounting = account_container(root)

    assert accounting.role_counts[ROLE_ORDINARY_MESSAGE] == 0
    assert accounting.role_counts[ROLE_SESSION_IDENTITY] == 0
    assert accounting.role_counts[ROLE_CONTACT_IDENTITY] == 0
    assert REQUIRED_ROLE_MISSING in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("domain", NARROWING_DOMAINS)
def test_an_unproven_direct_regular_file_unknown_still_blocks_in_a_proven_domain(
        tmp_path, domain):
    root = _domain_root(tmp_path, domain)
    (root / domain / "neutral_store.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert accounting.role_counts[ROLE_UNKNOWN] == 1
    assert GAP_UNKNOWN_DATABASE in accounting.gaps
    assert unmet_requirements(accounting) == (GAP_UNKNOWN_DATABASE,)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("domain", NARROWING_DOMAINS)
@pytest.mark.parametrize("name", ["message_future.db", "message_0.db", "biz_message_x.db"])
def test_a_message_shaped_database_blocks_even_in_a_physical_only_domain(
        tmp_path, domain, name):
    root = _domain_root(tmp_path, domain)
    (root / domain / name).write_bytes(b"synthetic")

    accounting = account_container(root)

    # Still visible with its honest role, and never required truth.
    assert accounting.role_counts[ROLE_UNSUPPORTED_MESSAGE_CANDIDATE] == 1
    assert accounting.role_counts[ROLE_ORDINARY_MESSAGE] == 2
    assert GAP_UNSUPPORTED_MESSAGE_CANDIDATE in accounting.gaps
    assert unmet_requirements(accounting) == (GAP_UNSUPPORTED_MESSAGE_CANDIDATE,)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("domain", NARROWING_DOMAINS)
def test_a_nested_directory_blocks_even_in_a_physical_only_domain(tmp_path, domain):
    root = _domain_root(tmp_path, domain)
    (root / domain / "deeper").mkdir()

    accounting = account_container(root)

    assert container.ContainerRejection(LOCATION_OTHER_DIRECTORY, "deeper",
                                       container.NESTED_DIRECTORY, domain) in accounting.rejections
    assert unmet_requirements(accounting) == (DIRECTORY_UNEXAMINED,)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("domain", NARROWING_DOMAINS)
def test_a_hidden_nested_directory_blocks_even_in_a_physical_only_domain(tmp_path, domain):
    root = _domain_root(tmp_path, domain)
    (root / domain / ".archive").mkdir()

    assert unmet_requirements(account_container(root)) == (DIRECTORY_UNEXAMINED,)


@pytest.mark.parametrize("domain", NARROWING_DOMAINS)
def test_a_neutral_symlink_blocks_even_in_a_physical_only_domain(tmp_path, domain):
    root = _domain_root(tmp_path, domain)
    (root / domain / "neutral_store.db").symlink_to(root / "message" / "message_0.db")

    accounting = account_container(root)

    assert container.ContainerRejection(
        LOCATION_OTHER_DIRECTORY, "neutral_store.db", REJECTED_NOT_REGULAR_FILE,
        domain) in accounting.rejections
    assert unmet_requirements(accounting) == (GAP_UNKNOWN_DATABASE,)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("domain", NARROWING_DOMAINS)
def test_a_message_shaped_symlink_blocks_even_in_a_physical_only_domain(tmp_path, domain):
    root = _domain_root(tmp_path, domain)
    (root / domain / "message_future.db").symlink_to(root / "message" / "message_0.db")

    assert unmet_requirements(account_container(root)) == (GAP_UNSUPPORTED_MESSAGE_CANDIDATE,)


@pytest.mark.parametrize("domain", NARROWING_DOMAINS)
def test_a_symlink_to_a_directory_blocks_even_in_a_physical_only_domain(tmp_path, domain):
    root = _domain_root(tmp_path, domain)
    (root / domain / "alias").symlink_to(root / "message")

    assert GAP_UNKNOWN_DATABASE in unmet_requirements(account_container(root))


@pytest.mark.parametrize("domain", NARROWING_DOMAINS)
def test_a_recognised_non_message_symlink_name_adds_no_blocker_in_a_physical_only_domain(
        tmp_path, domain):
    # Corrected by Decision C: a refused name blocks in a proven physical-only
    # domain only when it is that domain's own proven store, and media.db is not
    # one. The generic media classification cannot launder an unproven store
    # here, so the refusal stays a blocking unknown (see the sibling test for
    # the same symlink outside a proven domain, which still adds no blocker).
    root = _domain_root(tmp_path, domain)
    (root / domain / "media.db").symlink_to(root / "message" / "message_0.db")

    assert GAP_UNKNOWN_DATABASE in unmet_requirements(account_container(root))


@pytest.mark.parametrize("directory", ["session", "extra_domain"])
def test_a_recognised_non_message_symlink_name_adds_no_blocker_outside_a_proven_domain(
        tmp_path, directory):
    # Outside a proven domain nothing changed: a refused media-shaped name
    # carries no message claim and blocks nothing.
    root = _root(tmp_path, directories=(directory,) if directory == "extra_domain" else ())
    (root / directory / "a.db").unlink(missing_ok=True)
    (root / directory / "media.db").symlink_to(root / "message" / "message_0.db")

    assert unmet_requirements(account_container(root)) == ()


@pytest.mark.parametrize("domain", NARROWING_DOMAINS)
def test_an_unreadable_physical_only_directory_still_blocks_unchanged(tmp_path, domain):
    root = _domain_root(tmp_path, domain)
    (root / domain).chmod(0o000)
    try:
        accounting = account_container(root)
    finally:
        (root / domain).chmod(0o700)

    assert DIRECTORY_UNEXAMINED in unmet_requirements(accounting)


@pytest.mark.parametrize("domain", sorted(PROVEN_PHYSICAL_ONLY))
def test_every_proven_domain_is_narrowed_the_same_way(tmp_path, domain):
    root = _domain_root(tmp_path, domain)
    for name in sorted(container.PROVEN_STORE_NAMES[domain]):
        (root / domain / name).write_bytes(b"synthetic")
    assert unmet_requirements(account_container(root)) == ()

    (root / domain / "message_future.db").write_bytes(b"synthetic")
    assert unmet_requirements(account_container(root)) == (GAP_UNSUPPORTED_MESSAGE_CANDIDATE,)

    (root / domain / "message_future.db").unlink()
    (root / domain / "random_future.db").write_bytes(b"synthetic")
    assert unmet_requirements(account_container(root)) == (GAP_UNKNOWN_DATABASE,)

    (root / domain / "random_future.db").unlink()
    (root / domain / "deeper").mkdir()
    assert unmet_requirements(account_container(root)) == (DIRECTORY_UNEXAMINED,)


def test_the_approved_domains_are_still_admitted_and_ambiguous_ones_are_not():
    for domain in ("emoticon", "sns", "favorite", "head_image", "hardlink", "bizchat",
                   "third_app_icon"):
        assert container.domain_boundary_class(
            domain, LOCATION_OTHER_DIRECTORY) == BOUNDARY_KNOWN_PHYSICAL_ONLY
    for domain in ("chatbot", "general", "solitaire"):
        assert container.domain_boundary_class(
            domain, LOCATION_OTHER_DIRECTORY) == BOUNDARY_AMBIGUOUS


def test_a_physical_only_domain_never_changes_a_row_role_or_satisfies_a_required_role(tmp_path):
    # Two axes: the exemption is a verdict decision, never a relabelling.
    root = _root(tmp_path, message=(), session=False, contact=False, directories=("favorite",))
    for name in ("message_0.db", "session.db", "contact.db", "neutral_store.db"):
        (root / "favorite" / name).write_bytes(b"synthetic")

    accounting = account_container(root)

    assert accounting.role_counts[ROLE_ORDINARY_MESSAGE] == 0
    assert accounting.role_counts[ROLE_SESSION_IDENTITY] == 0
    assert accounting.role_counts[ROLE_CONTACT_IDENTITY] == 0
    assert accounting.role_counts[ROLE_UNSUPPORTED_MESSAGE_CANDIDATE] == 1
    assert REQUIRED_ROLE_MISSING in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


# -- the two ways an unproven store used to escape a proven domain -------------
#
# The generic role ledger is location-free, so a basename proven *somewhere*
# (sns.db, chatbot.db, media.db) kept a non-unknown role inside a physical-only
# domain, and a look-alike that merely ends in ".db" was never selected as a
# candidate at all. Either way the file was accounted yet never blocked. Both
# are the same defect: inside a proven domain only its own PROVEN_STORE_NAMES
# stores are exempt, and everything else stays ROLE_UNKNOWN and blocking.

@pytest.mark.parametrize("name", [
    "sns.db", "chatbot.db", "media.db", "message_fts.db", "session.db",
])
def test_a_locally_proven_basename_in_an_unproven_store_blocks_in_a_proven_domain(
        tmp_path, name):
    # favorite/sns.db is not a proven favorite store. A basename the generic
    # ledger happens to recognise must not launder an unproven store here.
    root = _domain_root(tmp_path, "favorite")
    (root / "favorite" / name).write_bytes(b"synthetic")

    accounting = account_container(root)

    assert accounting.role_counts[ROLE_UNKNOWN] == 1
    assert GAP_UNKNOWN_DATABASE in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("name", [
    "favorite.db.bak", "favorite.db.wal", "favorite.db.shm",
    "favorite.db.backup", "favorite.db-wal.bak",
])
def test_a_non_db_look_alike_inside_a_proven_domain_is_accounted_and_blocks(
        tmp_path, name):
    # "*.db" is the candidate filter, so a backup of a proven store used to be
    # silently dropped: visible nowhere, blocking nowhere. Inside a proven
    # physical-only domain an unaccounted regular file is still a hole.
    root = _domain_root(tmp_path, "favorite")
    (root / "favorite" / name).write_bytes(b"synthetic")

    accounting = account_container(root)

    assert [row.name for row in accounting.databases
            if row.directory_identity == "favorite"] == [name]
    assert accounting.role_counts[ROLE_UNKNOWN] == 1
    assert GAP_UNKNOWN_DATABASE in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


# -- a standard SQLite sidecar is a companion, not an independent store -------
#
# The invariant is exact: an exact proven store basename plus "-wal", "-shm" or
# "-journal". The test this replaces built its name with
# "favorite.db".replace(".db", "-wal"), which yields "favorite-wal" -- a name
# SQLite never creates. It exercised a look-alike, not the companion case.


def test_the_real_sqlite_wal_companion_name_is_not_the_replace_look_alike():
    # Pin the naming convention itself so the substitution cannot quietly return
    # and let a look-alike test read as a companion test.
    assert "favorite.db".replace(".db", "-wal") == "favorite-wal"
    assert "favorite.db" + "-wal" == "favorite.db-wal"


@pytest.mark.parametrize("domain,store", sorted(
    (domain, store)
    for domain, stores in PROVEN_STORE_NAMES.items()
    for store in stores))
@pytest.mark.parametrize("suffix", _SIDECAR_SUFFIXES)
def test_an_exact_standard_sidecar_of_a_proven_store_is_a_companion(
        tmp_path, domain, store, suffix):
    root = _domain_root(tmp_path, domain)
    (root / domain / store).write_bytes(b"synthetic")
    (root / domain / (store + suffix)).write_bytes(b"synthetic")

    accounting = account_container(root)

    # Structurally visible and accounted exactly once, but never a database.
    assert [(row.directory_identity, row.name) for row in accounting.sidecars] \
        == [(domain, store + suffix)]
    assert [row.name for row in accounting.databases
            if row.directory_identity == domain] == [store]
    # The companion itself is not a database, so it contributes no role at all.
    # A proven store keeps whatever truthful role the generic ledger gave it
    # (sns.db is auxiliary, favorite.db is unknown-but-exempt), so the portable
    # observables are the row counts and the verdict.
    assert len(accounting.databases) == 5
    assert accounting.role_counts[ROLE_ORDINARY_MESSAGE] == 2
    assert accounting.role_counts[ROLE_SESSION_IDENTITY] == 1
    assert accounting.role_counts[ROLE_CONTACT_IDENTITY] == 1
    assert unmet_requirements(accounting) == ()
    assert accounting.meets_requirements()


@pytest.mark.parametrize("name", [
    "favorite.db.wal", "favorite.db.shm", "favorite.db.bak",
    "favorite.db.backup", "favorite-wal", "favorite-shm", "favorite-journal",
    "favorite.db-wal.bak", "favorite.db-wal-wal",
])
def test_a_sidecar_look_alike_of_a_proven_store_still_blocks(tmp_path, name):
    # Only the three exact suffixes on an exact proven basename are companions.
    # A dotted variant, a bare-name variant and an extra suffix all stay
    # unproven file content.
    root = _domain_root(tmp_path, "favorite")
    (root / "favorite" / name).write_bytes(b"synthetic")

    accounting = account_container(root)

    assert accounting.sidecars == ()
    assert [row.name for row in accounting.databases
            if row.directory_identity == "favorite"] == [name]
    assert accounting.role_counts[ROLE_UNKNOWN] == 1
    assert unmet_requirements(accounting) == (GAP_UNKNOWN_DATABASE,)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("name", [
    "Favorite.db-wal", "favorite_1.db-wal", "favorite_fts.DB-wal",
    "favorite.db-WAL", "FAVORITE.DB-wal", "sns.db-wal",
])
def test_a_look_alike_store_cannot_lend_its_name_to_a_companion(tmp_path, name):
    # Case, a numeric variant and a case-shifted suffix all fail exact matching.
    root = _domain_root(tmp_path, "favorite")
    (root / "favorite" / name).write_bytes(b"synthetic")

    accounting = account_container(root)

    assert accounting.sidecars == ()
    assert accounting.role_counts[ROLE_UNKNOWN] == 1
    assert unmet_requirements(accounting) == (GAP_UNKNOWN_DATABASE,)


@pytest.mark.parametrize("domain", sorted(PROVEN_STORE_NAMES))
def test_a_sidecar_of_an_unproven_store_inherits_no_exemption(tmp_path, domain):
    # favorite/future.db-wal must never become a way to smuggle an unproven
    # store past the store predicate. Both files stay visible and both block: the
    # companion rule is keyed on a *proven* basename, so an unproven one buys no
    # exemption for its own sidecar either.
    root = _domain_root(tmp_path, domain)
    (root / domain / "future.db").write_bytes(b"synthetic")
    (root / domain / "future.db-wal").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert accounting.sidecars == ()
    assert [row.name for row in accounting.databases
            if row.directory_identity == domain] == [
                "future.db", "future.db-wal"]
    assert accounting.role_counts[ROLE_UNKNOWN] == 2
    assert unmet_requirements(accounting) == (GAP_UNKNOWN_DATABASE,)
    assert not accounting.meets_requirements()


@pytest.mark.parametrize("domain", sorted(PROVEN_STORE_NAMES))
def test_a_companion_alone_cannot_satisfy_any_required_role(tmp_path, domain):
    root = _root(tmp_path, message=(), session=False, contact=False,
                 directories=(domain,))
    store = sorted(PROVEN_STORE_NAMES[domain])[0]
    (root / domain / (store + "-shm")).write_bytes(b"synthetic")

    accounting = account_container(root)

    assert accounting.role_counts[ROLE_ORDINARY_MESSAGE] == 0
    assert accounting.role_counts[ROLE_SESSION_IDENTITY] == 0
    assert accounting.role_counts[ROLE_CONTACT_IDENTITY] == 0
    assert REQUIRED_ROLE_MISSING in unmet_requirements(accounting)
    assert not accounting.meets_requirements()


def test_a_message_shaped_name_is_still_a_candidate_beside_a_companion(tmp_path):
    # The companion handling must not become a laundering path for the one
    # shape that always blocks.
    root = _domain_root(tmp_path, "favorite")
    (root / "favorite" / "favorite.db-wal").write_bytes(b"synthetic")
    (root / "favorite" / "message_0.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert accounting.role_counts[ROLE_UNSUPPORTED_MESSAGE_CANDIDATE] == 1
    assert unmet_requirements(accounting) == (GAP_UNSUPPORTED_MESSAGE_CANDIDATE,)
    assert not accounting.meets_requirements()


def test_companion_accounting_is_exactly_once_and_deterministic(tmp_path):
    root = _domain_root(tmp_path, "favorite")
    for name in ("favorite.db", "favorite.db-wal", "favorite.db-shm",
                 "favorite.db-journal"):
        (root / "favorite" / name).write_bytes(b"synthetic")

    first = account_container(root)
    second = account_container(root)

    keys = [(row.location, row.directory_identity, row.name)
            for row in first.databases + first.rejections + first.sidecars]
    assert len(set(keys)) == len(keys)
    assert first.sidecars == second.sidecars
    assert list(first.sidecars) == sorted(
        first.sidecars,
        key=lambda row: (row.location, row.directory_identity, row.name))
    # accounts_for is the total-claim helper, so it takes every accounted key.
    assert first.accounts_for((
        ("contact", "contact.db"),
        ("favorite", "favorite.db"), ("favorite", "favorite.db-journal"),
        ("favorite", "favorite.db-shm"), ("favorite", "favorite.db-wal"),
        ("message", "message_0.db"), ("message", "message_1.db"),
        ("session", "session.db")))
    assert first.meets_requirements()


def test_aggregate_evidence_counts_companions_without_naming_them(tmp_path):
    root = _domain_root(tmp_path, "favorite")
    for name in ("favorite.db", "favorite.db-wal", "favorite.db-shm",
                 "future.db"):
        (root / "favorite" / name).write_bytes(b"synthetic")

    accounting = account_container(root)
    rendered = repr(accounting.evidence())

    entry = accounting.domain_summary["known_physical_only"]
    assert entry["companion_count"] == 2
    assert all(other["companion_count"] == 0
               for boundary, other in accounting.domain_summary.items()
               if boundary != "known_physical_only")
    assert "favorite" not in rendered
    assert "future.db" not in rendered
    assert "-wal" not in rendered


@pytest.mark.parametrize("suffix", _SIDECAR_SUFFIXES)
def test_a_companion_of_a_message_shard_is_not_an_independent_entry(
        tmp_path, suffix):
    # Unchanged global model: outside a proven store domain a sidecar is
    # companion noise, so message/message_0.db-wal stays out of the database
    # inventory and blocks nothing.
    root = _root(tmp_path)
    (root / "message" / ("message_0.db" + suffix)).write_bytes(b"synthetic")

    accounting = account_container(root)

    assert [row.name for row in accounting.databases
            if row.location == LOCATION_MESSAGE_DIRECTORY] == [
                "message_0.db", "message_1.db"]
    assert accounting.sidecars == ()
    assert accounting.gaps == ()
    assert unmet_requirements(accounting) == ()
    assert accounting.meets_requirements()
