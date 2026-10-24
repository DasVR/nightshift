from pathlib import Path

from pytest import MonkeyPatch

from server.config import Settings, get_settings, reset_settings_cache


def _clear_nightshift_env(monkeypatch: MonkeyPatch) -> None:
    for name in (
        "NIGHTSHIFT_DATA_DIR",
        "NIGHTSHIFT_HOST",
        "NIGHTSHIFT_PORT",
        "NIGHTSHIFT_LOG_LEVEL",
        "NIGHTSHIFT_NOTIFY_TARGETS",
        "NIGHTSHIFT_PHOTON_TARGET",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """Default settings are sane and ensure_dirs creates directories."""
    _clear_nightshift_env(monkeypatch)
    reset_settings_cache()

    settings = Settings(_env_file=None)
    assert settings.data_dir == Path("data")
    assert settings.host == "127.0.0.1"
    assert settings.port == 8788
    assert settings.log_level == "info"
    assert settings.notify_targets == ["discord:home"]
    assert settings.photon_target is None

    dir_settings = Settings(data_dir=tmp_path / "d", _env_file=None)
    dir_settings.ensure_dirs()
    assert dir_settings.data_dir.is_dir()
    assert dir_settings.runs_dir.is_dir()


def test_env_override(monkeypatch: MonkeyPatch) -> None:
    """Environment variables override defaults and comma lists are parsed."""
    _clear_nightshift_env(monkeypatch)
    monkeypatch.setenv("NIGHTSHIFT_PORT", "9999")
    monkeypatch.setenv("NIGHTSHIFT_NOTIFY_TARGETS", "discord:home,photon:nightshift-dev")
    reset_settings_cache()

    settings = get_settings()
    assert settings.port == 9999
    assert settings.notify_targets == ["discord:home", "photon:nightshift-dev"]
