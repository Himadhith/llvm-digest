from __future__ import annotations

import httpx
import pytest

from llvm_digest.http import HttpError, RateLimitedError

from .conftest import make_http


def test_retries_429_honouring_retry_after() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"ok": True})

    http, sleeps = make_http(handler)
    assert http.get_json("https://example.org/x") == {"ok": True}
    assert sleeps == [7.0]


def test_retries_5xx_with_backoff_then_succeeds() -> None:
    codes = iter([503, 502, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        code = next(codes)
        return httpx.Response(code, json={"ok": True} if code == 200 else {})

    http, sleeps = make_http(handler)
    assert http.get_json("https://example.org/x") == {"ok": True}
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0] - 0.3


def test_gives_up_after_max_retries() -> None:
    http, sleeps = make_http(lambda r: httpx.Response(500), retries=2)
    with pytest.raises(HttpError) as err:
        http.get_json("https://example.org/x")
    assert "after 3 attempts" in str(err.value)
    assert len(sleeps) == 2


def test_does_not_retry_404() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404)

    http, _ = make_http(handler)
    with pytest.raises(HttpError) as err:
        http.get_json("https://example.org/x")
    assert err.value.status == 404 and calls["n"] == 1


def test_timeout_is_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json=[1])

    http, _ = make_http(handler)
    assert http.get_json("https://example.org/x") == [1]


def test_github_primary_rate_limit_waits_until_reset() -> None:
    codes = iter([403, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        if next(codes) == 403:
            return httpx.Response(403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1010"})
        return httpx.Response(200, json={})

    http, sleeps = make_http(handler)
    http.get_json("https://api.github.com/x")
    assert sleeps == [11.0]  # reset (1010) - now (1000) + 1


def test_refuses_to_wait_longer_than_cap() -> None:
    http, _ = make_http(lambda r: httpx.Response(429, headers={"Retry-After": "3600"}))
    with pytest.raises(RateLimitedError):
        http.get_json("https://example.org/x")


def test_non_json_body_raises_http_error() -> None:
    http, _ = make_http(lambda r: httpx.Response(200, text="<html>nope</html>"))
    with pytest.raises(HttpError, match="not valid JSON"):
        http.get_json("https://example.org/x")


def test_error_messages_do_not_leak_url_path_or_query() -> None:
    http, _ = make_http(lambda r: httpx.Response(500), retries=0)
    with pytest.raises(HttpError) as err:
        http.request("POST", "https://hooks.slack.com/services/T000/B000/SECRET")
    assert "SECRET" not in str(err.value)
    assert "hooks.slack.com" in str(err.value)
