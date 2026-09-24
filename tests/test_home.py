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


def _capture_client(captured):
    def handler(request):
        import json
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"routes": [{"duration": "1500s", "distanceMeters": 8000}]})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_departure_time_is_sent_to_google_routes(monkeypatch):
    from datetime import date
    from moving_agent.home import departure_time
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    leave = departure_time(date(2026, 10, 14), "08:30")
    assert leave.isoformat() == "2026-10-14T08:30:00-07:00"  # San Francisco, daylight time
    captured = {}
    result = commute("1 Main St", "1 Market St", "transit", client=_capture_client(captured), departure=leave)
    assert captured["body"]["departureTime"] == "2026-10-14T15:30:00Z"
    assert "routingPreference" not in captured["body"]  # only for driving
    assert result["departure"] == "2026-10-14T08:30:00-07:00"


def test_driving_with_departure_uses_traffic(monkeypatch):
    from datetime import date
    from moving_agent.home import departure_time
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    captured = {}
    commute("1 Main St", "1 Market St", "drive", client=_capture_client(captured), departure=departure_time(date(2026, 10, 14), "08:30"))
    assert captured["body"]["routingPreference"] == "TRAFFIC_AWARE"


def test_no_departure_time_sends_none(monkeypatch):
    from datetime import date
    from moving_agent.home import departure_time
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    assert departure_time(date(2026, 10, 14), "") is None
    captured = {}
    commute("1 Main St", "1 Market St", "drive", client=_capture_client(captured))
    assert "departureTime" not in captured["body"] and "routingPreference" not in captured["body"]


def test_past_departure_is_not_sent(monkeypatch):
    from datetime import date
    from moving_agent.home import departure_time
    assert departure_time(date(2020, 1, 6), "08:30") is None  # Google rejects past departure times


def test_schools_are_explicitly_unavailable():
    from moving_agent.home import schools
    result = schools("1 Main St, San Francisco, CA")
    assert result["available"] is False and "school" in result["message"].lower()
