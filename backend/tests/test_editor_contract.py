from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.editor.schemas import BannerStyle, EditConfig, EditorElement, LayoutPreset, preset_config


@pytest.mark.parametrize("preset", list(LayoutPreset))
def test_every_required_preset_is_serializable_and_stays_in_logical_canvas(preset: LayoutPreset) -> None:
    config = preset_config(preset)

    restored = EditConfig.model_validate_json(config.model_dump_json())

    assert restored == config
    assert (restored.canvas_width, restored.canvas_height) == (1080, 1920)
    assert {element.kind for element in restored.elements} >= {"SCREEN", "WEBCAM", "CAPTIONS"}
    for element in restored.elements:
        assert element.x + element.width <= restored.canvas_width
        assert element.y + element.height <= restored.canvas_height


def test_editor_rejects_elements_outside_canvas_and_untrusted_banner_path() -> None:
    with pytest.raises(ValidationError, match="inside the 1080x1920 canvas"):
        EditorElement(id="screen", kind="SCREEN", x=1000, y=0, width=100, height=100)

    config = preset_config(LayoutPreset.WEBCAM_TOP_SCREEN_BOTTOM)
    payload = config.model_dump(mode="json")
    payload["banner"]["image_relative_path"] = "../../outside.png"
    with pytest.raises(ValidationError, match="relative"):
        EditConfig.model_validate(payload)


def test_banner_interval_has_a_valid_nonempty_range() -> None:
    with pytest.raises(ValidationError, match="end_ms"):
        BannerStyle(enabled=True, start_ms=1_000, end_ms=1_000)


def test_edit_config_is_pinned_to_v1_and_banner_assets_stay_below_assets() -> None:
    payload = preset_config(LayoutPreset.WEBCAM_TOP_SCREEN_BOTTOM).model_dump(mode="json")
    payload["schema_version"] = 2
    with pytest.raises(ValidationError, match="schema_version"):
        EditConfig.model_validate(payload)

    with pytest.raises(ValidationError, match="assets"):
        BannerStyle(image_relative_path="previews/logo.png")


@pytest.mark.parametrize("unsafe", [r"assets\logo.png", "C:/assets/logo.png", "/assets/logo.png", "logo.png"])
def test_banner_asset_requires_safe_posix_assets_path(unsafe: str) -> None:
    with pytest.raises(ValidationError, match="assets"):
        BannerStyle(image_relative_path=unsafe)
