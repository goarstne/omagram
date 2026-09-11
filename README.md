```text
╔════════════════════════════════════════════════════════════════════════════════════╗
║  ██████    ██      ██    ██████      ██████    ████████      ██████    ██      ██  ║
║██      ██  ████  ████  ██      ██  ██          ██      ██  ██      ██  ████  ████  ║
║██      ██  ██  ██  ██  ██████████  ██  ██████  ████████    ██████████  ██  ██  ██  ║
║██      ██  ██      ██  ██      ██  ██      ██  ██  ██      ██      ██  ██      ██  ║
║  ██████    ██      ██  ██      ██    ██████    ██    ██    ██      ██  ██      ██  ║
╠════════════════════════════════════════════════════════════════════════════════════╣
║  PROD: omagram (v0.1.0)                                         AUTHOR: @goarstne  ║
║  TYPE: Telegram TUI / Terminal Client               UI ENGINE: Textual + Telethon  ║
║  GRAPHICS: Sixel + Unicode Halfcell                TARGET: Linux / Foot / Omarchy  ║
║  REPO: github.com/goarstne/omagram                                   LICENSE: MIT  ║
╚════════════════════════════════════════════════════════════════════════════════════╝
```

> **OMAGRAM** — A fast, keyboard-first Telegram terminal client (TUI) for Linux & Omarchy with native Sixel/Halfcell raster graphics and instant QR-code authentication.

<p align="center">
  <img src="screenshots/chat-view.png" alt="omagram Terminal Screenshot" width="100%">
</p>

---

## ⚡ Quick Start & Core Highlights

- ⌨️ **Vim-Style Navigation:** Seamless keyboard workflow (`j`/`k` scroll, `Enter` open, `c` compose, `v` media, `Esc` back).
- 🖼️ **Native Terminal Graphics:** True pixel image and GIF rendering via Sixel (Foot) with automatic Unicode Halfcell fallback.
- 🎬 **Lazy AV Player:** Spawn `mpv` on demand for Telegram videos, looping GIFs, and YouTube links.
- 🔒 **Instant QR Login:** Scan once with your Telegram mobile app — direct MTProto session, zero third-party servers.
- 🎨 **Omarchy Theme Sync:** Automatically reads your active Omarchy desktop color palette in real-time.

```bash
git clone https://github.com/goarstne/omagram.git
cd omagram
uv run omagram
```

1. **Scan QR:** Scan the terminal QR code in Telegram (**Settings → Devices → Link Desktop Device**).
2. **Ready:** Launches directly into your chats!

---

## ⌨️ Controls & Keybindings

| Key | Action |
| :--- | :--- |
| `j` / `↓` | Next chat / Cursor down |
| `k` / `↑` | Previous chat / Cursor up |
| `Enter` / `o` | Open selected chat |
| `c` | Compose message (focus text input) |
| `Enter` *(in input)* | Send message |
| `Escape` | Unfocus input / return to chat list |
| `v` | Play latest video, GIF, or YouTube link in `mpv` |
| `b` / `Ctrl+b` | Toggle chat sidebar |
| `r` | Reload chats and messages |
| `i` | Show demoscene release credits card |
| `q` / `Ctrl+c` | Exit Omagram |

---

## 📡 Features & Architecture

- ⌨️ **Keyboard-Driven Workflow:** Designed from the ground up for power users with vim-like muscle memory.
- 🖼️ **Native Sixel Raster Engine:** Pixel-perfect inline previews in Foot terminal without blocky character approximations.
- 📼 **On-Demand Media Streaming:** Large videos and animations are downloaded only when requested and piped straight to `mpv`.
- 🔴 **YouTube Link Preview:** Automatically extracts YouTube metadata and previews thumbnails directly in the chat history.
- 🎨 **Dynamic Omarchy Theming:** Instant live synchronization with your system theme at `~/.local/state/omarchy/current/theme/colors.toml`.
- 🔒 **Direct Encrypted MTProto:** Direct peer-to-server TLS connection to official Telegram datacenters. No proxy, no middlemen, no data collection. Session keys are stored locally at `~/.local/state/omagram/`.
- 🎛️ **Omarchy Desktop Integration:** Includes an `omarchy-shell` / Quickshell status bar plugin.

---

## 💾 Installation & Requirements

- **Python 3.12+** and [uv](https://docs.astral.sh/uv/) (recommended)
- **mpv** (optional, for streaming video/GIF playback): `sudo pacman -S mpv`
- **Foot Terminal** (recommended for native Sixel raster graphics)

```bash
# Clone the repository
git clone https://github.com/goarstne/omagram.git
cd omagram

# Sync dependencies and connect
uv sync
uv run omagram
```

---

## 🎛️ Omarchy Bar Widget Integration

Omagram ships with a native launcher widget for the Omarchy status bar:

```bash
mkdir -p ~/.config/omarchy/plugins/local.omagram
cp omarchy-plugin/* ~/.config/omarchy/plugins/local.omagram/
omarchy plugin validate ~/.config/omarchy/plugins/local.omagram
omarchy-shell shell rescanPlugins
omarchy plugin enable local.omagram
```

Clicking the Telegram status icon spawns Omagram directly inside your terminal workspace.

---

## 🔧 Configuration & Custom API Keys (Optional)

Omagram connects using the official, publicly available Telegram Desktop credentials (`api_id=2040`) out-of-the-box.

If you prefer using your own custom developer credentials from [my.telegram.org](https://my.telegram.org):

1. Create `~/.config/omagram/.env` (or `.env` in the project root):
   ```env
   TG_API_ID=12345678
   TG_API_HASH=your_api_hash_here
   ```
2. Or invoke the interactive setup wizard:
   ```bash
   uv run omagram setup
   ```

---

## 🔍 Diagnostics & Logs

Omagram logs diagnostics to a rotating local logfile at `~/.local/state/omagram/omagram.log`. Passwords, private session keys, and message contents are never recorded.

```bash
# Launch with verbose debug diagnostics
uv run omagram --debug run

# Monitor connection stream live
tail -f ~/.local/state/omagram/omagram.log

# Enable detailed MTProto network traces
OMAGRAM_LOG_TELETHON=1 uv run omagram --debug run
```

### Test Suite & Media Probes

```bash
# Execute unit test suite
uv run python -m unittest discover -s tests -v

# Regenerate demo screenshot with simulated data
uv run python scripts/demo_screenshots.py

# Offline Sixel terminal capability probe
uv run python -m telegram_tui.media_probe /path/to/test_image.jpg
```

---

## 📜 License

MIT License. Copyright (c) 2026 Carsten (@goarstne).
