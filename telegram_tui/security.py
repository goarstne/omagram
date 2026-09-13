"""Shared hardening helpers: trusted binary lookup, safe directories/writes.

These exist because Omagram runs as a bar-widget launcher (``run-omagram``)
and shells out to a handful of external tools (``ffmpeg``, ``mpv``,
``xdg-open``). A process's ``PATH`` is inherited, not authenticated: anything
earlier in the shell environment than the real system directories can shadow
those tools with a look-alike binary. The helpers below always search a fixed,
hardcoded set of trusted directories instead of trusting whatever ``PATH``
happens to say, and apply the same "don't trust attacker-writable state" idea
to on-disk directories and file writes.

Out of scope: a fully compromised ``$HOME`` (e.g. an attacker who can already
write into ``~/.local/bin`` with the user's own uid) is not a privilege
boundary this module can restore.
"""

from __future__ import annotations

import errno
import os
import re
import stat
import tempfile
from pathlib import Path


class MediaTooLargeError(Exception):
    """Raised to abort a download once it exceeds a hard size cap."""


class UnsafePathError(RuntimeError):
    """Raised when a state/cache path fails a safety check (e.g. a symlink)."""


# Order matters: system directories are searched before any user-writable
# ones, regardless of what the inherited PATH says.
TRUSTED_SYSTEM_DIRS: tuple[str, ...] = (
    "/usr/local/sbin",
    "/usr/local/bin",
    "/usr/sbin",
    "/usr/bin",
    "/sbin",
    "/bin",
)

# Only tools that are legitimately user-installed (e.g. uv via the official
# installer) should ever be resolved from here, and only after the system
# directories above produced no match.
TRUSTED_USER_DIRS: tuple[Path, ...] = (
    Path.home() / ".local/bin",
    Path.home() / ".cargo/bin",
)


def _acceptable_mode(st: os.stat_result) -> bool:
    """Reject files owned by someone else, or group/other-writable ones."""
    if st.st_uid not in (0, os.geteuid()):
        return False
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return False
    return True


def _is_safe_executable(path: Path) -> bool:
    try:
        st = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        try:
            resolved = path.resolve(strict=True)
            real_st = resolved.stat()
        except OSError:
            return False
        if not stat.S_ISREG(real_st.st_mode):
            return False
        target = resolved
        check_st = real_st
    else:
        if not stat.S_ISREG(st.st_mode):
            return False
        target = path
        check_st = st
    if not _acceptable_mode(check_st):
        return False
    return os.access(target, os.X_OK)


def resolve_trusted_binary(name: str, *, allow_user_dirs: bool = True) -> str | None:
    """Find ``name`` in a fixed, trusted search order — never via raw PATH."""
    if not name or "/" in name:
        return None
    for directory in TRUSTED_SYSTEM_DIRS:
        candidate = Path(directory) / name
        if _is_safe_executable(candidate):
            return str(candidate)
    if allow_user_dirs:
        for directory in TRUSTED_USER_DIRS:
            candidate = directory / name
            if _is_safe_executable(candidate):
                return str(candidate)
    return None


def safe_subprocess_env() -> dict[str, str]:
    """Environment for child processes with a PATH we control, not inherit."""
    env = dict(os.environ)
    dirs = list(TRUSTED_SYSTEM_DIRS) + [str(d) for d in TRUSTED_USER_DIRS]
    env["PATH"] = os.pathsep.join(dict.fromkeys(dirs))
    return env


def _validate_no_symlink_ancestors(path: Path) -> None:
    """Walk up from ``path`` and reject any symlinked or foreign-owned ancestor.

    ``secure_state_dir``/``atomic_write_bytes`` only ever checked the final
    path component. That misses an attacker who pre-created an *ancestor*
    directory (e.g. ``~/.cache`` itself) as a symlink to somewhere they
    control — everything built underneath it would then look correctly
    owned/permissioned while actually living somewhere else entirely. The
    walk stops at the first root-owned ancestor (e.g. ``/home`` or ``/``),
    which is the expected system boundary and outside this module's remit.
    """
    current = path
    seen: set[Path] = set()
    while True:
        parent = current.parent
        if parent == current or parent in seen:
            break
        seen.add(parent)
        try:
            st = parent.lstat()
        except OSError:
            current = parent
            continue
        if stat.S_ISLNK(st.st_mode):
            raise UnsafePathError(f"refusing to use path with symlinked ancestor: {parent}")
        if st.st_uid == 0:
            break  # reached a root-owned system directory; trusted boundary
        if st.st_uid != os.geteuid():
            raise UnsafePathError(f"ancestor directory not owned by current user: {parent}")
        current = parent


