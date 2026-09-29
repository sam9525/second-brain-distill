import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = REPO_ROOT / "config.toml"
SUFFIX_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    vault: Path
    model: str
    schedule_time: str
    project_dirs: tuple[str, ...]
    device_suffix: str  # "" on the main device

    def note_name(self, date: str) -> str:
        # per-device names keep devices sharing one git-synced vault from
        # writing the same daily-note file
        return f"{date}-{self.device_suffix}" if self.device_suffix else date


def load_settings(path: Path = CONFIG_FILE) -> Settings:
    if not path.exists():
        raise ConfigError(
            f"{path.name} not found - copy config.example.toml to config.toml "
            "and set your vault path."
        )
    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    if "vault" not in raw:
        raise ConfigError(f"{path.name}: 'vault' is required.")
    vault = Path(raw["vault"]).expanduser()
    if not vault.is_dir():
        raise ConfigError(f"{path.name}: vault folder does not exist: {vault}")

    schedule_time = raw.get("schedule_time", "23:50")
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", schedule_time):
        raise ConfigError(f"{path.name}: schedule_time must be HH:MM (24h).")

    if "device_suffix" not in raw:
        raise ConfigError(
            f"{path.name}: device_suffix not set - run `python distill.py "
            "install-schedule` in a terminal once to choose it."
        )
    device_suffix = raw["device_suffix"]
    if device_suffix and not SUFFIX_PATTERN.fullmatch(device_suffix):
        raise ConfigError(
            f"{path.name}: device_suffix may only use letters, digits, - and _."
        )

    return Settings(
        vault=vault,
        model=raw.get("model", "sonnet"),
        schedule_time=schedule_time,
        project_dirs=tuple(
            str(Path(p).expanduser()) for p in raw.get("project_dirs", [])
        ),
        device_suffix=device_suffix,
    )
