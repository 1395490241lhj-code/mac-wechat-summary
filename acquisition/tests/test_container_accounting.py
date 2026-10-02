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
    (root / "hardlink" / "shards").mkdir(parents=True)
    (root / "hardlink" / "shards" / "x.db").write_bytes(b"synthetic")

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
        "unmet_requirements"}
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

@pytest.mark.parametrize("directory", [None, "message", "session", "contact", "extra_domain"])
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

@pytest.mark.parametrize("name,role", [
    ("whatever_this_is.db", ROLE_UNKNOWN),
    ("message_future.db", ROLE_UNSUPPORTED_MESSAGE_CANDIDATE),
])
def test_proven_physical_only_domain_accounts_and_keeps_roles_visible(tmp_path, name, role):
    root = _root(tmp_path, directories=("emoticon",))
    placeholder = root / "emoticon" / "a.db"
    placeholder.unlink()
    placeholder.with_name(name).write_bytes(b"synthetic")

    accounting = account_container(root)

    # Visible, and still exactly the honest role. Not relabelled, not supported.
    assert accounting.role_counts[role] == 1
    # Accounted exactly once.
    assert accounting.accounts_for([(row.directory_identity, row.name)
                                    for row in accounting.databases])
    # Non-blocking for Reader completeness only.
    assert unmet_requirements(accounting) == ()
    assert accounting.meets_requirements()
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


def test_proven_physical_only_nested_directory_is_visible_without_blocking(tmp_path):
    root = _root(tmp_path, directories=("emoticon",))
    nested = root / "emoticon" / "shards"
    nested.mkdir(parents=True)
    (nested / "whatever.db").write_bytes(b"synthetic")

    accounting = account_container(root)

    assert container.ContainerRejection(LOCATION_OTHER_DIRECTORY, "shards",
                                       container.NESTED_DIRECTORY, "emoticon") in accounting.rejections
    # Visible as physical structure, and no recursion was implied.
    assert accounting.evidence()["rejections"].count(container.NESTED_DIRECTORY) == 1
    assert DIRECTORY_UNEXAMINED not in unmet_requirements(accounting)
    assert accounting.meets_requirements()


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
    assert container.KNOWN_PHYSICAL_ONLY_DIRECTORIES == frozenset({"emoticon"})
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


def test_a_refused_message_shape_outside_a_physical_only_domain_is_accounted_only(tmp_path):
    # An auxiliary-shaped refusal is the cheapest possible path to a false pass if
    # the boundary exemption is applied without the role check. Nothing inside a
    # proven physical-only domain may become required truth, and nothing inside it
    # may quietly turn into a blocker it never was.
    root = _root(tmp_path, directories=("emoticon",))
    (root / "emoticon" / "sns.db").symlink_to(root / "emoticon")

    accounting = account_container(root)

    assert accounting.rejections
    assert accounting.meets_requirements()
