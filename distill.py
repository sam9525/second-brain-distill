"""Distill Claude Code + Antigravity sessions into Obsidian daily notes.

python distill.py run [--since ISO] [--until ISO]
python distill.py backfill 2026-09-04 2026-09-05
python distill.py install-schedule
"""

import argparse
import datetime as dt
import io
import sys

if sys.version_info < (3, 11):
    sys.exit("second-brain-distill needs Python 3.11+ (for tomllib).")

import tomllib  # noqa: E402

from distiller import pipeline, schedule  # noqa: E402
from distiller.config_wizard import ensure_config  # noqa: E402
from distiller.device import ensure_device_suffix  # noqa: E402
from distiller.settings import ConfigError, load_settings  # noqa: E402


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        # transcripts contain emoji; the console or log may default to cp950/cp1252
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    run_cmd = commands.add_parser(
        "run", help="summarize and ingest everything since the last successful run"
    )
    run_cmd.add_argument(
        "--since",
        type=dt.datetime.fromisoformat,
        help="local ISO time to start from (default: the watermark)",
    )
    run_cmd.add_argument(
        "--until",
        type=dt.datetime.fromisoformat,
        help="local ISO time to stop at (default: now)",
    )

    backfill_cmd = commands.add_parser(
        "backfill", help="rebuild daily notes for past dates into backfill/"
    )
    backfill_cmd.add_argument("dates", nargs="+", type=dt.date.fromisoformat)

    commands.add_parser(
        "install-schedule",
        help="register the daily run (Task Scheduler on Windows, launchd on macOS)",
    )

    args = parser.parse_args()
    try:
        ensure_config()
        ensure_device_suffix()
        settings = load_settings()
    except (ConfigError, tomllib.TOMLDecodeError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    if args.command == "run":
        return pipeline.run(settings, args.since, args.until)
    if args.command == "backfill":
        return pipeline.backfill(settings, args.dates)
    print(schedule.install(settings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
