"""Unit tests for the ORQEN configuration system."""

from pathlib import Path

import pytest

from orqen.config import OrqenConfig, load_config
from orqen.exceptions import ConfigurationError


def test_load_config_defaults() -> None:
    cfg = load_config(env={})
    assert isinstance(cfg, OrqenConfig)
    assert cfg.runtime.max_concurrent_tasks == 64
    assert cfg.runtime.default_timeout_seconds == 30.0
    assert cfg.runtime.environment == "development"
    assert cfg.logging.level == "INFO"
    assert cfg.logging.format == "text"
    assert cfg.logging.redact_secrets is True


def test_load_config_precedence(tmp_path: Path) -> None:
    toml_file = tmp_path / "orqen.toml"
    toml_file.write_text(
        """
data_dir = "/tmp/orqen-toml"

[runtime]
max_concurrent_tasks = 16
environment = "staging"

[logging]
level = "WARNING"
format = "json"
""".strip(),
        encoding="utf-8",
    )

    env = {
        "ORQEN_RUNTIME__MAX_CONCURRENT_TASKS": "32",
        "ORQEN_LOGGING__EXTRA_REDACTED_KEYS": "custom_token, partner_key",
    }
    overrides = {"runtime": {"environment": "production"}}

    cfg = load_config(toml_file, env=env, overrides=overrides)
    assert cfg.data_dir == Path("/tmp/orqen-toml")
    assert cfg.runtime.max_concurrent_tasks == 32
    assert cfg.runtime.environment == "production"
    assert cfg.logging.level == "WARNING"
    assert cfg.logging.format == "json"
    assert cfg.logging.extra_redacted_keys == ("custom_token", "partner_key")


def test_load_config_missing_file_raises(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.toml"
    with pytest.raises(ConfigurationError, match="Configuration file not found"):
        load_config(missing, env={})


def test_load_config_invalid_toml_raises(tmp_path: Path) -> None:
    bad_toml = tmp_path / "bad.toml"
    bad_toml.write_text("runtime = [unclosed", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Failed to parse configuration file"):
        load_config(bad_toml, env={})


def test_load_config_invalid_values_raise() -> None:
    with pytest.raises(ConfigurationError, match="Invalid ORQEN configuration"):
        load_config(env={"ORQEN_RUNTIME__MAX_CONCURRENT_TASKS": "0"})


def test_load_config_rejects_unknown_fields() -> None:
    with pytest.raises(ConfigurationError, match="Invalid ORQEN configuration"):
        load_config(env={}, overrides={"unknown_section": {"foo": "bar"}})
