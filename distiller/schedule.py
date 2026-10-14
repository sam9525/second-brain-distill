import datetime as dt
import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from distiller.settings import REPO_ROOT, Settings

TASK_NAME = "second-brain-distill"
LAUNCHD_LABEL = "com.second-brain-distill.daily"
LOG_FILE = REPO_ROOT / "distill.log"
ENTRY_POINT = REPO_ROOT / "distill.py"

# StartWhenAvailable false: a run missed while the PC was off/asleep is NOT fired
# at wake; the next on-time run splits the missed days into their own notes.
# InteractiveToken: runs as the logged-in user, no stored password needed.
TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Distill the day's AI sessions into the Obsidian vault.</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start}</StartBoundary>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <StartWhenAvailable>false</StartWhenAvailable>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT4H</ExecutionTimeLimit>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>cmd.exe</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{workdir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def install(settings: Settings) -> str:
    hour, minute = (int(part) for part in settings.schedule_time.split(":"))
    if sys.platform == "win32":
        return install_windows(hour, minute)
    if sys.platform == "darwin":
        return install_macos(hour, minute)
    return (
        "No scheduler installer for this OS. Add a cron entry instead:\n"
        f'{minute} {hour} * * * "{sys.executable}" "{ENTRY_POINT}" run --scheduled '
        f'>> "{LOG_FILE}" 2>&1'
    )


def install_windows(hour: int, minute: int) -> str:
    start = dt.datetime.combine(dt.date.today(), dt.time(hour, minute))
    # cmd /c strips the outer quote pair, leaving each path individually quoted
    arguments = (
        f'/c ""{sys.executable}" "{ENTRY_POINT}" run --scheduled >> "{LOG_FILE}" 2>&1"'
    )
    xml = TASK_XML.format(
        start=start.isoformat(timespec="seconds"),
        arguments=escape(arguments),
        workdir=escape(str(REPO_ROOT)),
    )
    # schtasks expects the task XML as UTF-16, matching the declaration
    with tempfile.TemporaryDirectory() as tmp:
        xml_path = Path(tmp) / "task.xml"
        xml_path.write_text(xml, encoding="utf-16")
        subprocess.run(
            ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"],
            check=True,
        )
    return (
        f"Task Scheduler task '{TASK_NAME}' runs daily at {hour:02d}:{minute:02d}; "
        f"output appends to {LOG_FILE}."
    )


def install_macos(hour: int, minute: int) -> str:
    if sys.platform == "win32":  # also tells the type checker os.getuid exists below
        raise RuntimeError("launchd install is macOS-only")
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
    plist = {
        "Label": LAUNCHD_LABEL,
        # launchd fires a StartCalendarInterval job missed during sleep on wake and
        # has no key to stop it; --scheduled makes that late start a no-op
        "ProgramArguments": [sys.executable, str(ENTRY_POINT), "run", "--scheduled"],
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "WorkingDirectory": str(REPO_ROOT),
        "StandardOutPath": str(LOG_FILE),
        "StandardErrorPath": str(LOG_FILE),
        # launchd jobs get a bare PATH; carry the installer's so `claude` resolves
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "")},
    }
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    with plist_path.open("wb") as fh:
        plistlib.dump(plist, fh)

    domain = f"gui/{os.getuid()}"
    # bootout fails harmlessly when the job isn't loaded yet (first install)
    subprocess.run(
        ["launchctl", "bootout", domain, str(plist_path)], capture_output=True
    )
    subprocess.run(["launchctl", "bootstrap", domain, str(plist_path)], check=True)
    return (
        f"launchd job '{LAUNCHD_LABEL}' runs daily at {hour:02d}:{minute:02d}; "
        f"output appends to {LOG_FILE}."
    )
