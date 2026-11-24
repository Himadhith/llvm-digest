"""Environment-driven configuration. No secrets are hardcoded; all come from the environment."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

#: Defaults taken from the live discourse.llvm.org category tree (site.json).
DEFAULT_SANITIZER_CATEGORIES = frozenset({12, 73})  # Sanitizers, CompilerRT
DEFAULT_LLC_CATEGORIES = frozenset({51, 52, 53, 54, 55, 56, 57, 58, 59, 38})  # CodeGen tree + AMDGPU
DEFAULT_CLANG_CATEGORIES = frozenset({6, 47, 48, 49, 113})  # Clang, Using, Building, Static Analyzer, ISO
#: Announcements, newsletters, automated reports, job postings, ...
DEFAULT_EXCLUDED_CATEGORIES = frozenset({75, 76, 16, 69, 37, 44, 46})
#: Generic categories whose topics are only relevant if the body talks about our domains.
DEFAULT_BODY_SCAN_CATEGORIES = frozenset({5, 1, 61, 17, 64})
DEFAULT_EXCLUDED_TAGS = frozenset({"llvm-weekly"})

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


class ConfigError(ValueError):
    """Raised for invalid configuration. Messages never include secret values."""


@dataclass(frozen=True)
class Config:
    """Validated runtime configuration."""

    enable_discourse: bool = True
    discourse_base_url: str = "https://discourse.llvm.org"
    enable_github: bool = True
    github_repo: str = "llvm/llvm-project"
    github_api_url: str = "https://api.github.com"
    github_token: str = field(default="", repr=False)

    webhook_url: str = field(default="", repr=False)
    webhook_format: str = "slack"

    state_db_path: str = "state/llvm_digest.db"
    poll_interval_minutes: int = 60
    log_level: str = "INFO"
    lookback_hours: int = 48
    max_pages: int = 3
    max_items_per_run: int = 10

    request_timeout_s: float = 15.0
    max_retries: int = 4
    min_request_interval_s: float = 0.5
    webhook_min_interval_s: float = 1.2

    summarizer: str = "extractive"
    llm_base_url: str = ""
    llm_api_key: str = field(default="", repr=False)
    llm_model: str = ""

    sanitizer_categories: frozenset[int] = DEFAULT_SANITIZER_CATEGORIES
    llc_categories: frozenset[int] = DEFAULT_LLC_CATEGORIES
    clang_categories: frozenset[int] = DEFAULT_CLANG_CATEGORIES
    excluded_categories: frozenset[int] = DEFAULT_EXCLUDED_CATEGORIES
    body_scan_categories: frozenset[int] = DEFAULT_BODY_SCAN_CATEGORIES
    excluded_tags: frozenset[str] = DEFAULT_EXCLUDED_TAGS

    @property
    def user_agent(self) -> str:
        """Discourse rejects requests without a User-Agent, so always send one."""
        return "llvm-digest-dam-agent/0.1 (+https://github.com/llvm/llvm-project)"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Config:
        """Build a validated :class:`Config` from an environment mapping."""
        d = cls()
        cfg = cls(
            enable_discourse=_bool(env, "ENABLE_DISCOURSE", d.enable_discourse),
            discourse_base_url=_https_url(env, "DISCOURSE_BASE_URL", d.discourse_base_url),
            enable_github=_bool(env, "ENABLE_GITHUB", d.enable_github),
            github_repo=_repo(env, "GITHUB_REPO", d.github_repo),
            github_api_url=_https_url(env, "GITHUB_API_URL", d.github_api_url),
            github_token=env.get("GITHUB_TOKEN", "").strip(),
            webhook_url=_optional_https_url(env, "WEBHOOK_URL"),
            webhook_format=_choice(env, "WEBHOOK_FORMAT", d.webhook_format, {"slack", "text"}),
            state_db_path=env.get("STATE_DB_PATH", d.state_db_path).strip() or d.state_db_path,
            poll_interval_minutes=_int(env, "POLL_INTERVAL_MINUTES", d.poll_interval_minutes, 1, 10080),
            log_level=_choice(env, "LOG_LEVEL", d.log_level, {"DEBUG", "INFO", "WARNING", "ERROR"}, upper=True),
            lookback_hours=_int(env, "LOOKBACK_HOURS", d.lookback_hours, 1, 24 * 30),
            max_pages=_int(env, "MAX_PAGES", d.max_pages, 1, 10),
            max_items_per_run=_int(env, "MAX_ITEMS_PER_RUN", d.max_items_per_run, 1, 100),
            request_timeout_s=_float(env, "REQUEST_TIMEOUT_S", d.request_timeout_s, 1.0, 120.0),
            max_retries=_int(env, "MAX_RETRIES", d.max_retries, 0, 10),
            min_request_interval_s=_float(env, "MIN_REQUEST_INTERVAL_S", d.min_request_interval_s, 0.0, 60.0),
            webhook_min_interval_s=_float(env, "WEBHOOK_MIN_INTERVAL_S", d.webhook_min_interval_s, 0.0, 60.0),
            summarizer=_choice(env, "SUMMARIZER", d.summarizer, {"extractive", "llm"}),
            llm_base_url=_optional_https_url(env, "LLM_BASE_URL"),
            llm_api_key=env.get("LLM_API_KEY", "").strip(),
            llm_model=env.get("LLM_MODEL", "").strip(),
            sanitizer_categories=_ids(env, "SANITIZER_CATEGORY_IDS", d.sanitizer_categories),
            llc_categories=_ids(env, "LLC_CATEGORY_IDS", d.llc_categories),
            clang_categories=_ids(env, "CLANG_CATEGORY_IDS", d.clang_categories),
            excluded_categories=_ids(env, "EXCLUDED_CATEGORY_IDS", d.excluded_categories),
            body_scan_categories=_ids(env, "BODY_SCAN_CATEGORY_IDS", d.body_scan_categories),
            excluded_tags=_strs(env, "EXCLUDED_TAGS", d.excluded_tags),
        )
        if not (cfg.enable_discourse or cfg.enable_github):
            raise ConfigError("at least one of ENABLE_DISCOURSE / ENABLE_GITHUB must be true")
        if cfg.summarizer == "llm" and not (cfg.llm_base_url and cfg.llm_model):
            raise ConfigError("SUMMARIZER=llm requires LLM_BASE_URL and LLM_MODEL")
        return cfg


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{name} must be a boolean (true/false)")


def _int(env: Mapping[str, str], name: str, default: int, lo: int, hi: int) -> int:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        raise ConfigError(f"{name} must be an integer") from None
    if not lo <= value <= hi:
        raise ConfigError(f"{name} must be between {lo} and {hi}")
    return value


def _float(env: Mapping[str, str], name: str, default: float, lo: float, hi: float) -> float:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        raise ConfigError(f"{name} must be a number") from None
    if not lo <= value <= hi:
        raise ConfigError(f"{name} must be between {lo} and {hi}")
    return value


def _choice(env: Mapping[str, str], name: str, default: str, allowed: set[str], *, upper: bool = False) -> str:
    raw = env.get(name)
    value = default if raw is None or not raw.strip() else raw.strip()
    value = value.upper() if upper else value.lower()
    if value not in allowed:
        raise ConfigError(f"{name} must be one of: {', '.join(sorted(allowed))}")
    return value


def _check_https(name: str, url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ConfigError(f"{name} must be an https:// URL")
    return url.rstrip("/")


def _https_url(env: Mapping[str, str], name: str, default: str) -> str:
    raw = env.get(name)
    return _check_https(name, default if raw is None or not raw.strip() else raw.strip())


def _optional_https_url(env: Mapping[str, str], name: str) -> str:
    raw = env.get(name, "").strip()
    return _check_https(name, raw) if raw else ""


def _repo(env: Mapping[str, str], name: str, default: str) -> str:
    raw = env.get(name)
    value = default if raw is None or not raw.strip() else raw.strip()
    if not _REPO_RE.match(value):
        raise ConfigError(f"{name} must look like owner/repo")
    return value


def _ids(env: Mapping[str, str], name: str, default: frozenset[int]) -> frozenset[int]:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return frozenset(int(p) for p in raw.split(",") if p.strip())
    except ValueError:
        raise ConfigError(f"{name} must be comma-separated integers") from None


def _strs(env: Mapping[str, str], name: str, default: frozenset[str]) -> frozenset[str]:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())
