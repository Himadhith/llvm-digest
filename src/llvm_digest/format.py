"""Render summaries as Slack Block Kit payloads or plain Markdown."""

from __future__ import annotations

from typing import Any

from .models import Summary
from .text import slack_escape, slack_label, truncate

_SECTION_LIMIT = 2900  # Slack section text is capped at 3000 characters


def _date(summary: Summary) -> str:
    return summary.candidate.item.created_at.strftime("%Y-%m-%d")


def render_markdown(summary: Summary) -> str:
    """Plain Markdown, used for console previews and generic webhooks."""
    cand = summary.candidate
    item = cand.item
    tags = " ".join(d.tag for d in cand.classification.domains)
    lines = [
        f"**{cand.classification.primary.tag}** [{item.title}]({item.url})",
        f"_{item.author} \u00b7 {_date(summary)} \u00b7 {item.kind}_",
        "",
        f"**Key problem:** {summary.problem}",
        *[f"- {b}" for b in summary.bullets],
        f"**Impact/context:** {summary.impact}",
    ]
    if len(cand.classification.domains) > 1:
        lines.append(f"_Also relevant to: {' '.join(d.tag for d in cand.classification.secondary)}_")
    del tags
    return "\n".join(lines)


def render_slack(summary: Summary) -> dict[str, Any]:
    """Slack Block Kit payload for an Incoming Webhook (with a plain-text fallback)."""
    cand = summary.candidate
    item = cand.item
    head = f"*{cand.classification.primary.tag}* <{item.url}|{slack_label(item.title)}>"
    meta = f"{slack_escape(item.author)} \u00b7 {_date(summary)} \u00b7 {slack_escape(item.kind)}"
    if cand.classification.secondary:
        meta += " \u00b7 also: " + " ".join(d.tag for d in cand.classification.secondary)
    bullets = "\n".join(f"\u2022 {slack_escape(b)}" for b in summary.bullets)
    body = f"*Key problem:* {slack_escape(summary.problem)}\n{bullets}"
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": truncate(head, _SECTION_LIMIT)}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": truncate(meta, 500)}]},
        {"type": "section", "text": {"type": "mrkdwn", "text": truncate(body, _SECTION_LIMIT)}},
        {"type": "section", "text": {"type": "mrkdwn", "text": truncate(f"*Impact/context:* {slack_escape(summary.impact)}", _SECTION_LIMIT)}},
        {"type": "divider"},
    ]
    fallback = slack_escape(f"{cand.classification.primary.tag} {item.title}")  # shown in notifications: must be escaped too
    return {"text": fallback, "blocks": blocks, "unfurl_links": False, "unfurl_media": False}


def render_text_payload(summary: Summary) -> dict[str, Any]:
    """Minimal ``{"text": ...}`` payload for Teams/Mattermost/generic sinks."""
    return {"text": render_markdown(summary)}
