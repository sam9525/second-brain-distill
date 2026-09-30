import datetime as dt
import subprocess
from pathlib import Path

import pytest

from distiller import antigravity_sessions, pipeline
from distiller.settings import ConfigError, Settings, load_settings


def make_settings(vault: Path) -> Settings:
    return Settings(
        vault=vault,
        model="sonnet",
        schedule_time="23:50",
        project_dirs=(),
        device_suffix="",
    )


def test_extractor_args_share_lookback_and_convert_claude_to_utc():
    since = dt.datetime(2026, 9, 28, 23, 50)
    until = dt.datetime(2026, 9, 29, 23, 50)
    claude_args, anti_args = pipeline.extractor_args(since, until)

    start = since - dt.timedelta(minutes=15)
    assert anti_args == ["since:2026-09-28T23:35:00", "until:2026-09-29T23:50:00"]
    assert claude_args == [
        f"since:{pipeline.utc_iso(start)}",
        f"until:{pipeline.utc_iso(until)}",
    ]
    assert claude_args[0].endswith("Z")


def test_extractor_args_leave_window_open_without_until():
    claude_args, anti_args = pipeline.extractor_args(dt.datetime(2026, 9, 28), None)
    assert len(claude_args) == len(anti_args) == 1


@pytest.mark.parametrize(
    ("exit_code", "saw_sentinel", "advanced"),
    [(0, True, True), (0, False, False), (1, True, False)],
)
def test_watermark_advances_only_on_clean_exit_with_sentinel(
    tmp_path, monkeypatch, exit_code, saw_sentinel, advanced
):
    watermark = tmp_path / ".ingest-watermark"
    watermark.write_text("2026-09-28T23:50:00")
    monkeypatch.setattr(pipeline, "WATERMARK_FILE", watermark)
    monkeypatch.setattr(pipeline, "extract", lambda *a: "")
    monkeypatch.setattr(pipeline, "call_claude", lambda *a: (exit_code, saw_sentinel))
    monkeypatch.setattr(pipeline, "pull_vault", lambda *a: True)
    monkeypatch.setattr(pipeline, "sync_vault", lambda *a: True)
    until = dt.datetime(2026, 9, 29, 23, 50)

    result = pipeline.run(make_settings(tmp_path), until=until)

    assert (result == 0) is advanced
    expected = "2026-09-29T23:50:00" if advanced else "2026-09-28T23:50:00"
    assert watermark.read_text() == expected


def test_watermark_holds_when_vault_sync_fails(tmp_path, monkeypatch):
    watermark = tmp_path / ".ingest-watermark"
    watermark.write_text("2026-09-28T23:50:00")
    monkeypatch.setattr(pipeline, "WATERMARK_FILE", watermark)
    monkeypatch.setattr(pipeline, "extract", lambda *a: "")
    monkeypatch.setattr(pipeline, "call_claude", lambda *a: (0, True))
    monkeypatch.setattr(pipeline, "pull_vault", lambda *a: True)
    monkeypatch.setattr(pipeline, "sync_vault", lambda *a: False)

    assert pipeline.run(make_settings(tmp_path)) == 1
    assert watermark.read_text() == "2026-09-28T23:50:00"


def make_vault(tmp_path: Path, with_remote: bool = True) -> Settings:
    vault = tmp_path / "vault"
    vault.mkdir()
    settings = make_settings(vault)
    pipeline.git(settings, "init", "-b", "main")
    pipeline.git(settings, "config", "user.email", "t@example.com")
    pipeline.git(settings, "config", "user.name", "t")
    if with_remote:
        remote = tmp_path / "remote.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)])
        pipeline.git(settings, "remote", "add", "origin", str(remote))
    (vault / "note.md").write_text("hi")
    return settings


