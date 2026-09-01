from __future__ import annotations

import pytest

from app.editor.captions import extract_caption_cues
from app.transcription.schemas import TranscriptDocument


def _document(*, words: list[dict[str, int | str]] | None) -> TranscriptDocument:
    return TranscriptDocument.model_validate(
        {
            "id": "transcript",
            "project_id": "project",
            "media_id": "media",
            "source": "SCREEN",
            "audio_stream_index": 0,
            "language": "pt",
            "duration_ms": 1_000,
            "engine": "fixture",
            "model": "fixture",
            "segments": [
                {
                    "start_ms": 100,
                    "end_ms": 300,
                    "text": "primeira segunda",
                    "words": words,
                }
            ],
        }
    )


@pytest.mark.parametrize(
    ("words", "timing_source", "expected"),
    [
        (
            [
                {"start_ms": 100, "end_ms": 180, "text": "primeira"},
                {"start_ms": 180, "end_ms": 300, "text": "segunda"},
            ],
            "WORDS",
            [("primeira", 100, 180, True), ("segunda", 180, 300, True)],
        ),
        (None, "SEGMENTS", [("primeira segunda", 100, 300, False)]),
    ],
)
def test_caption_extraction_reports_pure_word_or_segment_fallback_modes(
    words: list[dict[str, int | str]] | None,
    timing_source: str,
    expected: list[tuple[str, int, int, bool]],
) -> None:
    cues, actual_timing_source = extract_caption_cues(_document(words=words), 0, 1_000, 0)

    assert actual_timing_source == timing_source
    assert [(cue.text, cue.start_ms, cue.end_ms, cue.words is not None) for cue in cues] == expected
