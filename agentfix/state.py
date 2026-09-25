"""Safe local state primitives for agent-fix.

All persistent integration writes use this module so atomic file replacement,
private permissions, and symlink rejection have one owner.  Directory swaps and
multi-file restores provide process-level rollback; they are not promised to be
crash-atomic.  The functions are intentionally conservative and stdlib-only;
callers decide what data is eligible for backup or restoration.
"""
from __future__ import annotations

import datetime
import io
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path, PureWindowsPath
from typing import Any, Optional, Tuple


class StateError(RuntimeError):
    """A persistent-state operation was refused or could not be completed."""


def _is_link_or_reparse(path: Path) -> bool:
    """Detect symlinks and Windows reparse/junction components (3.8+ safe)."""
    try:
        if Path(path).is_symlink():
            return True
    except (OSError, ValueError):
        return True
    is_junction = getattr(Path(path), "is_junction", None)
    if callable(is_junction):
        try:
            if is_junction():
                return True
        except FileNotFoundError:
            return False
        except (OSError, ValueError):
            return True
    try:
        attrs = getattr(os.lstat(str(path)), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True
    return bool(attrs & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT


def _reject_symlink_chain(path: Path) -> None:
    current = Path(path).absolute()
    while True:
        if _is_link_or_reparse(current):
            raise StateError(f"refusing symlink or reparse path: {current}")
        parent = current.parent
        if parent == current:
            return
        current = parent


def ensure_safe_path(path: Path) -> Path:
    """Reject symlink components for a path before a persistent operation."""
    path = Path(path)
    _reject_symlink_chain(path)
    return path


def _path_identity(path: Path):
    try:
        info = os.lstat(str(path))
    except (OSError, ValueError) as exc:
        raise StateError(f"cannot inspect temporary state path: {path}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise StateError(f"temporary state path is not a regular file: {path}")
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_nlink,
    )


def _assert_path_identity(path: Path, expected) -> None:
    _reject_symlink_chain(path)
    if _path_identity(path) != expected:
        raise StateError(f"temporary state path changed during operation: {path}")


def _parent_identity(path: Path):
    """Identity of the directory that will hold the committed file.

    A same-user writer can rename the parent directory and move an attacker
    directory into its place, redirecting an otherwise verified rename. Locking
    the parent identity lets the post-commit check notice that redirect; it
    cannot be eliminated, because stdlib has no way to hold a directory handle
    across a rename.
    """
    parent = Path(path).absolute().parent
    try:
        info = os.lstat(str(parent))
    except (OSError, ValueError) as exc:
        raise StateError(f"cannot inspect state parent directory: {parent}") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise StateError(f"state parent is not a directory: {parent}")
    return (info.st_dev, info.st_ino)


def _assert_parent_identity(path: Path, expected) -> None:
    if _parent_identity(path) != expected:
        raise StateError(f"state parent directory changed during operation: {path}")


def _dir_signature(path: Path) -> str:
    """Hash the directory's immediate entry names.

    dev/ino alone is not a sound identity: a directory that is deleted frees
    its inode, and a newly created directory in the same parent can be handed
    the very same inode number. The entry-name signature survives a rename (so
    a legitimate install still verifies) while a replacement directory with
    different contents produces a different signature.
    """
    import hashlib

    try:
        with os.scandir(str(path)) as entries:
            names = sorted(entry.name for entry in entries)
    except (OSError, ValueError):
        return ""
    return hashlib.sha256(
        "\n".join(names[:64]).encode("utf-8", "replace")
    ).hexdigest()[:32]


def _dir_identity(path: Path):
    try:
        info = os.lstat(str(path))
    except (OSError, ValueError) as exc:
        raise StateError(f"cannot inspect directory state path: {path}") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise StateError(f"state path is not a directory: {path}")
    return (info.st_dev, info.st_ino, _dir_signature(path))


def _assert_dir_identity(path: Path, expected) -> None:
    ensure_safe_path(path)
    if _dir_identity(path) != expected:
        raise StateError(f"directory state path changed during operation: {path}")


def _capture_existing_target(path: Path, max_bytes: int = 20_000_000):
    """Capture current target bytes+mode so a failed commit can be undone."""
    try:
        _reject_symlink_chain(path)
        if not path.exists():
            return False, None, None
        ensure_regular_file(path)
        info = os.lstat(str(path))
        if info.st_size > max_bytes:
            raise StateError(f"existing state target exceeds rollback budget: {path}")
        return True, _read_regular_source(path, max_bytes), stat.S_IMODE(info.st_mode)
    except FileNotFoundError:
        return False, None, None


def _restore_captured_target(path: Path, existed: bool, old_data, old_mode) -> None:
    """Undo a committed-but-unverified replacement using the captured target.

    The restored content is written to a fresh private temp file and moved onto
    the target with os.replace. We never open the target for writing, so a
    concurrent actor cannot turn the rollback into a write through a hardlink
    or reparse planted at the target path; os.replace only swaps the directory
    entry, leaving any external inode it pointed at untouched. The rollback temp
    carries the same pre/post identity checks as every other commit, and a
    detected swap is retried a bounded number of times before failing closed.
    """
    for _attempt in range(3):
        rollback_temp = None
        failure = None
        retry = False
        try:
            _reject_symlink_chain(path)
            if not existed:
                if path.exists() or path.is_symlink():
                    path.unlink()
                _fsync_directory(path.parent)
                return
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".rollback", dir=str(path.parent)
            )
            rollback_temp = Path(temp_name)
            with os.fdopen(fd, "wb") as handle:
                fd = -1
                handle.write(old_data or b"")
                handle.flush()
                os.fsync(handle.fileno())
            try:
                _chmod_no_follow(rollback_temp, stat.S_IMODE(old_mode) & 0o700 if old_mode is not None else 0o600)
            except OSError:
                pass
            temp_identity = _path_identity(rollback_temp)
            try:
                _assert_path_identity(rollback_temp, temp_identity)
            except StateError as pre_exc:
                # The rollback temp was swapped before it was committed. Do not
                # touch the target; discard this attempt and capture a new one.
                failure = pre_exc
                retry = True
            else:
                os.replace(str(rollback_temp), str(path))
                rollback_temp = None
                try:
                    _assert_path_identity(path, temp_identity)
                except StateError as verify_exc:
                    # A concurrent actor swapped the rollback temp before the
                    # rename; the target now holds unverified content. Retry the
                    # restore, which overwrites it.
                    failure = verify_exc
                    retry = True
                else:
                    _fsync_directory(path.parent)
                    return
        except StateError as exc:
            failure = exc
        except Exception as exc:
            failure = StateError(
                f"commit verification failed and target rollback failed: {exc}"
            )
            failure.__cause__ = exc
        finally:
            if rollback_temp is not None:
                try:
                    rollback_temp.unlink()
                except FileNotFoundError:
                    pass
                except OSError as cleanup_exc:
                    retry = False
                    if failure is None:
                        failure = StateError(
                            f"target rollback cleanup failed: {cleanup_exc}"
                        )
                    else:
                        failure = StateError(
                            f"{failure}; target rollback cleanup failed: {cleanup_exc}"
                        )
        if not retry:
            raise failure
    raise StateError("commit verification failed and target rollback could not be verified")


def ensure_regular_file(path: Path) -> Path:
    """Require a real regular file, not a FIFO/device/reparse object."""
    path = Path(path)
    _reject_symlink_chain(path)
    try:
        mode = os.lstat(str(path)).st_mode
    except OSError as exc:
        raise StateError(f"state source is not a regular file: {path}") from exc
    if not stat.S_ISREG(mode):
        raise StateError(f"state source is not a regular file: {path}")
    return path


def _normalized_persistent_path(value: Path) -> Path:
    raw = Path(value)
    if ".." in raw.parts:
        raise StateError(f"persistent path must not contain '..': {raw}")
    # An embedded NUL makes the path unusable for any OS call; refuse it as a
    # typed StateError here so it never surfaces as a raw ValueError.
    if "\x00" in str(raw):
        raise StateError("persistent path must not contain a NUL character")
    normalized = Path(os.path.abspath(os.path.expanduser(str(raw))))
    # On Windows a component such as "CON" or "COM1" silently resolves to the
    # device rather than a file, so a write aimed at it escapes the directory
    # it was validated against. This is Windows-specific on purpose: those names
    # are ordinary files on POSIX, and archive members are already refused
    # unconditionally by _safe_zip_name because they travel between systems.
    if os.name == "nt":
        drive = normalized.anchor
        for component in normalized.parts:
            if component == drive or component in (os.sep, os.altsep):
                continue
            if not _safe_zip_name(component):
                raise StateError(
                    f"persistent path has a reserved device name component: {normalized}"
                )
    return normalized


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except (OSError, AttributeError):
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _private_mode(path: Path, private: bool) -> int:
    try:
        mode = stat.S_IMODE(os.lstat(str(path)).st_mode)
    except FileNotFoundError:
        return 0o600 if private else 0o644
    except OSError as exc:
        raise StateError(f"cannot inspect existing state {path}: {exc}") from exc
    if not stat.S_ISREG(os.lstat(str(path)).st_mode):
        raise StateError(f"state target is not a regular file: {path}")
    if private:
        # Keep owner access (including execute when present), but never
        # preserve group/other access for a secret-bearing config.
        mode = (mode & 0o700) | 0o600
    return mode


def _chmod_no_follow(path: Path, mode: int) -> None:
    """Set mode without intentionally following a replaced final symlink."""
    path = Path(path)
    _reject_symlink_chain(path)
    try:
        os.chmod(str(path), mode, follow_symlinks=False)
    except (NotImplementedError, TypeError):
        # Windows/Python builds may not expose follow_symlinks=False; the
        # before/after checks still make ordinary replacement refusal visible.
        _reject_symlink_chain(path)
        os.chmod(str(path), mode)
    _reject_symlink_chain(path)


def atomic_write_bytes(path: Path, data: bytes, private: bool = True) -> None:
    """Write bytes through a unique same-directory file and atomic replace.

    The current target is captured (bounded) first so that if a concurrent actor
    swaps our temp file between the final identity check and os.replace, the
    post-commit verification detects it and restores the pre-write target.
    """
    path = _normalized_persistent_path(path)
    _reject_symlink_chain(path)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    _reject_symlink_chain(path.parent)
    if path.exists() and not path.is_file():
        raise StateError(f"state target is not a regular file: {path}")
    old_existed, old_data, old_mode = _capture_existing_target(path)
    parent_identity = _parent_identity(path)
    mode = _private_mode(path, private)
    # Temp creation happens before the guarded block below, so its failure must
    # be converted here or a raw OSError escapes the typed persistence API and
    # bypasses callers that only handle StateError.
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
    except OSError as exc:
        raise StateError(f"cannot create state temp file in {path.parent}: {exc}") from exc
    temp = Path(temp_name)
    temp_identity = None
    committed = False
    try:
        try:
            _chmod_no_follow(temp, mode)
        except OSError:
            pass
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temp_identity = _path_identity(temp)
        _assert_path_identity(temp, temp_identity)
        _assert_parent_identity(path, parent_identity)
        _reject_symlink_chain(path)
        if path.is_symlink():
            raise StateError(f"refusing symlink target: {path}")
        os.replace(str(temp), str(path))
        committed = True
        try:
            _assert_path_identity(path, temp_identity)
            _assert_parent_identity(path, parent_identity)
        except StateError as verify_exc:
            # Only undo the write while the parent is still the one we checked;
            # restoring into a swapped parent would just redirect it again.
            if _parent_identity(path) == parent_identity:
                _restore_captured_target(path, old_existed, old_data, old_mode)
                raise StateError(
                    "state write commit verification failed; target restored to previous content"
                ) from verify_exc
            raise StateError(
                "state write commit verification failed: the parent directory was replaced, "
                "so the target was NOT rolled back and needs manual inspection"
            ) from verify_exc
        _fsync_directory(path.parent)
    except Exception as exc:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        # Only try to remove our temp while it is still present; once the
        # rename committed, temp no longer exists and unlink would misreport.
        if not committed and temp_identity is not None and temp.exists():
            try:
                if _path_identity(temp) == temp_identity:
                    temp.unlink()
            except (OSError, ValueError) as cleanup_exc:
                raise StateError(
                    f"atomic state write failed and temporary cleanup failed: {cleanup_exc}"
                ) from exc
        elif not committed and temp_identity is None and temp.exists():
            try:
                temp.unlink()
            except (OSError, ValueError) as cleanup_exc:
                raise StateError(
                    f"atomic state write failed and temporary cleanup failed: {cleanup_exc}"
                ) from exc
        raise


def atomic_write_text(path: Path, text: str, private: bool = True) -> None:
    atomic_write_bytes(path, text.encode("utf-8"), private=private)


def atomic_write_json(path: Path, value: Any, private: bool = True) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n", private=private)



_BACKUP_SEQUENCE_LOCK = threading.Lock()
_LAST_BACKUP_SEQUENCE = 0
_BACKUP_SEQUENCE_RE = re.compile(r"-(\d{20})-[0-9a-f]{12}(?:\.zip)?$")


def _next_backup_sequence() -> int:
    global _LAST_BACKUP_SEQUENCE
    with _BACKUP_SEQUENCE_LOCK:
        candidate = time.time_ns()
        if candidate <= _LAST_BACKUP_SEQUENCE:
            candidate = _LAST_BACKUP_SEQUENCE + 1
        _LAST_BACKUP_SEQUENCE = candidate
        return candidate


def backup_sort_key(path: Path):
    """Return a creation-order key while retaining compatibility with old names."""
    path = Path(path)
    match = _BACKUP_SEQUENCE_RE.search(path.name)
    if match:
        return (1, int(match.group(1)), path.name)
    try:
        modified_ns = path.stat().st_mtime_ns
    except OSError:
        modified_ns = 0
    return (0, modified_ns, path.name)


def unique_backup_path(directory: Path, prefix: str, suffix: str = ".zip") -> Path:
    """Return a collision-resistant, monotonic backup path."""
    directory = Path(directory)
    if not _safe_filename_component(prefix) or (suffix and not _safe_filename_component(suffix)):
        raise StateError("backup name component is unsafe")
    sequence = _next_backup_sequence()
    stamp = datetime.datetime.fromtimestamp(sequence / 1_000_000_000).strftime("%Y%m%d-%H%M%S")
    token = uuid.uuid4().hex[:12]
    return directory / f"{prefix}-{stamp}-{sequence:020d}-{token}{suffix}"


def _safe_zip_name(name: str) -> bool:
    if not name or "\x00" in name or "\\" in name or name.startswith("/") or ":" in name:
        return False
    if any(character in name for character in '<>|?*'):
        return False
    windows_path = PureWindowsPath(name)
    if windows_path.drive or windows_path.root or windows_path.is_absolute():
        return False
    reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    reserved.update(f"COM{index}" for index in range(1, 10))
    reserved.update(f"LPT{index}" for index in range(1, 10))
    # Windows also treats superscript digits as device-name digits, so COM³,
    # LPT² and CON¹ are device aliases. Normalising them to ASCII digits keeps
    # the reserved check exact: "COM³" is refused while "COM³-party" is not.
    superscripts = str.maketrans("¹²³", "123")
    parts = name.split("/")
    for part in parts:
        if not part or part in {".", ".."} or part.endswith((".", " ")):
            return False
        stem = part.split(".", 1)[0].translate(superscripts)
        if stem.upper() in reserved:
            return False
    return True


def _safe_filename_component(name: str) -> bool:
    return bool(name) and "/" not in name and "\\" not in name and _safe_zip_name(name)


def _raw_archive_budget(max_bytes: int) -> int:
    return max(64 * 1024, max_bytes * 2)


def _validate_zip_infos(infos, max_files: int, max_bytes: int):
    if len(infos) > max_files:
        raise StateError(f"backup exceeds file budget ({max_files})")
    names = set()
    total = 0
    compressed_total = 0
    metadata_total = 0
    name_limit = min(4096, max_bytes)
    for info in infos:
        if info.filename in names or not _safe_zip_name(info.filename):
            raise StateError(f"unsafe or duplicate backup member: {info.filename!r}")
        names.add(info.filename)
        name_bytes = len(info.filename.encode("utf-8", "surrogatepass"))
        metadata_total += name_bytes + len(info.extra) + len(info.comment)
        if name_bytes > name_limit or metadata_total > max_bytes:
            raise StateError(f"backup metadata exceeds size budget ({max_bytes} bytes)")
        if info.flag_bits & 0x1:
            raise StateError("encrypted backup members are not supported")
        mode = (info.external_attr >> 16) & 0xFFFF
        if mode and stat.S_ISLNK(mode):
            raise StateError(f"symlink backup member refused: {info.filename}")
        total += int(info.file_size)
        compressed_total += int(info.compress_size)
        if total > max_bytes or compressed_total > max_bytes * 2:
            raise StateError(f"backup exceeds size budget ({max_bytes} bytes)")


def _preflight_zip_entry_count(stream, max_files: int) -> None:
    """Reject an oversized central directory from the EOCD before parsing it.

    zipfile.ZipFile materialises every central-directory record in memory as
    soon as it is constructed, so checking len(infolist()) afterwards does not
    bound the parser's memory. The end-of-central-directory record states the
    entry count, so read that first and refuse early.
    """
    window_size = 65557  # 22-byte EOCD plus the largest legal archive comment
    stream.seek(0, os.SEEK_END)
    archive_size = stream.tell()
    if archive_size < 22:
        return
    window = min(archive_size, window_size)
    stream.seek(archive_size - window)
    tail = stream.read(window)
    index = tail.rfind(b"PK\x05\x06")
    if index < 0 or index + 22 > len(tail):
        return
    comment_length = int.from_bytes(tail[index + 20 : index + 22], "little")
    # The EOCD must be the final record. Trailing data after it is how a
    # forger dodges this preflight, and zipfile would still parse the archive
    # in full, so refuse rather than hand it to the parser.
    if index + 22 + comment_length != len(tail):
        raise StateError("backup has trailing data after its central directory")
    total_entries = int.from_bytes(tail[index + 10 : index + 12], "little")
    central_size = int.from_bytes(tail[index + 12 : index + 16], "little")
    central_offset = int.from_bytes(tail[index + 16 : index + 20], "little")
    if total_entries == 0xFFFF or central_offset == 0xFFFFFFFF:
        # ZIP64 sentinel: at least 65535 entries.
        if 65535 > max_files:
            raise StateError(f"backup exceeds file budget ({max_files} entries)")
        return
    if total_entries > max_files:
        raise StateError(f"backup exceeds file budget ({max_files} entries)")
    # The central directory must end exactly where the EOCD begins. A forged
    # offset or size would otherwise make the scan below stop early (or read
    # nothing) and hand the archive to the full parser anyway, so fail closed.
    eocd_offset = (archive_size - window) + index
    if central_offset + central_size != eocd_offset or central_offset <= 0:
        raise StateError("backup central directory is inconsistent with its trailer")
    # Walk the central directory and count records with a fixed 46-byte buffer.
    # This bounds the work before zipfile materialises a ZipInfo per entry, and
    # it never allocates a filename, extra field or comment while doing so.
    position = central_offset
    end = eocd_offset
    counted = 0
    while position < end:
        stream.seek(position)
        header = stream.read(46)
        if len(header) < 46 or header[:4] != b"PK\x01\x02":
            raise StateError("backup central directory is malformed")
        name_length = int.from_bytes(header[28:30], "little")
        extra_length = int.from_bytes(header[30:32], "little")
        comment_length = int.from_bytes(header[32:34], "little")
        position += 46 + name_length + extra_length + comment_length
        counted += 1
        if counted > max_files:
            raise StateError(f"backup exceeds file budget ({max_files} entries)")
    if position != end or counted != total_entries:
        raise StateError("backup central directory is inconsistent with its trailer")


def _inspect_zip_stream(stream, max_files: int, max_bytes: int):
    import zipfile

    try:
        stream.seek(0, os.SEEK_END)
        archive_size = stream.tell()
        raw_budget = _raw_archive_budget(max_bytes)
        if archive_size > raw_budget:
            raise StateError(f"backup exceeds raw archive budget ({raw_budget} bytes)")
        _preflight_zip_entry_count(stream, max_files)
        stream.seek(0)
        with zipfile.ZipFile(stream, "r") as archive:
            infos = archive.infolist()
            if len(archive.comment) > max_bytes:
                raise StateError(f"backup metadata exceeds size budget ({max_bytes} bytes)")
            _validate_zip_infos(infos, max_files, max_bytes)
            return infos
    except StateError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError) as exc:
        raise StateError(f"cannot inspect backup archive: {exc}") from exc


