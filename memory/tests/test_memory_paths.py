"""The one canonical, app-owned memory store location (M2.2d)."""

from __future__ import annotations

import re
import stat
from pathlib import Path

import memory_paths as paths

ROOT = Path(__file__).resolve().parents[2]


def test_the_canonical_location_is_deterministic(tmp_path):
    first = paths.canonical_store_path(tmp_path)
    assert first == paths.canonical_store_path(tmp_path)
    assert first == tmp_path / "Library/Application Support/WeChatCompanion/memory.sqlite"


def test_the_message_store_is_the_same_owned_directory(tmp_path):
    memory = paths.canonical_store_path(tmp_path)
    messages = paths.canonical_message_store_path(tmp_path)
    assert memory.parent == messages.parent
    assert messages.name == "messages.sqlite"


def test_deriving_a_path_creates_nothing(tmp_path):
    path = paths.canonical_store_path(tmp_path)
    assert not path.exists()
    assert not path.parent.exists()
    assert not (tmp_path / "Library").exists()


def test_preparing_the_directory_is_owner_only_and_idempotent(tmp_path):
    path = paths.prepare_store_directory(paths.canonical_store_path(tmp_path))
    assert path.parent.is_dir()
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert not path.exists()          # the directory, never the file
    paths.prepare_store_directory(path)


def test_the_relative_form_names_the_place_not_the_person():
    relative = paths.relative_store_path()
    assert relative == "Library/Application Support/WeChatCompanion/memory.sqlite"
    assert "/Users/" not in relative and not relative.startswith("/")


def test_the_swift_side_derives_the_same_location():
    """Two languages, one location: the literals must not drift apart."""
    swift = (ROOT / "apps/WeChatCompanion/WeChatCompanion/Ingestion/MemoryStoreLocation.swift"
             ).read_text(encoding="utf-8")

    def literal(name: str) -> str:
        return re.search(rf'static let {name} = "([^"]+)"', swift).group(1)

    assert literal("appDirectoryName") == paths.APP_DIRECTORY_NAME
    assert literal("storeFileName") == paths.STORE_FILE_NAME
    assert literal("messageStoreFileName") == paths.MESSAGE_STORE_FILE_NAME

    # The Swift array spells the two fixed segments and then reuses the named
    # constants, so comparing it to the Python tuple checks order as well.
    components = re.search(r"relativeComponents = \[(.*?)\]", swift, re.S).group(1)
    spelled = [part.strip().strip('",') for part in components.split(",") if part.strip()]
    assert spelled == ["Library", "Application Support", "appDirectoryName", "storeFileName"]
    assert list(paths.RELATIVE_STORE_COMPONENTS) == [
        "Library", "Application Support", paths.APP_DIRECTORY_NAME, paths.STORE_FILE_NAME,
    ]


def test_no_absolute_path_is_hardcoded_anywhere_in_the_module():
    source = Path(paths.__file__).read_text(encoding="utf-8")
    assert "/Users/" not in source and "/home/" not in source
