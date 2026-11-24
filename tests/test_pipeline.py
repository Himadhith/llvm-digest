from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from llvm_digest import cli
from llvm_digest.classify import Classifier
from llvm_digest.config import Config
from llvm_digest.dispatch import WebhookDispatcher
from llvm_digest.discourse import DiscourseClient
from llvm_digest.github import GitHubClient
from llvm_digest.models import Candidate, Classification, Domain, Summary
from llvm_digest.pipeline import Pipeline
from llvm_digest.state import StateStore
from llvm_digest.summarize import ExtractiveSummarizer, LlmSummarizer

from .conftest import NOW, make_http, make_item


def route(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    if host == "discourse.llvm.org" and path == "/latest.json":
        return httpx.Response(
            200,
            json={
                "users": [{"id": 1, "username": "alice"}],
                "topic_list": {
                    "topics": [
                        {"id": 1, "title": "ASan question", "slug": "a", "created_at": "2026-11-24T10:00:00Z", "category_id": 12, "posters": [{"description": "Original Poster", "user_id": 1}]},
                        {"id": 2, "title": "Values of LLVM", "slug": "v", "created_at": "2026-11-24T09:00:00Z", "category_id": 5},
                        {"id": 3, "title": "Lowering question", "slug": "l", "created_at": "2026-11-24T08:00:00Z", "category_id": 5},
                        {"id": 4, "title": "Weekly", "slug": "w", "created_at": "2026-11-24T07:00:00Z", "category_id": 37},
                    ]
                },
            },
        )
    if host == "discourse.llvm.org" and path == "/t/1.json":
        return httpx.Response(200, json={"post_stream": {"posts": [{"username": "alice", "cooked": "<p>ASan reports a leak that is not real.</p>"}]}})
    if host == "discourse.llvm.org" and path == "/t/2.json":
        return httpx.Response(200, json={"post_stream": {"posts": [{"username": "x", "cooked": "<p>Community values.</p>"}]}})
    if host == "discourse.llvm.org" and path == "/t/3.json":
        return httpx.Response(200, json={"post_stream": {"posts": [{"username": "x", "cooked": "<p>ISel fails in SelectionDAG for vectors.</p>"}]}})
    if host == "api.github.com":
        return httpx.Response(
            200,
            json={
                "items": [
                    {"number": 50, "title": "x86 crash", "html_url": "https://github.com/llvm/llvm-project/issues/50", "created_at": "2026-11-24T11:00:00Z", "user": {"login": "bob"}, "labels": [{"name": "backend:X86"}], "body": "Crash in isel."},
                    {"number": 51, "title": "InstCombine bug", "html_url": "https://github.com/llvm/llvm-project/issues/51", "created_at": "2026-11-24T11:00:00Z", "user": {"login": "bob"}, "labels": [{"name": "llvm:instcombine"}], "body": ""},
                ]
            },
        )
    return httpx.Response(404)


def pipeline(state: StateStore, cfg: Config | None = None) -> Pipeline:
    cfg = cfg or Config()
    http, _ = make_http(route)
    return Pipeline(cfg, state, Classifier(cfg), DiscourseClient(http, cfg.discourse_base_url), GitHubClient(http, cfg.github_api_url, cfg.github_repo))


def test_end_to_end_scan_filters_and_dedupes() -> None:
    with StateStore(":memory:") as state:
        result = pipeline(state).scan(NOW)
        keys = {c.item.key for c in result.candidates}
        assert keys == {"discourse:1", "discourse:3", "github:50"}
        assert result.irrelevant == 3  # topic 2, topic 4 (excluded category), issue 51
        by_key = {c.item.key: c for c in result.candidates}
        assert by_key["discourse:1"].item.body.startswith("ASan reports")  # hydrated
        assert by_key["discourse:3"].classification.primary is Domain.LLC  # found via body scan
        assert [c.classification.primary for c in result.candidates] == [Domain.SANITIZERS, Domain.LLC, Domain.LLC]

        state.mark_posted(keys)
        again = pipeline(state).scan(NOW)
        assert again.candidates == [] and again.already_known >= 3


def test_irrelevant_discourse_topics_are_remembered_but_github_ones_are_not() -> None:
    with StateStore(":memory:") as state:
        pipeline(state).scan(NOW)
        assert state.status("discourse:2") == "skipped"
        assert state.status("github:51") is None  # labels may still be added


def test_limit_is_balanced_across_domains_and_reports_deferred() -> None:
    with StateStore(":memory:") as state:
        cfg = Config(max_items_per_run=2)
        result = pipeline(state, cfg).scan(NOW)
        assert len(result.candidates) == 2
        assert result.candidates[0].classification.primary is Domain.SANITIZERS
        assert result.deferred == 1


def test_one_source_failing_does_not_block_the_other() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500) if request.url.host == "discourse.llvm.org" else route(request)

    cfg = Config(max_retries=0)
    http, _ = make_http(handler, retries=0)
    with StateStore(":memory:") as state:
        p = Pipeline(cfg, state, Classifier(cfg), DiscourseClient(http, cfg.discourse_base_url), GitHubClient(http, cfg.github_api_url, cfg.github_repo))
        result = p.scan(NOW)
        assert [c.item.key for c in result.candidates] == ["github:50"]
        assert result.errors and result.errors[0].startswith("discourse:")