def remote_head(tmp_path: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(tmp_path / "remote.git"), "rev-parse", "main"],
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_sync_vault_commits_and_pushes_main(tmp_path):
    settings = make_vault(tmp_path)
    assert pipeline.sync_vault(settings, "2026-09-30")
    local = pipeline.git(settings, "rev-parse", "main").stdout.strip()
    assert remote_head(tmp_path) == local
    log = pipeline.git(settings, "log", "-1", "--format=%s").stdout.strip()
    assert log == "chore: distill 2026-09-30"


def test_sync_vault_nothing_to_commit_still_pushes(tmp_path):
    settings = make_vault(tmp_path)
    pipeline.git(settings, "add", "-A")
    pipeline.git(settings, "commit", "-m", "earlier")
    assert pipeline.sync_vault(settings, "2026-09-30")
    local = pipeline.git(settings, "rev-parse", "main").stdout.strip()
    assert pipeline.git(settings, "log", "-1", "--format=%s").stdout.strip() == (
        "earlier"
    )
    assert remote_head(tmp_path) == local


def test_sync_vault_without_remote_commits_only(tmp_path):
    settings = make_vault(tmp_path, with_remote=False)
    assert pipeline.sync_vault(settings, "2026-09-30")
    assert pipeline.git(settings, "status", "--porcelain").stdout == ""


def test_sync_vault_fails_on_push_failure(tmp_path):
    settings = make_vault(tmp_path)
    pipeline.git(settings, "remote", "set-url", "origin", str(tmp_path / "missing"))
    assert not pipeline.sync_vault(settings, "2026-09-30")


def test_run_skips_ingest_when_pull_fails(tmp_path, monkeypatch):
    watermark = tmp_path / ".ingest-watermark"
    watermark.write_text("2026-09-28T23:50:00")
    monkeypatch.setattr(pipeline, "WATERMARK_FILE", watermark)
    monkeypatch.setattr(pipeline, "pull_vault", lambda *a: False)

    def fail(*a):
        raise AssertionError("ingest ran after a failed pull")

    monkeypatch.setattr(pipeline, "call_claude", fail)
    assert pipeline.run(make_settings(tmp_path)) == 1
    assert watermark.read_text() == "2026-09-28T23:50:00"


def push_from_other_device(tmp_path: Path, name: str, text: str) -> None:
    other = tmp_path / "other"
    if not other.exists():
        subprocess.run(["git", "clone", str(tmp_path / "remote.git"), str(other)])
    run = ["git", "-C", str(other), "-c", "user.email=o@x", "-c", "user.name=o"]
    (other / name).write_text(text)
    subprocess.run([*run, "add", "-A"])
    subprocess.run([*run, "commit", "-m", f"other {name}"])
    subprocess.run([*run, "push", "origin", "main"])


def test_pull_vault_brings_in_other_device_commits(tmp_path):
    settings = make_vault(tmp_path)
    assert pipeline.sync_vault(settings, "2026-09-29")
    push_from_other_device(tmp_path, "other.md", "from other")
    (settings.vault / "open-in-obsidian.md").write_text("uncommitted")

    assert pipeline.pull_vault(settings)
    assert (settings.vault / "other.md").read_text() == "from other"
    assert (settings.vault / "open-in-obsidian.md").read_text() == "uncommitted"


def test_sync_vault_rebases_and_retries_rejected_push(tmp_path):
    settings = make_vault(tmp_path)
    assert pipeline.sync_vault(settings, "2026-09-29")
    push_from_other_device(tmp_path, "other.md", "from other")
    (settings.vault / "mine.md").write_text("mine")

    assert pipeline.sync_vault(settings, "2026-09-30")
    local = pipeline.git(settings, "rev-parse", "main").stdout.strip()
    assert remote_head(tmp_path) == local
    assert (settings.vault / "other.md").exists()


def test_sync_vault_aborts_conflicting_rebase(tmp_path):
    settings = make_vault(tmp_path)
    assert pipeline.sync_vault(settings, "2026-09-29")
    push_from_other_device(tmp_path, "note.md", "theirs")
    (settings.vault / "note.md").write_text("mine")

    assert not pipeline.sync_vault(settings, "2026-09-30")
    assert pipeline.git(settings, "branch", "--show-current").stdout.strip() == "main"
    assert (settings.vault / "note.md").read_text() == "mine"


