from __future__ import annotations

import os
from pathlib import Path
import struct

import pytest


PAGE = 4096
SALT = (11, 22)


def _source(tmp_path: Path, name: str = "main.db"):
    from acquisition import KeyDescriptor
    from acquisition.snapshot import EncryptedSource

    main = tmp_path / name
    main.write_bytes(b"M" * PAGE)
    return EncryptedSource(
        main=main,
        wal=None,
        shm=None,
        key_descriptor=KeyDescriptor("a" * 64, "messages", 1, "b" * 64),
    )


def _checksum(data, seed=(0, 0), byte_order="little"):
    values = struct.unpack(
        ("<" if byte_order == "little" else ">") + f"{len(data) // 4}I", data
    )
    first, second = seed
    for index in range(0, len(values), 2):
        first = (first + values[index] + second) & 0xFFFFFFFF
        second = (second + values[index + 1] + first) & 0xFFFFFFFF
    return first, second


def _wal(frames, *, page_size=PAGE, salts=SALT, version=3007000):
    prefix = struct.pack(
        ">6I", 0x377F0682, version, page_size, 0, salts[0], salts[1]
    )
    checksum = _checksum(prefix)
    header = prefix + struct.pack(">2I", *checksum)
    body = bytearray()
    for page_number, database_size, payload, frame_salts in frames:
        frame_prefix = struct.pack(
            ">4I", page_number, database_size, frame_salts[0], frame_salts[1]
        )
        checksum = _checksum(frame_prefix[:8] + payload, checksum)
        body.extend(frame_prefix + struct.pack(">2I", *checksum))
        body.extend(payload)
    return header + body


def test_stable_snapshot_copies_only_explicit_files_without_mutating_source(tmp_path):
    from acquisition.snapshot import Snapshotter

    source = _source(tmp_path)
    wal = tmp_path / "main.db-wal"
    shm = tmp_path / "main.db-shm"
    decoy = tmp_path / "other.db"
    wal.write_bytes(b"wal")
    shm.write_bytes(b"shm")
    decoy.write_bytes(b"decoy")
    source = type(source)(source.main, wal, shm, source.key_descriptor)
    before = {path: path.read_bytes() for path in (source.main, wal, shm, decoy)}
    destination = tmp_path / "private"

    copied = Snapshotter().copy((source,), destination)

    assert len(copied) == 1
    assert copied[0].main.read_bytes() == before[source.main]
    assert copied[0].wal.read_bytes() == before[wal]
    assert copied[0].shm.read_bytes() == before[shm]
    assert {path: path.read_bytes() for path in before} == before
    assert decoy not in {copied[0].main, copied[0].wal, copied[0].shm}
    assert copied[0].main.stat().st_mode & 0o777 == 0o600


def test_snapshot_retries_a_mutated_generation(tmp_path):
    from acquisition.snapshot import Snapshotter

    source = _source(tmp_path)
    calls = 0

    def mutate_once(src, dst):
        nonlocal calls
        calls += 1
        dst.write_bytes(src.read_bytes())
        if calls == 1:
            src.write_bytes(b"N" * PAGE)

    copied = Snapshotter(attempts=2, copy_file=mutate_once).copy(
        (source,), tmp_path / "private"
    )
    assert calls == 2
    assert copied[0].main.read_bytes() == b"N" * PAGE


def test_snapshot_refuses_perpetual_mutation_and_replacement(tmp_path):
    from acquisition.snapshot import SnapshotError, Snapshotter

    source = _source(tmp_path)

    def mutate(src, dst):
        dst.write_bytes(src.read_bytes())
        replacement = src.with_suffix(".replacement")
        replacement.write_bytes(os.urandom(PAGE))
        replacement.replace(src)

    with pytest.raises(SnapshotError, match="^snapshot unstable$"):
        Snapshotter(attempts=2, copy_file=mutate).copy(
            (source,), tmp_path / "private"
        )


def test_snapshot_refuses_copied_size_mismatch(tmp_path):
    from acquisition.snapshot import SnapshotError, Snapshotter

    source = _source(tmp_path)

    def short_copy(src, dst):
        dst.write_bytes(src.read_bytes()[:-1])

    with pytest.raises(SnapshotError, match="^snapshot unstable$"):
        Snapshotter(attempts=1, copy_file=short_copy).copy(
            (source,), tmp_path / "private"
        )


def test_missing_source_is_source_busy(tmp_path):
    from acquisition.snapshot import SnapshotError, Snapshotter

    source = _source(tmp_path)
    source.main.unlink()
    with pytest.raises(SnapshotError, match="^source busy$"):
        Snapshotter().copy((source,), tmp_path / "private")


