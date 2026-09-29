import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

from distiller.settings import CONFIG_FILE, SUFFIX_PATTERN

OS_DEFAULT_SUFFIX = {"darwin": "Mac", "win32": "Windows"}


def ask_device_suffix(
    platform: str = sys.platform, ask: Callable[[str], str] = input
) -> str:
    """Ask whether this is the main device; if not, ask for its note suffix."""
    while True:
        answer = (
            ask(
                "Is this your main device? Its daily notes are named by date "
                "only (2026-09-29.md). [Y/n] "
            )
            .strip()
            .lower()
        )
        if answer in ("", "y", "yes"):
            return ""
        if answer in ("n", "no"):
            break

    default = OS_DEFAULT_SUFFIX.get(platform, "Linux")
    while True:
        suffix = (
            ask(
                f"Suffix for this device's notes (2026-09-29-{default}.md). "
                f"Give every extra device its own. [{default}] "
            ).strip()
            or default
        )
        if SUFFIX_PATTERN.fullmatch(suffix):
            return suffix
        print("Use only letters, digits, - and _.")


def ensure_device_suffix(path: Path = CONFIG_FILE) -> None:
    """On a device's first interactive run, ask for its suffix and save it.

    Scheduled runs have no terminal to ask in; load_settings reports the
    missing key for them instead."""
    if not path.exists() or not sys.stdin.isatty():
        return
    with path.open("rb") as fh:
        if "device_suffix" in tomllib.load(fh):
            return

    suffix = ask_device_suffix()
    text = path.read_text(encoding="utf-8")
    separator = "\n" if text and not text.endswith("\n") else ""
    path.write_text(
        f"{text}{separator}\n"
        '# chosen on first run; "" = main device (notes named by date only)\n'
        f'device_suffix = "{suffix}"\n',
        encoding="utf-8",
    )
