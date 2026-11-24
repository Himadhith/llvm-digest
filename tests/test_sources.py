from __future__ import annotations

from datetime import timedelta

import httpx

from llvm_digest.discourse import DiscourseClient
from llvm_digest.github import GitHubClient

from .conftest import NOW, make_http

LATEST = {
    "users": [{"id": 1, "username": "alice"}],
    "topic_list": {
        "topics": [
            {
                "id": 100,
                "title": "[RFC] Better ASan",
                "slug": "rfc-better-asan",
                "created_at": "2026-11-24T10:00:00.000Z",
                "category_id": 12,
                "tags": [{"id": 1, "name": "clang", "slug": "clang"}],
                "archetype": "regular",
                "posters": [{"description": "Original Poster, Most Recent Poster", "user_id": 1}],
            },
            {"id": 101, "title": "old", "slug": "old", "created_at": "2026-01-01T00:00:00Z", "category_id": 5, "tags": []},
            {"id": "bad", "title": 3},  # schema drift: must be skipped, not crash
            {"id": 102, "title": "PM", "slug": "pm", "created_at": "2026-11-24T09:00:00Z", "archetype": "private_message"},
            "not-a-dict",
        ]
    },
}


def test_discourse_recent_items_parses_and_tolerates_bad_rows() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=LATEST)

    http, _ = make_http(handler)
    items = DiscourseClient(http, "https://discourse.llvm.org").recent_items(NOW - timedelta(hours=48), max_pages=3)
    assert [i.source_id for i in items] == ["100"]
    item = items[0]
    assert item.author == "alice"
    assert item.labels == ("clang",)
    assert item.category_id == 12
    assert item.url == "https://discourse.llvm.org/t/rfc-better-asan/100"
    assert len(seen) == 3  # every page contained a recent topic, so all max_pages were read (and de-duplicated)
    assert seen[0].headers["accept"] == "application/json"
    assert seen[0].headers["user-agent"] == "test-agent"


def test_discourse_stops_when_page_has_nothing_recent() -> None:
    pages = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        pages["n"] += 1
        return httpx.Response(200, json={"topic_list": {"topics": [{"id": 5, "title": "t", "slug": "t", "created_at": "2020-01-01T00:00:00Z"}]}})

    http, _ = make_http(handler)
    assert DiscourseClient(http, "https://discourse.llvm.org").recent_items(NOW - timedelta(hours=48), max_pages=5) == []
    assert pages["n"] == 1


def test_discourse_hydrate_extracts_first_post() -> None:
    topic = {
        "post_stream": {
            "posts": [
                {"username": "alice", "name": "Alice A", "cooked": "<p>Hello <b>world</b>.</p><aside class='quote'>skip me</aside><pre><code>x=1</code></pre>"}
            ]
        }
    }
    http, _ = make_http(lambda r: httpx.Response(200, json=topic))
    client = DiscourseClient(http, "https://discourse.llvm.org")
    from .conftest import make_item

    hydrated = client.hydrate(make_item(source="discourse", source_id="100"))
    assert "Hello world." in hydrated.body
    assert "skip me" not in hydrated.body
    assert hydrated.author == "Alice A (@alice)"


def test_discourse_hydrate_survives_missing_posts() -> None:
    http, _ = make_http(lambda r: httpx.Response(200, json={"unexpected": True}))
    from .conftest import make_item

    item = make_item(source="discourse", source_id="1")
    assert DiscourseClient(http, "https://discourse.llvm.org").hydrate(item) is item


SEARCH = {
    "items": [
        {
            "number": 10,
            "title": "ASan crash",
            "html_url": "https://github.com/llvm/llvm-project/issues/10",
            "created_at": "2026-11-24T08:00:00Z",
            "user": {"login": "bob"},
            "labels": [{"name": "compiler-rt:asan"}, {"name": "crash"}],
            "body": "Crashes.\n```\ncode\n```\nSee [docs](http://x).",
        },
        {"number": 11, "title": "a PR", "html_url": "u", "created_at": "2026-11-24T08:00:00Z", "pull_request": {"url": "x"}},
        {"number": "bad"},
    ]
}


def test_github_issues_only_and_query_shape() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=SEARCH)

    http, _ = make_http(handler)
    items = GitHubClient(http, "https://api.github.com", "llvm/llvm-project", token="tok").recent_issues(NOW - timedelta(hours=48), 3)
    assert [i.source_id for i in items] == ["10"]
    assert items[0].labels == ("compiler-rt:asan", "crash")
    assert items[0].author == "@bob"
    assert "code" not in items[0].body.replace("Crashes", "")  # fenced code removed
    q = seen[0].url.params["q"]
    assert "repo:llvm/llvm-project" in q and "is:issue" in q and "created:>=2026-11-22T12:00:00Z" in q
    assert seen[0].headers["authorization"] == "Bearer tok"
    assert len(seen) == 1  # fewer than a full page -> no second request


def test_github_no_token_means_no_auth_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"items": []})

    http, _ = make_http(handler)
    GitHubClient(http, "https://api.github.com", "llvm/llvm-project").recent_issues(NOW, 1)
    assert "authorization" not in seen[0].headers
