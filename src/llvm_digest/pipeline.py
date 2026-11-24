"""Orchestration: fetch -> dedupe -> classify -> (hydrate) -> rank."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .classify import Classifier
from .config import Config
from .discourse import DiscourseClient
from .github import GitHubClient
from .http import HttpError
from .models import DOMAIN_PRIORITY, Candidate, Item
from .state import StateStore

log = logging.getLogger(__name__)


@dataclass
class ScanResult:
    """Outcome of one scan."""

    candidates: list[Candidate] = field(default_factory=list)
    fetched: int = 0
    already_known: int = 0
    irrelevant: int = 0
    deferred: int = 0  # relevant but beyond MAX_ITEMS_PER_RUN; picked up next run
    errors: list[str] = field(default_factory=list)


def _select(found: list[Candidate], limit: int) -> list[Candidate]:
    """Pick up to ``limit`` items, round-robin across domains so one busy domain cannot starve the rest.

    Within a domain, newest first; the final order keeps domain priority (Sanitizers, LLC, Clang).
    """
    queues: dict[object, list[Candidate]] = {d: [] for d in DOMAIN_PRIORITY}
    for cand in sorted(found, key=lambda c: -c.item.created_at.timestamp()):
        queues[cand.classification.primary].append(cand)
    chosen: list[Candidate] = []
    while len(chosen) < limit and any(queues.values()):
        for domain in DOMAIN_PRIORITY:
            if queues[domain] and len(chosen) < limit:
                chosen.append(queues[domain].pop(0))
    chosen.sort(key=lambda c: DOMAIN_PRIORITY.index(c.classification.primary))
    return chosen


class Pipeline:
    """Collects new, relevant items. Never mutates state except for ``skipped`` Discourse rows."""

    def __init__(
        self,
        config: Config,
        state: StateStore,
        classifier: Classifier,
        discourse: DiscourseClient | None,
        github: GitHubClient | None,
    ) -> None:
        self._cfg = config
        self._state = state
        self._classifier = classifier
        self._discourse = discourse
        self._github = github

    def scan(self, now: datetime) -> ScanResult:
        """Look back ``LOOKBACK_HOURS`` from ``now`` and return new relevant candidates."""
        since = now - timedelta(hours=self._cfg.lookback_hours)
        result = ScanResult()
        found: list[Candidate] = []

        if self._discourse is not None:
            try:
                for item in self._discourse.recent_items(since, self._cfg.max_pages):
                    result.fetched += 1
                    cand = self._handle_discourse(item, self._discourse, result)
                    if cand is not None:
                        found.append(cand)
            except HttpError as exc:
                log.error("discourse unavailable", extra={"error": str(exc)})
                result.errors.append(f"discourse: {exc}")

        if self._github is not None:
            try:
                for item in self._github.recent_issues(since, self._cfg.max_pages):
                    result.fetched += 1
                    cand = self._handle_github(item, result)
                    if cand is not None:
                        found.append(cand)
            except HttpError as exc:
                log.error("github unavailable", extra={"error": str(exc)})
                result.errors.append(f"github: {exc}")

        limit = self._cfg.max_items_per_run
        result.candidates = _select(found, limit)
        result.deferred = max(0, len(found) - len(result.candidates))
        return result

    def _handle_discourse(self, item: Item, client: DiscourseClient, result: ScanResult) -> Candidate | None:
        if self._state.is_known(item.key):
            result.already_known += 1
            return None
        classification = self._classifier.classify(item)
        hydrated = item
        if classification is None and self._classifier.needs_body_scan(item):
            hydrated = self._try_hydrate(client, item, result)
            classification = self._classifier.classify(hydrated, use_body=True)
        if classification is None:
            result.irrelevant += 1
            self._state.mark(item.key, "skipped", item.title, item.url)
            return None
        if hydrated is item:
            hydrated = self._try_hydrate(client, item, result)
        return Candidate(item=hydrated, classification=classification)

    def _handle_github(self, item: Item, result: ScanResult) -> Candidate | None:
        if self._state.is_known(item.key):
            result.already_known += 1
            return None
        classification = self._classifier.classify(item)
        if classification is None:
            # Not marked: maintainers often add labels minutes after an issue is filed.
            result.irrelevant += 1
            return None
        return Candidate(item=item, classification=classification)

    @staticmethod
    def _try_hydrate(client: DiscourseClient, item: Item, result: ScanResult) -> Item:
        try:
            return client.hydrate(item)
        except HttpError as exc:
            log.warning("could not fetch topic body", extra={"item": item.key, "error": str(exc)})
            result.errors.append(f"hydrate {item.key}: {exc}")
            return item