def inspect_zip(path: Path, max_files: int = 2000, max_bytes: int = 20_000_000):
    """Validate archive names, symlink members, duplicates, and expansion budget."""
    path = ensure_regular_file(path)
    try:
        raw_budget = _raw_archive_budget(max_bytes)
        if path.stat().st_size > raw_budget:
            raise StateError(f"backup exceeds raw archive budget ({raw_budget} bytes)")
        with path.open("rb") as stream:
            return _inspect_zip_stream(stream, max_files, max_bytes)
    except StateError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError) as exc:
        raise StateError(f"cannot inspect backup {path}: {exc}") from exc


def _read_archive_bytes(path: Path, max_files: int = 2000, max_bytes: int = 20_000_000):
    data = _read_regular_source(Path(path), _raw_archive_budget(max_bytes))
    _inspect_zip_stream(io.BytesIO(data), max_files, max_bytes)
    return data


def read_archive_bytes(path: Path, max_files: int = 2000, max_bytes: int = 20_000_000):
    """Read and validate one archive into a bounded immutable byte buffer."""
    return _read_archive_bytes(path, max_files=max_files, max_bytes=max_bytes)


def read_snapshotted_bytes(
    snapshot: Path, expected_identity, max_files: int = 2000, max_bytes: int = 20_000_000
):
    """Read a snapshot, refusing it if it is not the file that was verified.

    Without this, a writer that replaces the snapshot between creation and the
    read gets its own archive restored while the report still claims success.
    """
    _assert_path_identity(snapshot, expected_identity)
    return _read_archive_bytes(snapshot, max_files=max_files, max_bytes=max_bytes)


