"""HTTP client with timeouts, retries, 429 handling and request throttling."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import Any

import httpx

log = logging.getLogger(__name__)

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class HttpError(Exception):
    """A request failed permanently. Messages carry the host only (URLs may be secrets)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class RateLimitedError(HttpError):
    """The server asked us to wait longer than we are willing to."""


class RetryingClient:
    """Thin wrapper over :class:`httpx.Client`.

    * enforces a minimum interval between requests (client-side rate limiting)
    * retries timeouts, transport errors, 429 and 5xx with exponential backoff + jitter
    * honours ``Retry-After`` and GitHub's ``X-RateLimit-Reset``
    * never follows redirects unless asked, and never logs URLs
    """

    def __init__(
        self,
        *,
        user_agent: str,
        timeout_s: float = 15.0,
        max_retries: int = 4,
        min_interval_s: float = 0.5,
        max_backoff_s: float = 60.0,
        follow_redirects: bool = False,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_s),
            headers={"User-Agent": user_agent},
            follow_redirects=follow_redirects,
        )
        if client is not None:
            self._client.headers["User-Agent"] = user_agent  # httpx pre-fills its own default
        self._max_retries = max_retries
        self._min_interval_s = min_interval_s
        self._max_backoff_s = max_backoff_s
        self._sleep = sleep
        self._clock = clock
        self._wall_clock = wall_clock
        self._last_request: float | None = None

    def close(self) -> None:
        """Release the underlying connection pool."""
        self._client.close()

    def _throttle(self) -> None:
        if self._last_request is not None:
            wait = self._min_interval_s - (self._clock() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float | None:
        """Seconds to wait before retrying, or ``None`` if the response is not retryable."""
        status = response.status_code
        remaining = response.headers.get("X-RateLimit-Remaining")
        github_limited = status == 403 and remaining == "0"
        if status not in _RETRYABLE_STATUS and not github_limited:
            return None
        delay = self._backoff(attempt)
        retry_after = response.headers.get("Retry-After")
        reset = response.headers.get("X-RateLimit-Reset")
        if retry_after is not None:
            try:
                delay = max(0.0, float(retry_after))
            except ValueError:
                pass
        elif github_limited and reset is not None:
            try:
                delay = max(0.0, float(reset) - self._wall_clock()) + 1.0
            except ValueError:
                pass
        if delay > self._max_backoff_s:
            raise RateLimitedError(
                f"{response.request.url.host} asked us to wait {delay:.0f}s (limit {self._max_backoff_s:.0f}s)",
                status,
            )
        return delay

    @staticmethod
    def _backoff(attempt: int) -> float:
        return float(0.5 * (2**attempt) + random.random() * 0.25)

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Send a request, retrying transient failures. Raises :class:`HttpError` on failure."""
        host = httpx.URL(url).host
        for attempt in range(self._max_retries + 1):
            self._throttle()
            try:
                response = self._client.request(method, url, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt >= self._max_retries:
                    raise HttpError(f"{host}: {type(exc).__name__} after {attempt + 1} attempts") from None
                delay = self._backoff(attempt)
                log.warning("transient network error", extra={"host": host, "error": type(exc).__name__, "retry_in_s": round(delay, 2)})
                self._sleep(delay)
                continue
            if response.status_code < 400:
                return response
            delay_or_none = self._retry_delay(response, attempt)
            if delay_or_none is None:
                raise HttpError(f"{host}: HTTP {response.status_code}", response.status_code)
            if attempt >= self._max_retries:
                cls = RateLimitedError if response.status_code == 429 else HttpError
                raise cls(f"{host}: HTTP {response.status_code} after {attempt + 1} attempts", response.status_code)
            log.warning("retrying request", extra={"host": host, "status": response.status_code, "retry_in_s": round(delay_or_none, 2)})
            self._sleep(delay_or_none)
        raise HttpError(f"{host}: retries exhausted")  # pragma: no cover

    def get_json(self, url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
        """GET ``url`` and decode JSON. Raises :class:`HttpError` on non-JSON bodies."""
        response = self.request("GET", url, params=params, headers=headers)
        try:
            return response.json()
        except ValueError:
            raise HttpError(f"{httpx.URL(url).host}: response was not valid JSON", response.status_code) from None
