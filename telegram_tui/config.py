from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


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
    APP_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    return api_id, api_hash


def save_config(api_id: str, api_hash: str) -> None:
    """Persist only the Telegram app credentials with user-only permissions."""
    APP_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    env_path = Path.home() / ".config/omagram/.env"
    env_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    env_path.write_text(f"TG_API_ID={api_id.strip()}\nTG_API_HASH={api_hash.strip()}\n", encoding="utf-8")
    env_path.chmod(0o600)
