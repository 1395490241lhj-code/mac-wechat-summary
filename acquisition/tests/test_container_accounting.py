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
        "location_counts", "rejections", "gaps", "unmet_requirements"}
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
