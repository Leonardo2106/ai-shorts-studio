from __future__ import annotations

import enum
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

    @model_validator(mode="after")
    def inside_canvas(self) -> EditorElement:
        if self.x + self.width > 1080 or self.y + self.height > 1920:
            raise ValueError("element must remain inside the 1080x1920 canvas")
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
    shadow: bool = True
    box_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    max_width: int = Field(default=960, ge=100, le=1080)
    words_per_line: int = Field(default=5, ge=1, le=20)
    words_per_block: int = Field(default=10, ge=1, le=50)
    active_word_color: str | None = Field(default="#FFE600", pattern=r"^#[0-9A-Fa-f]{6}$")


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


class EditConfig(BaseModel):
    model_config = {"extra": "forbid"}
    schema_version: Literal[1] = 1
    canvas_width: int = Field(default=1080, frozen=True)
    canvas_height: int = Field(default=1920, frozen=True)
    preset: LayoutPreset
    elements: list[EditorElement] = Field(min_length=2, max_length=30)
    captions: CaptionStyle = Field(default_factory=CaptionStyle)
    banner: BannerStyle = Field(default_factory=BannerStyle)
    background_color: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$")


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
    text: str
    words: list[dict[str, int | str]] | None = None


class CaptionCueList(BaseModel):
    items: list[CaptionCue]
    timing_source: Literal["WORDS", "WORDS_AND_SEGMENTS", "SEGMENTS"]


def preset_config(preset: LayoutPreset) -> EditConfig:
    screen = EditorElement(id="screen", kind="SCREEN", x=0, y=960, width=1080, height=960)
    webcam = EditorElement(id="webcam", kind="WEBCAM", x=0, y=0, width=1080, height=960)
    banner = EditorElement(id="banner", kind="BANNER", x=0, y=1680, width=1080, height=240, visible=False, z_index=3)
    if preset == LayoutPreset.WEBCAM_TOP_SCREEN_MIDDLE_BANNER_BOTTOM:
        webcam.height = 720
        screen.y, screen.height = 720, 960
        banner.visible = True
    elif preset == LayoutPreset.SCREEN_FULLSCREEN_WEBCAM_OVERLAY:
        screen.y, screen.height = 0, 1920
        webcam.x, webcam.y, webcam.width, webcam.height, webcam.z_index = 700, 80, 320, 480, 2
    elif preset == LayoutPreset.WEBCAM_FULLSCREEN_SCREEN_PIP:
        webcam.height = 1920
        screen.x, screen.y, screen.width, screen.height, screen.z_index = 80, 1300, 920, 520, 2
    elements = [
        webcam,
        screen,
        EditorElement(id="captions", kind="CAPTIONS", x=60, y=1320, width=960, height=300, z_index=5),
        banner,
    ]
    return EditConfig(preset=preset, elements=elements, banner=BannerStyle(enabled=banner.visible))