def secure_state_dir(path: Path, *, mode: int = 0o700) -> Path:
    """Create/validate a private state directory, refusing to follow symlinks.

    The directory must end up owned by the current user and must not be a
    symlink (which could otherwise redirect session/config/cache writes to a
    location the attacker controls or can read). Ancestor directories are
    checked too — see ``_validate_no_symlink_ancestors``.
    """
    _validate_no_symlink_ancestors(path)
    try:
        st = path.lstat()
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True, mode=mode)
        try:
            os.mkdir(path, mode)
        except FileExistsError:
            pass
        st = path.lstat()
    if stat.S_ISLNK(st.st_mode):
        raise UnsafePathError(f"refusing to use symlinked state directory: {path}")
    if not stat.S_ISDIR(st.st_mode):
        raise UnsafePathError(f"expected a directory, found something else: {path}")
    if st.st_uid != os.geteuid():
        raise UnsafePathError(f"state directory not owned by current user: {path}")
    current_perms = stat.S_IMODE(st.st_mode)
    if current_perms & (stat.S_IRWXG | stat.S_IRWXO):
        os.chmod(path, mode)
    return path


def reject_symlink(path: Path) -> None:
    """Refuse to operate on a path that already exists as a symlink."""
    try:
        st = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(st.st_mode):
        raise UnsafePathError(f"refusing to follow symlink: {path}")


def atomic_write_bytes(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    """Write ``data`` to ``path`` atomically with fixed permissions from creation.

    Avoids the race of ``write_text()`` followed by ``chmod()``, where the
    file is briefly readable/writable at the umask default before being
    locked down. Ancestor directories are checked too — see
    ``_validate_no_symlink_ancestors``.
    """
    _validate_no_symlink_ancestors(path)
    reject_symlink(path)
    directory = path.parent
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=str(directory))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


SAFE_EXTENSION_RE = re.compile(r"^\.[A-Za-z0-9]{1,10}$")


def safe_extension(raw: str | None, *, fallback: str = "") -> str:
    """Return ``raw`` if it looks like a plain extension, else ``fallback``.

    Telegram-derived extensions ultimately come from metadata attached by
    whoever sent the message, so they must not be trusted verbatim when
    building on-disk file names.
    """
    if raw and SAFE_EXTENSION_RE.match(raw):
        return raw.lower()
    return fallback


# ==============================================================================
# dir_fd-anchored directory identity (TOCTOU-closed)
# ==============================================================================
#
# secure_state_dir()/atomic_write_bytes() above check a path string once and
# hand back a Path for later, independent use -- which then gets re-resolved
# by the OS on every subsequent operation. Between that check and each later
# use sits a window in which a directory component could be swapped out from
# under an already-"verified" path. VerifiedDir closes that window: every
# operation against it is performed relative to the file descriptor opened
# when the directory was verified (Linux *at() syscalls via dir_fd=), so the
# kernel resolves it to the exact inode that was checked, not whatever a
# fresh path lookup would now find. Chaining subdir() calls carries that
# guarantee down a whole directory tree.


def _translate_open_error(exc: OSError, name: str) -> Exception:
    if exc.errno == errno.ELOOP:
        return UnsafePathError(f"refusing to follow symlinked path component: {name!r}")
    if exc.errno == errno.ENOTDIR:
        return UnsafePathError(f"expected a directory at path component: {name!r}")
    return exc


