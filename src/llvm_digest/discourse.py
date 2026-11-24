"""Discourse client: list recent topics, then hydrate matching ones with their first post."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .http import HttpError, RetryingClient
from .models import Item, parse_dt
from .text import html_to_text

log = logging.getLogger(__name__)


class DiscourseClient:
    """Reads ``/latest.json?order=created`` and ``/t/{id}.json``; tolerant of schema drift."""

    def __init__(self, http: RetryingClient, base_url: str) -> None:
        self._http = http
        self._base = base_url.rstrip("/")

    def recent_items(self, since: datetime, max_pages: int) -> list[Item]:
        """Topics created at or after ``since`` (newest first), without bodies.

        Raises :class:`HttpError` if the very first page fails; later-page failures return what we have.
        """
        items: list[Item] = []
        seen: set[str] = set()
        for page in range(max_pages):
            try:
                data = self._http.get_json(
                    f"{self._base}/latest.json", params={"order": "created", "page": page}, headers={"Accept": "application/json"}
                )
            except HttpError:
                if page == 0:
                    raise
                log.warning("discourse page failed; returning partial results", extra={"page": page})
                break
            topics = _topics(data)
            if not topics:
                break
            users = _users(data)
            page_has_recent = False
            for raw in topics:
                item = self._stub(raw, users)
                if item is None:
                    continue
                if item.created_at >= since:
                    page_has_recent = True
                    if item.source_id not in seen:
                        seen.add(item.source_id)
                        items.append(item)
            # a page with no recent topics means we have gone far enough
            if not page_has_recent:
                break
        return items

    def hydrate(self, item: Item) -> Item:
        """Return ``item`` with its first-post text and author filled in."""
        data = self._http.get_json(f"{self._base}/t/{item.source_id}.json", headers={"Accept": "application/json"})
        posts = _posts(data)
        if not posts:
            return item
        first = posts[0]
        cooked = first.get("cooked")
        body = html_to_text(cooked) if isinstance(cooked, str) else ""
        author = _author(first) or item.author
        return Item(
            source=item.source,
            source_id=item.source_id,
            title=item.title,
            url=item.url,
            author=author,
            created_at=item.created_at,
            body=body,
            labels=item.labels,
            category_id=item.category_id,
        )

    def _stub(self, raw: Any, users: dict[int, str]) -> Item | None:
        if not isinstance(raw, dict):
            return None
        topic_id = raw.get("id")
        title = raw.get("title") or raw.get("fancy_title")
        created = raw.get("created_at")
        if not isinstance(topic_id, int) or not isinstance(title, str) or not isinstance(created, str):
            log.warning("skipping malformed topic", extra={"keys": sorted(raw)[:8]})
            return None
        if raw.get("archetype", "regular") != "regular" or raw.get("visible") is False:
            return None
        try:
            created_at = parse_dt(created)
        except ValueError:
            log.warning("skipping topic with bad timestamp", extra={"topic_id": topic_id})
            return None
        slug = raw.get("slug") if isinstance(raw.get("slug"), str) and raw.get("slug") else "topic"
        category = raw.get("category_id")
        return Item(
            source="discourse",
            source_id=str(topic_id),
            title=title.strip(),
            url=f"{self._base}/t/{slug}/{topic_id}",
            author=_original_poster(raw, users),
            created_at=created_at,
            labels=_tags(raw.get("tags")),
            category_id=category if isinstance(category, int) else None,
        )


def _topics(data: Any) -> list[Any]:
    topic_list = data.get("topic_list") if isinstance(data, dict) else None
    topics = topic_list.get("topics") if isinstance(topic_list, dict) else None
    return topics if isinstance(topics, list) else []


def _posts(data: Any) -> list[dict[str, Any]]:
    stream = data.get("post_stream") if isinstance(data, dict) else None
    posts = stream.get("posts") if isinstance(stream, dict) else None
    return [p for p in posts if isinstance(p, dict)] if isinstance(posts, list) else []


def _users(data: Any) -> dict[int, str]:
    users = data.get("users") if isinstance(data, dict) else None
    result: dict[int, str] = {}
    if isinstance(users, list):
        for u in users:
            if isinstance(u, dict) and isinstance(u.get("id"), int) and isinstance(u.get("username"), str):
                result[u["id"]] = u["username"]
    return result


def _original_poster(raw: dict[str, Any], users: dict[int, str]) -> str:
    posters = raw.get("posters")
    if isinstance(posters, list):
        for p in posters:
            if isinstance(p, dict) and "Original Poster" in str(p.get("description", "")):
                uid = p.get("user_id")
                if isinstance(uid, int) and uid in users:
                    return users[uid]
    return "unknown"


def _author(post: dict[str, Any]) -> str:
    username = post.get("username")
    name = post.get("name")
    if isinstance(username, str) and username:
        if isinstance(name, str) and name and name.lower() != username.lower():
            return f"{name} (@{username})"
        return f"@{username}"
    return ""


def _tags(raw: Any) -> tuple[str, ...]:
    """Tags are dicts on current Discourse, plain strings on older versions."""
    if not isinstance(raw, list):
        return ()
    names: list[str] = []
    for tag in raw:
        if isinstance(tag, str):
            names.append(tag)
        elif isinstance(tag, dict) and isinstance(tag.get("name"), str):
            names.append(tag["name"])
    return tuple(names)
