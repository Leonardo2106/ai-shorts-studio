from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.editor.schemas import (
    AudioConfig,
    AudioTrackConfig,
    BannerStyle,
    EditConfig,
    EditorElement,
    LayoutPreset,
    preset_config,
)


def _frontend_preset_snapshot(preset: LayoutPreset) -> dict[str, object]:
    def element(**values: object) -> dict[str, object]:
        return {
            "z_index": 0,
            "visible": True,
            "fit": "COVER",
            "opacity": 1.0,
            "border_width": 0,
            "border_color": "#000000",
            "radius": 0,
            "padding": 0,
            "zoom": 1.0,
            "focal_x": 0.5,
            "focal_y": 0.5,
            **values,
        }

    captions = element(id="captions", kind="CAPTIONS", x=60, y=1320, width=960, height=300, z_index=5)
    banner = element(
        id="banner",
        kind="BANNER",
        x=0,
        y=1680,
        width=1080,
        height=240,
        visible=preset == LayoutPreset.WEBCAM_TOP_SCREEN_MIDDLE_BANNER_BOTTOM,
        z_index=3,
    )
    if preset == LayoutPreset.WEBCAM_TOP_SCREEN_BOTTOM:
        elements = [
            element(id="webcam", kind="WEBCAM", x=0, y=0, width=1080, height=960),
            element(id="screen", kind="SCREEN", x=0, y=960, width=1080, height=960, fit="CONTAIN"),
            captions,
            banner,
        ]
    elif preset == LayoutPreset.WEBCAM_TOP_SCREEN_MIDDLE_BANNER_BOTTOM:
        elements = [
            element(id="webcam", kind="WEBCAM", x=0, y=0, width=1080, height=720),
            element(id="screen", kind="SCREEN", x=0, y=720, width=1080, height=960, fit="CONTAIN"),
            captions,
            banner,
        ]
    elif preset == LayoutPreset.SCREEN_FULLSCREEN_WEBCAM_OVERLAY:
        elements = [
            element(id="screen", kind="SCREEN", x=0, y=0, width=1080, height=1920),
            element(id="webcam", kind="WEBCAM", x=700, y=80, width=320, height=480, z_index=2),
            captions,
            banner,
        ]
    else:
        elements = [
            element(id="webcam", kind="WEBCAM", x=0, y=0, width=1080, height=1920),
            element(
                id="screen", kind="SCREEN", x=80, y=1300, width=920, height=520, z_index=2, fit="CONTAIN"
            ),
            captions,
            banner,
        ]
    return {
        "schema_version": 2,
        "canvas_width": 1080,
        "canvas_height": 1920,
        "preset": preset.value,
        "elements": elements,
        "captions": {
            "enabled": True,
            "font_family": "Arial",
            "font_size": 64,
            "color": "#FFFFFF",
            "weight": 700,
            "uppercase": False,
            "outline_width": 4,
            "outline_color": "#000000",
            "shadow": True,
            "italic": False,
            "box_color": None,
            "max_width": 960,
            "words_per_line": 5,
            "words_per_block": 10,
            "active_word_color": "#FFE600",
            "gap_tolerance_ms": 250,
            "min_display_ms": 300,
            "hold_ms": 150,
        },
        "banner": {
            "enabled": preset == LayoutPreset.WEBCAM_TOP_SCREEN_MIDDLE_BANNER_BOTTOM,
            "text": "Seu título aqui",
            "image_relative_path": None,
            "background_color": "#0F172A",
            "opacity": 0.9,
            "start_ms": 0,
            "end_ms": None,
        },
        "audio": {"mode": "TRANSCRIPT_DEFAULT", "tracks": []},
        "background_color": "#080C14",
    }


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


@pytest.mark.parametrize("preset", list(LayoutPreset))
def test_backend_preset_matches_independent_frontend_contract_snapshot(preset: LayoutPreset) -> None:
    assert preset_config(preset).model_dump(mode="json") == _frontend_preset_snapshot(preset)


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


def test_edit_config_migrates_v1_is_pinned_to_v2_and_banner_assets_stay_below_assets() -> None:
    payload = preset_config(LayoutPreset.WEBCAM_TOP_SCREEN_BOTTOM).model_dump(mode="json")
    payload["schema_version"] = 3
    with pytest.raises(ValidationError, match="schema_version"):
        EditConfig.model_validate(payload)

    payload["schema_version"] = 1
    payload.pop("audio")
    migrated = EditConfig.model_validate(payload)
    assert migrated.schema_version == 2
    assert migrated.audio.mode.value == "TRANSCRIPT_DEFAULT"
    assert migrated.audio.tracks == []

    with pytest.raises(ValidationError, match="assets"):
        BannerStyle(image_relative_path="previews/logo.png")


def test_audio_config_bounds_unique_tracks_and_enabled_count() -> None:
    track = AudioTrackConfig(media_id="screen", stream_index=1, gain_db=-12.5)
    assert track.gain_db == -12.5
    with pytest.raises(ValidationError, match="less than or equal to 12"):
        AudioTrackConfig(media_id="screen", stream_index=1, gain_db=12.1)
    with pytest.raises(ValidationError, match="unique"):
        AudioConfig(tracks=[track, track])
    with pytest.raises(ValidationError, match="at most 8 enabled"):
        AudioConfig(
            mode="CUSTOM",
            tracks=[AudioTrackConfig(media_id="screen", stream_index=index) for index in range(9)],
        )
    with pytest.raises(ValidationError, match="at most 32"):
        AudioConfig(
            mode="CUSTOM",
            tracks=[
                AudioTrackConfig(media_id="screen", stream_index=index, enabled=False) for index in range(33)
            ],
        )


def test_edit_config_canvas_is_pinned_and_element_ids_are_unique() -> None:
    payload = preset_config(LayoutPreset.WEBCAM_TOP_SCREEN_BOTTOM).model_dump(mode="json")
    payload["canvas_width"] = 720
    payload["canvas_height"] = 1280
    with pytest.raises(ValidationError, match="1080|1920"):
        EditConfig.model_validate(payload)

    payload = preset_config(LayoutPreset.WEBCAM_TOP_SCREEN_BOTTOM).model_dump(mode="json")
    payload["elements"][1]["id"] = payload["elements"][0]["id"]
    with pytest.raises(ValidationError, match="unique"):
        EditConfig.model_validate(payload)


@pytest.mark.parametrize("unsafe", [r"assets\logo.png", "C:/assets/logo.png", "/assets/logo.png", "logo.png"])
def test_banner_asset_requires_safe_posix_assets_path(unsafe: str) -> None:
    with pytest.raises(ValidationError, match="assets"):
        BannerStyle(image_relative_path=unsafe)
