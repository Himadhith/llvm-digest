"""Shared test helpers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

import httpx
import pytest

from llvm_digest.config import Config
from llvm_digest.http import RetryingClient
from llvm_digest.models import Item

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


def make_http(handler: Callable[[httpx.Request], httpx.Response], *, retries: int = 3) -> tuple[RetryingClient, list[float]]:
    """A RetryingClient on a mock transport that records sleeps instead of sleeping."""
    sleeps: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(handler))
    http = RetryingClient(
        user_agent="test-agent",
        max_retries=retries,
        min_interval_s=0.0,
        client=client,
        sleep=sleeps.append,
        wall_clock=lambda: 1000.0,
    )
    return http, sleeps


def make_item(
    *,
    source: str = "github",
    source_id: str = "1",
    title: str = "title",
    labels: tuple[str, ...] = (),
    body: str = "",
    category_id: int | None = None,
    author: str = "@someone",
) -> Item:
    """Build an Item with sensible defaults."""
    url = f"https://github.com/llvm/llvm-project/issues/{source_id}" if source == "github" else f"https://discourse.llvm.org/t/x/{source_id}"
    return Item(
        source=source,
        source_id=source_id,
        title=title,
        url=url,
        author=author,
        created_at=NOW,
        body=body,
        labels=labels,
        category_id=category_id,
    )


@pytest.fixture
def cfg() -> Config:
    """Default configuration."""
    return Config()
