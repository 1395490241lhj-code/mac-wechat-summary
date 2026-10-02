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

import pytest

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

    # Keyed by (location, name): the same name in two directories is two
    # databases, and one database in two roles would be a double count.
    assert accounting.accounts_for((
        (LOCATION_ROOT, "stray.db"),
        (LOCATION_MESSAGE_DIRECTORY, "message_0.db"),
        (LOCATION_MESSAGE_DIRECTORY, "message_1.db"),
        (LOCATION_SESSION_DIRECTORY, "session.db"),
        (LOCATION_CONTACT_DIRECTORY, "contact.db"),
        (LOCATION_OTHER_DIRECTORY, "a.db"),
    ))
    assert accounting.role_counts[ROLE_UNKNOWN] == 2


def test_a_directory_outside_the_named_three_is_examined_not_merely_counted(tmp_path):
    # emoticon/emoticon.db is real container layout this project has read.
    accounting = account_container(_root(tmp_path, directories=("emoticon",)))

    assert LOCATION_OTHER_DIRECTORY in accounting.examined_directories
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

    assert any(reason == UNREADABLE_DIRECTORY
               for _, _, reason in accounting.rejections)
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


def test_an_unknown_database_is_a_visible_gap_not_a_failure(tmp_path):
    accounting = account_container(_root(tmp_path, root_files=("zzz.db",)))

    assert GAP_UNKNOWN_DATABASE in accounting.gaps
    assert unmet_requirements(accounting) == ()


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

    assert (LOCATION_SESSION_DIRECTORY, "session.db",
            "not_a_regular_file") in accounting.rejections
    assert REQUIRED_ROLE_MISSING in unmet_requirements(accounting)


# -- 5: the closed vocabularies stay closed ----------------------------------


def test_every_accounted_role_and_location_is_in_the_closed_vocabulary(tmp_path):
    accounting = account_container(
        _root(tmp_path, directories=("hardlink",), root_files=("stray.db",)))

    assert set(accounting.role_counts) <= CONTAINER_ROLES
    assert {row.location for row in accounting.databases} <= CONTAINER_LOCATIONS
    assert set(accounting.examined_directories) <= CONTAINER_LOCATIONS
    assert {reason for _, reason in accounting.rejections} <= CONTAINER_REJECTION_REASONS


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
    assert evidence["unmet_requirements"] == ()


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