def _open_dir_component(name: str, *, dir_fd: int, create_mode: int) -> int:
    """Open a single path component as a directory relative to ``dir_fd``.

    O_NOFOLLOW makes symlink rejection atomic with the open itself -- there
    is no separate lstat() call whose result could go stale before this open
    runs. Missing components are created and then reopened the same way, so
    even a symlink planted in the gap between "create" and "open" is still
    caught by the final O_NOFOLLOW open rather than trusted.
    """
    flags = os.O_DIRECTORY | os.O_NOFOLLOW | os.O_RDONLY
    try:
        return os.open(name, flags, dir_fd=dir_fd)
    except FileNotFoundError:
        try:
            os.mkdir(name, create_mode, dir_fd=dir_fd)
        except FileExistsError:
            pass
        try:
            return os.open(name, flags, dir_fd=dir_fd)
        except OSError as exc:
            raise _translate_open_error(exc, name) from exc
    except OSError as exc:
        raise _translate_open_error(exc, name) from exc


def _walk_verified(path: Path, *, final_mode: int) -> int:
    """Resolve every component of ``path`` via dir_fd-relative opens.

    Purely lexical normalization only (``os.path.abspath``) -- never
    ``Path.resolve()``, which follows symlinks itself and would defeat the
    point before the walk even starts. Ownership is checked at every step;
    a root-owned directory is a trusted system boundary (matching the
    historical ancestor-walk in ``_validate_no_symlink_ancestors``), and
    ownership enforcement applies to every user-owned step regardless of
    depth. Intermediate components are created world-readable (0o755) if
    missing, matching normal XDG base-directory expectations; only the
    final component is created with ``final_mode`` (private by default).
    """
    normalized = os.path.abspath(str(path))
    parts = [part for part in normalized.split(os.sep) if part]
    fd = os.open(os.sep, os.O_DIRECTORY | os.O_RDONLY)
    try:
        for index, part in enumerate(parts):
            is_last = index == len(parts) - 1
            create_mode = final_mode if is_last else 0o755
            next_fd = _open_dir_component(part, dir_fd=fd, create_mode=create_mode)
            os.close(fd)
            fd = next_fd
            st = os.fstat(fd)
            if not stat.S_ISDIR(st.st_mode):
                raise UnsafePathError(f"expected a directory, found something else: {part!r}")
            if st.st_uid == 0:
                continue  # trusted system boundary (e.g. "/", "/home")
            if st.st_uid != os.geteuid():
                raise UnsafePathError(f"path component not owned by current user: {part!r}")
            if is_last:
                current_perms = stat.S_IMODE(st.st_mode)
                if current_perms & (stat.S_IRWXG | stat.S_IRWXO):
                    os.fchmod(fd, final_mode)
        return fd
    except BaseException:
        os.close(fd)
        raise


