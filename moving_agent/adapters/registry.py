"""Runs adapters: caching with expiry, typed errors, timeouts, opt-in for unofficial sources."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
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


class QuoteCache:
    """SQLite cache keyed by adapter + request. Keeps expired entries so they can be served as stale."""

    def __init__(self, path: Path | str = DEFAULT_CACHE):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(self.path, check_same_thread=False)
        self._con.execute("CREATE TABLE IF NOT EXISTS quotes (key TEXT PRIMARY KEY, stored_at REAL, payload TEXT)")

    @staticmethod
    def key(adapter_id: str, req: MoveRequest) -> str:
        blob = json.dumps(req.model_dump(mode="json"), sort_keys=True)
        return f"{adapter_id}:{hashlib.sha256(blob.encode()).hexdigest()[:24]}"

    def get(self, key: str) -> tuple[list[Quote], float] | None:
        row = self._con.execute("SELECT stored_at, payload FROM quotes WHERE key = ?", (key,)).fetchone()
        if not row:
            return None
        return [Quote.model_validate(q) for q in json.loads(row[1])], row[0]

    def put(self, key: str, quotes: list[Quote]) -> None:
        payload = json.dumps([q.model_dump(mode="json") for q in quotes])
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

    async def _run(self, adapter: QuoteAdapter, req: MoveRequest) -> AdapterResult:
        meta = adapter.metadata
        missing = [v for v in meta.auth_env if not os.getenv(v)]
        if missing:
            return AdapterResult(adapter_id=meta.id, error=ErrorCode.auth_missing, message=f"set {', '.join(missing)}")
        if not adapter.covers(req):
            return AdapterResult(adapter_id=meta.id, error=ErrorCode.no_coverage, message="outside this adapter's coverage")

        key = QuoteCache.key(meta.id, req) if self.cache else None
        cached = self.cache.get(key) if self.cache else None
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
            self.cache.put(key, quotes)
        return AdapterResult(adapter_id=meta.id, quotes=quotes, fetched_at=now())
