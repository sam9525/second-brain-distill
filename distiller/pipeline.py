import datetime as dt
import os
import shutil
import subprocess
import sys
import threading
from string import Template

from distiller.settings import REPO_ROOT, Settings

SCRIPTS_DIR = REPO_ROOT / "distiller"
CONFIG_DIR = REPO_ROOT / "config"
WATERMARK_FILE = REPO_ROOT / ".ingest-watermark"
# The schedule slot (local schedule_time) the last scheduled run started for, so the
# launchd poll runs once per night however many ticks land inside the grace window.
SCHEDULED_SLOT_FILE = REPO_ROOT / ".last-scheduled-slot"
SENTINEL = "INGEST_COMPLETE"
# Both extractors get the SAME lookback, and it MUST cover their active-file window
# (ACTIVE_BUFFER_SEC=900s): a session still live at the previous run was deferred
# then, so this run must reach back far enough to re-read it whole.
LOOKBACK = dt.timedelta(minutes=15)
# A scheduled run only proceeds within this long after schedule_time, in the
# computer's current timezone. Later (e.g. the machine slept through it) it is
# skipped, and the next on-time run covers the missed days (one note per day).
SCHEDULE_GRACE = dt.timedelta(minutes=30)


def utc_iso(local: dt.datetime) -> str:
    # Claude transcripts timestamp in UTC; the watermark is local time
    return local.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_iso(local: dt.datetime) -> str:
    return local.isoformat(timespec="seconds")


def extractor_args(
    since: dt.datetime, until: dt.datetime | None
) -> tuple[list[str], list[str]]:
    """Window arguments for (claude_sessions, antigravity_sessions)."""
    start = since - LOOKBACK
    claude_args = [f"since:{utc_iso(start)}"]
    anti_args = [f"since:{local_iso(start)}"]
    if until is not None:
        claude_args.append(f"until:{utc_iso(until)}")
        anti_args.append(f"until:{local_iso(until)}")
    return claude_args, anti_args


