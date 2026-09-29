import json
import sys
from collections.abc import Callable
from pathlib import Path

from distiller.settings import CONFIG_FILE, SCHEDULE_TIME_PATTERN

Ask = Callable[[str], str]


def clean(answer: str) -> str:
    # dragging a folder into a terminal wraps its path in quotes
    return answer.strip().strip("\"'")


def ask_vault(ask: Ask) -> str:
    while True:
        vault = clean(ask("Obsidian vault folder (daily notes are written here): "))
        if vault and Path(vault).expanduser().is_dir():
            return vault
        print(f"  Not an existing folder: {vault or '(empty)'}")


def ask_model(ask: Ask) -> str:
    return clean(ask("Claude model for summaries [sonnet]: ")) or "sonnet"


def ask_schedule_time(ask: Ask) -> str:
    while True:
        time = clean(ask("Daily run time, 24h HH:MM [23:50]: ")) or "23:50"
        if SCHEDULE_TIME_PATTERN.fullmatch(time):
            return time
        print("  Use 24h HH:MM, e.g. 23:50.")


def toml_string(value: str) -> str:
    # a JSON string literal is a valid TOML basic string (Windows backslashes too)
    return json.dumps(value, ensure_ascii=False)


def ensure_config(path: Path = CONFIG_FILE, ask: Ask = input) -> None:
    """On the first interactive run, create config.toml from terminal answers.

    Scheduled runs have no terminal to ask in; load_settings reports the
    missing file for them instead."""
    if path.exists() or not sys.stdin.isatty():
        return

    print(f"No {path.name} yet - answer a few questions to create it.\n")
    vault = ask_vault(ask)
    model = ask_model(ask)
    schedule_time = ask_schedule_time(ask)

    # project_dirs is left out on purpose: it only sharpens Antigravity's
    # project labels, and config.example.toml explains it for those who want it
    path.write_text(
        "# Created by the first-run setup; edit freely. "
        "See config.example.toml for what each key does.\n"
        f"vault = {toml_string(vault)}\n"
        f"model = {toml_string(model)}\n"
        f"schedule_time = {toml_string(schedule_time)}\n",
        encoding="utf-8",
    )
    print(f"\nSaved {path}.\n")
