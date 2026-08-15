from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.errors import AppError
from app.media.probe import FFprobeService, parse_ffprobe

FIXTURES = Path(__file__).parent / "fixtures"


def _payload(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_parse_ffprobe_without_audio_is_valid_video() -> None:
    probe = parse_ffprobe(_payload("ffprobe_no_audio.json"))

    assert probe.duration_ms == 2500
    assert probe.audio_streams == []
    assert probe.video_streams[0].fps == pytest.approx(29.970029)


def test_parse_ffprobe_preserves_all_audio_tracks() -> None:
    probe = parse_ffprobe(_payload("ffprobe_multi_audio.json"), probe_version="ffprobe fixture")

    assert probe.probe_version == "ffprobe fixture"
    assert [track.index for track in probe.audio_streams] == [1, 2]
    assert [track.language for track in probe.audio_streams] == ["eng", "por"]
    assert probe.audio_streams[1].channels == 1
    assert probe.video_streams[0].fps == 60.0


def test_parse_ffprobe_rejects_payload_without_video_stream() -> None:
    with pytest.raises(AppError) as error:
        parse_ffprobe({"streams": [{"index": 1, "codec_type": "audio"}]})

    assert error.value.code == "INVALID_MEDIA"


def test_ffprobe_uses_argument_array_for_invalid_media(tmp_path: Path) -> None:
    media = tmp_path / "untrusted name; still data.mp4"
    media.write_bytes(b"invalid")
    service = FFprobeService("ffprobe", 1)
    with (
        patch.object(service, "available_path", return_value="ffprobe"),
        patch.object(service, "_run_capped", return_value=(1, "")) as run,
        pytest.raises(AppError) as error,
    ):
        service.inspect(media)

    assert error.value.code == "INVALID_MEDIA"
    args = run.call_args.args[0]
    assert args[-1] == str(media)
