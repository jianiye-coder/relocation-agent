"""Photo inventory extraction is bounded, editable, and never does moving math in the model."""

import asyncio
from io import BytesIO
from PIL import Image

import pytest
from fastapi.testclient import TestClient

from moving_agent import photo_inventory


def jpeg():
    buffer = BytesIO()
    Image.new("RGB", (2, 2)).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_photo_items_are_normalized_then_estimated_deterministically(monkeypatch):
    async def fake_vision(images, model_name):
        assert model_name == "google:gemini-flash-latest" and len(images) == 1
        return [photo_inventory.DetectedItem(item="sofa", qty=1), photo_inventory.DetectedItem(item="upright piano", qty=1)]

    monkeypatch.setattr(photo_inventory, "_run_vision", fake_vision)
    result = asyncio.run(photo_inventory.analyze(
        [("living-room.jpg", "image/jpeg", jpeg())], "google:gemini-flash-latest"
    ))
    assert result.editable_text == "sofa\nupright piano"
    assert result.estimate.total_cuft == 105
    assert result.estimate.total_lbs == 745
    assert result.estimate.special_items == ["upright piano"]


@pytest.mark.parametrize("images,error", [
    ([], "Upload 1 to 10"),
    ([("a.txt", "text/plain", b"x")], "image files"),
    ([(f"{n}.jpg", "image/jpeg", b"x") for n in range(11)], "Upload 1 to 10"),
    ([("big.jpg", "image/jpeg", b"x" * (8 * 1024 * 1024 + 1))], "8 MB"),
])
def test_photo_validation(images, error):
    with pytest.raises(photo_inventory.PhotoInventoryError, match=error):
        asyncio.run(photo_inventory.analyze(images, "google:gemini-flash-latest"))


def test_corrupt_image_is_rejected():
    with pytest.raises(photo_inventory.PhotoInventoryError, match="damaged"):
        asyncio.run(photo_inventory.analyze([("bad.jpg", "image/jpeg", b"not-an-image")], "google:gemini-flash-latest"))


def test_photo_scan_is_hidden_by_default():
    from moving_agent.web import app as web
    client = TestClient(web.app)
    page = client.get("/").text
    assert "Room photos" not in page and "inventory_photos" not in page and 'name="inventory_text"' in page
    assert client.post("/api/inventory/photos", files=[("photos", ("room.jpg", jpeg(), "image/jpeg"))]).status_code == 404


def test_missing_model_leaves_text_fallback_available(monkeypatch):
    from moving_agent.web import app as web
    monkeypatch.setenv("ENABLE_PHOTO_INVENTORY", "1")
    client = TestClient(web.app)
    response = client.post("/api/inventory/photos", files=[("photos", ("room.jpg", jpeg(), "image/jpeg"))])
    assert response.status_code == 422
    assert "API key" in response.json()["detail"]
    assert 'name="inventory_text"' in client.get("/").text


def test_too_many_uploads_rejected_before_analysis(monkeypatch):
    from moving_agent.web import app as web
    monkeypatch.setenv("ENABLE_PHOTO_INVENTORY", "1")
    response = TestClient(web.app).post("/api/inventory/photos", files=[
        ("photos", ("room.jpg", jpeg(), "image/jpeg")) for _ in range(11)
    ])
    assert response.status_code == 400


def test_selected_model_receives_images(monkeypatch):
    from moving_agent import agent as planning
    from pydantic_ai import BinaryImage
    from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
    from pydantic_ai.models.function import FunctionModel
    selected = []

    def model(messages, info):
        parts = [p for message in messages for p in message.parts if isinstance(p, UserPromptPart)]
        assert any(isinstance(value, BinaryImage) for part in parts for value in part.content)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"items": [{"item": "sofa", "qty": 2}]})])

    def build(name):
        selected.append(name)
        return FunctionModel(model)

    monkeypatch.setattr(planning, "build_model", build)
    result = asyncio.run(photo_inventory.analyze([("room.jpg", "image/jpeg", jpeg())], "flatkey:visual-model"))
    assert selected == ["flatkey:visual-model"]
    assert result.estimate.total_cuft == 70


def test_corrected_items_drive_plan_math(intake):
    from moving_agent.web import app as web
    from .test_web import form
    client = TestClient(web.app)
    response = client.post("/plan", data=form(intake, inventory_text="2 sofas"))
    assert response.status_code == 200
    session = web.SESSIONS[str(response.url).rsplit("/", 1)[-1]]
    assert session.intake.volume == 70 and session.intake.weight == 490


def test_photo_endpoint_returns_editable_items_without_persisting_uploads(monkeypatch):
    from moving_agent.web import app as web

    async def fake_analyze(images, model_name):
        assert images == [("room.jpg", "image/jpeg", b"photo")]
        return photo_inventory.PhotoInventoryResult(
            items=[photo_inventory.DetectedItem(item="sofa", qty=1)],
            editable_text="sofa",
            estimate=photo_inventory.estimate("sofa"),
        )

    monkeypatch.setenv("ENABLE_PHOTO_INVENTORY", "1")
    monkeypatch.setattr(web, "model_configured", lambda: True)
    monkeypatch.setattr(web, "pick_model", lambda: "google:gemini-flash-latest")
    monkeypatch.setattr(web.photo_inventory, "analyze", fake_analyze)
    response = TestClient(web.app).post("/api/inventory/photos", files=[("photos", ("room.jpg", b"photo", "image/jpeg"))])
    assert response.status_code == 200
    assert response.json()["editable_text"] == "sofa"
    assert response.json()["estimate"]["total_cuft"] == 35
