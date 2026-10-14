# second-brain-distill

Nightly pipeline that extracts Claude Code and Antigravity session transcripts, summarizes them into daily notes, and ingests them into an Obsidian second-brain vault. Runs on Windows and macOS.

## Supported AI tools

| Tool        | Role           | Reads from                             |
| ----------- | -------------- | -------------------------------------- |
| Claude Code | Session source | `~/.claude/projects/`                  |
| Antigravity | Session source | `~/.gemini/antigravity/conversations/` |

Adding another tool means writing a new extractor in `distiller/`, running it from `pipeline.py`, and adding a matching section to the prompts.

## How it works

Each run collects every Claude Code and Antigravity session since the last successful run, strips tool noise, and hands the conversations to `claude -p` (running inside your vault). Claude writes the day's note to `memory/Inbox/` and then runs your vault's ingest workflow.

The run only counts as done when `claude` exits cleanly and prints `INGEST_COMPLETE`. Until then the watermark (`.ingest-watermark`) stays put, so a failed or missed night is picked up by the next run. Nothing is lost; check `distill.log` if notes stop appearing.

## Setup

Needs Python 3.11+ and the [`claude` CLI](https://docs.claude.com/en/docs/claude-code) on your PATH. No other dependencies.

1. Adapt the prompts in `config/` to your vault. They use `$name` placeholders filled in at run time.
   - `run-prompt.txt`: where the daily note goes and how each vault folder is ingested. Keep the `INGEST_COMPLETE` instruction: the watermark only advances when Claude prints it.
   - `summary-rules.txt`: the note's format. Replace the project tag vocabulary with your own projects.
2. Run `python distill.py install-schedule` in a terminal. On first run it creates `config.toml` by asking for your vault folder, model and daily run time (press Enter to accept a default). Then it registers the daily run: Task Scheduler on Windows, launchd on macOS. Output goes to `distill.log`.

   Last, it asks whether this is your main device. The main device names daily notes by date only (`2026-09-29.md`). Every other device adds a suffix (`2026-09-29-Mac.md`), so devices sharing a git-synced vault never write the same file. The suggested suffix is `Mac` or `Windows`; type your own on a second device of the same kind.

   To change an answer later, edit `config.toml` (`config.example.toml` explains each key), then re-run `install-schedule` if you changed the run time. If Antigravity sessions get grouped under the wrong project, set the optional `project_dirs` key there.

On macOS, if your vault is under `~/Documents` or `~/Desktop`, give your Python binary Full Disk Access (System Settings → Privacy & Security), or scheduled runs can't read it.

## Usage

### `install-schedule`: register the nightly run

```
python distill.py install-schedule
```

Registers a daily `run` at `schedule_time`, with output appended to `distill.log`. Re-running it replaces the existing schedule.

- Windows: Task Scheduler task `second-brain-distill`, run as the logged-in user.
- macOS: launchd agent `com.second-brain-distill.daily`.

A run missed while the machine was off or asleep is not started when it wakes. The scheduler calls `run --scheduled`, which does nothing if it starts more than 30 minutes after `schedule_time` (launchd starts missed jobs on wake and can't be told not to). The next on-time run then catches up, writing one note per missed date. If you installed the schedule before this change, re-run `install-schedule`.
- Other systems: it prints a cron line for you to add yourself.

### `run`: summarize and ingest

```
python distill.py run
python distill.py run --since 2026-09-28T09:00 --until 2026-09-28T18:00
```

This is what the scheduler calls every night. It covers the sessions between the watermark and now, writes `memory/Inbox/<date>.md` (dated by the end of the window), then ingests the vault. The watermark only moves forward when the run succeeds.

If the window spans missed nights, it is cut at each missed day's `schedule_time` and each piece is ingested as its own dated note, oldest first, exactly as that night's run would have done. The watermark advances after each day, so a failure keeps the days already done and retries the rest next run.

- With no watermark yet (first run), it covers the last 24 hours.
- `--since` / `--until` take local ISO times and override the window. `--since` replaces the watermark as the start. On success the watermark moves to the end of the window, even an `--until` in the past, so the next nightly run starts from there.
- Exit code 0 means ingested, 1 means Claude failed or never printed `INGEST_COMPLETE` (the range is retried next run), and 2 means `config.toml` is missing or invalid.
- Claude runs with `--dangerously-skip-permissions` in your vault, so it can edit any file there without asking. Keep the vault in git.

### `backfill`: run specific past days

```
python distill.py backfill 2026-09-04
python distill.py backfill 2026-09-04 2026-09-05 2026-09-06
```

Does exactly what `run` does (pull the vault, write the daily note, ingest, commit and push) once per date, covering that whole day. It never moves the watermark, so the nightly run is unaffected. Exit code 1 if any date failed; re-running a date appends to its note without duplicating bullets.

## Development

```
python -m pytest
python -m ruff check . && python -m ruff format .
python -m mypy
```