def read_backup_layout(path: Path, max_files: int = 2000, max_bytes: int = 20_000_000):
    """Return a manifest and member names from one validated archive byte stream."""
    data = _read_archive_bytes(path, max_files=max_files, max_bytes=max_bytes)
    return read_backup_layout_bytes(data, max_files=max_files, max_bytes=max_bytes)


def read_backup_layout_bytes(
    data: bytes, max_files: int = 2000, max_bytes: int = 20_000_000
):
    return _read_backup_layout_from_stream(
        io.BytesIO(data), max_files=max_files, max_bytes=max_bytes
    )


def _read_backup_layout_from_stream(
    stream, max_files: int = 2000, max_bytes: int = 20_000_000
):
    infos = _inspect_zip_stream(stream, max_files, max_bytes)
    names = [info.filename for info in infos]
    if "_manifest.json" not in names:
        raise StateError("backup has no manifest")
    try:
        stream.seek(0)
        with zipfile.ZipFile(stream, "r") as archive:
            raw = archive.read("_manifest.json")
        manifest = json.loads(raw.decode("utf-8"))
    except StateError:
        raise
    except (OSError, UnicodeError, ValueError, zipfile.BadZipFile, RuntimeError) as exc:
        raise StateError(f"cannot read backup manifest: {exc}") from exc
    if not isinstance(manifest, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) or not value
        for key, value in manifest.items()
    ):
        raise StateError("backup manifest is invalid")
    return manifest, names