def test_no_wal_is_valid(tmp_path):
    from acquisition.snapshot import replay_committed_wal

    main = tmp_path / "main"
    main.write_bytes(b"A" * PAGE)
    assert replay_committed_wal(main, None) == 0
    assert main.read_bytes() == b"A" * PAGE


def test_last_committed_page_image_wins_and_trailing_transaction_is_ignored(tmp_path):
    from acquisition.snapshot import replay_committed_wal

    main = tmp_path / "main"
    wal = tmp_path / "wal"
    main.write_bytes(b"A" * PAGE * 2)
    wal.write_bytes(_wal([
        (1, 0, b"B" * PAGE, SALT),
        (1, 2, b"C" * PAGE, SALT),
        (2, 0, b"D" * PAGE, SALT),
    ]))

    assert replay_committed_wal(main, wal) == 2
    assert main.read_bytes() == b"C" * PAGE + b"A" * PAGE


def test_wal_checksum_chain_is_required_before_accepting_a_commit(tmp_path):
    from acquisition.snapshot import SnapshotFormatError, replay_committed_wal

    main = tmp_path / "main"
    wal = tmp_path / "wal"
    main.write_bytes(b"A" * PAGE)
    forged = bytearray(_wal([(1, 0, b"B" * PAGE, SALT)]))
    forged[32 + 4:32 + 8] = struct.pack(">I", 1)
    wal.write_bytes(forged)

    with pytest.raises(SnapshotFormatError, match="^snapshot format unsupported$"):
        replay_committed_wal(main, wal)
    assert main.read_bytes() == b"A" * PAGE


def test_commit_size_bounds_every_page_in_its_transaction(tmp_path):
    from acquisition.snapshot import SnapshotFormatError, replay_committed_wal

    main = tmp_path / "main"
    wal = tmp_path / "wal"
    main.write_bytes(b"A" * PAGE * 3)
    wal.write_bytes(_wal([
        (3, 0, b"B" * PAGE, SALT),
        (1, 1, b"C" * PAGE, SALT),
    ]))

    with pytest.raises(SnapshotFormatError, match="^snapshot format unsupported$"):
        replay_committed_wal(main, wal)


def test_wal_is_read_incrementally(monkeypatch, tmp_path):
    from acquisition.snapshot import replay_committed_wal

    main = tmp_path / "main"
    wal = tmp_path / "wal"
    main.write_bytes(b"A" * PAGE)
    wal.write_bytes(_wal([(1, 1, b"B" * PAGE, SALT)]))
    original_open = Path.open

    class IncrementalReader:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.wrapped.__exit__(*args)

        def read(self, size=-1):
            assert 0 <= size <= PAGE
            return self.wrapped.read(size)

        def seek(self, *args):
            return self.wrapped.seek(*args)

    def guarded_open(path, *args, **kwargs):
        opened = original_open(path, *args, **kwargs)
        return IncrementalReader(opened) if path == wal else opened

    monkeypatch.setattr(Path, "open", guarded_open)
    assert replay_committed_wal(main, wal) == 1
    assert main.read_bytes() == b"B" * PAGE


def test_commit_database_size_truncates_private_main(tmp_path):
    from acquisition.snapshot import replay_committed_wal

    main = tmp_path / "main"
    wal = tmp_path / "wal"
    main.write_bytes(b"A" * PAGE * 3)
    wal.write_bytes(_wal([(1, 1, b"B" * PAGE, SALT)]))
    replay_committed_wal(main, wal)
    assert main.read_bytes() == b"B" * PAGE


@pytest.mark.parametrize(
    "wal_bytes",
    [
        _wal([(1, 1, b"B" * PAGE, (99, 22))]),
        _wal([(1, 1, b"B" * PAGE, SALT)])[:-1],
        _wal([(0, 1, b"B" * PAGE, SALT)]),
        _wal([(2, 1, b"B" * PAGE, SALT)]),
        _wal([(1, 1, b"B" * 1024, SALT)], page_size=1024),
        _wal([(1, 1, b"B" * PAGE, SALT)], version=3006000),
    ],
    ids=[
        "bad-salt", "truncated", "zero-page", "commit-size",
        "page-size", "version",
    ],
)
def test_malformed_or_unsupported_wal_fails_closed(tmp_path, wal_bytes):
    from acquisition.snapshot import SnapshotFormatError, replay_committed_wal

    main = tmp_path / "main"
    wal = tmp_path / "wal"
    main.write_bytes(b"A" * PAGE)
    wal.write_bytes(wal_bytes)
    with pytest.raises(SnapshotFormatError, match="^snapshot format unsupported$"):
        replay_committed_wal(main, wal)


