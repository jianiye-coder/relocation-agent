"""Voice transcript extraction stays reviewable and does not require browser audio in tests."""

import asyncio

from fastapi.testclient import TestClient

from moving_agent import voice_intake


def test_voice_autofill_requires_a_configured_model():
    with __import__("pytest").raises(voice_intake.VoiceIntakeError, match="Configure an AI provider"):
        asyncio.run(voice_intake.extract("I am moving from Chicago to San Francisco", None))


def test_voice_autofill_endpoint_returns_only_reviewable_fields(monkeypatch):
    from moving_agent.web import app as web

    async def fake_extract(transcript, model_name):
        assert transcript == "I am moving from Chicago to San Francisco" and model_name == "test-model"
        return voice_intake.VoiceIntakeFields(
            name="Jenny", from_zip="60614", to_zip="94110", home_size="1br", budget_usd=5000,
            needs=["truck", "labor"], pets=["cat"],
        )

    monkeypatch.setattr(web, "pick_model", lambda: "test-model")
    monkeypatch.setattr(web.voice_intake, "extract", fake_extract)
    response = TestClient(web.app).post("/api/intake/voice-autofill", json={"transcript": "I am moving from Chicago to San Francisco"})
    assert response.status_code == 200
    assert response.json() == {"fields": {"name": "Jenny", "from_zip": "60614", "to_zip": "94110", "home_size": "1br", "pets": ["cat"], "needs": ["truck", "labor"], "budget_usd": 5000}}


def test_voice_intake_ui_requires_review_before_submission():
    from moving_agent.web import app as web

    page = TestClient(web.app).get("/").text
    for text in ["Start voice input", "Fill my details", "Voice transcript", "Nothing is submitted until you choose Find my options", "SpeechRecognition"]:
        assert text in page
