"""Shared HTTP session and retry policy.

One process-wide :class:`requests.Session` serves both phases so connection pooling and the
retry policy are consistent everywhere.

Policy (see :func:`build_retry`):

- up to **4 retries** (5 attempts) for connection errors and transient status codes;
- exponential backoff with jitter (0.5 s factor, 0.5 s jitter, 30 s ceiling);
- ``Retry-After`` from the server is honoured;
- only idempotent methods (``GET``/``HEAD``/``OPTIONS``) are retried. The HKEx JSF login
  ``POST`` is never replayed: a read timeout can happen *after* the server processed it, and a
  duplicate POST would restart the search session.

Optional pacing: set ``REQUEST_DELAY_SECONDS`` (for example ``0.5``) to serialise request
starts process-wide. The default is ``0`` (no delay), which keeps throughput unchanged; a
non-zero value is the polite setting when scraping an undocumented endpoint.
"""

from __future__ import annotations

import os
import threading
import time
from importlib.util import find_spec
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import __version__

# requests is a base dependency; the flag is kept for callers that guard on it.
REQUESTS_AVAILABLE = find_spec("requests") is not None

REPO_URL = "https://github.com/simonmak-ascent/hkex-filing-scraper"

# Retry policy
RETRY_TOTAL = 4
RETRY_BACKOFF_FACTOR = 0.5
RETRY_BACKOFF_MAX = 30.0
RETRY_BACKOFF_JITTER = 0.5
RETRY_STATUS_FORCELIST = frozenset({408, 429, 500, 502, 503, 504})
RETRY_ALLOWED_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Connection pooling
POOL_CONNECTIONS = 10
POOL_MAXSIZE = 30

_session: Optional[requests.Session] = None
_session_lock = threading.Lock()

_pace_lock = threading.Lock()
_next_request_at = 0.0


def user_agent() -> str:
    """A descriptive User-Agent with a contact URL, as scraping etiquette requires."""
    return f"hkex-filing-scraper/{__version__} (+{REPO_URL})"


def request_delay() -> float:
    """Seconds to wait between request starts (``REQUEST_DELAY_SECONDS``, default 0)."""
    raw = os.environ.get("REQUEST_DELAY_SECONDS", "0") or "0"
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


def build_retry() -> Retry:
    """Build the bounded retry policy used by every request."""
    return Retry(
        total=RETRY_TOTAL,
        connect=RETRY_TOTAL,
        read=RETRY_TOTAL,
        status=RETRY_TOTAL,
        backoff_factor=RETRY_BACKOFF_FACTOR,
        backoff_max=RETRY_BACKOFF_MAX,
        backoff_jitter=RETRY_BACKOFF_JITTER,
        status_forcelist=sorted(RETRY_STATUS_FORCELIST),
        allowed_methods=sorted(RETRY_ALLOWED_METHODS),
        respect_retry_after_header=True,
        # Return the final response instead of raising, so callers can log the status,
        # headers, and body before deciding what to do with it.
        raise_on_status=False,
    )


def make_session() -> requests.Session:
    """Create a new session with the shared retry policy mounted."""
    adapter = HTTPAdapter(
        max_retries=build_retry(),
        pool_connections=POOL_CONNECTIONS,
        pool_maxsize=POOL_MAXSIZE,
    )
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent(), "Accept-Encoding": "gzip, deflate"})
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get_session() -> requests.Session:
    """Return the process-wide session, creating it on first use."""
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                _session = make_session()
    return _session


def reset_session() -> None:
    """Close and forget the shared session (used by tests)."""
    global _session
    with _session_lock:
        if _session is not None:
            try:
                _session.close()
            except Exception:  # pragma: no cover - best effort
                pass
        _session = None


def pace() -> None:
    """Throttle request starts to at most one per ``REQUEST_DELAY_SECONDS``, process-wide.

    Returns immediately when pacing is disabled, so the default path adds no latency.
    """
    delay = request_delay()
    if delay <= 0:
        return
    global _next_request_at
    with _pace_lock:
        now = time.monotonic()
        wait = _next_request_at - now
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _next_request_at = now + delay


def reset_pacing() -> None:
    """Forget the pacing clock (used by tests)."""
    global _next_request_at
    with _pace_lock:
        _next_request_at = 0.0
