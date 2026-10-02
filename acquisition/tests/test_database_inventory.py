"""The database-role inventory of one already-selected message directory.

P4 compatibility groundwork, synthetic fixtures only. The inventory is what
stops "every *.db is a message shard": a directory that also holds a
business-message, a search, a media, an auxiliary or an unrecognised database
must account for each of them by name, and only an ordinary message shard may
become a message-role source.

No real database, container, credential or protected store is read here, and
classification is structural: the refresher works on *encrypted* sources, so a
name shape is the only evidence available, by design.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT, ROOT / "bridge"):
    sys.path.insert(0, str(path))

from acquisition import AcquisitionSourceSet, EncryptedSource, KeyDescriptor  # noqa: E402
from acquisition.database_inventory import (  # noqa: E402
    DATABASE_ROLES,
    GAP_BUSINESS_MESSAGE_UNREAD,
    GAP_UNKNOWN_DATABASE,
    GAP_UNSUPPORTED_MESSAGE_CANDIDATE,
    INVENTORY_GAPS,
    REJECTED_NOT_REGULAR_FILE,
    ROLE_AUXILIARY,
    ROLE_BUSINESS_MESSAGE,
    ROLE_MEDIA,
    ROLE_ORDINARY_MESSAGE,
    ROLE_SEARCH_INDEX,
    ROLE_UNKNOWN,
    ROLE_UNSUPPORTED_MESSAGE_CANDIDATE,
    DatabaseInventory,
    DatabaseInventoryEntry,
    inventory_message_directory,
)
from acquisition.source_refresher import BoundedSourceRefresher  # noqa: E402


def _descriptor() -> KeyDescriptor:
    return KeyDescriptor("a" * 64, "messages", 1, "b" * 64)


def _directory(tmp_path: Path, names) -> Path:
    message_dir = tmp_path / "message"
    message_dir.mkdir(parents=True)
    for name in names:
        (message_dir / name).write_bytes(b"synthetic")
    return message_dir


def _source_set(message_dir: Path) -> AcquisitionSourceSet:
    return AcquisitionSourceSet(
        (EncryptedSource(message_dir / "message_0.db", None, None, _descriptor()),),
        None,
        None,
    )


# -- 1: an ordinary message shard is recognised as exactly that --------------


def test_an_ordinary_message_shard_is_classified(tmp_path):
    message_dir = _directory(tmp_path, ["message_0.db", "message_12.db"])

    inventory = inventory_message_directory(message_dir)

    assert inventory.by_role(ROLE_ORDINARY_MESSAGE) == (
        "message_0.db",
        "message_12.db",
    )
    assert inventory.gaps == ()


# -- 2..5: every non-message role is its own, never an anonymous shard --------


def test_a_business_message_shard_is_its_own_role(tmp_path):
    message_dir = _directory(tmp_path, ["message_0.db", "biz_message_0.db"])

    inventory = inventory_message_directory(message_dir)

    assert inventory.by_role(ROLE_BUSINESS_MESSAGE) == ("biz_message_0.db",)
    # Not anonymous, and not silently ordinary: the read does not cover it.
    assert inventory.by_role(ROLE_ORDINARY_MESSAGE) == ("message_0.db",)
    assert inventory.gaps == (GAP_BUSINESS_MESSAGE_UNREAD,)


def test_a_native_search_index_is_not_a_message_coverage_source(tmp_path):
    message_dir = _directory(tmp_path, ["message_0.db", "message_fts.db"])

    inventory = inventory_message_directory(message_dir)

    assert inventory.by_role(ROLE_SEARCH_INDEX) == ("message_fts.db",)
    assert inventory.by_role(ROLE_ORDINARY_MESSAGE) == ("message_0.db",)
    assert inventory.gaps == ()


def test_a_media_database_is_classified(tmp_path):
    message_dir = _directory(tmp_path, ["message_0.db", "media.db", "media_2.db"])

    inventory = inventory_message_directory(message_dir)

    assert inventory.by_role(ROLE_MEDIA) == ("media.db", "media_2.db")
    assert inventory.by_role(ROLE_ORDINARY_MESSAGE) == ("message_0.db",)
    assert inventory.gaps == ()


def test_a_known_auxiliary_database_is_classified(tmp_path):
    message_dir = _directory(tmp_path, ["message_0.db", "session.db", "contact.db"])

    inventory = inventory_message_directory(message_dir)

    assert inventory.by_role(ROLE_AUXILIARY) == ("contact.db", "session.db")
    assert inventory.by_role(ROLE_ORDINARY_MESSAGE) == ("message_0.db",)
    assert inventory.gaps == ()


# -- 6..7: an unrecognised or message-like database stays visible ------------


def test_an_arbitrary_database_stays_visible_as_unknown(tmp_path):
    message_dir = _directory(tmp_path, ["message_0.db", "zzz_unknown.db"])

    inventory = inventory_message_directory(message_dir)

    assert inventory.by_role(ROLE_UNKNOWN) == ("zzz_unknown.db",)
    assert inventory.by_role(ROLE_ORDINARY_MESSAGE) == ("message_0.db",)
    assert inventory.gaps == (GAP_UNKNOWN_DATABASE,)


def test_a_message_like_database_this_reader_cannot_claim_is_a_gap(tmp_path):
    # A message-bearing name that is not the one documented shard shape: it may
    # carry messages, and this reader cannot safely call it a supported source.
    # An unrelated database stays merely unknown, not a message candidate.
    message_dir = _directory(
        tmp_path, ["message_0.db", "biz_message_x.db", "message_shard.db", "zzz.db"])

    inventory = inventory_message_directory(message_dir)

    assert inventory.by_role(ROLE_UNSUPPORTED_MESSAGE_CANDIDATE) == (
        "biz_message_x.db",
        "message_shard.db",
    )
    assert inventory.by_role(ROLE_ORDINARY_MESSAGE) == ("message_0.db",)
    assert inventory.by_role(ROLE_UNKNOWN) == ("zzz.db",)
    # Both are visible: the message-shaped names are candidates this reader
    # cannot claim, and the unrelated one is still accounted as unknown.
    assert inventory.gaps == (
        GAP_UNKNOWN_DATABASE,
        GAP_UNSUPPORTED_MESSAGE_CANDIDATE,
    )


def test_the_gap_vocabulary_is_closed(tmp_path):
    message_dir = _directory(
        tmp_path,
        ["message_0.db", "biz_message_0.db", "zzz.db", "message_shard.db"],
    )

    inventory = inventory_message_directory(message_dir)

    assert len(inventory.gaps) == 3
    assert set(inventory.gaps) <= INVENTORY_GAPS
    assert set(DATABASE_ROLES) == {
        ROLE_ORDINARY_MESSAGE,
        ROLE_BUSINESS_MESSAGE,
        ROLE_SEARCH_INDEX,
        ROLE_MEDIA,
        ROLE_AUXILIARY,
        ROLE_UNKNOWN,
        ROLE_UNSUPPORTED_MESSAGE_CANDIDATE,
    }


# -- 8: nothing that is not a regular file inside the directory gets in ------


def test_only_regular_files_in_the_selected_directory_are_candidates(tmp_path):
    message_dir = _directory(tmp_path, ["message_0.db", "link.db"])
    (message_dir / "subdir.db").mkdir()
    (message_dir / "link.db").unlink()
    (message_dir / "link.db").symlink_to(message_dir / "message_0.db")

    inventory = inventory_message_directory(message_dir)

    assert inventory.by_role(ROLE_ORDINARY_MESSAGE) == ("message_0.db",)
    assert inventory.rejections == (
        ("link.db", REJECTED_NOT_REGULAR_FILE),
        ("subdir.db", REJECTED_NOT_REGULAR_FILE),
    )
    # A refusal is not a message source, but it is still an unaccounted
    # database: neither name is a recognised non-message role.
    assert inventory.gaps == (GAP_UNKNOWN_DATABASE,)


def test_a_refused_message_shaped_child_still_leaves_a_gap(tmp_path):
    """A supported-shaped name that was refused is still unread history.

    ``message_1.db`` is refused because it is a symlink, so it never becomes a
    shard. But the read did not cover it either, and the name says it could
    carry messages -- so it is a message candidate this reader cannot claim,
    exactly like a regular file whose shape it does not support. Silently
    dropping it would let the read claim complete coverage over history it
    never accounted for.
    """
    message_dir = _directory(tmp_path, ["message_0.db", "message_1.db"])
    (message_dir / "message_1.db").unlink()
    (message_dir / "message_1.db").symlink_to(message_dir / "message_0.db")

    inventory = inventory_message_directory(message_dir)

    assert inventory.rejections == (("message_1.db", REJECTED_NOT_REGULAR_FILE),)
    assert inventory.message_shards == ("message_0.db",)
    assert inventory.gaps == (GAP_UNSUPPORTED_MESSAGE_CANDIDATE,)


def test_a_refused_recognised_non_message_child_is_not_a_gap(tmp_path):
    """The converse: a refused database that carries no messages is fine.

    A media or auxiliary name the inventory recognises has no message-bearing
    claim to lose, so refusing it is a refusal and nothing more.
    """
    message_dir = _directory(tmp_path, ["message_0.db", "media.db", "session.db"])
    (message_dir / "media.db").unlink()
    (message_dir / "media.db").symlink_to(message_dir / "message_0.db")

    inventory = inventory_message_directory(message_dir)

    assert inventory.rejections == (("media.db", REJECTED_NOT_REGULAR_FILE),)
    assert inventory.gaps == ()


def test_a_hidden_name_is_not_a_candidate(tmp_path):
    message_dir = _directory(tmp_path, ["message_0.db", ".hidden.db"])

    inventory = inventory_message_directory(message_dir)

    assert inventory.by_role(ROLE_ORDINARY_MESSAGE) == ("message_0.db",)
    assert inventory.gaps == ()


def test_a_non_database_file_is_not_a_candidate(tmp_path):
    message_dir = _directory(
        tmp_path, ["message_0.db", "notes.txt", "message_0.db-wal"])

    inventory = inventory_message_directory(message_dir)

    assert inventory.by_role(ROLE_ORDINARY_MESSAGE) == ("message_0.db",)
    assert inventory.gaps == ()


# -- 9..11: total accounting, and one role per path --------------------------


def test_the_inventory_does_not_depend_on_directory_order(tmp_path):
    names = [
        "message_0.db", "message_1.db", "biz_message_0.db", "message_fts.db",
        "media.db", "session.db", "zzz.db", "message_shard.db",
    ]
    first = inventory_message_directory(_directory(tmp_path / "a", names))
    second = inventory_message_directory(
        _directory(tmp_path / "b", list(reversed(names))))

    assert first == second
    assert first.entries == tuple(
        sorted(first.entries, key=lambda entry: entry.name))


def test_a_repeated_name_is_refused_rather_than_collapsed():
    entry = DatabaseInventoryEntry(name="message_0.db", role=ROLE_ORDINARY_MESSAGE)

    with pytest.raises(ValueError):
        DatabaseInventory(entries=(entry, entry))


def test_a_role_outside_the_closed_vocabulary_is_refused():
    with pytest.raises(ValueError):
        DatabaseInventoryEntry(name="message_0.db", role="message")


def test_every_candidate_is_accounted_exactly_once(tmp_path):
    names = [
        "message_0.db", "biz_message_0.db", "message_fts.db", "media.db",
        "session.db", "zzz.db", "message_shard.db",
    ]
    message_dir = _directory(tmp_path, names)
    (message_dir / "subdir.db").mkdir()

    inventory = inventory_message_directory(message_dir)

    on_disk = {path.name for path in message_dir.iterdir()}
    accounted = [entry.name for entry in inventory.entries]
    accounted += [name for name, _ in inventory.rejections]
    assert sorted(accounted) == sorted(on_disk)
    assert len(set(accounted)) == len(accounted)
    assert inventory.accounts_for(on_disk)


# -- 12: auxiliary presence can never strengthen message coverage ------------


def test_recognised_auxiliary_databases_never_strengthen_message_coverage(tmp_path):
    bare = inventory_message_directory(_directory(tmp_path / "bare", ["message_0.db"]))
    loaded = inventory_message_directory(
        _directory(
            tmp_path / "loaded",
            [
                "message_0.db", "message_1.db", "message_fts.db", "media.db",
                "media_2.db", "session.db", "contact.db",
            ],
        )
    )

    assert loaded.by_role(ROLE_ORDINARY_MESSAGE) == (
        "message_0.db",
        "message_1.db",
    )
    # No auxiliary role contributes a gap, and none can be read as a shard.
    assert loaded.gaps == bare.gaps == ()
    assert loaded.supports_message_read is True
    auxiliary = (
        loaded.by_role(ROLE_SEARCH_INDEX)
        + loaded.by_role(ROLE_MEDIA)
        + loaded.by_role(ROLE_AUXILIARY)
    )
    assert set(auxiliary) & set(loaded.by_role(ROLE_ORDINARY_MESSAGE)) == set()


# -- 13: only an ordinary shard ever reaches the provider --------------------


def test_the_refresher_hands_the_provider_only_ordinary_message_shards(tmp_path):
    names = [
        "message_0.db", "message_1.db", "biz_message_0.db", "message_fts.db",
        "media.db", "session.db", "zzz.db", "message_shard.db",
    ]
    message_dir = _directory(tmp_path, names)
    refresher = BoundedSourceRefresher()
    source_set = _source_set(message_dir)

    refreshed = refresher.refresh(source_set)

    assert [source.main.name for source in refreshed.message_sources] == [
        "message_0.db",
        "message_1.db",
    ]
    assert set(refresher.inventory(source_set).gaps) == {
        GAP_BUSINESS_MESSAGE_UNREAD,
        GAP_UNKNOWN_DATABASE,
        GAP_UNSUPPORTED_MESSAGE_CANDIDATE,
    }


def test_a_directory_of_only_non_message_databases_refreshes_nothing(tmp_path):
    message_dir = _directory(
        tmp_path, ["message_0.db", "biz_message_0.db", "media.db", "zzz.db"])
    refresher = BoundedSourceRefresher()
    source_set = _source_set(message_dir)

    (message_dir / "message_0.db").unlink()

    assert refresher.refresh(source_set) is source_set
    inventory = refresher.inventory(source_set)
    assert inventory.by_role(ROLE_ORDINARY_MESSAGE) == ()
    assert GAP_UNKNOWN_DATABASE in inventory.gaps


# -- 14: ordinary multi-shard growth is unchanged ----------------------------


def test_ordinary_shards_still_refresh_as_before(tmp_path):
    message_dir = _directory(tmp_path, ["message_0.db", "message_1.db"])
    (message_dir / "message_0.db-wal").write_bytes(b"wal")
    (message_dir / "message_0.db-shm").write_bytes(b"shm")
    source_set = _source_set(message_dir)

    refreshed = BoundedSourceRefresher().refresh(source_set)

    first = refreshed.message_sources[0]
    assert first.wal == message_dir / "message_0.db-wal"
    assert first.shm == message_dir / "message_0.db-shm"
    assert refreshed.message_sources[1].wal is None
    assert all(
        source.key_descriptor == source_set.message_sources[0].key_descriptor
        for source in refreshed.message_sources
    )
    (message_dir / "message_2.db").write_bytes(b"synthetic")
    assert len(BoundedSourceRefresher().refresh(source_set).message_sources) == 3


# -- 15: the explicitly-selected-directory boundary does not widen -----------


def test_the_inventory_never_leaves_the_selected_message_directory(tmp_path):
    message_dir = _directory(tmp_path, ["message_0.db"])
    sibling = _directory(tmp_path / "other_account", ["message_9.db", "media.db"])
    (tmp_path / "other_account" / "deep").mkdir()
    (tmp_path / "other_account" / "deep" / "message_8.db").write_bytes(b"synthetic")
    source_set = _source_set(message_dir)
    refresher = BoundedSourceRefresher()

    assert [s.main.name for s in refresher.refresh(source_set).message_sources] == [
        "message_0.db"]
    assert refresher.inventory(source_set).accounts_for({"message_0.db"})
    assert str(sibling) not in str(refresher.inventory(source_set).entries)


def test_a_source_set_spanning_two_directories_is_left_alone(tmp_path):
    _directory(tmp_path, ["message_0.db"])
    other_dir = _directory(tmp_path / "other", ["message_0.db", "media.db"])
    source_set = AcquisitionSourceSet(
        (
            EncryptedSource(
                tmp_path / "message" / "message_0.db", None, None, _descriptor()),
            EncryptedSource(
                other_dir / "message_0.db", None, None, _descriptor()),
        ),
        None,
        None,
    )
    refresher = BoundedSourceRefresher()

    assert refresher.refresh(source_set) is source_set
    # A split source set is not a directory this refresher is entitled to
    # choose, so it inspects neither end.
    assert refresher.inventory(source_set).entries == ()


# -- message/message_resource.db: one publicly documented companion shape -----
#
# Three independent public sources spell the exact path message/message_resource.db
# and describe it as message-attachment resource metadata (ledger: the A10
# classification doc). The message-directory inventory is, by definition, the
# message/ location, so it recognises that exact name as media. The location-free
# basename classifier stays untouched: the same name anywhere else is still an
# explicit message-shaped candidate, and an indexed variant has no public proof.


def test_message_resource_is_media_in_the_message_directory_inventory(tmp_path):
    message_dir = _directory(tmp_path, ["message_0.db", "message_resource.db"])

    inventory = inventory_message_directory(message_dir)

    assert inventory.by_role(ROLE_MEDIA) == ("message_resource.db",)
    assert inventory.by_role(ROLE_ORDINARY_MESSAGE) == ("message_0.db",)
    assert inventory.message_shards == ("message_0.db",)
    assert inventory.gaps == ()


def test_message_resource_never_becomes_a_message_shard(tmp_path):
    message_dir = _directory(tmp_path, ["message_resource.db"])

    inventory = inventory_message_directory(message_dir)

    assert inventory.message_shards == ()
    assert not inventory.supports_message_read


def test_the_location_free_classifier_still_treats_the_name_as_a_candidate():
    from acquisition.database_inventory import classify_database_name

    assert classify_database_name("message_resource.db") == ROLE_UNSUPPORTED_MESSAGE_CANDIDATE


@pytest.mark.parametrize("name", [
    "message_resource_1.db", "message_resource_0.db", "Message_Resource.db",
    "xmessage_resource.db", "message_resource.db.bak",
])
def test_only_the_exact_message_resource_name_is_recognised(tmp_path, name):
    message_dir = _directory(tmp_path, ["message_0.db", name])

    inventory = inventory_message_directory(message_dir)

    assert inventory.by_role(ROLE_MEDIA) == ()


@pytest.mark.parametrize("name", ["message_resource_1.db", "message_resource_0.db"])
def test_an_indexed_resource_variant_stays_a_visible_candidate(tmp_path, name):
    message_dir = _directory(tmp_path, ["message_0.db", name])

    inventory = inventory_message_directory(message_dir)

    assert inventory.by_role(ROLE_UNSUPPORTED_MESSAGE_CANDIDATE) == (name,)
    assert inventory.gaps == (GAP_UNSUPPORTED_MESSAGE_CANDIDATE,)


def test_a_refused_message_resource_carries_no_message_claim(tmp_path):
    # Consistent with every other recognised non-message name: refusing it is a
    # refusal and nothing more.
    message_dir = _directory(tmp_path, ["message_0.db", "message_resource.db"])
    (message_dir / "message_resource.db").unlink()
    (message_dir / "message_resource.db").symlink_to(message_dir / "message_0.db")

    inventory = inventory_message_directory(message_dir)

    assert inventory.rejections == (("message_resource.db", REJECTED_NOT_REGULAR_FILE),)
    assert inventory.gaps == ()
