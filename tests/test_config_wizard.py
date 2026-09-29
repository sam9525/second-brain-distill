import io
import sys

import pytest

from distiller import config_wizard
from distiller.settings import load_settings


class FakeTty(io.StringIO):
    def isatty(self) -> bool:
        return True


def scripted(*answers: str):
    replies = iter(answers)
    return lambda prompt: next(replies)


def test_wizard_writes_config_that_load_settings_accepts(tmp_path, monkeypatch):
    vault = tmp_path / "My Vault"
    vault.mkdir()
    config = tmp_path / "config.toml"
    monkeypatch.setattr(sys, "stdin", FakeTty())

    # vault is typed as a dragged-in quoted path; defaults accepted for the rest
    config_wizard.ensure_config(config, scripted(f'"{vault}"', "", ""))
    config.write_text(
        config.read_text(encoding="utf-8") + 'device_suffix = ""\n', encoding="utf-8"
    )

    settings = load_settings(config)
    assert settings.vault == vault
    assert settings.project_dirs == ()
    assert settings.model == "sonnet"
    assert settings.schedule_time == "23:50"


def test_wizard_reasks_until_answers_are_valid(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    monkeypatch.setattr(sys, "stdin", FakeTty())
    answers = scripted(
        "",  # empty vault
        str(tmp_path / "missing"),  # vault that doesn't exist
        str(tmp_path),
        "opus",
        "7pm",  # bad time
        "19:00",
    )

    config_wizard.ensure_config(config, answers)

    text = config.read_text(encoding="utf-8")
    assert "project_dirs" not in text
    assert 'model = "opus"' in text
    assert 'schedule_time = "19:00"' in text


def test_existing_config_is_left_alone(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text("vault = 'x'\n")
    monkeypatch.setattr(sys, "stdin", FakeTty())

    config_wizard.ensure_config(config, lambda prompt: pytest.fail("asked"))

    assert config.read_text() == "vault = 'x'\n"


def test_scheduled_run_never_prompts(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    monkeypatch.setattr(sys, "stdin", io.StringIO())  # not a terminal

    config_wizard.ensure_config(config, lambda prompt: pytest.fail("asked"))

    assert not config.exists()
