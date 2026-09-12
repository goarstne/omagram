from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from .security import UnsafePathError, atomic_write_bytes, reject_symlink, secure_state_dir


APP_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "omagram"
SESSION_PATH = Path(os.environ.get("TG_SESSION", APP_DIR / "telegram"))


# Default to official Telegram Desktop credentials (public in TDesktop open-source).
# This allows instant QR-code onboarding without requiring end users to create
# an app on my.telegram.org. Users can still supply custom keys via .env.
DEFAULT_API_ID = 2040
DEFAULT_API_HASH = "b18441a1ff607e10a989891a5462e627"


def load_config() -> tuple[int, str]:
    load_dotenv(Path.home() / ".config/omagram/.env")
    load_dotenv()
    raw_id = os.environ.get("TG_API_ID", "").strip()
    api_hash = os.environ.get("TG_API_HASH", "").strip()
    if raw_id and api_hash:
        try:
            api_id = int(raw_id)
        except ValueError as exc:
            raise RuntimeError("TG_API_ID must be a numeric integer.") from exc
    else:
        api_id = DEFAULT_API_ID
        api_hash = DEFAULT_API_HASH
    secure_state_dir(APP_DIR)
    return api_id, api_hash


def save_config(api_id: str, api_hash: str) -> None:
    """Persist only the Telegram app credentials with user-only permissions."""
    secure_state_dir(APP_DIR)
    env_dir = Path.home() / ".config/omagram"
    secure_state_dir(env_dir)
    env_path = env_dir / ".env"
    atomic_write_bytes(
        env_path,
        f"TG_API_ID={api_id.strip()}\nTG_API_HASH={api_hash.strip()}\n".encode("utf-8"),
        mode=0o600,
    )


def validate_session_path(path: Path) -> Path:
    """Ensure the Telethon session lives under a private, non-symlinked directory.

    ``path`` may come from the ``TG_SESSION`` environment variable, so this
    also guards against a tampered/attacker-controlled override pointing the
    session file (which carries live Telegram auth) somewhere unsafe.
    """
    secure_state_dir(path.parent)
    reject_symlink(path)
    return path


__all__ = [
    "APP_DIR",
    "SESSION_PATH",
    "DEFAULT_API_ID",
    "DEFAULT_API_HASH",
    "load_config",
    "save_config",
    "validate_session_path",
    "UnsafePathError",
]