def snapshot_zip(
    path: Path, max_files: int = 2000, max_bytes: int = 20_000_000
) -> Tuple[Path, Tuple]:
    """Copy one validated archive to a private immutable-path snapshot.

    Returns the snapshot path together with the identity it was verified at, so
    a caller that reads it later can prove it is reading these bytes. Returning
    the path alone leaves a window in which another writer can replace the
    snapshot between creation and the caller's re-open.
    """
    path = _normalized_persistent_path(path)
    ensure_regular_file(path)
    inspect_zip(path, max_files=max_files, max_bytes=max_bytes)
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=".agentfix-restore-", suffix=".zip", dir=str(path.parent)
        )
    except OSError as exc:
        raise StateError(f"cannot create snapshot temp file in {path.parent}: {exc}") from exc
    temp = Path(temp_name)
    source_fd = None
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        source_fd = os.open(str(path), flags)
        source_before = os.fstat(source_fd)
        if not stat.S_ISREG(source_before.st_mode):
            os.close(source_fd)
            source_fd = None
            raise StateError(f"backup source is not a regular file: {path}")
        with os.fdopen(source_fd, "rb") as source:
            source_fd = None
            with os.fdopen(fd, "wb") as destination:
                fd = -1
                total = 0
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _raw_archive_budget(max_bytes):
                        raise StateError("backup snapshot exceeds size budget")
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
        _reject_symlink_chain(path)
        if _path_identity(path) != (
            source_before.st_dev,
            source_before.st_ino,
            source_before.st_size,
            source_before.st_mtime_ns,
            source_before.st_nlink,
        ):
            raise StateError(f"backup source changed while being snapshotted: {path}")
        temp_identity = _path_identity(temp)
        _assert_path_identity(temp, temp_identity)
        _reject_symlink_chain(path)
        try:
            _chmod_no_follow(temp, 0o600)
        except OSError:
            pass
        _assert_path_identity(temp, temp_identity)
        _reject_symlink_chain(temp)
        inspect_zip(temp, max_files=max_files, max_bytes=max_bytes)
        _assert_path_identity(temp, temp_identity)
        return temp, temp_identity
    except Exception as exc:
        if source_fd is not None:
            try:
                os.close(source_fd)
            except OSError:
                pass
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            temp.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            raise StateError(
                f"backup snapshot failed and temporary cleanup failed: {cleanup_exc}"
            ) from exc
        raise


def create_zip_atomic(
    path: Path,
    members,
    manifest: Any,
    max_files: int = 2000,
    max_bytes: int = 20_000_000,
) -> Path:
    """Create a private ZIP through a temp file and atomic rename."""
    import zipfile

    path = _normalized_persistent_path(path)
    _reject_symlink_chain(path)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    _reject_symlink_chain(path.parent)
    manifest_text = json.dumps(manifest, indent=2, ensure_ascii=False)
    manifest_size = len(manifest_text.encode("utf-8"))
    if max_files < 1 or manifest_size > max_bytes:
        raise StateError("backup manifest exceeds configured budget")
    old_existed, old_data, old_mode = _capture_existing_target(path, max_bytes)
    parent_identity = _parent_identity(path)
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
    except OSError as exc:
        raise StateError(f"cannot create archive temp file in {path.parent}: {exc}") from exc
    temp = Path(temp_name)
    count = 1  # the manifest is an archive member too
    total = 0
    names = set()
    temp_identity = None
    committed = False
    try:
        with os.fdopen(fd, "w+b") as handle:
            fd = -1
            with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for arcname, source_value in members:
                    source = Path(source_value)
                    _reject_symlink_chain(source)
                    if not source.is_file():
                        raise StateError(f"backup source is not a regular file: {source}")
                    data = _read_regular_source(source, max_bytes)
                    size = len(data)
                    if count + 1 > max_files or total + size + manifest_size > max_bytes:
                        raise StateError("backup source exceeds configured budget")
                    count += 1
                    total += size
                    if not _safe_zip_name(arcname) or arcname in names or arcname == "_manifest.json":
                        raise StateError(f"unsafe or duplicate backup member name: {arcname!r}")
                    names.add(arcname)
                    archive.writestr(arcname, data)
                archive.writestr("_manifest.json", manifest_text)
            handle.flush()
            os.fsync(handle.fileno())
        temp_identity = _path_identity(temp)
        _assert_path_identity(temp, temp_identity)
        # Re-read the committed temporary archive so source growth/replacement
        # during archive.write cannot bypass the declared budget or name rules.
        inspect_zip(temp, max_files=max_files, max_bytes=max_bytes)
        try:
            _chmod_no_follow(temp, 0o600)
        except OSError:
            pass
        _assert_path_identity(temp, temp_identity)
        _assert_parent_identity(path, parent_identity)
        _reject_symlink_chain(path)
        os.replace(str(temp), str(path))
        committed = True
        try:
            _assert_path_identity(path, temp_identity)
            _assert_parent_identity(path, parent_identity)
        except StateError as verify_exc:
            # Only undo the write while the parent is still the one we checked.
            if _parent_identity(path) == parent_identity:
                _restore_captured_target(path, old_existed, old_data, old_mode)
                raise StateError(
                    "backup commit verification failed; target restored to previous archive"
                ) from verify_exc
            raise StateError(
                "backup commit verification failed: the parent directory was replaced, "
                "so the target was NOT rolled back and needs manual inspection"
            ) from verify_exc
        _fsync_directory(path.parent)
        return path
    except Exception as exc:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if not committed and temp.exists():
            try:
                if temp_identity is not None and _path_identity(temp) != temp_identity:
                    raise StateError("temporary archive path was replaced; not deleting it")
                temp.unlink()
            except (OSError, ValueError) as cleanup_exc:
                raise StateError(
                    f"backup archive creation failed and temporary cleanup failed: {cleanup_exc}"
                ) from exc
        raise


