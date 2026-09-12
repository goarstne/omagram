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
