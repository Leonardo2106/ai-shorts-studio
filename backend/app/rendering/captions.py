from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import AppError
from app.editor.schemas import CaptionCue
from app.rendering.schemas import CaptionStylePlan, RenderPlan

_MAX_ASS_EVENTS = 10_000
_MAX_ASS_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class _CaptionEvent:
    start_ms: int
    end_ms: int
    text: str


def build_ass_document(plan: RenderPlan) -> str | None:
    """Build trusted ASS from normalized caption/banner data only."""
    if not plan.captions.enabled and not (plan.banner.enabled and plan.banner.text):
        return None
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {plan.canvas.output_width}",
        f"PlayResY: {plan.canvas.output_height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
    ]
    if plan.captions.enabled and plan.captions.style is not None and plan.captions.rect is not None:
        style = plan.captions.style
        rect = plan.captions.rect
        text_alpha = _ass_alpha(plan.captions.opacity)
        box_opacity = plan.captions.opacity * ((255 - 80) / 255) if style.box_color else plan.captions.opacity
        box = _ass_color(style.box_color or "#000000", alpha=_ass_alpha(box_opacity))
        lines.append(
            "Style: Captions,"
            f"{style.font_family},{style.font_size},"
            f"{_ass_color(style.active_word_color or style.color, alpha=text_alpha)},"
            f"{_ass_color(style.color, alpha=text_alpha)},"
            f"{_ass_color(style.outline_color, alpha=text_alpha)},"
            f"{box},{-1 if style.weight >= 600 else 0},{-1 if style.italic else 0},0,0,"
            f"100,100,0,0,{3 if style.box_color else 1},{style.outline_width},{2 if style.shadow else 0},"
            f"2,{rect.x},{plan.canvas.output_width - rect.x - rect.width},"
            f"{plan.canvas.output_height - rect.y - rect.height},1"
        )
    if plan.banner.enabled and plan.banner.rect is not None and plan.banner.text:
        rect = plan.banner.rect
        banner_alpha = _ass_alpha(plan.banner.content_opacity)
        lines.append(
            "Style: Banner,Arial,"
            f"{max(12, round(rect.height * 0.28))},{_ass_color('#FFFFFF', alpha=banner_alpha)},"
            f"{_ass_color('#FFFFFF', alpha=banner_alpha)},{_ass_color('#000000', alpha=banner_alpha)},"
            f"{_ass_color('#000000', alpha=banner_alpha)},"
            f"-1,0,0,0,100,100,0,0,1,1,1,5,{rect.x},{plan.canvas.output_width - rect.x - rect.width},0,1"
        )
    lines.extend(
        [
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
    )
    if plan.captions.enabled and plan.captions.style is not None and plan.captions.rect is not None:
        for event in _caption_events(
            plan.captions.cues,
            plan.captions.style,
            plan.captions.opacity,
            plan.clip.duration_ms,
        ):
            if event.text:
                x = plan.captions.rect.x + plan.captions.rect.width // 2
                y = plan.captions.rect.y + plan.captions.rect.height // 2
                lines.append(
                    f"Dialogue: {(plan.captions.z_index or 0) + 100},{_ass_time(event.start_ms)},"
                    f"{_ass_time(event.end_ms)},Captions,,0,0,0,,"
                    f"{{\\an5\\pos({x},{y})}}{event.text}"
                )
    if plan.banner.enabled and plan.banner.rect is not None and plan.banner.text:
        rect = plan.banner.rect
        x, y = rect.x + rect.width // 2, rect.y + rect.height // 2
        lines.append(
            f"Dialogue: {(plan.banner.z_index or 0) + 100},{_ass_time(plan.banner.start_ms)},"
            f"{_ass_time(plan.banner.end_ms or plan.clip.duration_ms)},"
            f"Banner,,0,0,0,,{{\\an5\\pos({x},{y})}}{_ass_text(plan.banner.text)}"
        )
    document = "\n".join(lines) + "\n"
    size_bytes = len(document.encode("utf-8"))
    if size_bytes > _MAX_ASS_BYTES:
        raise _ass_budget_error(size_bytes=size_bytes)
    return document


def _caption_events(
    cues: list[CaptionCue],
    style: CaptionStylePlan,
    opacity: float,
    clip_duration_ms: int,
) -> list[_CaptionEvent]:
    if len(cues) > _MAX_ASS_EVENTS:
        raise _ass_budget_error(event_count=len(cues))
    ordered_cues = [
        cue
        for _index, cue in sorted(
            enumerate(cues),
            key=lambda item: (item[1].start_ms, item[1].end_ms, item[0]),
        )
    ]
    estimated_bytes = _estimated_ass_event_bytes(ordered_cues, style)
    if estimated_bytes > _MAX_ASS_BYTES:
        raise _ass_budget_error(size_bytes=estimated_bytes)
    events: list[_CaptionEvent] = []
    word_run: list[CaptionCue] = []
    for cue in ordered_cues:
        if _is_single_word_cue(cue):
            word_run.append(cue)
            continue
        if word_run:
            events.extend(_active_word_events(word_run, style, opacity))
            word_run = []
        events.append(_CaptionEvent(cue.start_ms, cue.end_ms, _cue_text(cue, style)))
    if word_run:
        events.extend(_active_word_events(word_run, style, opacity))
    return _normalize_event_times(events, style, clip_duration_ms)


def _active_word_events(
    cues: list[CaptionCue], style: CaptionStylePlan, opacity: float
) -> list[_CaptionEvent]:
    events: list[_CaptionEvent] = []
    max_chars = _max_chars_per_line(style)
    for block in _word_blocks(cues, style):
        for active_index, active_cue in enumerate(block):
            parts: list[str] = []
            line_words = 0
            line_chars = 0
            for word_index, cue in enumerate(block):
                value = cue.text.upper() if style.uppercase else cue.text
                if line_words and (line_words >= style.words_per_line or line_chars + len(value) + 1 > max_chars):
                    parts.append(r"\N")
                    line_words = 0
                    line_chars = 0
                color = style.active_word_color if word_index == active_index else style.color
                parts.append(
                    f"{{\\1c{_ass_color(color or style.color, alpha=_ass_alpha(opacity))}}}{_ass_text(value)}"
                )
                line_words += 1
                line_chars += len(value) + (1 if line_chars else 0)
            events.append(_CaptionEvent(active_cue.start_ms, active_cue.end_ms, " ".join(parts)))
    return events


def _estimated_ass_event_bytes(cues: list[CaptionCue], style: CaptionStylePlan) -> int:
    def escaped_bound(value: object) -> int:
        return len(str(value).encode("utf-8")) * 3 + 64

    total = 0
    index = 0
    while index < len(cues):
        if _is_single_word_cue(cues[index]):
            run_end = index
            while run_end < len(cues) and _is_single_word_cue(cues[run_end]):
                run_end += 1
            run = cues[index:run_end]
            for block in _word_blocks(run, style):
                rendered_block = sum(escaped_bound(cue.text) for cue in block)
                total += len(block) * (rendered_block + 256)
            index = run_end
        else:
            cue = cues[index]
            total += escaped_bound(cue.text) + 256
            if cue.words:
                total += sum(escaped_bound(word.get("text", "")) + 24 for word in cue.words)
            index += 1
        if total > _MAX_ASS_BYTES:
            return total
    return total


def _ass_budget_error(*, event_count: int | None = None, size_bytes: int | None = None) -> AppError:
    details: dict[str, int] = {
        "max_events": _MAX_ASS_EVENTS,
        "max_bytes": _MAX_ASS_BYTES,
    }
    if event_count is not None:
        details["event_count"] = event_count
    if size_bytes is not None:
        details["estimated_bytes"] = size_bytes
    return AppError(
        "CAPTION_ASS_LIMIT_EXCEEDED",
        "Captions are too large to render safely. Shorten the transcript or use smaller caption blocks.",
        status_code=422,
        details=details,
    )


def _normalize_event_times(
    events: list[_CaptionEvent],
    style: CaptionStylePlan,
    clip_duration_ms: int,
) -> list[_CaptionEvent]:
    ordered = sorted(events, key=lambda item: (item.start_ms, item.end_ms))
    normalized: list[_CaptionEvent] = []
    for index, event in enumerate(ordered):
        start = max(0, min(event.start_ms, clip_duration_ms))
        if start >= clip_duration_ms:
            continue
        next_start = ordered[index + 1].start_ms if index + 1 < len(ordered) else clip_duration_ms
        boundary = max(start, min(next_start, clip_duration_ms))
        natural_end = max(start, min(event.end_ms, clip_duration_ms))
        gap = next_start - event.end_ms
        desired_end = next_start if 0 <= gap <= style.gap_tolerance_ms else natural_end + style.hold_ms
        desired_end = max(desired_end, start + style.min_display_ms)
        end = min(desired_end, boundary, clip_duration_ms)
        if end > start:
            normalized.append(_CaptionEvent(start, end, event.text))
    return normalized


def _cue_text(cue: CaptionCue, style: CaptionStylePlan) -> str:
    raw = cue.text.upper() if style.uppercase else cue.text
    if not cue.words:
        return _line_broken_text(raw, style)
    parts: list[str] = []
    line_words = 0
    line_chars = 0
    max_chars = _max_chars_per_line(style)
    for word in cue.words:
        value = str(word["text"])
        value = value.upper() if style.uppercase else value
        if line_words and (line_words >= style.words_per_line or line_chars + len(value) + 1 > max_chars):
            parts.append(r"\N")
            line_words = 0
            line_chars = 0
        duration_cs = max(1, (int(word["end_ms"]) - int(word["start_ms"])) // 10)
        parts.append(f"{{\\kf{duration_cs}}}{_ass_text(value)}")
        line_words += 1
        line_chars += len(value) + (1 if line_chars else 0)
    return " ".join(parts)


def _line_broken_text(value: str, style: CaptionStylePlan) -> str:
    lines: list[str] = []
    max_chars = _max_chars_per_line(style)
    for source_line in value.replace("\r", "").split("\n"):
        words = source_line.split()
        if not words:
            lines.append("")
            continue
        current: list[str] = []
        for word in words:
            current_length = sum(len(item) for item in current) + max(0, len(current) - 1)
            if current and (
                len(current) >= style.words_per_line or current_length + len(word) + 1 > max_chars
            ):
                lines.append(" ".join(current))
                current = []
            current.append(word)
        if current:
            lines.append(" ".join(current))
    return r"\N".join(_ass_text(line) for line in lines)


def _max_chars_per_line(style: CaptionStylePlan) -> int:
    return max(1, round(style.max_width / max(1.0, style.font_size * 0.55)))


def _is_single_word_cue(cue: CaptionCue) -> bool:
    return cue.words is not None and len(cue.words) == 1


def _word_blocks(cues: list[CaptionCue], style: CaptionStylePlan) -> list[list[CaptionCue]]:
    blocks: list[list[CaptionCue]] = []
    block: list[CaptionCue] = []
    for cue in cues:
        if block and cue.start_ms - block[-1].end_ms > style.gap_tolerance_ms:
            blocks.append(block)
            block = []
        block.append(cue)
        if len(block) >= style.words_per_block or _has_strong_terminal(cue.text):
            blocks.append(block)
            block = []
    if block:
        blocks.append(block)
    return blocks


def _has_strong_terminal(value: str) -> bool:
    trailing = value.rstrip().rstrip('"\'”’»)]}')
    return trailing.endswith((".", "!", "?", "…"))


def _ass_text(value: str) -> str:
    return value.replace("\\", "／").replace("{", "（").replace("}", "）").replace("\r", " ").replace("\n", r"\N")


def _ass_time(milliseconds: int) -> str:
    centiseconds = max(0, milliseconds // 10)
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    seconds, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{fraction:02d}"


def _ass_color(value: str, *, alpha: int = 0) -> str:
    red, green, blue = value[1:3], value[3:5], value[5:7]
    return f"&H{alpha:02X}{blue}{green}{red}"


def _ass_alpha(opacity: float) -> int:
    return max(0, min(255, round(255 * (1 - opacity))))
