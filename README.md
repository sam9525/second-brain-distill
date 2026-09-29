# second-brain-distill

Nightly pipeline that extracts Claude Code and Antigravity session transcripts, summarizes them into daily notes, and ingests them into an Obsidian second-brain vault. Runs on Windows and macOS.

## Setup

Needs Python 3.11+ and the [`claude` CLI](https://docs.claude.com/en/docs/claude-code) on your PATH. No other dependencies.

1. `cp config.example.toml config.toml` and set `vault` (and `project_dirs`).
2. Edit `config/run-prompt.txt` to match your vault's folder layout. Keep the `INGEST_COMPLETE` instruction: the watermark only advances when Claude prints it.
3. `python distill.py install-schedule` registers the daily run (Task Scheduler on Windows, launchd on macOS). Output goes to `distill.log`.

`python distill.py run` runs it once by hand; `python distill.py backfill 2026-09-04` rebuilds past days into `backfill/` for review.

On macOS, if your vault is under `~/Documents` or `~/Desktop`, give your Python binary Full Disk Access (System Settings → Privacy & Security), or scheduled runs can't read it.