def _shm_for_wal(wal_bytes: bytes, *, mx_frame: int, database_size: int) -> bytes:
    import sys

    order = "<" if sys.byteorder == "little" else ">"
    magic = struct.unpack(">I", wal_bytes[:4])[0]
    big_end = 1 if magic == 0x377F0683 else 0
    page_size = PAGE if mx_frame else 0
    if mx_frame:
        frame_offset = 32 + (mx_frame - 1) * (24 + PAGE)
        frame_checksum = struct.unpack(">2I", wal_bytes[frame_offset + 16:frame_offset + 24])
    else:
        frame_checksum = (0, 0)
    prefix = struct.pack(
        order + "III BB H IIII",
        3007000, 0, 1, 1, big_end, page_size,
        mx_frame, database_size, frame_checksum[0], frame_checksum[1],
    ) + wal_bytes[16:24]
    checksum = _checksum(prefix, byte_order=sys.byteorder)
    header = prefix + struct.pack(order + "2I", *checksum)
    assert len(header) == 48
    return header + header + bytes(32768 - 96)


def test_shm_mxframe_hides_stale_physical_wal_tail(tmp_path):
    from acquisition.snapshot import replay_committed_wal

    main = tmp_path / "main"
    wal = tmp_path / "wal"
    shm = tmp_path / "shm"
    main.write_bytes(b"A" * PAGE * 2)
    valid = _wal([
        (1, 0, b"B" * PAGE, SALT),
        (1, 2, b"C" * PAGE, SALT),
    ])
    # A physical stale tail that is not part of the current wal-index generation.
    stale = bytearray(_wal([(2, 2, b"Z" * PAGE, (99, 98))]))[32:]
    wal.write_bytes(valid + stale)
    shm.write_bytes(_shm_for_wal(valid, mx_frame=2, database_size=2))

    assert replay_committed_wal(main, wal, shm) == 2
    assert main.read_bytes() == b"C" * PAGE + b"A" * PAGE


def test_shm_zero_mxframe_treats_physical_wal_as_inactive_residue(tmp_path):
    from acquisition.snapshot import replay_committed_wal

    main = tmp_path / "main"
    wal = tmp_path / "wal"
    shm = tmp_path / "shm"
    before = b"A" * PAGE
    main.write_bytes(before)
    physical = _wal([(1, 1, b"Z" * PAGE, SALT)])
    wal.write_bytes(physical)
    shm.write_bytes(_shm_for_wal(physical, mx_frame=0, database_size=0))

    assert replay_committed_wal(main, wal, shm) == 0
    assert main.read_bytes() == before


@pytest.mark.parametrize("mutation", ["copy-mismatch", "checksum", "salt", "not-commit"])
def test_shm_active_boundary_must_be_self_consistent(tmp_path, mutation):
    from acquisition.snapshot import SnapshotFormatError, replay_committed_wal

    main = tmp_path / "main"
    wal = tmp_path / "wal"
    shm = tmp_path / "shm"
    main.write_bytes(b"A" * PAGE)
    wal_bytes = _wal([(1, 1, b"B" * PAGE, SALT)])
    wal.write_bytes(wal_bytes)
    shm_bytes = bytearray(_shm_for_wal(wal_bytes, mx_frame=1, database_size=1))
    if mutation == "copy-mismatch":
        shm_bytes[48] ^= 1
    elif mutation == "checksum":
        shm_bytes[40] ^= 1
        shm_bytes[88] ^= 1
    elif mutation == "salt":
        shm_bytes[32] ^= 1
        shm_bytes[80] ^= 1
        # restore each header checksum after forging the salt so the salt check owns refusal
        import sys
        order = "<" if sys.byteorder == "little" else ">"
        for offset in (0, 48):
            ck = _checksum(bytes(shm_bytes[offset:offset + 40]), byte_order=sys.byteorder)
            shm_bytes[offset + 40:offset + 48] = struct.pack(order + "2I", *ck)
    else:
        uncommitted = _wal([(1, 0, b"B" * PAGE, SALT)])
        wal.write_bytes(uncommitted)
        shm_bytes = bytearray(_shm_for_wal(uncommitted, mx_frame=1, database_size=1))
    shm.write_bytes(shm_bytes)

    with pytest.raises(SnapshotFormatError, match="^snapshot format unsupported$"):
        replay_committed_wal(main, wal, shm)