def _read_regular_source(source: Path, max_bytes: int) -> bytes:
    """Read one source without following a post-check symlink race."""
    source = Path(source)
    fd = None
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        fd = os.open(str(source), flags)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise StateError(f"backup source is not a regular file: {source}")
        with os.fdopen(fd, "rb") as handle:
            fd = None
            data = handle.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise StateError(f"backup source exceeds configured budget: {source}")
        _reject_symlink_chain(source)
        after = os.lstat(str(source))
        before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if before_identity != after_identity:
            raise StateError(f"backup source changed while being read: {source}")
        return data
    except StateError:
        raise
    except OSError as exc:
        raise StateError(f"cannot read backup source {source}: {exc}") from exc
    except ValueError as exc:
        # An embedded NUL (or similar unusable path) must be a typed refusal,
        # not a raw ValueError escaping the persistence API.
        raise StateError(f"backup source path is not usable: {source!r}") from exc
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def _remove_path(path: Path) -> None:
    # A junction/reparse reports as a directory but must be unlinked, never
    # recursed into: shutil.rmtree on a junction either fails or risks walking
    # into the directory it points at.
    if path.is_symlink() or _is_link_or_reparse(path):
        Path(path).unlink()
    elif path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _rollback_swapped_directory(
    target: Path,
    backup: Optional[Path],
    cause: Exception,
    parent_identity=None,
) -> None:
    """Undo a swap whose installed target failed post-commit verification."""
    try:
        # If the parent was replaced after the commit, moving the backup back
        # would hand the only good copy to whoever controls that directory.
        # Leave it in place for diagnosis instead.
        if parent_identity is not None and _parent_identity(target) != parent_identity:
            raise StateError(
                f"directory swap rollback refused: parent directory changed for {target}"
            )
        if target.is_symlink() or target.exists():
            _remove_path(target)
        if backup is not None and backup.exists():
            os.replace(str(backup), str(target))
        _fsync_directory(target.parent)
    except Exception as rollback_exc:
        raise StateError(
            f"directory swap verification failed and rollback failed: {rollback_exc}"
        ) from cause
    raise StateError(
        "directory swap installed an unverified target; rolled back to previous directory"
    ) from cause


def _swap_marker_path(target: Path) -> Path:
    return target.parent / f".{target.name}.swap.json"


_SWAP_MARKER_OWNER = "agentfix.state.directory-swap"


def _validate_swap_stage(target: Path, stage: Path) -> str:
    prefix = f".{target.name}.stage-"
    if stage.parent != target.parent or not stage.name.startswith(prefix):
        raise StateError("staged directory swap stage is not owned by target")
    token = stage.name[len(prefix) :]
    if not _safe_filename_component(token):
        raise StateError("staged directory swap stage has an invalid ownership token")
    return token

