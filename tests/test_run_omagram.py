"""Blackbox regression tests for the run-omagram launcher script.

These guard the concrete finding from the marketplace review: PATH must be
built exclusively from a fixed, trusted set of directories, with no fallback
to whatever PATH the script inherited — a hostile directory prepended ahead
of the real system/user directories (a tampered shell rc file, a dev-tool
shim directory, etc.) must never be able to shadow "uv".
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_OMAGRAM = REPO_ROOT / "run-omagram"


def _make_fake_uv(path: Path, marker: str) -> None:
    path.write_text(f"#!/bin/bash\necho '{marker}'\necho \"PATH=$PATH\"\n")
    path.chmod(0o755)


class RunOmagramPathTests(unittest.TestCase):
    def test_ignores_malicious_uv_earlier_in_inherited_path(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as evil_dir:
            home_path = Path(home)
            legit_bin = home_path / ".local" / "bin"
            legit_bin.mkdir(parents=True)
            _make_fake_uv(legit_bin / "uv", "LEGIT_UV")
            _make_fake_uv(Path(evil_dir) / "uv", "MALICIOUS_UV")

            env = dict(os.environ)
            env["HOME"] = str(home_path)
            # Hostile PATH: an attacker-controlled directory placed before
            # everything else, mimicking a compromised shell rc file or a
            # dev-tool shim directory that runs earlier than system paths.
            env["PATH"] = evil_dir + os.pathsep + os.environ.get("PATH", "")

            result = subprocess.run(
                [str(RUN_OMAGRAM), "--help"],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

        self.assertIn("LEGIT_UV", result.stdout)
        self.assertNotIn("MALICIOUS_UV", result.stdout)

    def test_child_path_contains_only_trusted_directories(self):
        with tempfile.TemporaryDirectory() as home:
            home_path = Path(home)
            legit_bin = home_path / ".local" / "bin"
            legit_bin.mkdir(parents=True)
            _make_fake_uv(legit_bin / "uv", "LEGIT_UV")

            env = dict(os.environ)
            env["HOME"] = str(home_path)
            env["PATH"] = "/some/random/untrusted/dir" + os.pathsep + os.environ.get("PATH", "")

            result = subprocess.run(
                [str(RUN_OMAGRAM), "--help"],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

        path_line = next(line for line in result.stdout.splitlines() if line.startswith("PATH="))
        seen_path = path_line[len("PATH="):]
        expected = os.pathsep.join(
            ["/usr/local/bin", "/usr/bin", "/bin", f"{home_path}/.local/bin", f"{home_path}/.cargo/bin"]
        )
        self.assertEqual(seen_path, expected)
        self.assertNotIn("/some/random/untrusted/dir", seen_path)

    def test_rejects_group_writable_uv_even_in_trusted_directory(self):
        with tempfile.TemporaryDirectory() as home:
            home_path = Path(home)
            legit_bin = home_path / ".local" / "bin"
            legit_bin.mkdir(parents=True)
            uv_path = legit_bin / "uv"
            _make_fake_uv(uv_path, "SHOULD_NOT_RUN")
            uv_path.chmod(0o775)  # group-writable: looks planted/tampered

            env = dict(os.environ)
            env["HOME"] = str(home_path)

            result = subprocess.run(
                [str(RUN_OMAGRAM), "--help"],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
                input="",
            )

        self.assertNotIn("SHOULD_NOT_RUN", result.stdout)
        self.assertIn("uv", result.stderr.lower())
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
