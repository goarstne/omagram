from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from telegram_tui.security import (
    UnsafePathError,
    atomic_write_bytes,
    open_verified_dir,
    reject_symlink,
    resolve_trusted_binary,
    safe_extension,
    safe_subprocess_env,
    secure_state_dir,
)


class ResolveTrustedBinaryTests(unittest.TestCase):
    def _make_executable(self, path: Path, mode: int = 0o755) -> None:
        path.write_text("#!/bin/sh\n")
        path.chmod(mode)

    def test_finds_binary_in_trusted_system_dir(self):
        with tempfile.TemporaryDirectory() as system_dir, tempfile.TemporaryDirectory() as user_dir:
            self._make_executable(Path(system_dir) / "tool")
            with patch("telegram_tui.security.TRUSTED_SYSTEM_DIRS", (system_dir,)), patch(
                "telegram_tui.security.TRUSTED_USER_DIRS", (Path(user_dir),)
            ):
                found = resolve_trusted_binary("tool")
        self.assertEqual(found, str(Path(system_dir) / "tool"))

    def test_system_dir_takes_priority_over_user_dir(self):
        with tempfile.TemporaryDirectory() as system_dir, tempfile.TemporaryDirectory() as user_dir:
            self._make_executable(Path(system_dir) / "tool")
            self._make_executable(Path(user_dir) / "tool")
            with patch("telegram_tui.security.TRUSTED_SYSTEM_DIRS", (system_dir,)), patch(
                "telegram_tui.security.TRUSTED_USER_DIRS", (Path(user_dir),)
            ):
                found = resolve_trusted_binary("tool")
        self.assertEqual(found, str(Path(system_dir) / "tool"))

    def test_ignores_planted_path_shadow_directory(self):
        """A directory prepended to PATH must not shadow the trusted lookup."""
        with tempfile.TemporaryDirectory() as system_dir, tempfile.TemporaryDirectory() as evil_dir:
            self._make_executable(Path(system_dir) / "tool")
            self._make_executable(Path(evil_dir) / "tool")
            shadowed_path = os.pathsep.join([evil_dir, os.environ.get("PATH", "")])
            with patch("telegram_tui.security.TRUSTED_SYSTEM_DIRS", (system_dir,)), patch(
                "telegram_tui.security.TRUSTED_USER_DIRS", ()
            ), patch.dict(os.environ, {"PATH": shadowed_path}):
                found = resolve_trusted_binary("tool")
        self.assertEqual(found, str(Path(system_dir) / "tool"))

    def test_rejects_group_writable_binary(self):
        with tempfile.TemporaryDirectory() as system_dir:
            target = Path(system_dir) / "tool"
            self._make_executable(target, mode=0o775)
            with patch("telegram_tui.security.TRUSTED_SYSTEM_DIRS", (system_dir,)), patch(
                "telegram_tui.security.TRUSTED_USER_DIRS", ()
            ):
                found = resolve_trusted_binary("tool")
        self.assertIsNone(found)

    def test_rejects_binary_owned_by_someone_else(self):
        """Even correct file modes don't help if the owner isn't root/us."""
        with tempfile.TemporaryDirectory() as system_dir:
            target = Path(system_dir) / "tool"
            self._make_executable(target)
            with patch("telegram_tui.security.TRUSTED_SYSTEM_DIRS", (system_dir,)), patch(
                "telegram_tui.security.TRUSTED_USER_DIRS", ()
            ), patch("telegram_tui.security.os.geteuid", return_value=os.geteuid() + 1234):
                found = resolve_trusted_binary("tool")
        self.assertIsNone(found)

    def test_rejects_name_with_path_separator(self):
        self.assertIsNone(resolve_trusted_binary("../evil"))

    def test_missing_binary_returns_none(self):
        with tempfile.TemporaryDirectory() as system_dir:
            with patch("telegram_tui.security.TRUSTED_SYSTEM_DIRS", (system_dir,)), patch(
                "telegram_tui.security.TRUSTED_USER_DIRS", ()
            ):
                self.assertIsNone(resolve_trusted_binary("does-not-exist"))


class SafeSubprocessEnvTests(unittest.TestCase):
    def test_path_is_rebuilt_from_trusted_dirs_only(self):
        with patch.dict(os.environ, {"PATH": "/tmp/evil:/usr/bin", "SOME_VAR": "kept"}):
            env = safe_subprocess_env()
        self.assertNotIn("/tmp/evil", env["PATH"])
        self.assertIn("/usr/bin", env["PATH"])
        self.assertEqual(env["SOME_VAR"], "kept")


