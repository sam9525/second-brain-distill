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


def ask_project_dirs(ask: Ask) -> list[str]:
    while True:
        answer = ask(
            "Folders where you keep your projects, comma-separated; used to "
            "label Antigravity sessions (Enter to skip): "
        )
        # ponytail: comma-split, so a folder name containing a comma can't be
        # entered here; edit config.toml by hand for that
        dirs = [clean(d) for d in answer.split(",") if clean(d)]
        missing = [d for d in dirs if not Path(d).expanduser().is_dir()]
        if not missing:
            return dirs
        print(f"  Not an existing folder: {', '.join(missing)}")


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
    project_dirs = ask_project_dirs(ask)
    model = ask_model(ask)
    schedule_time = ask_schedule_time(ask)

    dirs = ", ".join(toml_string(d) for d in project_dirs)
    path.write_text(
        "# Created by the first-run setup; edit freely. "
        "See config.example.toml for what each key does.\n"
        f"vault = {toml_string(vault)}\n"
        f"model = {toml_string(model)}\n"
        f"schedule_time = {toml_string(schedule_time)}\n"
        f"project_dirs = [{dirs}]\n",
        encoding="utf-8",
    )
    print(f"\nSaved {path}.\n")
