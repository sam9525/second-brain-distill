import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = REPO_ROOT / "config.toml"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    vault: Path
    model: str
    schedule_time: str
    project_dirs: tuple[str, ...]


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

    return Settings(
        vault=vault,
        model=raw.get("model", "sonnet"),
        schedule_time=schedule_time,
        project_dirs=tuple(
            str(Path(p).expanduser()) for p in raw.get("project_dirs", [])
        ),
    )
