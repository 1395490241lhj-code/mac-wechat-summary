"""Stable private copies and encrypted WAL replay for explicit sources."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import secrets
import shutil
import struct
import sys
from typing import Callable

from .keystore import KeyDescriptor


PAGE_SIZE = 4096
WAL_HEADER_SIZE = 32
WAL_FRAME_HEADER_SIZE = 24
SHM_INDEX_HEADER_SIZE = 48
SHM_INDEX_HEADER_COPIES = 2
_WAL_MAGIC = {0x377F0682, 0x377F0683}
_WAL_VERSION = 3007000


class SnapshotError(Exception):
    def __init__(self, reason: str) -> None:
        if reason not in {"source busy", "snapshot unstable"}:
            raise ValueError("snapshot reason invalid")
        super().__init__(reason)


class SnapshotFormatError(Exception):
    def __init__(self, reason: str = "snapshot format unsupported") -> None:
        if reason != "snapshot format unsupported":
            raise ValueError("snapshot format reason invalid")
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class EncryptedSource:
    main: Path = field(repr=False)
    wal: Path | None = field(default=None, repr=False)
    shm: Path | None = field(default=None, repr=False)
    key_descriptor: KeyDescriptor = field(default=None)

    def __post_init__(self) -> None:
        paths = (self.main, self.wal, self.shm)
        if (not isinstance(self.main, Path)
                or any(path is not None and not isinstance(path, Path) for path in paths)
                or len({path for path in paths if path is not None}) != sum(
                    path is not None for path in paths
                )
                or not isinstance(self.key_descriptor, KeyDescriptor)):
            raise ValueError("encrypted source invalid")


@dataclass(frozen=True, slots=True)
class EncryptedSnapshot:
    main: Path = field(repr=False)
    wal: Path | None = field(default=None, repr=False)
    shm: Path | None = field(default=None, repr=False)
    key_descriptor: KeyDescriptor = field(default=None)


def _signature(path: Path) -> tuple[int, int, int, int]:
    value = path.stat()
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


class Snapshotter:
    def __init__(
        self,
        *,
        attempts: int = 3,
        copy_file: Callable[[Path, Path], object] | None = None,
    ) -> None:
        if type(attempts) is not int or attempts < 1:
            raise ValueError("snapshot attempts invalid")
        self._attempts = attempts
        self._copy_file = copy_file or shutil.copyfile

    def copy(
        self, sources: tuple[EncryptedSource, ...], destination: Path
    ) -> tuple[EncryptedSnapshot, ...]:
        if (not isinstance(sources, tuple) or not sources
                or not all(isinstance(source, EncryptedSource) for source in sources)
                or not isinstance(destination, Path)):
            raise ValueError("snapshot input invalid")
        source_paths = tuple(
            path
            for source in sources
            for path in (source.main, source.wal, source.shm)
            if path is not None
        )
        changed = False
        busy = False
        for _ in range(self._attempts):
            shutil.rmtree(destination, ignore_errors=True)
            destination.mkdir(parents=True, mode=0o700)
            os.chmod(destination, 0o700)
            try:
                before = tuple(_signature(path) for path in source_paths)
            except OSError:
                busy = True
                continue
            copies: dict[Path, Path] = {}
            source_failed = False
            for path in source_paths:
                target = destination / secrets.token_hex(16)
                try:
                    self._copy_file(path, target)
                    os.chmod(target, 0o600)
                except OSError as error:
                    if error.filename is not None and Path(error.filename) == path:
                        busy = True
                        source_failed = True
                        break
                    raise
                copies[path] = target
            if source_failed:
                continue
            try:
                after = tuple(_signature(path) for path in source_paths)
            except OSError:
                busy = True
                continue
            sizes_match = all(
                copies[path].stat().st_size == signature[2]
                for path, signature in zip(source_paths, before, strict=True)
            )
            if before != after or not sizes_match:
                changed = True
                continue
            return tuple(EncryptedSnapshot(
                copies[source.main],
                copies[source.wal] if source.wal is not None else None,
                copies[source.shm] if source.shm is not None else None,
                source.key_descriptor,
            ) for source in sources)
        shutil.rmtree(destination, ignore_errors=True)
        if changed:
            raise SnapshotError("snapshot unstable")
        if busy:
            raise SnapshotError("source busy")
        raise SnapshotError("snapshot unstable")


def _checksum(
    data: bytes, seed: tuple[int, int], byte_order: str
) -> tuple[int, int]:
    values = struct.unpack(
        ("<" if byte_order == "little" else ">") + f"{len(data) // 4}I",
        data,
    )
    first, second = seed
    for index in range(0, len(values), 2):
        first = (first + values[index] + second) & 0xFFFFFFFF
        second = (second + values[index + 1] + first) & 0xFFFFFFFF
    return first, second


def _read_exact(stream, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise SnapshotFormatError()
    return data


@dataclass(frozen=True, slots=True)
class _WalIndexBoundary:
    frame_limit: int
    database_size: int
    frame_checksum: tuple[int, int]


def _wal_index_boundary(
    shm: Path | None, wal_header: bytes, wal_magic: int
) -> _WalIndexBoundary | None:
    if shm is None:
        return None
    try:
        with shm.open("rb") as stream:
            block = _read_exact(
                stream, SHM_INDEX_HEADER_SIZE * SHM_INDEX_HEADER_COPIES
            )
        first = block[:SHM_INDEX_HEADER_SIZE]
        second = block[SHM_INDEX_HEADER_SIZE:]
        if first != second:
            raise SnapshotFormatError()
        byte_order = "<" if sys.byteorder == "little" else ">"
        values = struct.unpack(byte_order + "III BB H IIII IIII", first)
        (
            version, _unused, _change, is_init, big_end_checksum, page_size,
            max_frame, database_size, frame_sum1, frame_sum2,
            _salt1, _salt2, header_sum1, header_sum2,
        ) = values
        header_checksum = _checksum(first[:40], (0, 0), sys.byteorder)
        if (version != _WAL_VERSION or is_init != 1
                or big_end_checksum not in (0, 1)
                or header_checksum != (header_sum1, header_sum2)):
            raise SnapshotFormatError()
        if max_frame == 0:
            if page_size not in (0, PAGE_SIZE):
                raise SnapshotFormatError()
            return _WalIndexBoundary(0, database_size, (frame_sum1, frame_sum2))
        if (page_size != PAGE_SIZE or database_size == 0
                or bool(big_end_checksum) != (wal_magic == 0x377F0683)
                or first[32:40] != wal_header[16:24]):
            raise SnapshotFormatError()
        return _WalIndexBoundary(
            max_frame, database_size, (frame_sum1, frame_sum2)
        )
    except SnapshotFormatError:
        raise
    except (OSError, struct.error):
        raise SnapshotFormatError() from None


def replay_committed_wal(
    main: Path, wal: Path | None, shm: Path | None = None
) -> int:
    if wal is None:
        return 0
    try:
        with wal.open("rb") as stream:
            header = _read_exact(stream, WAL_HEADER_SIZE)
            magic, version, page_size, _, salt1, salt2, sum1, sum2 = struct.unpack(
                ">8I", header
            )
            if magic not in _WAL_MAGIC or version != _WAL_VERSION or page_size != PAGE_SIZE:
                raise SnapshotFormatError()
            byte_order = "little" if magic == 0x377F0682 else "big"
            checksum = _checksum(header[:24], (0, 0), byte_order)
            if checksum != (sum1, sum2):
                raise SnapshotFormatError()
            boundary = _wal_index_boundary(shm, header, magic)
            if boundary is not None and boundary.frame_limit == 0:
                return 0
            frame_count = 0
            last_commit = 0
            committed_size = 0
            transaction_max_page = 0
            while boundary is None or frame_count < boundary.frame_limit:
                frame_header = stream.read(WAL_FRAME_HEADER_SIZE)
                if boundary is None and not frame_header:
                    break
                if len(frame_header) != WAL_FRAME_HEADER_SIZE:
                    raise SnapshotFormatError()
                payload = _read_exact(stream, PAGE_SIZE)
                page_number, database_size, frame_salt1, frame_salt2, sum1, sum2 = (
                    struct.unpack(">6I", frame_header)
                )
                checksum = _checksum(frame_header[:8] + payload, checksum, byte_order)
                if (page_number == 0 or frame_salt1 != salt1 or frame_salt2 != salt2
                        or checksum != (sum1, sum2)):
                    raise SnapshotFormatError()
                frame_count += 1
                transaction_max_page = max(transaction_max_page, page_number)
                if database_size:
                    if transaction_max_page > database_size:
                        raise SnapshotFormatError()
                    last_commit = frame_count
                    committed_size = database_size
                    transaction_max_page = 0
            if boundary is not None and (
                frame_count != boundary.frame_limit
                or last_commit != boundary.frame_limit
                or committed_size != boundary.database_size
                or checksum != boundary.frame_checksum
            ):
                raise SnapshotFormatError()
        if not last_commit:
            return 0
        with wal.open("rb") as stream, main.open("r+b") as database:
            stream.seek(WAL_HEADER_SIZE)
            for _ in range(last_commit):
                frame_header = _read_exact(stream, WAL_FRAME_HEADER_SIZE)
                payload = _read_exact(stream, PAGE_SIZE)
                page_number = struct.unpack(">I", frame_header[:4])[0]
                database.seek((page_number - 1) * PAGE_SIZE)
                database.write(payload)
            database.truncate(committed_size * PAGE_SIZE)
        return last_commit
    except SnapshotFormatError:
        raise
    except struct.error:
        raise SnapshotFormatError() from None
