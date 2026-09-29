import io
import sys
from pathlib import Path

import pytest

from distiller import device
from distiller.settings import ConfigError, Settings, load_settings


def scripted(*answers: str):
    replies = iter(answers)
    return lambda prompt: next(replies)


@pytest.mark.parametrize("answer", ["", "y", "YES"])
def test_main_device_gets_no_suffix(answer):
    assert device.ask_device_suffix("darwin", scripted(answer)) == ""


@pytest.mark.parametrize(
    ("platform", "expected"), [("darwin", "Mac"), ("win32", "Windows")]
)
def test_other_device_defaults_to_os_name(platform, expected):
    assert device.ask_device_suffix(platform, scripted("n", "")) == expected


def test_other_device_can_type_its_own_suffix_and_bad_ones_are_reasked():
    ask = scripted("maybe", "no", "Mac Mini!", "MacMini")
    assert device.ask_device_suffix("darwin", ask) == "MacMini"


def write_config(tmp_path: Path, extra: str = "") -> Path:
    config = tmp_path / "config.toml"
    config.write_text(f'vault = "{tmp_path.as_posix()}"{extra}', encoding="utf-8")
    return config


class FakeTty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_first_interactive_run_saves_suffix_that_load_settings_reads(
    tmp_path, monkeypatch
):
    config = write_config(tmp_path)
    monkeypatch.setattr(sys, "stdin", FakeTty())
    monkeypatch.setattr(device, "ask_device_suffix", lambda: "Mac")

    device.ensure_device_suffix(config)

    assert load_settings(config).note_name("2026-09-29") == "2026-09-29-Mac"


def test_existing_suffix_is_never_reasked(tmp_path, monkeypatch):
    config = write_config(tmp_path, '\ndevice_suffix = ""\n')
    monkeypatch.setattr(sys, "stdin", FakeTty())
    monkeypatch.setattr(device, "ask_device_suffix", lambda: pytest.fail("asked"))

    device.ensure_device_suffix(config)

    assert load_settings(config).note_name("2026-09-29") == "2026-09-29"


def test_scheduled_run_without_suffix_errors_instead_of_guessing(tmp_path, monkeypatch):
    config = write_config(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO())  # not a terminal

    device.ensure_device_suffix(config)

    with pytest.raises(ConfigError, match="device_suffix not set"):
        load_settings(config)


def test_load_settings_rejects_unsafe_suffix(tmp_path):
    config = write_config(tmp_path, '\ndevice_suffix = "../Mac"\n')
    with pytest.raises(ConfigError, match="letters, digits"):
        load_settings(config)


def test_note_name_only_suffixes_secondary_devices(tmp_path):
    main = Settings(tmp_path, "sonnet", "23:50", (), device_suffix="")
    mac = Settings(tmp_path, "sonnet", "23:50", (), device_suffix="Mac")
    assert main.note_name("2026-09-29") == "2026-09-29"
    assert mac.note_name("2026-09-29") == "2026-09-29-Mac"
