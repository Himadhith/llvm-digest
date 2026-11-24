"""Command line interface.

Commands
--------
``scan``         print new relevant items as JSON (DAM agent summarises these)       [no state change]
``precheck``     like ``scan`` but exit 1 when nothing is new (DAM schedule precheck) [no state change]
``mark-posted``  record item keys as posted after the agent has delivered them
``preview``      scan + summarise, print the Slack-style preview to the console        [no state change]
``run``          scan + summarise + post to WEBHOOK_URL + mark posted (``--dry-run`` = preview)
``stats``        row counts from the state database
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv

from .classify import Classifier
from .config import Config, ConfigError
from .discourse import DiscourseClient
from .dispatch import WebhookDispatcher
from .format import render_markdown
from .github import GitHubClient
from .http import RetryingClient
from .logging_setup import configure_logging
from .pipeline import Pipeline, ScanResult
from .state import StateStore
from .summarize import ExtractiveSummarizer, LlmSummarizer, Summarizer, impact_hints
from .models import Candidate, Summary
from .text import truncate

log = logging.getLogger("llvm_digest")

EXIT_OK = 0
EXIT_NOTHING_NEW = 1  # DAM precheck contract: exit 1 skips the run
EXIT_ERROR = 2  # any other code makes DAM run the schedule anyway
_BODY_LIMIT = 4000


def build_pipeline(cfg: Config, state: StateStore, http: RetryingClient) -> Pipeline:
    """Wire up clients according to configuration."""
    discourse = DiscourseClient(http, cfg.discourse_base_url) if cfg.enable_discourse else None
    github = GitHubClient(http, cfg.github_api_url, cfg.github_repo, cfg.github_token) if cfg.enable_github else None
    return Pipeline(cfg, state, Classifier(cfg), discourse, github)


def _make_http(cfg: Config, min_interval_s: float | None = None) -> RetryingClient:
    return RetryingClient(
        user_agent=cfg.user_agent,
        timeout_s=cfg.request_timeout_s,
        max_retries=cfg.max_retries,
        min_interval_s=cfg.min_request_interval_s if min_interval_s is None else min_interval_s,
    )


def candidate_to_json(candidate: Candidate) -> dict[str, Any]:
    """Machine-readable form handed to the DAM agent for summarisation."""
    item = candidate.item
    cls = candidate.classification
    return {
        "key": item.key,
        "source": item.source,
        "title": item.title,
        "url": item.url,
        "author": item.author,
        "created_at": item.created_at.isoformat(),
        "category": cls.primary.value,
        "also_relevant_to": [d.value for d in cls.secondary],
        "why_matched": list(cls.reasons),
        "labels": list(item.labels),
        "impact_hints": impact_hints(item),
        "body_excerpt": truncate(item.body, _BODY_LIMIT) if item.body else "",
    }


def scan_to_json(result: ScanResult) -> str:
    """Serialise a scan for stdout."""
    return json.dumps(
        {
            "new_items": [candidate_to_json(c) for c in result.candidates],
            "stats": {
                "fetched": result.fetched,
                "already_known": result.already_known,
                "irrelevant": result.irrelevant,
                "deferred_to_next_run": result.deferred,
            },
            "errors": result.errors,
        },
        indent=2,
    )


def _summarizer(cfg: Config, http: RetryingClient) -> Summarizer:
    return LlmSummarizer(http, cfg) if cfg.summarizer == "llm" else ExtractiveSummarizer()


def _summaries(cfg: Config, http: RetryingClient, result: ScanResult) -> list[Summary]:
    summarizer = _summarizer(cfg, http)
    return [summarizer.summarize(c) for c in result.candidates]


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="llvm-digest", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env-file", default=None, help="path to a .env file (default: ./.env if present)")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="print new relevant items as JSON")
    sub.add_parser("precheck", help="scan; exit 1 if nothing is new (DAM schedule precheck)")
    mp = sub.add_parser("mark-posted", help="mark item keys (e.g. github:244580) as posted")
    mp.add_argument("keys", nargs="+")
    sub.add_parser("preview", help="scan + summarise and print a preview (no posting, no state change)")
    run = sub.add_parser("run", help="scan + summarise + post to the webhook")
    run.add_argument("--dry-run", action="store_true", help="print instead of posting; do not mark anything posted")
    sub.add_parser("stats", help="show state database counts")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    args = _parser().parse_args(argv)
    load_dotenv(args.env_file)  # existing environment variables win over .env
    try:
        cfg = Config.from_env(os.environ)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    configure_logging(cfg.log_level)

    with StateStore(cfg.state_db_path) as state:
        if args.command == "stats":
            print(json.dumps(state.counts()))
            return EXIT_OK
        if args.command == "mark-posted":
            print(f"marked {state.mark_posted(args.keys)} item(s) as posted")
            return EXIT_OK
        return _scan_commands(args, cfg, state)


def _scan_commands(args: argparse.Namespace, cfg: Config, state: StateStore) -> int:
    http = _make_http(cfg)
    try:
        result = build_pipeline(cfg, state, http).scan(datetime.now(timezone.utc))
        if args.command in ("scan", "precheck"):
            if args.command == "precheck" and not result.candidates:
                log.info("nothing new", extra={"stats_fetched": result.fetched, "errors": len(result.errors)})
                return EXIT_NOTHING_NEW if not result.errors else EXIT_ERROR
            print(scan_to_json(result))
            return EXIT_OK
        summaries = _summaries(cfg, http, result)
        if args.command == "preview" or getattr(args, "dry_run", False):
            _print_preview(summaries, result)
            return EXIT_OK
        return _post(cfg, state, summaries)
    finally:
        http.close()


def _print_preview(summaries: list[Summary], result: ScanResult) -> None:
    print(f"=== {len(summaries)} new item(s) | fetched={result.fetched} known={result.already_known} irrelevant={result.irrelevant} deferred={result.deferred} ===")
    for s in summaries:
        print()
        print(render_markdown(s))
    for err in result.errors:
        print(f"\n! {err}")


def _post(cfg: Config, state: StateStore, summaries: list[Summary]) -> int:
    if not summaries:
        log.info("nothing new to post")
        return EXIT_OK
    if not cfg.webhook_url:
        print("WEBHOOK_URL is not set; use --dry-run, or run under DAM where the agent posts to Slack", file=sys.stderr)
        return EXIT_ERROR
    http = _make_http(cfg, cfg.webhook_min_interval_s)
    try:
        dispatcher = WebhookDispatcher(http, cfg.webhook_url, cfg.webhook_format)
        failures = 0
        for summary in summaries:
            item = summary.candidate.item
            if dispatcher.send(summary):
                state.mark(item.key, "posted", item.title, item.url)
            else:
                failures += 1
        log.info("run complete", extra={"posted": len(summaries) - failures, "failed": failures})
        return EXIT_ERROR if failures else EXIT_OK
    finally:
        http.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
