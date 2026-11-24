"""Core data types shared by every module."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class Domain(str, Enum):
    """The three areas the digest cares about, in priority order."""

    SANITIZERS = "Sanitizers"
    LLC = "LLC Backend"
    CLANG = "Clang Frontend"

    @property
    def tag(self) -> str:
        """Bracketed tag used in messages, e.g. ``[Sanitizers]``."""
        return f"[{self.value}]"


#: Priority used to pick a primary domain when an item matches several.
DOMAIN_PRIORITY: tuple[Domain, ...] = (Domain.SANITIZERS, Domain.LLC, Domain.CLANG)


def parse_dt(value: str) -> datetime:
    """Parse an ISO-8601 timestamp (with optional ``Z``) into an aware UTC datetime."""
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class Item:
    """A Discourse topic or GitHub issue, normalised."""

    source: str  # "discourse" | "github"
    source_id: str
    title: str
    url: str
    author: str
    created_at: datetime
    body: str = ""  # plain text, already cleaned
    labels: tuple[str, ...] = ()  # Discourse tags or GitHub labels
    category_id: int | None = None  # Discourse only

    @property
    def key(self) -> str:
        """Globally unique dedupe key, e.g. ``discourse:92805`` or ``github:244580``."""
        return f"{self.source}:{self.source_id}"

    @property
    def kind(self) -> str:
        """Human-readable item kind."""
        if self.source == "github":
            return f"GitHub issue #{self.source_id}"
        return f"Discourse topic {self.source_id}"


@dataclass(frozen=True)
class Classification:
    """Why an item is relevant."""

    primary: Domain
    domains: tuple[Domain, ...]
    reasons: tuple[str, ...]

    @property
    def secondary(self) -> tuple[Domain, ...]:
        """Matched domains other than the primary one."""
        return tuple(d for d in self.domains if d is not self.primary)


@dataclass(frozen=True)
class Candidate:
    """An item that passed filtering and has not been posted yet."""

    item: Item
    classification: Classification


@dataclass(frozen=True)
class Summary:
    """The executive summary of one candidate."""

    candidate: Candidate
    problem: str  # 1-2 sentences
    bullets: tuple[str, ...]  # 2-3 key points
    impact: str  # affected platforms / versions / context