class VerifiedDir:
    """A directory pinned to an open, symlink-free, ownership-verified fd.

    Every method resolves its ``name`` argument relative to the fd captured
    at open time, never by re-joining and re-resolving a path string, so
    operations always land on the exact directory ``open_verified_dir()``
    checked -- not whatever a later lookup of the same path would find.
    """

    def __init__(self, path: Path, fd: int) -> None:
        self.path = path
        self._fd: int | None = fd

    @property
    def fd(self) -> int:
        if self._fd is None:
            raise RuntimeError("VerifiedDir already closed")
        return self._fd

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "VerifiedDir":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def subdir(self, name: str, *, mode: int = 0o700) -> "VerifiedDir":
        """Open (creating if needed) a single-component child directory."""
        if not name or "/" in name or name in (".", ".."):
            raise UnsafePathError(f"invalid directory component: {name!r}")
        child_fd = _open_dir_component(name, dir_fd=self.fd, create_mode=mode)
        try:
            st = os.fstat(child_fd)
            if st.st_uid != os.geteuid():
                raise UnsafePathError(f"directory not owned by current user: {self.path / name}")
            current_perms = stat.S_IMODE(st.st_mode)
            if current_perms & (stat.S_IRWXG | stat.S_IRWXO):
                os.fchmod(child_fd, mode)
        except BaseException:
            os.close(child_fd)
            raise
        return VerifiedDir(self.path / name, child_fd)

    def subpath(self, *names: str, mode: int = 0o700) -> "VerifiedDir":
        """Open (creating if needed) a multi-component descendant directory.

        Equivalent to chaining ``subdir()`` calls, but closes every
        intermediate fd along the way instead of leaking it -- ``self``
        is left untouched (the caller may still be holding onto it), and
        only the final, returned ``VerifiedDir`` keeps an open fd.
        """
        current: "VerifiedDir" = self
        for index, name in enumerate(names):
            nxt = current.subdir(name, mode=mode)
            if index > 0:
                current.close()
            current = nxt
        return current

    def stat(self, name: str | None = None) -> os.stat_result:
        """Like ``lstat`` (does not follow a symlink at ``name`` itself)."""
        if name is None:
            return os.fstat(self.fd)
        return os.stat(name, dir_fd=self.fd, follow_symlinks=False)

    def exists(self, name: str) -> bool:
        try:
            self.stat(name)
            return True
        except OSError:
            return False

    def is_file(self, name: str) -> bool:
        try:
            return stat.S_ISREG(self.stat(name).st_mode)
        except OSError:
            return False

    def is_symlink(self, name: str) -> bool:
        try:
            return stat.S_ISLNK(self.stat(name).st_mode)
        except OSError:
            return False

    def size(self, name: str) -> int:
        return self.stat(name).st_size

    def remove(self, name: str) -> None:
        try:
            os.unlink(name, dir_fd=self.fd)
        except FileNotFoundError:
            pass

    def list_names(self) -> list[str]:
        # scandir() on an fd effectively takes ownership of it (fdopendir);
        # hand it a dup so self._fd stays open and reusable afterward.
        with os.scandir(os.dup(self.fd)) as entries:
            return [entry.name for entry in entries]

    def open_binary(self, name: str):
        """Open an existing regular file within this directory for reading.

        O_NOFOLLOW makes symlink rejection atomic with the open, same as
        every other operation here -- a caller that then hands the returned
        file object to e.g. an image library validates the exact file this
        directory's identity was checked against, not a fresh path lookup.
        """
        if not name or "/" in name:
            raise UnsafePathError(f"invalid file name: {name!r}")
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        return os.fdopen(fd, "rb")

    def open_write_stream(self, name: str, *, mode: int = 0o600) -> int:
        """Open a fresh file for streamed writing; caller closes the fd.

        For content this module doesn't build up as one in-memory ``bytes``
        blob (e.g. a chunked download) -- use ``write_atomic`` instead when
        the whole payload is already in memory.
        """
        if not name or "/" in name:
            raise UnsafePathError(f"invalid file name: {name!r}")
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW
        return os.open(name, flags, mode, dir_fd=self.fd)

    def replace(self, src_name: str, dst_name: str) -> None:
        """Atomically publish ``src_name`` as ``dst_name``, same directory."""
        os.replace(src_name, dst_name, src_dir_fd=self.fd, dst_dir_fd=self.fd)

    def write_atomic(self, name: str, data: bytes, *, mode: int = 0o600) -> None:
        """Atomically create/replace ``name`` with ``data``.

        Refuses outright if ``name`` already exists as a symlink, rather
        than relying on ``os.replace()``'s (correct, but easy to reason
        about wrong) habit of replacing the symlink itself instead of
        following it -- matching ``atomic_write_bytes()``'s behavior above.
        """
        if not name or "/" in name:
            raise UnsafePathError(f"invalid file name: {name!r}")
        if self.is_symlink(name):
            raise UnsafePathError(f"refusing to follow symlink: {self.path / name}")
        tmp_name = f".{name}.tmp-{os.urandom(8).hex()}"
        fd = self.open_write_stream(tmp_name, mode=mode)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            self.replace(tmp_name, name)
        except BaseException:
            self.remove(tmp_name)
            raise


def open_verified_dir(path: Path, *, mode: int = 0o700) -> VerifiedDir:
    """Resolve+verify every component of ``path`` via dir_fd-relative opens.

    Stronger than ``secure_state_dir()``: instead of checking a path string
    once and handing back a ``Path`` for later, independent (re-resolved)
    use, this returns a ``VerifiedDir`` anchored to the fd opened here, so
    every later operation against it stays pinned to the exact directory
    that was just checked.
    """
    fd = _walk_verified(path, final_mode=mode)
    return VerifiedDir(path, fd)