def _recover_directory_swap(
    target: Path, requested_stage: Optional[Path] = None
) -> Optional[Tuple[str, bool]]:
    """Finish or roll back a swap; the flag says whether requested stage was consumed."""
    target = Path(target).absolute()
    requested = Path(requested_stage).absolute() if requested_stage is not None else None
    marker = _swap_marker_path(target)
    if not marker.exists():
        return None
    ensure_safe_path(marker)
    ensure_regular_file(marker)
    try:
        if marker.stat().st_size > 64 * 1024:
            raise StateError("staged directory swap marker is too large")
        raw_marker = _read_regular_source(marker, 64 * 1024)
        data = json.loads(raw_marker.decode("utf-8"))
        if not isinstance(data, dict):
            raise StateError("staged directory swap marker is invalid")
        if data.get("version") != 1:
            raise StateError("unsupported staged directory swap marker version")
        target_value = data.get("target")
        if (
            not isinstance(target_value, str)
            or "\x00" in target_value
            or Path(target_value).absolute() != target
        ):
            raise StateError("staged directory swap marker targets another directory")
        stage_value = data.get("stage")
        if not isinstance(stage_value, str) or "\x00" in stage_value:
            raise StateError("staged directory swap marker has no valid stage")
        stage = Path(stage_value).absolute()
        backup_value = data.get("backup")
        if backup_value is not None and (
            not isinstance(backup_value, str)
            or not backup_value
            or "\x00" in backup_value
        ):
            raise StateError("staged directory swap marker has an invalid backup")
        backup = Path(backup_value).absolute() if backup_value else None
        state_value = data.get("state")
        if not isinstance(state_value, str):
            raise StateError("staged directory swap marker has no valid state")
        state_name = state_value
        stage_token = _validate_swap_stage(target, stage)
        if data.get("owner") != _SWAP_MARKER_OWNER or data.get("token") != stage_token:
            raise StateError("staged directory swap marker is not owned by this operation")
        stage_dev = data.get("stage_dev")
        stage_ino = data.get("stage_ino")
        stage_sig = data.get("stage_sig")
        backup_dev = data.get("backup_dev")
        backup_ino = data.get("backup_ino")
        backup_sig = data.get("backup_sig")
        old_target_dev = data.get("old_target_dev")
        old_target_ino = data.get("old_target_ino")
        old_target_sig = data.get("old_target_sig")
        old_target_present = [
            value is not None for value in (old_target_dev, old_target_ino, old_target_sig)
        ]
        if any(old_target_present) and not all(old_target_present):
            raise StateError("staged directory swap marker has a partial old target identity")
        if all(old_target_present):
            if (
                not isinstance(old_target_dev, int)
                or not isinstance(old_target_ino, int)
                or isinstance(old_target_dev, bool)
                or isinstance(old_target_ino, bool)
                or not isinstance(old_target_sig, str)
                or not _safe_filename_component(old_target_sig)
            ):
                raise StateError("staged directory swap marker has an invalid old target identity")
        present = [value is not None for value in (stage_dev, stage_ino, stage_sig)]
        if any(present) and not all(present):
            raise StateError("staged directory swap marker has a partial stage identity")
        if all(present):
            if (
                not isinstance(stage_dev, int)
                or not isinstance(stage_ino, int)
                or isinstance(stage_dev, bool)
                or isinstance(stage_ino, bool)
                or not isinstance(stage_sig, str)
                or not _safe_filename_component(stage_sig)
            ):
                raise StateError("staged directory swap marker has an invalid stage identity")
        # The backup path alone is not proof of anything: a concurrent actor can
        # replace the directory sitting at that name. Record its identity too.
        backup_present = [value is not None for value in (backup_dev, backup_ino, backup_sig)]
        if any(backup_present) and not all(backup_present):
            raise StateError("staged directory swap marker has a partial backup identity")
        if all(backup_present):
            if (
                not isinstance(backup_dev, int)
                or not isinstance(backup_ino, int)
                or isinstance(backup_dev, bool)
                or isinstance(backup_ino, bool)
                or not isinstance(backup_sig, str)
                or not _safe_filename_component(backup_sig)
            ):
                raise StateError("staged directory swap marker has an invalid backup identity")
    except StateError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise StateError(f"cannot recover staged directory swap: {exc}") from exc
    if state_name not in {"prepared", "old-moved", "new-installed"}:
        raise StateError("staged directory swap marker has an invalid state")
    if backup is not None and (backup.parent != target.parent or backup == target or backup == stage):
        raise StateError("staged directory swap marker escapes target parent")
    if backup is not None:
        backup_prefix = f".{target.name}.old-"
        if not backup.name.startswith(backup_prefix):
            raise StateError("staged directory swap marker has an invalid backup name")
        if not _safe_filename_component(backup.name[len(backup_prefix) :]):
            raise StateError("staged directory swap marker has an invalid backup name")

    # Validate every referenced object before mutating target. A malformed or
    # reparse marker must remain available for diagnosis, never be partially
    # applied.
    ensure_safe_path(target)
    ensure_safe_path(stage)
    if stage.exists() and not stage.is_dir():
        raise StateError(f"staged directory swap stage is not a directory: {stage}")
    if backup is not None:
        ensure_safe_path(backup)
        if backup.exists() and not backup.is_dir():
            raise StateError(f"staged directory swap backup is not a directory: {backup}")
    if target.exists() and not target.is_dir():
        raise StateError(f"staged directory swap target is not a directory: {target}")
    target_exists = target.exists()
    stage_exists = stage.exists()
    backup_exists = backup is not None and backup.exists()
    if target_exists and backup_exists and stage_exists:
        raise StateError("staged directory swap has inconsistent target/stage/backup state")
    if state_name == "new-installed" and not target_exists:
        raise StateError("staged directory swap marker claims new target is missing")
    # When a target is present, a state recorded past the old-target rename can
    # only be trusted with the recorded identities. Without them there is no way
    # to tell a completed swap from an unverified object merely occupying the
    # path, so fail closed instead of consuming the marker. When no target
    # exists there is nothing to adopt, so the rollback path stays available.
    if state_name in {"old-moved", "new-installed"} and target_exists and stage_dev is None:
        raise StateError(
            "staged directory swap marker lacks the stage identity needed to verify the target"
        )
    if state_name == "old-moved" and backup is None:
        raise StateError("staged directory swap marker claims old-moved without a backup")
    if state_name in {"old-moved", "new-installed"} and backup_exists and backup_dev is None:
        # A marker written by this module always records the backup identity
        # once the old target has been moved; its absence means a forged or
        # stale marker whose backup could have been swapped, so refuse to
        # install it.
        raise StateError(
            "staged directory swap marker lacks the backup identity needed to restore"
        )
    if state_name == "old-moved" and target_exists and stage_exists:
        raise StateError("staged directory swap has a stage and an unverified target")

    if target_exists:
        if state_name == "prepared" and backup_exists:
            raise StateError("staged directory swap has target and backup before old target moved")
        if state_name == "new-installed" and stage_exists:
            raise StateError("staged directory swap has a stage after new target was installed")
        # A marker whose target is present while the stage is already consumed
        # is ambiguous in every state: a crash between the stage rename and the
        # marker update looks identical to a concurrent actor landing an
        # unverified object, and the target can also be swapped after a
        # new-installed marker was written but before the backup was removed.
        # Only accept the target when it still carries the recorded stage
        # identity; otherwise restore the backup or fail closed.
        if not stage_exists:
            verified = stage_dev is not None and _dir_identity(target) == (
                stage_dev,
                stage_ino,
                stage_sig,
            )
            if not verified:
                if backup_exists:
                    # The backup is the only good copy left; refuse to move it
                    # into place unless it is still the directory we recorded.
                    if backup_dev is not None and _dir_identity(backup) != (
                        backup_dev,
                        backup_ino,
                        backup_sig,
                    ):
                        raise StateError(
                            "staged directory swap backup changed since it was recorded; refusing"
                        )
                    _remove_path(target)
                    os.replace(str(backup), str(target))
                    marker.unlink(missing_ok=True)
                    _fsync_directory(target.parent)
                    return "rolled_back", False
                # Nothing to restore: the unverified object must not be kept
                # or silently adopted. Leave the marker for diagnosis.
                raise StateError(
                    "staged directory swap left an unverified target with no backup; refusing"
                )
        requested_consumed = (
            state_name in {"old-moved", "new-installed"}
            and requested is not None
            and stage == requested
            and not stage_exists
        )
        completed = requested_consumed or state_name == "new-installed"
        if completed:
            _chmod_no_follow(target, 0o700)
        if backup_exists:
            _remove_path(backup)
        marker.unlink(missing_ok=True)
        # A prepared marker with the stage still present means the old target
        # was never moved; an old-moved/new-installed marker with the stage
        # already absent means the pending swap completed. Only the latter
        # consumes the caller's requested stage.
        return "installed", requested_consumed

    # A backup can exist while the marker still says prepared: the process
    # crashed after renaming the old target but before persisting old-moved.
    if backup_exists:
        # A prepared marker predates the backup rename, so it carries the old
        # target's identity instead; later states record the backup directly.
        if backup_dev is not None:
            expected = (backup_dev, backup_ino, backup_sig)
        elif old_target_dev is not None:
            expected = (old_target_dev, old_target_ino, old_target_sig)
        else:
            raise StateError(
                "staged directory swap marker lacks the backup identity needed to restore"
            )
        if _dir_identity(backup) != expected:
            raise StateError(
                "staged directory swap backup changed since it was recorded; refusing"
            )
        os.replace(str(backup), str(target))
        marker.unlink(missing_ok=True)
        return "rolled_back", False
    if state_name == "prepared" and stage_exists:
        os.replace(str(stage), str(target))
        _chmod_no_follow(target, 0o700)
        _fsync_directory(target.parent)
        marker.unlink(missing_ok=True)
        return "installed", requested is not None and stage == requested
    raise StateError("staged directory swap cannot be recovered safely")


