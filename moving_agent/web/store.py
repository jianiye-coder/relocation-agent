"""Where plans (sessions) live between requests.

Locally a dict is enough: one long-running process. On Vercel every request can
land on a different function instance, so plans go to Redis through the Upstash
REST API when its credentials are set (the Vercel Upstash integration adds them).

A session is pickled without its offer sources (they hold a SQLite connection and
locks); the app puts its own sources back when it loads one. Only this app writes
to the Redis database, with its own token, so unpickling what comes back is safe.
"""

from __future__ import annotations

import base64
import dataclasses
import os
import pickle
from collections.abc import Callable
from typing import Any

import httpx

TTL_SECONDS = 7 * 24 * 3600
KEY_PREFIX = "relocation-agent:plan:"


def redis_credentials() -> tuple[str, str] | None:
    """Upstash REST URL and token, under either name the Vercel integration uses."""
    for url_var, token_var in (("KV_REST_API_URL", "KV_REST_API_TOKEN"), ("UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN")):
        url, token = os.getenv(url_var, "").strip(), os.getenv(token_var, "").strip()
        if url and token:
            return url.rstrip("/"), token
    return None


class RedisSessions:
    """Dict-like store backed by Upstash Redis over REST (no extra dependency)."""

    def __init__(self, url: str, token: str, attach: Callable[[Any], Any], client: httpx.Client | None = None):
        self.url, self.attach = url, attach
        self.client = client or httpx.Client(timeout=10)
        self.headers = {"Authorization": f"Bearer {token}"}

    def _command(self, *args: str) -> Any:
        response = self.client.post(self.url, headers=self.headers, json=list(args))
        response.raise_for_status()
        return response.json().get("result")

    def __setitem__(self, rid: str, session: Any) -> None:
        light = dataclasses.replace(session, deps=dataclasses.replace(session.deps, sources=[]))
        blob = base64.b64encode(pickle.dumps(light)).decode()
        self._command("SET", KEY_PREFIX + rid, blob, "EX", str(TTL_SECONDS))

    def get(self, rid: str, default: Any = None) -> Any:
        blob = self._command("GET", KEY_PREFIX + rid)
        if not blob:
            return default
        return self.attach(pickle.loads(base64.b64decode(blob)))

    def __getitem__(self, rid: str) -> Any:
        session = self.get(rid)
        if session is None:
            raise KeyError(rid)
        return session

    def __contains__(self, rid: str) -> bool:
        return self.get(rid) is not None


def session_store(attach: Callable[[Any], Any]) -> dict | RedisSessions:
    creds = redis_credentials()
    return RedisSessions(*creds, attach=attach) if creds else {}
