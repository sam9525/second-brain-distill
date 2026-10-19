import plistlib
import sys

import pytest

from distiller import schedule


def test_windows_task_does_not_fire_missed_runs_on_wake():
    assert "<StartWhenAvailable>false</StartWhenAvailable>" in schedule.TASK_XML


@pytest.mark.skipif(sys.platform == "win32", reason="launchd is macOS-only")
def test_launchd_job_runs_in_scheduled_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(schedule.subprocess, "run", lambda *a, **k: None)

    schedule.install_macos(23, 50)

    plist_path = (
        tmp_path / "Library" / "LaunchAgents" / f"{schedule.LAUNCHD_LABEL}.plist"
    )
    with plist_path.open("rb") as fh:
        plist = plistlib.load(fh)
    assert plist["ProgramArguments"][-2:] == ["run", "--scheduled"]
    # polls instead of StartCalendarInterval, which launchd evaluates in a stale
    # timezone after the computer's timezone changes
    assert "StartCalendarInterval" not in plist
    assert plist["StartInterval"] == schedule.LAUNCHD_POLL_SECONDS
