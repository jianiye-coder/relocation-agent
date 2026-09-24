import httpx

from moving_agent.home import commute, utilities


def test_commute_requires_google_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    result = commute("1 Main St, San Francisco, CA", "1 Market St, San Francisco, CA", "transit")
    assert result["available"] is False and "GOOGLE_MAPS_API_KEY" in result["message"]


def test_commute_normalizes_google_routes_response(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"routes": [{"duration": "1800s", "distanceMeters": 12000}]}))
    result = commute("1 Main St", "1 Market St", "drive", client=httpx.Client(transport=transport))
    assert result == {"available": True, "minutes": 30, "miles": 7.5, "source": "Google Routes", "fetched_at": result["fetched_at"]}


def test_utilities_require_configured_sources(monkeypatch):
    monkeypatch.delenv("BROADBAND_API_KEY", raising=False)
    monkeypatch.delenv("NREL_API_KEY", raising=False)
    result = utilities("1 Main St, San Francisco, CA")
    assert result["broadband"]["available"] is False
    assert result["electricity"]["available"] is False


def _routes_client(status, body):
    return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body)))


def test_commute_empty_routes_is_unavailable_not_an_error(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    for body in ({"routes": []}, {}):  # Google returns {} or an empty list when there is no route
        result = commute("1 Main St", "Nowhere", "transit", client=_routes_client(200, body))
        assert result["available"] is False and "No route" in result["message"]


def test_commute_http_error_is_unavailable(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    result = commute("1 Main St", "1 Market St", "drive", client=_routes_client(403, {"error": "denied"}))
    assert result["available"] is False and "unavailable" in result["message"]
