from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class VideoStream(BaseModel):
    index: int
    codec_name: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    bitrate: int | None = None
    duration_ms: int | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class AudioStream(BaseModel):
    index: int
    codec_name: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    channel_layout: str | None = None
    bitrate: int | None = None
    duration_ms: int | None = None
    language: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class MediaProbe(BaseModel):
    schema_version: int = 1
    probe_version: str | None = None
    duration_ms: int
    format_name: str | None = None
    bitrate: int | None = None
    video_streams: list[VideoStream]
    audio_streams: list[AudioStream]
    metadata: dict[str, str] = Field(default_factory=dict)


class MediaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: str
    original_filename: str
    size_bytes: int
    sha256: str
    probe: MediaProbe
    content_url: str


def media_response(media: Any, api_prefix: str) -> MediaResponse:
    return MediaResponse(
        id=media.id,
        role=media.role.value,
        original_filename=media.original_filename,
        size_bytes=media.size_bytes,
        sha256=media.sha256,
        probe=MediaProbe.model_validate(media.probe_data),
        content_url=f"{api_prefix}/projects/{media.project_id}/media/{media.id}/content",
    )