class SecureStateDirTests(unittest.TestCase):
    def test_creates_private_directory(self):
        with tempfile.TemporaryDirectory() as base:
            target = Path(base) / "nested" / "state"
            result = secure_state_dir(target)
            self.assertTrue(result.is_dir())
            self.assertEqual(stat.S_IMODE(result.stat().st_mode), 0o700)

    def test_tightens_existing_loose_permissions(self):
        with tempfile.TemporaryDirectory() as base:
            target = Path(base) / "state"
            target.mkdir(mode=0o755)
            secure_state_dir(target)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)

    def test_rejects_symlinked_directory(self):
        with tempfile.TemporaryDirectory() as base:
            real_dir = Path(base) / "real"
            real_dir.mkdir()
            link = Path(base) / "link"
            link.symlink_to(real_dir)
            with self.assertRaises(UnsafePathError):
                secure_state_dir(link)

    def test_rejects_non_directory(self):
        with tempfile.TemporaryDirectory() as base:
            target = Path(base) / "afile"
            target.write_text("x")
            with self.assertRaises(UnsafePathError):
                secure_state_dir(target)

    def test_rejects_symlinked_ancestor_directory(self):
        """A symlinked grandparent must be caught, not just the leaf itself."""
        with tempfile.TemporaryDirectory() as base:
            real_root = Path(base) / "real_root"
            real_root.mkdir()
            link_root = Path(base) / "link_root"
            link_root.symlink_to(real_root)
            target = link_root / "nested" / "state"
            with self.assertRaises(UnsafePathError):
                secure_state_dir(target)
            # Nothing must have been created through the symlinked ancestor.
            self.assertEqual(list(real_root.iterdir()), [])

    def test_rejects_ancestor_owned_by_someone_else(self):
        with tempfile.TemporaryDirectory() as base:
            target = Path(base) / "nested" / "state"
            with patch(
                "telegram_tui.security.os.geteuid", return_value=os.geteuid() + 4321
            ):
                with self.assertRaises(UnsafePathError):
                    secure_state_dir(target)

    def test_stops_walk_at_root_owned_ancestor(self):
        """Well-formed nested creation must not be rejected by its own walk."""
        with tempfile.TemporaryDirectory() as base:
            target = Path(base) / "a" / "b" / "c"
            result = secure_state_dir(target)
            self.assertTrue(result.is_dir())


class RejectSymlinkTests(unittest.TestCase):
    def test_allows_missing_path(self):
        with tempfile.TemporaryDirectory() as base:
            reject_symlink(Path(base) / "missing")  # must not raise

    def test_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as base:
            real_file = Path(base) / "real"
            real_file.write_text("x")
            link = Path(base) / "link"
            link.symlink_to(real_file)
            with self.assertRaises(UnsafePathError):
                reject_symlink(link)


class AtomicWriteBytesTests(unittest.TestCase):
    def test_writes_content_with_exact_mode(self):
        with tempfile.TemporaryDirectory() as base:
            target = Path(base) / "secret.env"
            atomic_write_bytes(target, b"hello", mode=0o600)
            self.assertEqual(target.read_bytes(), b"hello")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_refuses_to_follow_symlink_target(self):
        with tempfile.TemporaryDirectory() as base:
            real_file = Path(base) / "real"
            real_file.write_text("original")
            link = Path(base) / "link"
            link.symlink_to(real_file)
            with self.assertRaises(UnsafePathError):
                atomic_write_bytes(link, b"pwned")
            self.assertEqual(real_file.read_text(), "original")

    def test_no_leftover_temp_file_on_failure(self):
        with tempfile.TemporaryDirectory() as base:
            target = Path(base) / "nested" / "secret.env"
            with self.assertRaises(OSError):
                atomic_write_bytes(target, b"data")  # parent dir does not exist
            self.assertEqual(list(Path(base).iterdir()), [])

    def test_rejects_symlinked_ancestor_directory(self):
        with tempfile.TemporaryDirectory() as base:
            real_root = Path(base) / "real_root"
            real_root.mkdir()
            link_root = Path(base) / "link_root"
            link_root.symlink_to(real_root)
            target = link_root / "secret.env"
            with self.assertRaises(UnsafePathError):
                atomic_write_bytes(target, b"pwned")
            self.assertEqual(list(real_root.iterdir()), [])

    def test_rejects_ancestor_owned_by_someone_else(self):
        with tempfile.TemporaryDirectory() as base:
            target = Path(base) / "secret.env"
            with patch(
                "telegram_tui.security.os.geteuid", return_value=os.geteuid() + 4321
            ):
                with self.assertRaises(UnsafePathError):
                    atomic_write_bytes(target, b"data")
            self.assertFalse(target.exists())


class SafeExtensionTests(unittest.TestCase):
    def test_accepts_plain_extension(self):
        self.assertEqual(safe_extension(".mp4", fallback=".bin"), ".mp4")

    def test_normalizes_case(self):
        self.assertEqual(safe_extension(".MP4", fallback=".bin"), ".mp4")

    def test_rejects_path_traversal(self):
        self.assertEqual(safe_extension("/../../etc/passwd", fallback=".bin"), ".bin")

    def test_rejects_missing_dot(self):
        self.assertEqual(safe_extension("mp4", fallback=".bin"), ".bin")

    def test_rejects_none(self):
        self.assertEqual(safe_extension(None, fallback=".bin"), ".bin")

    def test_rejects_overlong_extension(self):
        self.assertEqual(safe_extension("." + "a" * 20, fallback=".bin"), ".bin")


