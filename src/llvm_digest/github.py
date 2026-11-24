"""GitHub client: recent issues (never PRs) from the search API, classified locally by label."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .http import HttpError, RetryingClient
from .models import Item, parse_dt
from .text import markdown_to_text

log = logging.getLogger(__name__)

_PER_PAGE = 100  # search API maximum


class GitHubClient:
    """Uses ``GET /search/issues?q=repo:... is:issue created:>=...``.

    We fetch *all* recent issues and classify by label locally: the search API limits query
    length, and ``llvm-project`` has dozens of ``backend:*`` labels.
    """

    def __init__(self, http: RetryingClient, api_url: str, repo: str, token: str = "") -> None:
        self._http = http
        self._api = api_url.rstrip("/")
        self._repo = repo
        self._headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    def recent_issues(self, since: datetime, max_pages: int) -> list[Item]:
        """Issues created at or after ``since``, newest first."""
        since_utc = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        query = f"repo:{self._repo} is:issue created:>={since_utc}"
        items: list[Item] = []
        for page in range(1, max_pages + 1):
            try:
                data = self._http.get_json(
                    f"{self._api}/search/issues",
                    params={"q": query, "sort": "created", "order": "desc", "per_page": _PER_PAGE, "page": page},
                    headers=self._headers,
                )
            except HttpError:
                if page == 1:
                    raise
                log.warning("github page failed; returning partial results", extra={"page": page})
                break
            raw_items = data.get("items") if isinstance(data, dict) else None
            if not isinstance(raw_items, list) or not raw_items:
                break
            for raw in raw_items:
                item = self._item(raw)
                if item is not None:
                    items.append(item)
            if len(raw_items) < _PER_PAGE:
                break
        return items

    @staticmethod
    def _item(raw: Any) -> Item | None:
        if not isinstance(raw, dict) or raw.get("pull_request") is not None:
            return None  # issues only
        number = raw.get("number")
        title = raw.get("title")
        url = raw.get("html_url")
        created = raw.get("created_at")
        if not (isinstance(number, int) and isinstance(title, str) and isinstance(url, str) and isinstance(created, str)):
            log.warning("skipping malformed issue", extra={"keys": sorted(raw)[:8]})
            return None
        try:
            created_at = parse_dt(created)
        except ValueError:
            return None
        user = raw.get("user")
        login = user.get("login") if isinstance(user, dict) else None
        labels_raw = raw.get("labels")
        labels = tuple(
            lab["name"] for lab in labels_raw if isinstance(lab, dict) and isinstance(lab.get("name"), str)
        ) if isinstance(labels_raw, list) else ()
        body = raw.get("body")
        return Item(
            source="github",
            source_id=str(number),
            title=title.strip(),
            url=url,
            author=f"@{login}" if isinstance(login, str) and login else "unknown",
            created_at=created_at,
            body=markdown_to_text(body) if isinstance(body, str) else "",
            labels=labels,
        )
