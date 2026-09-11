from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import sys

import qrcode
from telethon.errors import SessionPasswordNeededError

from .app import TelegramTui
from .client import TelegramBackend
from .config import SESSION_PATH, load_config, save_config
from .logging_setup import (
    configure_logging,
    install_asyncio_exception_logging,
    install_exception_logging,
)


logger = logging.getLogger("omagram.cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Keyboard-first Telegram client for Omarchy")
    parser.add_argument("command", nargs="?", choices=["run", "auth", "setup"], default="run")
    parser.add_argument("--debug", action="store_true", help="enable verbose diagnostics")
    parser.add_argument("--console-log", action="store_true", help="also print diagnostics to stderr")
    parser.add_argument("--log-file", help="override the log file path")
    return parser


async def setup_credentials() -> tuple[int, str]:
    install_asyncio_exception_logging(asyncio.get_running_loop())
    logger.info("setup started")
    print("Omagram – einmalige Einrichtung\n")
    print("Erstelle unter https://my.telegram.org → API development tools eine App.")
    api_id = input("API ID: ").strip()
    api_hash = getpass.getpass("API Hash (wird nicht angezeigt): ").strip()
    if not api_id.isdigit() or not api_hash:
        raise RuntimeError("API ID muss numerisch sein und API Hash darf nicht leer sein.")
    save_config(api_id, api_hash)
    print("Gespeichert in ~/.config/omagram/.env\n")
    return int(api_id), api_hash


def print_qr(url: str) -> None:
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    print("\033[2J\033[H")
    print("Scanne diesen QR-Code in Telegram:")
    print("Einstellungen → Geräte → Desktopgerät verknüpfen\n")
    qr.print_ascii(invert=True)
    print("\nDer QR-Code wird automatisch erneuert.")


async def perform_interactive_login(backend: TelegramBackend) -> None:
    qr_login = await backend.client.qr_login()
    logger.info("qr login initialized")
    while True:
        print_qr(qr_login.url)
        try:
            await qr_login.wait(timeout=35)
            logger.info("qr login completed")
            break
        except asyncio.TimeoutError:
            logger.info("qr login expired; recreating")
            qr_login = await qr_login.recreate()
        except SessionPasswordNeededError:
            logger.info("2fa password requested")
            password = getpass.getpass("2FA-Passwort: ")
            await backend.client.sign_in(password=password)
            logger.info("2fa sign-in completed")
            break
        except Exception:
            logger.exception("qr login attempt failed")
            choice = input("[q] QR erneut oder [p] Telefon-Code: ").strip().lower()
            if choice != "p":
                qr_login = await backend.client.qr_login()
                continue
            phone = input("Telefonnummer (+49…): ").strip()
            logger.info("phone-code login requested")
            await backend.send_code(phone)
            code = input("Telegram-Code: ").strip()
            password = getpass.getpass("2FA-Passwort (leer falls keines): ") or None
            await backend.sign_in(phone, code, password)
            logger.info("phone-code login completed")
            break
    print(f"\nAuthenticated. Session saved to {SESSION_PATH}")


async def authenticate(api_id: int, api_hash: str) -> None:
    install_asyncio_exception_logging(asyncio.get_running_loop())
    logger.info("authentication started session=%s", SESSION_PATH)
    backend = TelegramBackend(api_id, api_hash, str(SESSION_PATH))
    try:
        logger.info("auth connecting")
        await backend.connect()
        authorized = await backend.authorized()
        logger.info("auth connected authorized=%s", authorized)
        if authorized:
            print(f"Already authorized. Session: {SESSION_PATH}")
            return
        await perform_interactive_login(backend)
    except Exception:
        logger.exception("authentication failed")
        raise
    finally:
        if backend.client.is_connected():
            await backend.disconnect()
            logger.info("auth disconnected")


async def run_app(api_id: int, api_hash: str) -> None:
    """Connect before Textual starts; authenticate via QR if needed, then launch TUI."""
    install_asyncio_exception_logging(asyncio.get_running_loop())
    logger.info("run started session=%s", SESSION_PATH)
    backend = TelegramBackend(api_id, api_hash, str(SESSION_PATH))
    try:
        logger.info("preflight connect started")
        await asyncio.wait_for(backend.connect(), timeout=20)
        logger.info("preflight connect completed")

        authorized = await backend.authorized()
        logger.info("preflight auth check authorized=%s", authorized)
        if not authorized:
            if not sys.stdin.isatty():
                raise RuntimeError(
                    "Session ist nicht autorisiert. Bitte starte 'omagram auth' in einem interaktiven Terminal."
                )
            await perform_interactive_login(backend)

        await TelegramTui(backend).run_async()
    finally:
        if backend.client.is_connected():
            await backend.disconnect()
        logger.info("run stopped")


def main() -> None:
    args = build_parser().parse_args()
    log_path = configure_logging(
        debug=args.debug,
        log_path=args.log_file,
        console=args.console_log,
    )
    install_exception_logging()
    logger.info("command received command=%s log_file=%s", args.command, log_path)
    try:
        if args.command == "setup":
            asyncio.run(setup_credentials())
            return
        api_id, api_hash = load_config()
        if args.command == "auth":
            asyncio.run(authenticate(api_id, api_hash))
            return
        asyncio.run(run_app(api_id, api_hash))
    except KeyboardInterrupt:
        logger.info("interrupted by user")
        pass
    except Exception as exc:
        logger.exception("command failed")
        raise SystemExit(f"error: {exc}") from exc


if __name__ == "__main__":
    main()

