# second-brain-distill

Nightly pipeline that extracts Claude Code and Antigravity session transcripts, summarizes them into daily notes, and ingests them into an Obsidian second-brain vault. Runs on Windows and macOS.

## Setup

Needs Python 3.11+ and the [`claude` CLI](https://docs.claude.com/en/docs/claude-code) on your PATH. No other dependencies.

1. Edit `config/run-prompt.txt` to match your vault's folder layout. Keep the `INGEST_COMPLETE` instruction: the watermark only advances when Claude prints it.
2. Run `python distill.py install-schedule` in a terminal. On first run it creates `config.toml` by asking for your vault folder, project folders, model and daily run time (press Enter to accept a default). Then it registers the daily run: Task Scheduler on Windows, launchd on macOS. Output goes to `distill.log`.

   Last, it asks whether this is your main device. The main device names daily notes by date only (`2026-09-29.md`). Every other device adds a suffix (`2026-09-29-Mac.md`), so devices sharing a git-synced vault never write the same file. The suggested suffix is `Mac` or `Windows`; type your own on a second device of the same kind.

   To change an answer later, edit `config.toml` (`config.example.toml` explains each key), then re-run `install-schedule` if you changed the run time.

`python distill.py run` runs it once by hand; `python distill.py backfill 2026-09-04` rebuilds past days into `backfill/` for review.

On macOS, if your vault is under `~/Documents` or `~/Desktop`, give your Python binary Full Disk Access (System Settings → Privacy & Security), or scheduled runs can't read it.
