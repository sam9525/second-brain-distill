import datetime as dt
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
    until = dt.datetime(2026, 9, 29, 23, 50)

    result = pipeline.run(make_settings(tmp_path), until=until)

    assert (result == 0) is advanced
    expected = "2026-09-29T23:50:00" if advanced else "2026-09-28T23:50:00"
    assert watermark.read_text() == expected


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


def test_backfill_prompt_template_fills_every_placeholder():
    prompt = pipeline.render(
        "backfill-prompt.txt",
        date="2026-09-04",
        claude_out="",
        anti_out="",
        out_file="/tmp/backfill/2026-09-04.md",
    )
    assert "/tmp/backfill/2026-09-04.md" in prompt


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
