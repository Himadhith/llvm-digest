"""Webhook dispatcher with retry/backoff (via :class:`RetryingClient`) and rate limiting."""

from __future__ import annotations

import logging
from typing import Any

from .format import render_slack, render_text_payload
from .http import HttpError, RetryingClient
from .models import Summary

log = logging.getLogger(__name__)


class WebhookDispatcher:
    """POSTs one message per summary. The webhook URL is a credential and is never logged."""

    def __init__(self, http: RetryingClient, webhook_url: str, fmt: str = "slack") -> None:
        if not webhook_url:
            raise ValueError("WEBHOOK_URL is not set")
        self._http = http
        self._url = webhook_url
        self._fmt = fmt

    def payload(self, summary: Summary) -> dict[str, Any]:
        """Build the payload for the configured format."""
        return render_slack(summary) if self._fmt == "slack" else render_text_payload(summary)

    def send(self, summary: Summary) -> bool:
        """Deliver one summary. Returns ``False`` (and logs the host only) when delivery fails."""
        try:
            self._http.request("POST", self._url, json=self.payload(summary))
        except HttpError as exc:
            log.error("webhook delivery failed", extra={"item": summary.candidate.item.key, "status": exc.status})
            return False
        return True