def test_sync_vault_refuses_off_main(tmp_path):
    settings = make_vault(tmp_path)
    pipeline.git(settings, "switch", "-c", "develop")
    assert not pipeline.sync_vault(settings, "2026-09-30")
    assert pipeline.git(settings, "status", "--porcelain").stdout != ""


def test_run_prompt_template_fills_every_placeholder():
    prompt = pipeline.render(
        "run-prompt.txt",
        date="2026-09-29",
        note_name="2026-09-29-Mac",
        claude_out="C-OUT",
        anti_out="A-OUT",
    )
    assert "C-OUT" in prompt and "A-OUT" in prompt
    assert "memory/Inbox/2026-09-29-Mac.md" in prompt
    assert "memory/Inbox/2026-09-29.md" not in prompt
    assert pipeline.SENTINEL in prompt


def test_backfill_ingests_each_date_and_leaves_watermark(tmp_path, monkeypatch):
    watermark = tmp_path / ".ingest-watermark"
    watermark.write_text("2026-09-28T23:50:00")
    monkeypatch.setattr(pipeline, "WATERMARK_FILE", watermark)
    monkeypatch.setattr(pipeline, "pull_vault", lambda *a: True)
    monkeypatch.setattr(pipeline, "call_claude", lambda *a: (0, True))
    extracted: list[list[str]] = []
    monkeypatch.setattr(
        pipeline, "extract", lambda s, args, st: extracted.append(args) or ""
    )
    synced: list[str] = []
    monkeypatch.setattr(
        pipeline, "sync_vault", lambda st, date: synced.append(date) or True
    )

    days = [dt.date(2026, 9, 4), dt.date(2026, 9, 5)]
    assert pipeline.backfill(make_settings(tmp_path), days) == 0
    assert extracted == [["2026-09-04"]] * 2 + [["2026-09-05"]] * 2
    assert synced == ["2026-09-04", "2026-09-05"]
    assert watermark.read_text() == "2026-09-28T23:50:00"


def test_backfill_fails_when_a_date_misses_the_sentinel(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "pull_vault", lambda *a: True)
    monkeypatch.setattr(pipeline, "extract", lambda *a: "")
    monkeypatch.setattr(pipeline, "call_claude", lambda *a: (0, False))
    monkeypatch.setattr(pipeline, "sync_vault", lambda *a: True)
    assert pipeline.backfill(make_settings(tmp_path), [dt.date(2026, 9, 4)]) == 1


def test_load_settings_rejects_missing_vault(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(f'vault = "{(tmp_path / "nope").as_posix()}"')
    with pytest.raises(ConfigError, match="does not exist"):
        load_settings(config)


def test_load_settings_rejects_bad_schedule_time(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(f'vault = "{tmp_path.as_posix()}"\nschedule_time = "25:00"')
    with pytest.raises(ConfigError, match="HH:MM"):
        load_settings(config)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "see `d:/data/Program/JARVIS/src/x.ts` here",
            "d:/data/Program/JARVIS/src/x.ts",
        ),
        ("open `/Users/sam/code/app/main.py` now", "/Users/sam/code/app/main.py"),
        ("file:///d:/data/Program/JARVIS/x.ts", None),
        ("edit tests/home/WorkspaceRail.test.tsx", None),
    ],
)
def test_abs_path_matches_windows_and_mac_but_not_uris_or_relative(text, expected):
    match = antigravity_sessions.ABS_PATH.search(text)
    assert (match.group(0) if match else None) == expected


def test_workspace_prefers_configured_project_dirs(monkeypatch):
    monkeypatch.setattr(antigravity_sessions, "PROJECT_DIRS", ["/users/sam/code"])
    assert antigravity_sessions.is_workspace_path("/Users/sam/code/app/main.py")
    assert not antigravity_sessions.is_workspace_path("/Users/sam/scratch/x.py")
