from __future__ import annotations

import enum
import json
from datetime import datetime
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class LayoutPreset(enum.StrEnum):
    WEBCAM_TOP_SCREEN_BOTTOM = "WEBCAM_TOP_SCREEN_BOTTOM"
    WEBCAM_TOP_SCREEN_MIDDLE_BANNER_BOTTOM = "WEBCAM_TOP_SCREEN_MIDDLE_BANNER_BOTTOM"
    SCREEN_FULLSCREEN_WEBCAM_OVERLAY = "SCREEN_FULLSCREEN_WEBCAM_OVERLAY"
    WEBCAM_FULLSCREEN_SCREEN_PIP = "WEBCAM_FULLSCREEN_SCREEN_PIP"


class FitMode(enum.StrEnum):
    COVER = "COVER"
    CONTAIN = "CONTAIN"
    CROP = "CROP"


class EditorElement(BaseModel):
    model_config = {"extra": "forbid"}
    id: str = Field(min_length=1, max_length=80)
    kind: str = Field(pattern=r"^(SCREEN|WEBCAM|CAPTIONS|BANNER|BACKGROUND|TEXT|IMAGE)$")
    x: int = Field(ge=0, le=1080)
    y: int = Field(ge=0, le=1920)
    width: int = Field(gt=0, le=1080)
    height: int = Field(gt=0, le=1920)
    z_index: int = Field(default=0, ge=-100, le=100)
    visible: bool = True
    fit: FitMode = FitMode.COVER
    opacity: float = Field(default=1.0, ge=0, le=1)
    border_width: int = Field(default=0, ge=0, le=100)
    border_color: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$")
    radius: int = Field(default=0, ge=0, le=540)
    padding: int = Field(default=0, ge=0, le=200)
    zoom: float = Field(default=1.0, ge=1.0, le=3.0)
    focal_x: float = Field(default=0.5, ge=0.0, le=1.0)
    focal_y: float = Field(default=0.5, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def inside_canvas(self) -> EditorElement:
        if self.x + self.width > 1080 or self.y + self.height > 1920:
            raise ValueError("element must remain inside the 1080x1920 canvas")
        framing_is_default = self.zoom == 1.0 and self.focal_x == 0.5 and self.focal_y == 0.5
        if self.kind not in {"SCREEN", "WEBCAM"} and not framing_is_default:
            raise ValueError("zoom and focal point are supported only for SCREEN and WEBCAM elements")
        if self.fit == FitMode.CONTAIN and not framing_is_default:
            raise ValueError("zoom and focal point are not supported with CONTAIN fit")
        return self


class CaptionStyle(BaseModel):
    model_config = {"extra": "forbid"}
    enabled: bool = True
    font_family: str = Field(default="Arial", max_length=100)
    font_size: int = Field(default=64, ge=12, le=240)
    color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    weight: int = Field(default=700, ge=100, le=900)
    uppercase: bool = False
    outline_width: int = Field(default=4, ge=0, le=20)
    outline_color: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$")
    shadow: bool = True
    italic: bool = False
    box_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    max_width: int = Field(default=960, ge=100, le=1080)
    words_per_line: int = Field(default=5, ge=1, le=20)
    words_per_block: int = Field(default=10, ge=1, le=50)
    active_word_color: str | None = Field(default="#FFE600", pattern=r"^#[0-9A-Fa-f]{6}$")
    gap_tolerance_ms: int = Field(default=250, ge=0, le=1000)
    min_display_ms: int = Field(default=300, ge=0, le=2000)
    hold_ms: int = Field(default=150, ge=0, le=2000)


class BannerStyle(BaseModel):
    model_config = {"extra": "forbid"}
    enabled: bool = False
    text: str = Field(default="", max_length=500)
    image_relative_path: str | None = Field(default=None, max_length=500)
    background_color: str = Field(default="#111111", pattern=r"^#[0-9A-Fa-f]{6}$")
    opacity: float = Field(default=0.9, ge=0, le=1)
    start_ms: int = Field(default=0, ge=0)
    end_ms: int | None = Field(default=None, ge=0)

    @field_validator("image_relative_path")
    @classmethod
    def safe_asset_reference(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "\\" in value or ":" in value:
            raise ValueError("image_relative_path must use a POSIX assets/ path")
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or not value.strip() or not path.parts or path.parts[0] != "assets":
            raise ValueError("image_relative_path must be a safe path below assets/")
        return value

    @model_validator(mode="after")
    def valid_interval(self) -> BannerStyle:
        if self.end_ms is not None and self.end_ms <= self.start_ms:
            raise ValueError("banner end_ms must be greater than start_ms")
        return self


class AudioConfigMode(enum.StrEnum):
    TRANSCRIPT_DEFAULT = "TRANSCRIPT_DEFAULT"
    CUSTOM = "CUSTOM"


class AudioTrackConfig(BaseModel):
    model_config = {"extra": "forbid"}

    media_id: str = Field(min_length=1, max_length=100)
    stream_index: int = Field(ge=0)
    enabled: bool = True
    gain_db: float = Field(default=0.0, ge=-60.0, le=12.0)


class AudioConfig(BaseModel):
    model_config = {"extra": "forbid"}

    mode: AudioConfigMode = AudioConfigMode.TRANSCRIPT_DEFAULT
    tracks: list[AudioTrackConfig] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def valid_track_selection(self) -> AudioConfig:
        identities = [(track.media_id, track.stream_index) for track in self.tracks]
        if len(identities) != len(set(identities)):
            raise ValueError("audio tracks must be unique by media_id and stream_index")
        if sum(track.enabled for track in self.tracks) > 8:
            raise ValueError("audio config supports at most 8 enabled tracks")
        return self


class EditConfig(BaseModel):
    model_config = {"extra": "forbid"}
    schema_version: Literal[2] = 2
    canvas_width: Literal[1080] = 1080
    canvas_height: Literal[1920] = 1920
    preset: LayoutPreset
    elements: list[EditorElement] = Field(min_length=2, max_length=30)
    captions: CaptionStyle = Field(default_factory=CaptionStyle)
    banner: BannerStyle = Field(default_factory=BannerStyle)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    background_color: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$")

    @model_validator(mode="before")
    @classmethod
    def migrate_v1_payload(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        version = value.get("schema_version", 1)
        if version != 1:
            return value
        migrated = dict(value)
        migrated["schema_version"] = 2
        migrated.setdefault("audio", {"mode": AudioConfigMode.TRANSCRIPT_DEFAULT.value, "tracks": []})
        return migrated

    @model_validator(mode="after")
    def unique_element_ids(self) -> EditConfig:
        ids = [element.id for element in self.elements]
        if len(ids) != len(set(ids)):
            raise ValueError("editor element ids must be unique")
        return self


class EditConfigResponse(BaseModel):
    id: str
    project_id: str
    candidate_id: str
    schema_version: int
    config: EditConfig
    created_at: datetime
    updated_at: datetime


class CaptionCue(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(max_length=20_000)
    words: list[dict[str, int | str]] | None = Field(default=None, max_length=2_000)

    @field_validator("words")
    @classmethod
    def bounded_word_text(cls, value: list[dict[str, int | str]] | None) -> list[dict[str, int | str]] | None:
        if value is not None and any(len(str(word.get("text", ""))) > 1_000 for word in value):
            raise ValueError("caption word text must not exceed 1000 characters")
        return value


class CaptionCueList(BaseModel):
    items: list[CaptionCue]
    timing_source: Literal["WORDS", "WORDS_AND_SEGMENTS", "SEGMENTS"]


def preset_config(preset: LayoutPreset) -> EditConfig:
    captions = EditorElement(id="captions", kind="CAPTIONS", x=60, y=1320, width=960, height=300, z_index=5)
    banner = EditorElement(
        id="banner", kind="BANNER", x=0, y=1680, width=1080, height=240, visible=False, z_index=3
    )
    if preset == LayoutPreset.WEBCAM_TOP_SCREEN_BOTTOM:
        elements = [
            EditorElement(id="webcam", kind="WEBCAM", x=0, y=0, width=1080, height=960),
            EditorElement(
                id="screen", kind="SCREEN", x=0, y=960, width=1080, height=960, fit=FitMode.CONTAIN
            ),
            captions,
            banner,
        ]
    elif preset == LayoutPreset.WEBCAM_TOP_SCREEN_MIDDLE_BANNER_BOTTOM:
        banner.visible = True
        elements = [
            EditorElement(id="webcam", kind="WEBCAM", x=0, y=0, width=1080, height=720),
            EditorElement(
                id="screen", kind="SCREEN", x=0, y=720, width=1080, height=960, fit=FitMode.CONTAIN
            ),
            captions,
            banner,
        ]
    elif preset == LayoutPreset.SCREEN_FULLSCREEN_WEBCAM_OVERLAY:
        elements = [
            EditorElement(id="screen", kind="SCREEN", x=0, y=0, width=1080, height=1920),
            EditorElement(id="webcam", kind="WEBCAM", x=700, y=80, width=320, height=480, z_index=2),
            captions,
            banner,
        ]
    else:
        elements = [
            EditorElement(id="webcam", kind="WEBCAM", x=0, y=0, width=1080, height=1920),
            EditorElement(
                id="screen",
                kind="SCREEN",
                x=80,
                y=1300,
                width=920,
                height=520,
                z_index=2,
                fit=FitMode.CONTAIN,
            ),
            captions,
            banner,
        ]
    return EditConfig(
        preset=preset,
        elements=elements,
        captions=CaptionStyle(),
        audio=AudioConfig(),
        banner=BannerStyle(
            enabled=banner.visible,
            text="Seu título aqui",
            image_relative_path=None,
            background_color="#0F172A",
            opacity=0.9,
            start_ms=0,
            end_ms=None,
        ),
        background_color="#080C14",
    )


def normalize_legacy_edit_config(config: EditConfig) -> tuple[EditConfig, bool]:
    """Migrate only literal Roadmap 01 presets with unsupported decorative geometry."""
    legacy_variants = (
        (LayoutPreset.SCREEN_FULLSCREEN_WEBCAM_OVERLAY, "WEBCAM"),
        (LayoutPreset.WEBCAM_FULLSCREEN_SCREEN_PIP, "SCREEN"),
    )
    serialized = _canonical_edit_config(config)
    for preset, video_kind in legacy_variants:
        legacy = _roadmap01_legacy_preset(preset)
        if serialized == _canonical_edit_config(legacy):
            normalized = config.model_copy(deep=True)
            next(item for item in normalized.elements if item.kind == video_kind).radius = 0
            # Roadmap 01 exposed these generic element fields, but neither captions nor
            # banner used them. They are neutralized only after an exact preset match.
            for element in normalized.elements:
                if element.kind in {"CAPTIONS", "BANNER"}:
                    element.radius = 0
                    element.padding = 0
            return normalized, True
    return config, False


def _canonical_edit_config(config: EditConfig) -> str:
    return json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def _roadmap01_legacy_preset(preset: LayoutPreset) -> EditConfig:
    """Build an independent snapshot of the two literal frontend Roadmap 01 payloads."""
    defaults: dict[str, object] = {
        "z_index": 0,
        "visible": True,
        "fit": "COVER",
        "opacity": 1,
        "border_width": 0,
        "border_color": "#000000",
        "radius": 0,
        "padding": 0,
    }

    def element(**values: object) -> dict[str, object]:
        return {**defaults, **values}

    captions = element(
        id="captions",
        kind="CAPTIONS",
        x=60,
        y=1390,
        width=960,
        height=300,
        z_index=20,
        padding=24,
        radius=16,
    )
    banner = element(
        id="banner",
        kind="BANNER",
        x=0,
        y=1680,
        width=1080,
        height=240,
        visible=False,
        z_index=10,
        opacity=0.95,
        radius=24,
        padding=28,
    )
    if preset == LayoutPreset.SCREEN_FULLSCREEN_WEBCAM_OVERLAY:
        elements = [
            element(id="screen", kind="SCREEN", x=0, y=0, width=1080, height=1920),
            element(
                id="webcam",
                kind="WEBCAM",
                x=700,
                y=80,
                width=320,
                height=480,
                z_index=2,
                radius=36,
                border_width=6,
                border_color="#FFFFFF",
            ),
            captions,
            banner,
        ]
    elif preset == LayoutPreset.WEBCAM_FULLSCREEN_SCREEN_PIP:
        elements = [
            element(id="webcam", kind="WEBCAM", x=0, y=0, width=1080, height=1920),
            element(
                id="screen",
                kind="SCREEN",
                x=80,
                y=1300,
                width=920,
                height=520,
                z_index=2,
                fit="CONTAIN",
                radius=28,
                border_width=6,
                border_color="#FFFFFF",
            ),
            captions,
            banner,
        ]
    else:  # pragma: no cover - private caller is intentionally exhaustive
        raise ValueError(f"No Roadmap 01 legacy snapshot for {preset}")
    return EditConfig.model_validate(
        {
            "schema_version": 1,
            "canvas_width": 1080,
            "canvas_height": 1920,
            "preset": preset.value,
            "elements": elements,
            "captions": {
                "enabled": True,
                "font_family": "Inter",
                "font_size": 64,
                "color": "#FFFFFF",
                "weight": 800,
                "uppercase": False,
                "outline_width": 4,
                "shadow": True,
                "box_color": "#000000",
                "max_width": 960,
                "words_per_line": 5,
                "words_per_block": 10,
                "active_word_color": "#38BDF8",
            },
            "banner": {
                "enabled": False,
                "text": "Seu título aqui",
                "image_relative_path": None,
                "background_color": "#0F172A",
                "opacity": 0.9,
                "start_ms": 0,
                "end_ms": None,
            },
            "background_color": "#080C14",
        }
    )
