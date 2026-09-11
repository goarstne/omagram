```text
╔════════════════════════════════════════════════════════════════════════════════════╗
║  ██████    ██      ██    ██████      ██████    ████████      ██████    ██      ██  ║
║██      ██  ████  ████  ██      ██  ██          ██      ██  ██      ██  ████  ████  ║
║██      ██  ██  ██  ██  ██████████  ██  ██████  ████████    ██████████  ██  ██  ██  ║
║██      ██  ██      ██  ██      ██  ██      ██  ██  ██      ██      ██  ██      ██  ║
║  ██████    ██      ██  ██      ██    ██████    ██    ██    ██      ██  ██      ██  ║
╠════════════════════════════════════════════════════════════════════════════════════╣
║  SYSOP: @goarstne        NODE: 01        BAUD: 14400        STATUS: ONLINE         ║
║  PROTOCOL: MTProto 2.0   GRAPHICS: SIXEL / ANSI-HALFCELL    OS: LINUX / OMARCHY    ║
╚════════════════════════════════════════════════════════════════════════════════════╝
```

> **CONNECT 14400 / V.32bis · CARRIER DETECTED · WELCOME TO OMAGRAM BBS**  
> A keyboard-first Telegram terminal client (TUI) with retro BBS aesthetics, native Sixel/Halfcell raster graphics, and zero-friction QR login for Omarchy & Linux.

<p align="center">
  <img src="screenshots/chat-view.png" alt="omagram Terminal Screenshot" width="100%">
</p>

---

## ⚡ TL;DR – Quick Dial-In (Connect in 30 Seconds)

No API-key hassle. No registering developer apps on `my.telegram.org`. Just clone, run, and scan:

```bash
git clone https://github.com/goarstne/omagram.git
cd omagram
uv run omagram
```

1. **Scan ASCII QR:** Open Telegram on your phone → **Settings → Devices → Link Desktop Device**.
2. **Carrier Locked:** The TUI launches seamlessly right after the scan into your chat list!

---

## 📟 BBS Command Matrix (Keybindings)

```text
[=== NAVIGATION & TRANSMISSION CONTROLS ===]
```

| Key | Action |
| :--- | :--- |
| `j` / `↓` | Next dialog / Cursor down |
| `k` / `↑` | Previous dialog / Cursor up |
| `Enter` / `o` | Open selected chat channel |
| `c` | Compose transmission (focus message composer) |
| `Enter` *(in field)* | Transmit message |
| `Escape` | Leave input / return focus to channel list |
| `v` | Spawn `mpv` player for latest video, GIF, or YouTube link |
| `b` / `Ctrl+b` | Toggle sidebar (channel list) visibility |
| `r` | Reload channels and fetch fresh message packets |
| `i` | Display BBS Node Info boot card |
| `q` / `Ctrl+c` | Hang up / Exit Omagram |

---

## 📡 System Specs & Features

- ⌨️ **Vim-Style Keyboard Transmission:** Lightning-fast navigation with zero mouse dependency.
- 🖼️ **Native Terminal Graphics:** True pixel rendering in Foot terminal using native Sixel escapes, with automatic fallback to Unicode Halfcell rendering.
- 📼 **Lazy AV Media Transceiver:** Stream Telegram video files and looping GIFs on demand with `v` via `mpv`.
- 🔴 **YouTube Packet Interceptor:** Detects embedded YouTube URLs, displays thumbnail cards, and streams them instantly in `mpv` (or fallback browser).
- 🎨 **Omarchy Theme Sync:** Reads active desktop theme palettes on the fly from `~/.local/state/omarchy/current/theme/colors.toml`.
- 🔒 **Direct Encrypted MTProto Carrier:** Direct client-to-datacenter encrypted connection. No intermediate proxy servers, no third-party logging. Your session token remains strictly local in `~/.local/state/omagram/`.
- 🎛️ **Omarchy Desktop Bar Integration:** Bundled single-click launcher widget for `omarchy-shell` / Quickshell.

---

## 💾 Dial-In Requirements & Installation

- **Python 3.12+** and [uv](https://docs.astral.sh/uv/) (recommended)
- **mpv** (optional, for streaming video/GIF playback): `sudo pacman -S mpv`
- **Foot Terminal** (recommended for hardware-accelerated Sixel raster graphics)

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

Clicking the Telegram status icon spawns Omagram directly inside your preferred terminal workspace.

---

## 🔧 SysOp Configuration (Optional Custom Keys)

By default, Omagram connects using the official, publicly available Telegram Desktop credentials (`api_id=2040`) so you never have to register manually.

If you are a SysOp who prefers using your own application credentials from [my.telegram.org](https://my.telegram.org):

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

## 🔍 Line Diagnostics & Logs

Omagram logs diagnostics to a rotating local logfile at `~/.local/state/omagram/omagram.log`. Passwords, private session keys, and message bodies are never recorded.

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

# Regenerate demo screenshot with simulated BBS data
uv run python scripts/demo_screenshots.py

# Offline Sixel terminal capability probe
uv run python -m telegram_tui.media_probe /path/to/test_image.jpg
```

---

## 📜 License

MIT License. Copyright (c) 2026 Carsten (@goarstne).
