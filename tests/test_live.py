"""Live smoke tests against the real services. Skipped unless RUN_LIVE=1."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from llvm_digest.config import Config
from llvm_digest.discourse import DiscourseClient
from llvm_digest.github import GitHubClient
from llvm_digest.http import RetryingClient

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to hit live services"),
]


@pytest.fixture
def http() -> RetryingClient:
    cfg = Config()
    return RetryingClient(user_agent=cfg.user_agent, timeout_s=20, max_retries=2, min_interval_s=0.5)


def test_live_discourse_latest_and_hydrate(http: RetryingClient) -> None:
    client = DiscourseClient(http, "https://discourse.llvm.org")
    items = client.recent_items(datetime.now(timezone.utc) - timedelta(days=7), max_pages=1)
    assert items, "expected recent topics"
    assert all(i.url.startswith("https://discourse.llvm.org/t/") for i in items)
    hydrated = client.hydrate(items[0])
    assert hydrated.body and hydrated.author != "unknown"


def test_live_github_issues_exclude_prs(http: RetryingClient) -> None:
    client = GitHubClient(http, "https://api.github.com", "llvm/llvm-project", token=os.environ.get("GITHUB_TOKEN", ""))
    items = client.recent_issues(datetime.now(timezone.utc) - timedelta(days=2), max_pages=1)
    assert items
    assert all("/issues/" in i.url for i in items)
