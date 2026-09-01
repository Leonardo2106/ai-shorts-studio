from __future__ import annotations

import enum
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.editor.schemas import CaptionCue, EditConfig, FitMode


class RenderKind(enum.StrEnum):
    PREVIEW = "PREVIEW"
    FINAL = "FINAL"


class RenderQuality(enum.StrEnum):
    FAST = "FAST"
    BALANCED = "BALANCED"
    HIGH = "HIGH"


class RenderInputRole(enum.StrEnum):
    SCREEN = "SCREEN"
    WEBCAM = "WEBCAM"


class RenderLayerKind(enum.StrEnum):
    SCREEN = "SCREEN"
    WEBCAM = "WEBCAM"
    CAPTIONS = "CAPTIONS"
    BANNER = "BANNER"


class AudioMode(enum.StrEnum):
    SILENT = "SILENT"
    SINGLE_TRACK = "SINGLE_TRACK"
    MIXED_TRACKS = "MIXED_TRACKS"
    PRIMARY_TRANSCRIPT_STREAM = "SINGLE_TRACK"


class OutputContainer(enum.StrEnum):
    MP4 = "MP4"


class NormalizedRect(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class ResolvedMediaInput(BaseModel):
    """Media facts resolved from trusted Project/Media metadata by the service layer."""

    model_config = {"extra": "forbid", "frozen": True}

    media_id: str = Field(min_length=1, max_length=100)
    role: RenderInputRole
    path: Path
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    duration_ms: int = Field(gt=0)
    video_stream_indexes: list[int] = Field(min_length=1)
    audio_stream_indexes: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_stream_indexes(self) -> ResolvedMediaInput:
        if len(self.video_stream_indexes) != len(set(self.video_stream_indexes)):
            raise ValueError("video stream indexes must be unique")
        if len(self.audio_stream_indexes) != len(set(self.audio_stream_indexes)):
            raise ValueError("audio stream indexes must be unique")
        return self


class ResolvedTranscriptSource(BaseModel):
    """Immutable transcript identity and its explicitly selected source audio stream."""

    model_config = {"extra": "forbid", "frozen": True}

    transcript_id: str = Field(min_length=1, max_length=100)
    cache_key: str = Field(min_length=1, max_length=128)
    media_id: str = Field(min_length=1, max_length=100)
    audio_stream_index: int = Field(ge=0)
    caption_cues: list[CaptionCue] = Field(default_factory=list, max_length=10_000)
    timing_source: Literal["WORDS", "WORDS_AND_SEGMENTS", "SEGMENTS"]


class ResolvedBannerAsset(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}

    relative_path: str = Field(min_length=1, max_length=500)
    path: Path
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")

    @model_validator(mode="after")
    def safe_project_asset(self) -> ResolvedBannerAsset:
        relative = PurePosixPath(self.relative_path)
        if (
            "\\" in self.relative_path
            or ":" in self.relative_path
            or relative.is_absolute()
            or ".." in relative.parts
            or not relative.parts
            or relative.parts[0] != "assets"
        ):
            raise ValueError("banner asset must remain below project assets/")
        return self


class ResolvedRenderContext(BaseModel):
    """Trusted, FFmpeg-free input assembled from existing repository contracts."""

    model_config = {"extra": "forbid", "frozen": True}

    project_id: str = Field(min_length=1, max_length=100)
    candidate_id: str = Field(min_length=1, max_length=100)
    edit_config_id: str = Field(min_length=1, max_length=100)
    clip_start_ms: int = Field(ge=0)
    clip_end_ms: int = Field(gt=0)
    webcam_offset_ms: int = Field(ge=-86_400_000, le=86_400_000)
    edit_config: EditConfig
    media: list[ResolvedMediaInput] = Field(min_length=1, max_length=2)
    transcript: ResolvedTranscriptSource | None = None
    banner_asset: ResolvedBannerAsset | None = None

    @model_validator(mode="after")
    def valid_context(self) -> ResolvedRenderContext:
        if self.clip_end_ms <= self.clip_start_ms:
            raise ValueError("clip end must be greater than clip start")
        roles = [item.role for item in self.media]
        if len(roles) != len(set(roles)):
            raise ValueError("render context can contain only one input per media role")
        media_ids = [item.media_id for item in self.media]
        if len(media_ids) != len(set(media_ids)):
            raise ValueError("render context media ids must be unique")
        return self


class ClipPlan(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}

    timeline_start_ms: int = Field(ge=0)
    timeline_end_ms: int = Field(gt=0)
    duration_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def consistent_duration(self) -> ClipPlan:
        if self.timeline_end_ms - self.timeline_start_ms != self.duration_ms:
            raise ValueError("clip duration must match its timeline interval")
        return self


class CanvasPlan(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}

    logical_width: Literal[1080] = 1080
    logical_height: Literal[1920] = 1920
    output_width: int = Field(gt=0, le=1080)
    output_height: int = Field(gt=0, le=1920)
    fps: int = Field(ge=1, le=60)

    @model_validator(mode="after")
    def vertical_canvas(self) -> CanvasPlan:
        if self.output_width * 16 != self.output_height * 9:
            raise ValueError("render output must preserve the 9:16 aspect ratio")
        if self.output_width % 2 or self.output_height % 2:
            raise ValueError("render output dimensions must be even")
        return self


class InputPlan(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}

    input_index: int = Field(ge=0)
    media_id: str
    role: RenderInputRole
    path: Path
    sha256: str
    video_stream_index: int = Field(ge=0)
    audio_stream_indexes: list[int]
    timeline_offset_ms: int
    source_start_ms: int = Field(ge=0)
    source_end_ms: int = Field(gt=0)
    source_duration_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def valid_source_interval(self) -> InputPlan:
        if self.source_end_ms <= self.source_start_ms or self.source_end_ms > self.source_duration_ms:
            raise ValueError("input trim must remain inside source duration")
        return self


class LayerPlan(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}

    id: str
    kind: RenderLayerKind
    rect: NormalizedRect
    z_index: int = Field(ge=-100, le=100)
    fit: FitMode
    opacity: float = Field(ge=0, le=1)
    border_width: int = Field(ge=0)
    border_color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    radius: int = Field(ge=0)
    padding: int = Field(ge=0)
    zoom: float = Field(default=1.0, ge=1.0, le=3.0)
    focal_x: float = Field(default=0.5, ge=0.0, le=1.0)
    focal_y: float = Field(default=0.5, ge=0.0, le=1.0)
    input_index: int | None = Field(default=None, ge=0)


class CaptionStylePlan(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}

    font_family: str = Field(min_length=1, max_length=100)
    font_size: int = Field(ge=1)
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    weight: int = Field(ge=100, le=900)
    uppercase: bool
    outline_width: int = Field(ge=0)
    outline_color: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$")
    shadow: bool
    italic: bool = False
    box_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    max_width: int = Field(gt=0)
    words_per_line: int = Field(ge=1, le=20)
    words_per_block: int = Field(ge=1, le=50)
    active_word_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    gap_tolerance_ms: int = Field(default=250, ge=0, le=1000)
    min_display_ms: int = Field(default=300, ge=0, le=2000)
    hold_ms: int = Field(default=150, ge=0, le=2000)


class CaptionPlan(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}

    enabled: bool
    layer_id: str | None = None
    rect: NormalizedRect | None = None
    z_index: int | None = None
    opacity: float = Field(default=1.0, ge=0, le=1)
    style: CaptionStylePlan | None = None
    cues: list[CaptionCue] = Field(default_factory=list, max_length=10_000)
    timing_source: Literal["WORDS", "WORDS_AND_SEGMENTS", "SEGMENTS"] | None = None
    transcript_id: str | None = None
    transcript_cache_key: str | None = None


class BannerPlan(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}

    enabled: bool
    layer_id: str | None = None
    rect: NormalizedRect | None = None
    z_index: int | None = None
    text: str = Field(default="", max_length=500)
    background_color: str = Field(default="#111111", pattern=r"^#[0-9A-Fa-f]{6}$")
    opacity: float = Field(default=0.9, ge=0, le=1)
    content_opacity: float = Field(default=1.0, ge=0, le=1)
    start_ms: int = Field(default=0, ge=0)
    end_ms: int | None = Field(default=None, gt=0)
    asset_relative_path: str | None = None
    asset_path: Path | None = None
    asset_sha256: str | None = None


class AudioSourcePlan(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}

    input_index: int = Field(ge=0)
    media_id: str
    stream_index: int = Field(ge=0)
    source_start_ms: int = Field(ge=0)
    source_end_ms: int = Field(gt=0)
    timeline_offset_ms: int
    gain_db: float = Field(default=0.0, ge=-60.0, le=12.0)


class AudioPlan(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}

    mode: AudioMode
    sources: list[AudioSourcePlan] = Field(default_factory=list, max_length=8)
    sample_rate: Literal[48000] = 48000
    channels: Literal[2] = 2
    limiter: Literal[True] = True

    @model_validator(mode="before")
    @classmethod
    def migrate_single_source_contract(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        if migrated.get("mode") == "PRIMARY_TRANSCRIPT_STREAM":
            migrated["mode"] = AudioMode.SINGLE_TRACK.value
        source = migrated.pop("source", None)
        if source is not None and "sources" not in migrated:
            migrated["sources"] = [source]
        return migrated

    @model_validator(mode="after")
    def sources_match_mode(self) -> AudioPlan:
        expected = {
            AudioMode.SILENT: (0, 0),
            AudioMode.SINGLE_TRACK: (1, 1),
            AudioMode.MIXED_TRACKS: (2, 8),
        }
        minimum, maximum = expected[self.mode]
        if not minimum <= len(self.sources) <= maximum:
            raise ValueError("audio source count does not match the selected render audio mode")
        identities = [(source.media_id, source.stream_index) for source in self.sources]
        if len(identities) != len(set(identities)):
            raise ValueError("render audio sources must be unique")
        return self

    @property
    def source(self) -> AudioSourcePlan | None:
        """Compatibility accessor for callers that consumed the v1 single-source plan."""
        return self.sources[0] if len(self.sources) == 1 else None


class OutputPlan(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}

    kind: RenderKind
    quality: RenderQuality
    container: Literal[OutputContainer.MP4] = OutputContainer.MP4
    relative_directory: Literal["previews", "renders"]
    extension: Literal[".mp4"] = ".mp4"
    video_codec: Literal["libx264"] = "libx264"
    audio_codec: Literal["aac"] = "aac"
    pixel_format: Literal["yuv420p"] = "yuv420p"
    encoder_preset: Literal["ultrafast", "veryfast", "medium", "slow"]
    crf: int = Field(ge=0, le=51)
    faststart: Literal[True] = True
    overwrite: Literal[False] = False


class RenderPlan(BaseModel):
    """Normalized trusted plan. It is inspectable without building or executing FFmpeg."""

    model_config = {"extra": "forbid", "frozen": True}

    schema_version: Literal[1] = 1
    project_id: str
    candidate_id: str
    edit_config_id: str
    kind: RenderKind
    quality: RenderQuality
    clip: ClipPlan
    canvas: CanvasPlan
    background_color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    inputs: list[InputPlan] = Field(min_length=1, max_length=2)
    layers: list[LayerPlan] = Field(min_length=1, max_length=30)
    captions: CaptionPlan
    banner: BannerPlan
    audio: AudioPlan
    output: OutputPlan
    edit_config_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    dependency_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    cacheable: bool


class RenderRequest(BaseModel):
    model_config = {"extra": "forbid"}

    quality: RenderQuality = RenderQuality.BALANCED


class RenderArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    candidate_id: str
    edit_config_id: str
    job_id: str
    kind: RenderKind
    quality: RenderQuality
    dependency_fingerprint: str
    size_bytes: int
    duration_ms: int
    width: int
    height: int
    has_audio: bool
    content_url: str
    created_at: datetime


class RenderArtifactList(BaseModel):
    items: list[RenderArtifactResponse]
