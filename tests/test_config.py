from __future__ import annotations

import pytest

from llvm_digest.config import Config, ConfigError


def test_defaults_are_valid() -> None:
    cfg = Config.from_env({})
    assert cfg.lookback_hours == 48
    assert cfg.github_repo == "llvm/llvm-project"
    assert cfg.enable_discourse and cfg.enable_github


def test_overrides_parse() -> None:
    cfg = Config.from_env({"LOOKBACK_HOURS": "6", "SANITIZER_CATEGORY_IDS": "12, 99", "EXCLUDED_TAGS": "Foo,bar", "ENABLE_GITHUB": "no"})
    assert cfg.lookback_hours == 6
    assert cfg.sanitizer_categories == {12, 99}
    assert cfg.excluded_tags == {"foo", "bar"}
    assert not cfg.enable_github


@pytest.mark.parametrize(
    "env",
    [
        {"LOOKBACK_HOURS": "abc"},
        {"LOOKBACK_HOURS": "0"},
        {"MAX_RETRIES": "99"},
        {"DISCOURSE_BASE_URL": "http://discourse.llvm.org"},
        {"WEBHOOK_URL": "http://hooks.example.org/x"},
        {"GITHUB_REPO": "not-a-repo"},
        {"WEBHOOK_FORMAT": "xml"},
        {"ENABLE_DISCOURSE": "false", "ENABLE_GITHUB": "false"},
        {"SUMMARIZER": "llm"},
        {"SANITIZER_CATEGORY_IDS": "1,x"},
    ],
)
def test_invalid_values_rejected(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        Config.from_env(env)


def test_secrets_are_not_in_repr() -> None:
    cfg = Config.from_env({"WEBHOOK_URL": "https://hooks.slack.com/services/SECRET", "GITHUB_TOKEN": "ghp_SECRET", "LLM_API_KEY": "sk-SECRET"})
    assert "SECRET" not in repr(cfg)
