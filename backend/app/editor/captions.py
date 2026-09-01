from __future__ import annotations

from typing import Literal

from app.core.errors import AppError
from app.editor.schemas import CaptionCue
from app.transcription.schemas import TranscriptDocument

type CaptionTimingSource = Literal["WORDS", "WORDS_AND_SEGMENTS", "SEGMENTS"]

_MAX_CAPTION_CUES = 10_000


def extract_caption_cues(
    document: TranscriptDocument,
    clip_start_ms: int,
    clip_end_ms: int,
    offset_ms: int,
) -> tuple[list[CaptionCue], CaptionTimingSource]:
    """Extract v1 caption cues on the clip-local timeline.

    Word timing is preferred per segment. Segments without word timestamps keep
    the existing segment-level fallback, including for mixed transcripts.
    """
    cues: list[CaptionCue] = []
    has_words = any(segment.words for segment in document.segments)
    has_segment_fallback = False
    for segment in document.segments:
        if has_words and segment.words:
            for word in segment.words:
                start_ms = word.start_ms + offset_ms
                end_ms = word.end_ms + offset_ms
                if end_ms <= clip_start_ms or start_ms >= clip_end_ms:
                    continue
                local_start_ms = max(0, start_ms - clip_start_ms)
                local_end_ms = min(clip_end_ms, end_ms) - clip_start_ms
                cues.append(
                    CaptionCue(
                        start_ms=local_start_ms,
                        end_ms=local_end_ms,
                        text=word.text,
                        words=[
                            {
                                "start_ms": local_start_ms,
                                "end_ms": local_end_ms,
                                "text": word.text,
                            }
                        ],
                    )
                )
                _validate_cue_budget(cues)
        else:
            has_segment_fallback = True
            start_ms = segment.start_ms + offset_ms
            end_ms = segment.end_ms + offset_ms
            if end_ms <= clip_start_ms or start_ms >= clip_end_ms:
                continue
            cues.append(
                CaptionCue(
                    start_ms=max(0, start_ms - clip_start_ms),
                    end_ms=min(clip_end_ms, end_ms) - clip_start_ms,
                    text=segment.text,
                )
            )
            _validate_cue_budget(cues)

    timing_source: CaptionTimingSource = (
        "WORDS_AND_SEGMENTS" if has_words and has_segment_fallback else "WORDS" if has_words else "SEGMENTS"
    )
    return cues, timing_source


def _validate_cue_budget(cues: list[CaptionCue]) -> None:
    if len(cues) > _MAX_CAPTION_CUES:
        raise AppError(
            "CAPTION_LIMIT_EXCEEDED",
            "Transcript produces too many caption cues.",
            status_code=422,
        )