def atomic_replace_directory(stage: Path, target: Path) -> None:
    """Install a staged directory with process-level rollback and crash recovery."""
    stage = Path(stage).absolute()
    target = Path(target).absolute()
    recovery = _recover_directory_swap(target, requested_stage=stage)
    if recovery is not None and recovery[1]:
        return
    ensure_safe_path(target)
    stage_token = _validate_swap_stage(target, stage)
    ensure_safe_path(stage)
    if not stage.is_dir():
        raise StateError(f"staged directory missing: {stage}")
    target.parent.mkdir(parents=True, exist_ok=True)
    ensure_safe_path(target.parent)
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_dir():
            raise StateError(f"install target is not a regular directory: {target}")
        backup = target.parent / f".{target.name}.old-{uuid.uuid4().hex[:12]}"
    else:
        backup = None
    marker = _swap_marker_path(target)
    if marker.exists():
        raise StateError(f"staged directory swap marker already exists: {marker}")
    stage_identity = _dir_identity(stage)
    # The target's parent is where the stage, backup, and target renames all
    # land. Locking it stops a concurrent rename of the parent from redirecting
    # the install, the backup, or the rollback into a replacement directory.
    parent_identity = _parent_identity(target)
    # The old target's identity is known before the rename, so it is recorded
    # now. If we crash between the rename and the old-moved marker update, this
    # is what proves the backup really is the directory we moved aside.
    old_target_exists = target.exists()
    old_target_identity = _dir_identity(target) if old_target_exists else None
    marker_data = {
        "version": 1,
        "owner": _SWAP_MARKER_OWNER,
        "token": stage_token,
        "target": str(target),
        "stage": str(stage),
        "stage_dev": stage_identity[0],
        "stage_ino": stage_identity[1],
        "stage_sig": stage_identity[2],
        "backup": str(backup) if backup else None,
        "state": "prepared",
    }
    if old_target_identity is not None:
        marker_data["old_target_dev"] = old_target_identity[0]
        marker_data["old_target_ino"] = old_target_identity[1]
        marker_data["old_target_sig"] = old_target_identity[2]
    atomic_write_json(marker, marker_data, private=True)
    installed = False
    try:
        if backup is not None:
            os.replace(target, backup)
            # The renamed directory keeps the old target's permissions. Tighten
            # it immediately: a crash before cleanup must not leave a
            # world-readable copy of the old config sitting in the parent.
            try:
                _chmod_no_follow(backup, 0o700)
            except OSError:
                pass
            _fsync_directory(target.parent)
            backup_identity = _dir_identity(backup)
            marker_data["state"] = "old-moved"
            marker_data["backup_dev"] = backup_identity[0]
            marker_data["backup_ino"] = backup_identity[1]
            marker_data["backup_sig"] = backup_identity[2]
            atomic_write_json(marker, marker_data, private=True)
        _assert_dir_identity(stage, stage_identity)
        _assert_parent_identity(target, parent_identity)
        os.replace(stage, target)
        # Verify the object that actually landed is the stage we validated
        # before treating the swap as installed; a concurrent actor may have
        # replaced the stage path in the window before os.replace.
        try:
            _assert_dir_identity(target, stage_identity)
            _assert_parent_identity(target, parent_identity)
        except StateError as verify_exc:
            _rollback_swapped_directory(target, backup, verify_exc, parent_identity)
        # The stage keeps its build-time permissions, so lock the installed
        # config down before recording the install; a crash after this point
        # must not leave a world-readable target behind.
        try:
            _chmod_no_follow(target, 0o700)
        except OSError:
            pass
        _fsync_directory(target.parent)
        installed = True
        marker_data["state"] = "new-installed"
        atomic_write_json(marker, marker_data, private=True)
        # The target can still be replaced between the marker update and the
        # backup cleanup. Re-verify before deleting the only good copy so an
        # unverified object is never left behind with the old directory gone.
        try:
            _assert_dir_identity(target, stage_identity)
        except StateError as verify_exc:
            installed = False
            _rollback_swapped_directory(target, backup, verify_exc, parent_identity)
    except Exception:
        # Never resolve an ambiguous failure by deleting evidence. If the old
        # target was already moved aside and the installed target does not match
        # the staged directory we verified, the target is unverified whatever its
        # type: a regular attacker directory is as suspect as a reparse point.
        # Keep the marker and the backup so the state stays diagnosable.
        parent_swapped = False
        try:
            parent_swapped = _parent_identity(target) != parent_identity
        except (OSError, ValueError, StateError):
            parent_swapped = True
        target_untrusted = parent_swapped or _is_link_or_reparse(target)
        if backup is not None and backup.exists() and not parent_swapped:
            if target.exists() or target.is_symlink():
                try:
                    target_untrusted = target_untrusted or (
                        not target.is_dir() or _dir_identity(target) != stage_identity
                    )
                except (OSError, ValueError):
                    target_untrusted = True
            # An absent target is not an unverified object: restoring the
            # backup over it is the safe outcome, not an adoption.
        if not installed and backup is not None and backup.exists() and not target_untrusted:
            if not target.exists():
                os.replace(str(backup), str(target))
        if not installed and not target_untrusted:
            try:
                marker.unlink()
            except OSError:
                pass
        raise
    try:
        if backup is not None and backup.exists():
            _remove_path(backup)
        marker.unlink(missing_ok=True)
    except Exception:
        # The marker remains; the next install/operation will finish cleanup.
        raise
    try:
        _chmod_no_follow(target, 0o700)
    except OSError:
        pass
    _fsync_directory(target.parent)


def _snapshot_target(
    target: Path, max_bytes: int = 20_000_000, remaining: Optional[int] = None
):
    _reject_symlink_chain(target)
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    _reject_symlink_chain(target.parent)
    if not target.exists():
        return False, None, None
    ensure_regular_file(target)
    st = os.lstat(str(target))
    budget = max_bytes if remaining is None else remaining
    if st.st_size > budget:
        raise StateError(f"restore rollback snapshot exceeds configured budget: {target}")
    return True, _read_regular_source(target, budget), stat.S_IMODE(st.st_mode)


