from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.editor.schemas import FitMode
from app.rendering.schemas import AudioMode, LayerPlan, RenderLayerKind, RenderPlan


@dataclass(frozen=True)
class FilterGraph:
    value: str
    video_output: str = "video_out"
    audio_output: str | None = None


class FilterGraphBuilder:
    """Translate a validated RenderPlan into the supported FFmpeg filter subset."""

    def build(self, plan: RenderPlan, *, ass_path: Path | None = None) -> FilterGraph:
        filters: list[str] = []
        width, height = plan.canvas.output_width, plan.canvas.output_height
        filters.append(f"color=c={_ff_color(plan.background_color)}:s={width}x{height}:r={plan.canvas.fps}:d={_seconds(plan.clip.duration_ms)}[base0]")
        base_label = "base0"
        video_layers = [
            layer for layer in plan.layers if layer.kind in {RenderLayerKind.SCREEN, RenderLayerKind.WEBCAM}
        ]
        prepared: dict[str, str] = {}
        for number, layer in enumerate(video_layers):
            assert layer.input_index is not None
            source = plan.inputs[layer.input_index]
            source_label = f"source{number}"
            chain = (
                f"[{source.input_index}:{source.video_stream_index}]"
                f"trim=start=0:end={_seconds(plan.clip.duration_ms)},"
                f"setpts=PTS-STARTPTS,fps={plan.canvas.fps},"
                f"{self._fit(layer)}"
            )
            if layer.opacity < 1:
                chain += f",format=rgba,colorchannelmixer=aa={layer.opacity:.4f}"
            filters.append(f"{chain}[{source_label}]")
            prepared[layer.id] = source_label

        overlay_number = 0
        for layer in plan.layers:
            if layer.kind in {RenderLayerKind.SCREEN, RenderLayerKind.WEBCAM}:
                next_base = f"base{overlay_number + 1}"
                filters.append(
                    f"[{base_label}][{prepared[layer.id]}]overlay=x={layer.rect.x}:y={layer.rect.y}:"
                    f"eof_action=pass:shortest=0[{next_base}]"
                )
                base_label = next_base
                overlay_number += 1
            elif layer.kind == RenderLayerKind.BANNER and plan.banner.enabled and plan.banner.rect is not None:
                rect = plan.banner.rect
                enabled = _enable(plan.banner.start_ms, plan.banner.end_ms or plan.clip.duration_ms)
                next_base = f"base{overlay_number + 1}"
                filters.append(
                    f"[{base_label}]drawbox=x={rect.x}:y={rect.y}:w={rect.width}:h={rect.height}:"
                    f"color={_ff_color(plan.banner.background_color)}@{plan.banner.opacity:.4f}:t=fill:enable='{enabled}'"
                    f"[{next_base}]"
                )
                base_label = next_base
                overlay_number += 1
                if plan.banner.asset_path is not None:
                    asset_index = len(plan.inputs)
                    asset_label = "banner_asset"
                    filters.append(
                        f"[{asset_index}:v]{_cover(rect.width, rect.height)},format=rgba,"
                        f"colorchannelmixer=aa={plan.banner.content_opacity:.4f}[{asset_label}]"
                    )
                    next_base = f"base{overlay_number + 1}"
                    filters.append(
                        f"[{base_label}][{asset_label}]overlay=x={rect.x}:y={rect.y}:"
                        f"enable='{enabled}':eof_action=pass:shortest=0[{next_base}]"
                    )
                    base_label = next_base
                    overlay_number += 1

        if ass_path is not None:
            filters.append(f"[{base_label}]ass=filename='{_escape_filter_path(ass_path)}'[video_out]")
        else:
            filters.append(f"[{base_label}]null[video_out]")

        audio_output: str | None = None
        if plan.audio.mode != AudioMode.SILENT:
            audio_labels: list[str] = []
            for index, audio_source in enumerate(plan.audio.sources):
                label = f"audio_source{index}"
                audio_labels.append(label)
                filters.append(
                    f"[{audio_source.input_index}:{audio_source.stream_index}]"
                    f"atrim=start=0:end={_seconds(plan.clip.duration_ms)},"
                    "asetpts=PTS-STARTPTS,aresample=48000,"
                    "aformat=sample_fmts=fltp:channel_layouts=stereo,"
                    f"volume={audio_source.gain_db:.2f}dB[{label}]"
                )
            if len(audio_labels) == 1:
                filters.append(f"[{audio_labels[0]}]alimiter=limit=0.95[audio_out]")
            else:
                joined = "".join(f"[{label}]" for label in audio_labels)
                filters.append(
                    f"{joined}amix=inputs={len(audio_labels)}:duration=longest:"
                    "dropout_transition=0:normalize=1,alimiter=limit=0.95[audio_out]"
                )
            audio_output = "audio_out"
        return FilterGraph(value=";".join(filters), audio_output=audio_output)

    @staticmethod
    def _fit(layer: LayerPlan) -> str:
        width, height = layer.rect.width, layer.rect.height
        border = min(layer.border_width, max(0, (min(width, height) - 2) // 2))
        available_width, available_height = width - border * 2, height - border * 2
        padding = min(layer.padding, max(0, (min(available_width, available_height) - 2) // 2))
        inner_width, inner_height = available_width - padding * 2, available_height - padding * 2
        if layer.fit == FitMode.CONTAIN:
            operation = (
                f"scale={inner_width}:{inner_height}:force_original_aspect_ratio=decrease,"
                f"pad={inner_width}:{inner_height}:(ow-iw)/2:(oh-ih)/2:color=black@0"
            )
        elif (
            layer.zoom == 1.0
            and layer.focal_x == 0.5
            and layer.focal_y == 0.5
            and layer.fit == FitMode.COVER
        ):
            operation = _cover(inner_width, inner_height)
        elif layer.zoom == 1.0 and layer.focal_x == 0.5 and layer.focal_y == 0.5:
            operation = _crop(inner_width, inner_height)
        else:
            operation = _framed_crop(
                inner_width,
                inner_height,
                layer.zoom,
                layer.focal_x,
                layer.focal_y,
            )
        if padding:
            operation += (
                f",format=rgba,pad={available_width}:{available_height}:{padding}:{padding}:color=black@0"
            )
        if border:
            operation += f",pad={width}:{height}:{border}:{border}:color={_ff_color(layer.border_color)}"
        return operation


def _framed_crop(width: int, height: int, zoom: float, focal_x: float, focal_y: float) -> str:
    crop_width = f"min(iw,ih*{width}/{height})/{zoom:.6f}"
    crop_height = f"min(ih,iw*{height}/{width})/{zoom:.6f}"
    x = f"max(0,min(iw-ow,(iw-ow)*{focal_x:.6f}))"
    y = f"max(0,min(ih-oh,(ih-oh)*{focal_y:.6f}))"
    return f"crop=w='{crop_width}':h='{crop_height}':x='{x}':y='{y}',scale={width}:{height}"


def _cover(width: int, height: int) -> str:
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}:(iw-ow)/2:(ih-oh)/2"
    )


def _crop(width: int, height: int) -> str:
    return (
        f"crop=w='min(iw,ih*{width}/{height})':h='min(ih,iw*{height}/{width})':"
        f"x=(iw-ow)/2:y=(ih-oh)/2,scale={width}:{height}"
    )


def _seconds(milliseconds: int) -> str:
    return f"{milliseconds / 1000:.3f}"


def _enable(start_ms: int, end_ms: int) -> str:
    return f"between(t,{_seconds(start_ms)},{_seconds(end_ms)})"


def _ff_color(value: str) -> str:
    return f"0x{value[1:].upper()}"


def _escape_filter_path(path: Path) -> str:
    value = str(path.resolve()).replace("\\", "/")
    for character in ("\\", ":", "'", "[", "]", ",", ";"):
        value = value.replace(character, f"\\{character}")
    return value