class OpenVerifiedDirTests(unittest.TestCase):
    """VerifiedDir/open_verified_dir: the dir_fd-anchored, TOCTOU-closed layer."""

    def _open_fd_count(self) -> int:
        return len(os.listdir(f"/proc/{os.getpid()}/fd"))

    def test_creates_private_directory(self):
        with tempfile.TemporaryDirectory() as base:
            target = Path(base) / "nested" / "state"
            with open_verified_dir(target) as verified:
                self.assertTrue(target.is_dir())
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
                self.assertEqual(verified.path, target)

    def test_intermediate_components_are_world_readable(self):
        with tempfile.TemporaryDirectory() as base:
            target = Path(base) / "nested" / "state"
            open_verified_dir(target).close()
            self.assertEqual(stat.S_IMODE((Path(base) / "nested").stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)

    def test_rejects_symlinked_directory(self):
        with tempfile.TemporaryDirectory() as base:
            real_dir = Path(base) / "real"
            real_dir.mkdir()
            link = Path(base) / "link"
            link.symlink_to(real_dir)
            with self.assertRaises(UnsafePathError):
                open_verified_dir(link)

    def test_rejects_symlinked_ancestor_directory(self):
        with tempfile.TemporaryDirectory() as base:
            real_root = Path(base) / "real_root"
            real_root.mkdir()
            link_root = Path(base) / "link_root"
            link_root.symlink_to(real_root)
            with self.assertRaises(UnsafePathError):
                open_verified_dir(link_root / "nested" / "state")
            self.assertEqual(list(real_root.iterdir()), [])

    def test_rejects_ancestor_owned_by_someone_else(self):
        with tempfile.TemporaryDirectory() as base:
            target = Path(base) / "nested" / "state"
            with patch(
                "telegram_tui.security.os.geteuid", return_value=os.geteuid() + 4321
            ):
                with self.assertRaises(UnsafePathError):
                    open_verified_dir(target)

    def test_subdir_creates_and_verifies_child(self):
        with tempfile.TemporaryDirectory() as base:
            with open_verified_dir(Path(base) / "root") as root:
                with root.subdir("child") as child:
                    self.assertEqual(child.path, Path(base) / "root" / "child")
                    self.assertEqual(stat.S_IMODE(child.stat().st_mode), 0o700)

    def test_subdir_rejects_symlinked_child(self):
        with tempfile.TemporaryDirectory() as base:
            with open_verified_dir(Path(base) / "root") as root:
                outside = Path(base) / "outside"
                outside.mkdir()
                (Path(base) / "root" / "child").symlink_to(outside)
                with self.assertRaises(UnsafePathError):
                    root.subdir("child")

    def test_subpath_does_not_leak_intermediate_fds(self):
        with tempfile.TemporaryDirectory() as base:
            with open_verified_dir(Path(base) / "root") as root:
                before = self._open_fd_count()
                for _ in range(20):
                    root.subpath("a", "b", "c").close()
                after = self._open_fd_count()
        self.assertEqual(before, after)

    def test_write_atomic_writes_and_replaces(self):
        with tempfile.TemporaryDirectory() as base:
            with open_verified_dir(Path(base) / "root") as root:
                root.write_atomic("secret.env", b"first", mode=0o600)
                self.assertEqual((Path(base) / "root" / "secret.env").read_bytes(), b"first")
                root.write_atomic("secret.env", b"second", mode=0o600)
                path = Path(base) / "root" / "secret.env"
                self.assertEqual(path.read_bytes(), b"second")
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                # No stray temp files left behind.
                self.assertEqual(root.list_names(), ["secret.env"])

    def test_write_atomic_refuses_existing_symlink(self):
        with tempfile.TemporaryDirectory() as base:
            with open_verified_dir(Path(base) / "root") as root:
                real_target = Path(base) / "elsewhere.txt"
                real_target.write_text("do not touch")
                (Path(base) / "root" / "secret.env").symlink_to(real_target)
                with self.assertRaises(UnsafePathError):
                    root.write_atomic("secret.env", b"pwned")
                self.assertEqual(real_target.read_text(), "do not touch")

    def test_list_names_and_remove(self):
        with tempfile.TemporaryDirectory() as base:
            with open_verified_dir(Path(base) / "root") as root:
                root.write_atomic("a.txt", b"x")
                root.write_atomic("b.txt", b"y")
                self.assertEqual(sorted(root.list_names()), ["a.txt", "b.txt"])
                root.remove("a.txt")
                self.assertEqual(root.list_names(), ["b.txt"])
                root.remove("does-not-exist")  # must not raise

    def test_open_binary_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as base:
            with open_verified_dir(Path(base) / "root") as root:
                real_target = Path(base) / "elsewhere.txt"
                real_target.write_text("secret")
                (Path(base) / "root" / "leak").symlink_to(real_target)
                with self.assertRaises(OSError):
                    root.open_binary("leak")


if __name__ == "__main__":
    unittest.main()