def _restore_transaction(items, max_bytes: int = 20_000_000) -> None:
    snapshots = []
    staged = []
    target_keys = set()
    created_dirs = set()
    cleanup_errors = []
    rollback_remaining = max_bytes
    failure: Optional[BaseException] = None
    failed = True
    try:
        for target, data in items:
            target = _normalized_persistent_path(target)
            key = os.path.normcase(str(target))
            if key in target_keys:
                raise StateError(f"duplicate restore target: {target}")
            target_keys.add(key)
            missing = []
            parent = target.parent
            while not parent.exists() and parent != parent.parent:
                missing.append(parent)
                parent = parent.parent
            created_dirs.update(missing)
            # Create the missing parents first: the capture below is a
            # precondition for every later commit and rollback check, and the
            # mkstemp below needs the directory to exist anyway.
            for directory in sorted(missing, key=lambda item: len(item.parts)):
                directory.mkdir(parents=True, exist_ok=True)
            # Capture the parent now, while it is still the directory we
            # validated. A rollback that re-checks only the target's own type
            # would otherwise write the old secret into a parent that was
            # renamed away and replaced after the commit.
            parent_identity = _parent_identity(target)
            snapshot = _snapshot_target(target, max_bytes, rollback_remaining)
            snapshots.append((target,) + snapshot + (parent_identity,))
            if snapshot[0] and snapshot[1] is not None:
                rollback_remaining -= len(snapshot[1])
            try:
                fd, temp_name = tempfile.mkstemp(
                    prefix=f".{target.name}.", suffix=".restore", dir=str(target.parent)
                )
            except OSError as exc:
                raise StateError(
                    f"cannot create restore temp file in {target.parent}: {exc}"
                ) from exc
            temp = Path(temp_name)
            try:
                with os.fdopen(fd, "wb") as handle:
                    fd = -1
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    _chmod_no_follow(temp, 0o600)
                except OSError:
                    pass
                temp_identity = _path_identity(temp)
                _assert_path_identity(temp, temp_identity)
            except Exception:
                if fd >= 0:
                    os.close(fd)
                temp.unlink(missing_ok=True)
                raise
            staged.append((target, temp, temp_identity, parent_identity))

        for target, temp, temp_identity, parent_identity in staged:
            _assert_path_identity(temp, temp_identity)
            _assert_parent_identity(target, parent_identity)
            _reject_symlink_chain(target)
            if target.is_symlink():
                raise StateError(f"refusing symlink restore target: {target}")
            os.replace(str(temp), str(target))
            _assert_path_identity(target, temp_identity)
            _assert_parent_identity(target, parent_identity)
        for target, existed, _, old_mode, _ in snapshots:
            if existed:
                try:
                    _chmod_no_follow(target, old_mode & 0o700)
                except OSError:
                    pass
        for parent in {target.parent for target, _, _, _ in staged}:
            _fsync_directory(parent)
        failed = False
    except Exception as exc:
        failure = exc
        for target, existed, old_data, old_mode, parent_identity in reversed(snapshots):
            try:
                _reject_symlink_chain(target)
                # If the parent was replaced, writing the old bytes back would
                # deposit the previous secret into an attacker-controlled
                # directory. Report the partial state instead of redirecting it.
                if _parent_identity(target) != parent_identity:
                    raise StateError(
                        f"restore rollback refused: parent directory changed for {target}"
                    )
                # The object at the target may have changed type after the
                # commit (file replaced by a directory, or a junction planted).
                # Refuse rather than deleting an unknown object: report the
                # partial state and leave the old bytes in the report instead.
                if _is_link_or_reparse(target):
                    raise StateError(
                        f"restore rollback refused: target is a symlink or reparse point: {target}"
                    )
                if existed:
                    if target.exists() and not target.is_file():
                        raise StateError(
                            f"restore rollback refused: target is no longer a regular file: {target}"
                        )
                    atomic_write_bytes(target, old_data or b"", private=True)
                    try:
                        _chmod_no_follow(target, old_mode & 0o700)
                    except OSError:
                        pass
                elif target.exists():
                    target.unlink()
            except Exception as rollback_exc:
                failure = StateError(
                    f"restore failed and rollback failed for {target}: {rollback_exc}"
                )
                failure.__cause__ = exc
                break
    finally:
        for _, temp, _, _ in staged:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                cleanup_errors.append(exc)
        if failed:
            for directory in sorted(created_dirs, key=lambda item: len(item.parts), reverse=True):
                try:
                    directory.rmdir()
                except OSError:
                    pass
    if failure is not None:
        if cleanup_errors:
            raise StateError(
                f"restore failed and temporary cleanup failed: {cleanup_errors[0]}"
            ) from failure
        raise failure
    if cleanup_errors:
        raise StateError("restore completed but temporary file cleanup failed")


def restore_zip_members(
    archive_path: Path,
    destinations,
    allowed_roots,
    max_bytes: int = 20_000_000,
):
    """Validate one archive byte stream and restore selected members transactionally."""
    data = _read_archive_bytes(archive_path, max_bytes=max_bytes)
    return restore_zip_members_bytes(
        data,
        destinations,
        allowed_roots,
        max_bytes=max_bytes,
        archive_path=archive_path,
    )


def restore_zip_members_bytes(
    data: bytes,
    destinations,
    allowed_roots,
    max_bytes: int = 20_000_000,
    archive_path: Optional[Path] = None,
):
    return _restore_zip_members_from_bytes(
        data,
        destinations,
        allowed_roots,
        max_bytes=max_bytes,
        archive_path=archive_path,
    )


def _restore_zip_members_from_bytes(
    data: bytes,
    destinations,
    allowed_roots,
    max_bytes: int = 20_000_000,
    archive_path: Optional[Path] = None,
):
    """Restore from one in-memory archive, never reopening a replaceable path."""
    stream = io.BytesIO(data)
    try:
        infos = _inspect_zip_stream(stream, 2000, max_bytes)
    except StateError:
        raise
    except (OSError, RuntimeError, EOFError, zipfile.BadZipFile, ValueError) as exc:
        raise StateError(f"cannot inspect backup archive: {exc}") from exc
    by_name = {info.filename: info for info in infos}
    if isinstance(allowed_roots, (str, Path)):
        allowed_roots = [allowed_roots]
    roots = []
    for root in allowed_roots:
        root = _normalized_persistent_path(root)
        ensure_safe_path(root)
        if not root.is_dir():
            raise StateError(f"restore allowed root is not a directory: {root}")
        roots.append(os.path.normcase(str(root)))
    if not roots:
        raise StateError("restore requires at least one allowed root")
    archive_key = None
    if archive_path is not None:
        archive_key = os.path.normcase(str(_normalized_persistent_path(archive_path)))
    selected = []
    destination_keys = set()
    total = 0
    try:
        stream.seek(0)
        archive = zipfile.ZipFile(stream, "r")
    except (OSError, RuntimeError, EOFError, zipfile.BadZipFile, ValueError) as exc:
        raise StateError(f"cannot open backup archive: {exc}") from exc
    with archive:
        for name, target in destinations.items():
            target = _normalized_persistent_path(target)
            inside = False
            matched_root = None
            target_key = os.path.normcase(str(target))
            for root in roots:
                try:
                    inside = os.path.commonpath([root, target_key]) == root
                except ValueError:
                    inside = False
                if inside:
                    matched_root = root
                    break
            if not inside:
                raise StateError(f"restore target escapes allowed roots: {target}")
            # Archive-derived members already pass _safe_zip_name, but these
            # destinations can also be supplied directly, so reject any
            # component below the allowed root that is a Windows device alias
            # or otherwise unsafe. The volume/drive part is not ours to judge.
            relative = os.path.relpath(str(target), matched_root)
            if relative not in (os.curdir, ""):
                for component in Path(relative).parts:
                    if not _safe_zip_name(component):
                        raise StateError(
                            f"restore target has an unsafe path component: {target}"
                        )
            if archive_key is not None and target_key == archive_key:
                raise StateError("restore target cannot be the archive itself")
            info = by_name.get(name)
            if info is None or info.is_dir():
                raise StateError(f"backup member not found: {name}")
            total += int(info.file_size)
            if total > max_bytes:
                raise StateError("restore exceeds size budget")
            try:
                with archive.open(info, "r") as source:
                    member_data = source.read(max_bytes + 1)
            except (OSError, RuntimeError, EOFError, zipfile.BadZipFile) as exc:
                raise StateError(f"cannot read backup member {name!r}: {exc}") from exc
            if len(member_data) > max_bytes:
                raise StateError("restore member exceeds size budget")
            key = os.path.normcase(str(target))
            if key in destination_keys:
                raise StateError(f"duplicate restore target: {target}")
            destination_keys.add(key)
            selected.append((target, member_data))
    _restore_transaction(selected, max_bytes=max_bytes)
    return [str(target) for target, _ in selected]