def extract(script: str, args: list[str], settings: Settings) -> str:
    # Extraction runs here, never from inside the prompt: asking the LLM to run the
    # extractors let a source be silently skipped while the watermark still advanced.
    env = os.environ | {
        "PYTHONIOENCODING": "utf-8",
        "DISTILL_PROJECT_DIRS": os.pathsep.join(settings.project_dirs),
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    return result.stdout.decode("utf-8", errors="replace")


def render(template_name: str, **values: str) -> str:
    text = (CONFIG_DIR / template_name).read_text(encoding="utf-8")
    rules = (CONFIG_DIR / "summary-rules.txt").read_text(encoding="utf-8")
    return Template(text).substitute(values, rules=rules)


def call_claude(prompt: str, settings: Settings) -> tuple[int, bool]:
    """Run `claude -p` in the vault, streaming its output live.

    Returns (exit code, whether the INGEST_COMPLETE sentinel was printed)."""
    claude = shutil.which("claude")
    if claude is None:
        print("error: `claude` CLI not found on PATH.", file=sys.stderr)
        return 127, False
    # Prompt goes over stdin: session transcripts make it hundreds of KB, far past
    # the ~32KB Windows command-line limit.
    proc = subprocess.Popen(
        [claude, "--dangerously-skip-permissions", "-p", "--model", settings.model],
        cwd=settings.vault,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdin is not None and proc.stdout is not None

    def feed_prompt() -> None:
        assert proc.stdin is not None
        proc.stdin.write(prompt)
        proc.stdin.close()

    # writer thread: a blocking stdin write must never stall reading stdout
    threading.Thread(target=feed_prompt, daemon=True).start()
    saw_sentinel = False
    for line in proc.stdout:
        print(line, end="", flush=True)
        saw_sentinel = saw_sentinel or SENTINEL in line
    return proc.wait(), saw_sentinel


def git(settings: Settings, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=settings.vault, capture_output=True, text=True
    )


def on_main(settings: Settings) -> bool:
    branch = git(settings, "branch", "--show-current")
    if branch.stdout.strip() == "main":
        return True
    print(f"Vault is not on main ({branch.stdout.strip() or branch.stderr.strip()}).")
    return False


def vault_remote(settings: Settings) -> str | None:
    remotes = git(settings, "remote").stdout.split()
    if not remotes:
        return None
    return "origin" if "origin" in remotes else remotes[0]


def pull_main(settings: Settings, remote: str) -> bool:
    # rebase keeps main linear; autostash covers edits left open in Obsidian
    pull = git(settings, "pull", "--rebase", "--autostash", remote, "main")
    if pull.returncode == 0:
        return True
    git(settings, "rebase", "--abort")  # a conflict must not leave main mid-rebase
    print(f"Vault pull from {remote} failed:\n{pull.stdout}{pull.stderr}")
    return False


def pull_vault(settings: Settings) -> bool:
    """Bring the vault up to date before ingest.

    Ingest rewrites wiki/index files every device shares, so ingesting into a stale
    copy all but guarantees a conflicting push."""
    if not on_main(settings):
        return False
    remote = vault_remote(settings)
    if remote is None:
        print("Vault: no remote configured - pull skipped.")
        return True
    return pull_main(settings, remote)


def sync_vault(settings: Settings, date: str) -> bool:
    """Commit everything in the vault on main and push main.

    A vault with no remote is local-only by choice, so only the push is skipped."""
    if not on_main(settings):
        return False
    git(settings, "add", "-A")
    if git(settings, "diff", "--cached", "--quiet").returncode == 0:
        print("Vault: nothing to commit.")
    else:
        commit = git(settings, "commit", "-m", f"chore: distill {date}")
        if commit.returncode != 0:
            print(f"Vault commit failed:\n{commit.stdout}{commit.stderr}")
            return False
        print(f"Vault: committed chore: distill {date}.")
    remote = vault_remote(settings)
    if remote is None:
        print("Vault: no remote configured - push skipped.")
        return True
    # still pushes on "nothing to commit": a previous run's push may have failed
    push = git(settings, "push", remote, "main")
    if push.returncode != 0:
        # another device may have pushed during the ingest: rebase onto it once
        if not pull_main(settings, remote):
            return False
        push = git(settings, "push", remote, "main")
    if push.returncode != 0:
        print(f"Vault push to {remote} failed:\n{push.stderr}")
        return False
    print(f"Vault: pushed main to {remote}.")
    return True


def read_watermark(now: dt.datetime) -> dt.datetime:
    if WATERMARK_FILE.exists():
        return dt.datetime.fromisoformat(WATERMARK_FILE.read_text().strip())
    start = now - dt.timedelta(days=1)
    print(f"No watermark found - bootstrapping from {local_iso(start)}")
    return start


def ingest(
    settings: Settings,
    date: str,
    claude_args: list[str],
    anti_args: list[str],
    window: str,
) -> bool:
    """Pull the vault, summarize the window into the daily note, ingest, push.

    True only when ingest finished (sentinel seen) and the vault synced."""
    if not pull_vault(settings):
        print("Vault pull failed - ingest skipped.")
        return False

    claude_out = extract("claude_sessions.py", claude_args, settings)
    anti_out = extract("antigravity_sessions.py", anti_args, settings)
    print(
        f"Claude output: {len(claude_out)} chars | "
        f"Antigravity output: {len(anti_out)} chars"
    )

    prompt = render(
        "run-prompt.txt",
        date=date,
        note_name=settings.note_name(date),
        claude_out=claude_out,
        anti_out=anti_out,
    )
    print(f"Daily note task - {date} ({window})")
    exit_code, saw_sentinel = call_claude(prompt, settings)

    # Exit 0 alone is not enough: a run once exited 0 after writing the daily note
    # but never reached ingest, and the watermark advanced past the skipped work.
    if exit_code == 0 and saw_sentinel:
        if sync_vault(settings, date):
            return True
        print("Vault git sync failed.")
    elif exit_code == 0:
        print(
            f"Claude exited 0 but never printed {SENTINEL} - ingest likely incomplete."
        )
    else:
        print(f"Ingest exited {exit_code}.")
    return False


def now_local() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


def last_scheduled(schedule_time: str, now: dt.datetime) -> dt.datetime:
    """The most recent daily schedule_time at or before now."""
    hour, minute = (int(part) for part in schedule_time.split(":"))
    slot = dt.datetime.combine(now.date(), dt.time(hour, minute))
    return slot if slot <= now else slot - dt.timedelta(days=1)


def claim_scheduled_slot(schedule_time: str, now: dt.datetime) -> bool:
    """Whether a scheduled tick should run now; records the slot when it should.

    Silent on refusal: launchd polls every few minutes, so most ticks refuse."""
    slot = last_scheduled(schedule_time, now)
    if now - slot > SCHEDULE_GRACE:
        return False
    if (
        SCHEDULED_SLOT_FILE.exists()
        and SCHEDULED_SLOT_FILE.read_text().strip() == local_iso(slot)
    ):
        return False
    SCHEDULED_SLOT_FILE.write_text(local_iso(slot), encoding="ascii")
    return True


def split_by_day(
    start: dt.datetime, end: dt.datetime, schedule_time: str
) -> list[tuple[dt.datetime, dt.datetime]]:
    """Cut [start, end) at each day's schedule_time before end's date.

    Each piece is what that night's run would have covered, so a window spanning
    missed nights yields one piece (one daily note) per date, dated by its end."""
    hour, minute = (int(part) for part in schedule_time.split(":"))
    pieces = []
    day = start.date()
    while day < end.date():
        cut = dt.datetime.combine(day, dt.time(hour, minute))
        if cut > start:
            pieces.append((start, cut))
            start = cut
        day += dt.timedelta(days=1)
    pieces.append((start, end))
    return pieces


def run(
    settings: Settings,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
    scheduled: bool = False,
) -> int:
    now = now_local()
    if scheduled and not claim_scheduled_slot(settings.schedule_time, now):
        return 0
    end = until or now
    window_start = since or read_watermark(now)
    for piece_start, piece_end in split_by_day(
        window_start, end, settings.schedule_time
    ):
        # the final piece stays open-ended unless --until closed the window
        piece_until = piece_end if piece_end < end or until is not None else None
        claude_args, anti_args = extractor_args(piece_start, piece_until)
        window = f"since {local_iso(piece_start)}"
        date = piece_end.date().isoformat()
        if not ingest(settings, date, claude_args, anti_args, window):
            print("Watermark NOT advanced; this range retries next run.")
            return 1
        WATERMARK_FILE.write_text(local_iso(piece_end), encoding="ascii")
        print(f"Watermark advanced to {local_iso(piece_end)}.")
    return 0


def backfill(settings: Settings, dates: list[dt.date]) -> int:
    """Run the normal pull/summarize/ingest/push for each whole past day.

    The watermark is left alone: it tracks the incremental run, not these days."""
    failures = 0
    for day in dates:
        date = day.isoformat()
        failures += not ingest(settings, date, [date], [date], "backfill")
    return 1 if failures else 0
