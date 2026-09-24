"""Runs adapters: caching with expiry, typed errors, timeouts, opt-in for unofficial sources."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import logging
import sqlite3
import threading
import time
from pathlib import Path

import httpx

from .base import (
    AdapterError,
    AdapterResult,
    ErrorCode,
    MoveRequest,
    Quote,
    QuoteAdapter,
    now,
)

DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "cache.db"
log = logging.getLogger(__name__)


class QuoteCache:
    """SQLite cache keyed by adapter + request. Keeps expired entries so they can be served as stale.

    One connection is shared by every thread (agent tools run in parallel threads), so each
    operation holds a lock. check_same_thread=False only disables SQLite's check; it doesn't
    make concurrent use safe.
    """

    def __init__(self, path: Path | str = DEFAULT_CACHE):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._con = sqlite3.connect(self.path, check_same_thread=False)
        with self._lock:
            self._con.execute("CREATE TABLE IF NOT EXISTS quotes (key TEXT PRIMARY KEY, stored_at REAL, payload TEXT)")

    @staticmethod
    def key(adapter_id: str, req: MoveRequest) -> str:
        blob = json.dumps(req.model_dump(mode="json"), sort_keys=True)
        return f"{adapter_id}:{hashlib.sha256(blob.encode()).hexdigest()[:24]}"

    def get(self, key: str) -> tuple[list[Quote], float] | None:
        with self._lock:
            row = self._con.execute("SELECT stored_at, payload FROM quotes WHERE key = ?", (key,)).fetchone()
        if not row:
            return None
        return [Quote.model_validate(q) for q in json.loads(row[1])], row[0]

    def put(self, key: str, quotes: list[Quote]) -> None:
        payload = json.dumps([q.model_dump(mode="json") for q in quotes])
        with self._lock:
            self._con.execute("INSERT OR REPLACE INTO quotes VALUES (?, ?, ?)", (key, time.time(), payload))
            self._con.commit()


def _classify(exc: Exception) -> tuple[ErrorCode, str]:
    """Map any failure to the shared error set."""
    if isinstance(exc, AdapterError):
        return exc.code, exc.message
    if isinstance(exc, asyncio.TimeoutError | httpx.TimeoutException):
        return ErrorCode.unavailable, "timed out"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return ErrorCode.rate_limited, "rate limited by provider"
        if status in (401, 403):
            return ErrorCode.blocked, f"provider refused the request ({status})"
        return ErrorCode.unavailable, f"provider error {status}"
    if isinstance(exc, httpx.HTTPError):
        return ErrorCode.unavailable, str(exc) or type(exc).__name__
    return ErrorCode.unavailable, f"{type(exc).__name__}: {exc}"


class Registry:
    def __init__(self, adapters: list[QuoteAdapter] | None = None, cache: QuoteCache | None = None,
                 timeout_s: float = 20.0, opt_in_unofficial: set[str] | None = None):
        self.adapters: list[QuoteAdapter] = list(adapters or [])
        self.cache = cache
        self.timeout_s = timeout_s
        self.opt_in = opt_in_unofficial or set(filter(None, os.getenv("ENABLE_UNOFFICIAL_ADAPTERS", "").split(",")))

    def register(self, adapter: QuoteAdapter) -> None:
        self.adapters.append(adapter)

    def active(self) -> list[QuoteAdapter]:
        """Default-enabled adapters plus unofficial ones the user explicitly opted into."""
        return [a for a in self.adapters if a.metadata.enabled_by_default or a.metadata.id in self.opt_in]

    async def quotes(self, req: MoveRequest) -> list[AdapterResult]:
        return list(await asyncio.gather(*(self._run(a, req) for a in self.active())))

    def _cache_get(self, key: str):
        """A cache problem must never fail a quote request: log it and fetch live instead."""
        try:
            return self.cache.get(key)
        except Exception as exc:
            log.warning("quote cache read failed (%s); fetching live", exc)
            return None

    def _cache_put(self, key: str, quotes: list[Quote]) -> None:
        try:
            self.cache.put(key, quotes)
        except Exception as exc:
            log.warning("quote cache write failed (%s); continuing without caching", exc)

    async def _run(self, adapter: QuoteAdapter, req: MoveRequest) -> AdapterResult:
        meta = adapter.metadata
        missing = [v for v in meta.auth_env if not os.getenv(v)]
        if missing:
            return AdapterResult(adapter_id=meta.id, error=ErrorCode.auth_missing, message=f"set {', '.join(missing)}")
        if not adapter.covers(req):
            return AdapterResult(adapter_id=meta.id, error=ErrorCode.no_coverage, message="outside this adapter's coverage")

        key = QuoteCache.key(meta.id, req) if self.cache else None
        cached = self._cache_get(key) if self.cache else None
        if cached and time.time() - cached[1] < meta.cache_ttl_seconds:
            return AdapterResult(adapter_id=meta.id, quotes=cached[0], from_cache=True)

        try:
            quotes = await asyncio.wait_for(adapter.fetch_quotes(req), timeout=self.timeout_s)
        except Exception as exc:
            code, message = _classify(exc)
            if cached:  # serve the expired copy, clearly marked
                return AdapterResult(adapter_id=meta.id, quotes=cached[0], from_cache=True, stale=True,
                                     error=ErrorCode.stale, message=f"{code.value}: {message}; showing an expired result")
            return AdapterResult(adapter_id=meta.id, error=code, message=message)

        for q in quotes:  # adapters must stamp their own results; this keeps them honest
            if q.adapter_id != meta.id:
                raise ValueError(f"{meta.id} returned a quote labeled {q.adapter_id}")
        if self.cache:
            self._cache_put(key, quotes)
        return AdapterResult(adapter_id=meta.id, quotes=quotes, fetched_at=now())
