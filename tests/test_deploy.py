"""Deployment pieces: plans kept in Redis between requests, the access code, and Vercel's entrypoint."""

import json
import re
import tomllib
from importlib import import_module
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from moving_agent.adapters.registry import default_cache_path
from moving_agent.web import app as web
from moving_agent.web.store import RedisSessions, redis_credentials, session_store

from .test_web import form


class FakeUpstash:
    """Upstash REST: POST a JSON command array, get {"result": ...} back."""

    def __init__(self):
        self.data, self.commands = {}, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret-token"
        command = json.loads(request.content)
        self.commands.append(command[0])
        if command[0] == "SET":
            self.data[command[1]] = command[2]
            return httpx.Response(200, json={"result": "OK"})
        return httpx.Response(200, json={"result": self.data.get(command[1])})


@pytest.fixture
def redis_sessions(monkeypatch):
    upstash = FakeUpstash()
    store = RedisSessions("https://example.upstash.io", "secret-token", attach=web._attach,
                          client=httpx.Client(transport=httpx.MockTransport(upstash)))
    monkeypatch.setattr(web, "SESSIONS", store)
    return upstash


def test_credentials_come_from_either_integration_name(monkeypatch):
    assert redis_credentials() is None and session_store(lambda s: s) == {}
    monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "https://x.upstash.io/")
    monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "t")
    assert redis_credentials() == ("https://x.upstash.io", "t")
    monkeypatch.setenv("KV_REST_API_URL", "https://kv.upstash.io")
    monkeypatch.setenv("KV_REST_API_TOKEN", "k")
    assert redis_credentials() == ("https://kv.upstash.io", "k")


def test_plan_survives_between_requests_through_redis(intake, redis_sessions):
    client = TestClient(web.app)
    response = client.post("/plan", data=form(intake))
    assert response.status_code == 200
    rid = re.search(r"/plan/(\w+)", str(response.url)).group(1)
    stored = redis_sessions.data[f"relocation-agent:plan:{rid}"]
    assert stored  # the plan was written to Redis, not only kept in memory

    # A later request (on any instance) reads it back, with the offer sources re-attached.
    loaded = web.SESSIONS.get(rid)
    assert loaded.deps.sources is web.SOURCES and loaded.deps.plans
    page = client.get(f"/plan/{rid}/home")
    assert page.status_code == 200 and "Recommended" in page.text


def test_changes_to_a_plan_are_saved_back(intake, redis_sessions):
    client = TestClient(web.app)
    rid = re.search(r"/plan/(\w+)", str(client.post("/plan", data=form(intake)).url)).group(1)
    client.post(f"/housing/{rid}/select", data={"address": "1 Main St, San Francisco, CA"})
    assert web.SESSIONS.get(rid).intake.candidate_addresses == ["1 Main St, San Francisco, CA"]


def test_unknown_plan_in_redis_shows_the_form(redis_sessions):
    response = TestClient(web.app).get("/plan/nothere")
    assert response.status_code == 404 and "That plan is no longer available" in response.text


def test_access_code_gates_every_page(intake, monkeypatch):
    monkeypatch.setenv("ACCESS_CODE", "moveday")
    client = TestClient(web.app)
    first = client.get("/housing", follow_redirects=False)
    assert first.status_code == 303 and first.headers["location"] == "/unlock?next=%2Fhousing"
    assert client.post("/plan", data=form(intake)).status_code == 401

    wrong = client.post("/unlock", data={"code": "nope", "next": "/"})
    assert wrong.status_code == 401 and "doesn&#39;t match" in wrong.text
    right = client.post("/unlock", data={"code": "moveday", "next": "/housing"}, follow_redirects=False)
    assert right.status_code == 303 and right.headers["location"] == "/housing"
    assert client.get("/").status_code == 200 and "Find my options" in client.get("/").text


def test_unlock_only_redirects_within_the_site(monkeypatch):
    monkeypatch.setenv("ACCESS_CODE", "moveday")
    response = TestClient(web.app).post("/unlock", data={"code": "moveday", "next": "//evil.example"}, follow_redirects=False)
    assert response.headers["location"] == "/"


def test_no_access_code_means_open_locally():
    assert TestClient(web.app).get("/").status_code == 200


def test_cache_goes_to_tmp_on_vercel(monkeypatch):
    assert default_cache_path().parts[-2:] == ("data", "cache.db")
    monkeypatch.setenv("VERCEL", "1")
    assert str(default_cache_path()).startswith("/tmp/")


def test_vercel_entrypoint_points_at_the_fastapi_app():
    config = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    module, attr = config["tool"]["vercel"]["entrypoint"].split(":")
    assert isinstance(getattr(import_module(module), attr), FastAPI)