def test_hydrate_failure_keeps_candidate_with_title_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500) if request.url.path.startswith("/t/") else route(request)

    cfg = Config(enable_github=False)
    http, _ = make_http(handler, retries=0)
    with StateStore(":memory:") as state:
        result = Pipeline(cfg, state, Classifier(cfg), DiscourseClient(http, cfg.discourse_base_url), None).scan(NOW)
        assert [c.item.key for c in result.candidates] == ["discourse:1"]
        assert any(e.startswith("hydrate") for e in result.errors)


def _summary() -> Summary:
    cand = Candidate(make_item(title="Crash", body="It crashes."), Classification(Domain.CLANG, (Domain.CLANG,), ()))
    return ExtractiveSummarizer().summarize(cand)


def test_webhook_sends_payload_and_never_logs_url(caplog: pytest.LogCaptureFixture) -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, text="ok")

    http, _ = make_http(handler)
    dispatcher = WebhookDispatcher(http, "https://hooks.slack.com/services/T/B/SECRET")
    assert dispatcher.send(_summary())
    body = json.loads(sent[0].content)
    assert "blocks" in body and body["text"].startswith("[Clang Frontend]")
    assert "SECRET" not in caplog.text


def test_webhook_retries_429_then_succeeds() -> None:
    codes = iter([429, 200])
    http, sleeps = make_http(lambda r: httpx.Response(next(codes), headers={"Retry-After": "2"}))
    assert WebhookDispatcher(http, "https://hooks.example.org/x", "text").send(_summary())
    assert sleeps == [2.0]


def test_webhook_failure_returns_false_without_leaking(caplog: pytest.LogCaptureFixture) -> None:
    http, _ = make_http(lambda r: httpx.Response(500), retries=1)
    assert not WebhookDispatcher(http, "https://hooks.example.org/SECRET").send(_summary())
    assert "SECRET" not in caplog.text


def test_webhook_requires_url() -> None:
    http, _ = make_http(lambda r: httpx.Response(200))
    with pytest.raises(ValueError):
        WebhookDispatcher(http, "")


def test_llm_summarizer_parses_json_and_falls_back() -> None:
    cfg = Config(summarizer="llm", llm_base_url="https://llm.example.org/v1", llm_model="m")
    cand = Candidate(make_item(title="Crash", body="It crashes on x86."), Classification(Domain.CLANG, (Domain.CLANG,), ()))
    good = {"choices": [{"message": {"content": 'Sure: {"problem": "P.", "bullets": ["a", "b"], "impact": "x86"}'}}]}
    http, _ = make_http(lambda r: httpx.Response(200, json=good))
    s = LlmSummarizer(http, cfg).summarize(cand)
    assert (s.problem, s.bullets, s.impact) == ("P.", ("a", "b"), "x86")

    bodies: list[tuple[int, Any]] = [(500, {}), (200, {"choices": []}), (200, {"choices": [{"message": {"content": "no json"}}]})]
    for status, payload in bodies:

        def handler(request: httpx.Request, st: int = status, pl: Any = payload) -> httpx.Response:
            return httpx.Response(st, json=pl)

        http, _ = make_http(handler, retries=0)
        s = LlmSummarizer(http, cfg).summarize(cand)
        assert s.problem == "It crashes on x86."  # extractive fallback


def test_cli_scan_json_and_mark_posted(tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "s.db"))
    monkeypatch.setattr(cli, "_make_http", lambda cfg, min_interval_s=None: make_http(route)[0])
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)

    assert cli.main(["scan"]) == cli.EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert {i["key"] for i in data["new_items"]} == {"discourse:1", "discourse:3", "github:50"}
    assert data["new_items"][0]["category"] == "Sanitizers"

    assert cli.main(["mark-posted", "discourse:1", "discourse:3", "github:50"]) == cli.EXIT_OK
    capsys.readouterr()
    assert cli.main(["precheck"]) == cli.EXIT_NOTHING_NEW  # nothing new -> DAM skips the run
    assert cli.main(["stats"]) == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["posted"] == 3


def test_cli_run_dry_run_posts_nothing(tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "s.db"))
    monkeypatch.setattr(cli, "_make_http", lambda cfg, min_interval_s=None: make_http(route)[0])
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    assert cli.main(["run", "--dry-run"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "[Sanitizers]" in out and "Key problem" in out
    assert cli.main(["stats"]) == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out).get("posted", 0) == 0


def test_cli_run_without_webhook_fails_cleanly(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "s.db"))
    monkeypatch.delenv("WEBHOOK_URL", raising=False)
    monkeypatch.setattr(cli, "_make_http", lambda cfg, min_interval_s=None: make_http(route)[0])
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    assert cli.main(["run"]) == cli.EXIT_ERROR


def test_cli_run_posts_and_marks(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "s.db"))
    monkeypatch.setenv("WEBHOOK_URL", "https://hooks.example.org/x")
    monkeypatch.setattr(cli, "_make_http", lambda cfg, min_interval_s=None: make_http(lambda r: httpx.Response(200) if r.url.host == "hooks.example.org" else route(r))[0])
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    assert cli.main(["run"]) == cli.EXIT_OK
    with StateStore(str(tmp_path / "s.db")) as state:
        assert state.counts()["posted"] == 3


def test_cli_bad_config_exits_2(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("LOOKBACK_HOURS", "nope")
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    assert cli.main(["stats"]) == cli.EXIT_ERROR
    assert "LOOKBACK_HOURS" in capsys.readouterr().err
