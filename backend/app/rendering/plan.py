from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal, cast

from app.core.errors import AppError
from app.editor.schemas import AudioConfigMode, CaptionCue, EditConfig, EditorElement, FitMode
from app.rendering.schemas import (
    AudioMode,
    AudioPlan,
    AudioSourcePlan,
    BannerPlan,
    CanvasPlan,
    CaptionPlan,
    CaptionStylePlan,
    ClipPlan,
    InputPlan,
    LayerPlan,
    NormalizedRect,
    OutputPlan,
    RenderInputRole,
    RenderKind,
    RenderLayerKind,
    RenderPlan,
    RenderQuality,
    ResolvedMediaInput,
    ResolvedRenderContext,
)


class RenderPlanBuilder:
    """Normalize trusted repository contracts before any FFmpeg-specific work."""

    def build(
        self,
        context: ResolvedRenderContext,
        *,
        kind: RenderKind,
        quality: RenderQuality,
    ) -> RenderPlan:
        config = context.edit_config
        clip = ClipPlan(
            timeline_start_ms=context.clip_start_ms,
            timeline_end_ms=context.clip_end_ms,
            duration_ms=context.clip_end_ms - context.clip_start_ms,
        )
        canvas = self._canvas(kind)
        media_by_role = {item.role: item for item in context.media}
        required_roles = self._required_roles(config, context)
        ordered_media = [media_by_role[role] for role in RenderInputRole if role in required_roles]
        inputs = [self._input_plan(index, source, context, clip) for index, source in enumerate(ordered_media)]
        input_by_role = {item.role: item for item in inputs}
        layers = self._layers(config, canvas, input_by_role)
        self._validate_compositing_policy(layers)
        captions = self._captions(config, context, canvas, clip)
        banner = self._banner(config, context, canvas, clip)
        audio = self._audio(config, context, inputs)
        output = self._output(kind, quality)
        edit_fingerprint = _fingerprint(config.model_dump(mode="json"))
        dependency_material = {
            "render_plan_schema": 1,
            "renderer_schema": 2,
            "kind": kind,
            "quality": quality,
            "clip": clip.model_dump(mode="json"),
            "canvas": canvas.model_dump(mode="json"),
            "edit_config": config.model_dump(mode="json"),
            "inputs": [
                {
                    "media_id": item.media_id,
                    "role": item.role,
                    "sha256": item.sha256,
                    "video_stream_index": item.video_stream_index,
                    "audio_stream_indexes": item.audio_stream_indexes,
                    "timeline_offset_ms": item.timeline_offset_ms,
                    "source_start_ms": item.source_start_ms,
                    "source_end_ms": item.source_end_ms,
                }
                for item in inputs
            ],
            "transcript": (
                {
                    "id": context.transcript.transcript_id,
                    "cache_key": context.transcript.cache_key,
                    "media_id": context.transcript.media_id,
                    "audio_stream_index": context.transcript.audio_stream_index,
                    "timing_source": context.transcript.timing_source,
                    "caption_cues": [cue.model_dump(mode="json") for cue in context.transcript.caption_cues],
                }
                if captions.enabled and context.transcript is not None
                else None
            ),
            "banner_asset": (
                {
                    "relative_path": context.banner_asset.relative_path,
                    "sha256": context.banner_asset.sha256,
                }
                if banner.enabled and context.banner_asset is not None
                else None
            ),
            "audio": audio.model_dump(mode="json"),
            "output": output.model_dump(mode="json"),
        }
        return RenderPlan(
            project_id=context.project_id,
            candidate_id=context.candidate_id,
            edit_config_id=context.edit_config_id,
            kind=kind,
            quality=quality,
            clip=clip,
            canvas=canvas,
            background_color=config.background_color,
            inputs=inputs,
            layers=layers,
            captions=captions,
            banner=banner,
            audio=audio,
            output=output,
            edit_config_fingerprint=edit_fingerprint,
            dependency_fingerprint=_fingerprint(dependency_material),
            cacheable=kind == RenderKind.PREVIEW,
        )

    @staticmethod
    def _canvas(kind: RenderKind) -> CanvasPlan:
        if kind == RenderKind.PREVIEW:
            return CanvasPlan(output_width=540, output_height=960, fps=24)
        return CanvasPlan(output_width=1080, output_height=1920, fps=30)

    @staticmethod
    def _required_roles(config: EditConfig, context: ResolvedRenderContext) -> set[RenderInputRole]:
        roles: set[RenderInputRole] = set()
        visible_video_roles: set[RenderInputRole] = set()
        media_by_role = {item.role: item for item in context.media}
        for element in config.elements:
            if not element.visible:
                continue
            if element.kind in {RenderInputRole.SCREEN.value, RenderInputRole.WEBCAM.value}:
                role = RenderInputRole(element.kind)
                if role not in media_by_role:
                    raise AppError(
                        "RENDER_INPUT_MISSING",
                        f"Visible {role.value.lower()} layer has no project media input.",
                        status_code=422,
                    )
                roles.add(role)
                visible_video_roles.add(role)
            elif element.kind in {RenderLayerKind.CAPTIONS.value, RenderLayerKind.BANNER.value}:
                continue
            else:
                raise AppError(
                    "RENDER_LAYER_UNSUPPORTED",
                    f"Visible editor layer {element.kind} is not supported by the MVP renderer.",
                    status_code=422,
                )
        if config.audio.mode == AudioConfigMode.TRANSCRIPT_DEFAULT and context.transcript is not None:
            audio_media = next((item for item in context.media if item.media_id == context.transcript.media_id), None)
            if audio_media is None:
                raise AppError(
                    "RENDER_AUDIO_INPUT_MISSING",
                    "Transcript audio source is not associated with the project media inputs.",
                    status_code=422,
                )
            roles.add(audio_media.role)
        elif config.audio.mode == AudioConfigMode.CUSTOM:
            media_by_id = {item.media_id: item for item in context.media}
            for track in config.audio.tracks:
                if not track.enabled:
                    continue
                audio_media = media_by_id.get(track.media_id)
                if audio_media is None:
                    raise AppError(
                        "RENDER_AUDIO_INPUT_MISSING",
                        "An enabled custom audio track is not associated with project media.",
                        status_code=422,
                        details={"media_id": track.media_id, "stream_index": track.stream_index},
                    )
                roles.add(audio_media.role)
        if not visible_video_roles:
            raise AppError("RENDER_VIDEO_MISSING", "Render requires at least one visible video layer.", status_code=422)
        return roles

    @staticmethod
    def _input_plan(
        index: int,
        source: ResolvedMediaInput,
        context: ResolvedRenderContext,
        clip: ClipPlan,
    ) -> InputPlan:
        _require_regular_file(source.path, code="RENDER_INPUT_FILE_MISSING", label="Render media input")
        timeline_offset = context.webcam_offset_ms if source.role == RenderInputRole.WEBCAM else 0
        source_start = clip.timeline_start_ms - timeline_offset
        source_end = clip.timeline_end_ms - timeline_offset
        if source_start < 0 or source_end > source.duration_ms:
            raise AppError(
                "RENDER_CLIP_OUTSIDE_INPUT",
                "Clip interval is outside one of the required media inputs after synchronization.",
                status_code=422,
                details={"media_id": source.media_id, "role": source.role.value},
            )
        return InputPlan(
            input_index=index,
            media_id=source.media_id,
            role=source.role,
            path=source.path,
            sha256=source.sha256.lower(),
            video_stream_index=source.video_stream_indexes[0],
            audio_stream_indexes=source.audio_stream_indexes,
            timeline_offset_ms=timeline_offset,
            source_start_ms=source_start,
            source_end_ms=source_end,
            source_duration_ms=source.duration_ms,
        )

    def _layers(
        self,
        config: EditConfig,
        canvas: CanvasPlan,
        inputs: dict[RenderInputRole, InputPlan],
    ) -> list[LayerPlan]:
        visible = [
            element
            for element in config.elements
            if element.visible
            and not (element.kind == RenderLayerKind.CAPTIONS.value and not config.captions.enabled)
            and not (element.kind == RenderLayerKind.BANNER.value and not config.banner.enabled)
        ]
        captions = [item for item in visible if item.kind == RenderLayerKind.CAPTIONS.value]
        banners = [item for item in visible if item.kind == RenderLayerKind.BANNER.value]
        if len(captions) > 1 or len(banners) > 1:
            raise AppError(
                "RENDER_LAYER_AMBIGUOUS",
                "Only one visible captions layer and one visible banner layer are supported.",
                status_code=422,
            )
        layers: list[LayerPlan] = []
        for element in visible:
            try:
                kind = RenderLayerKind(element.kind)
            except ValueError as exc:
                raise AppError(
                    "RENDER_LAYER_UNSUPPORTED",
                    f"Visible editor layer {element.kind} is not supported by the MVP renderer.",
                    status_code=422,
                ) from exc
            input_index = None
            if kind in {RenderLayerKind.SCREEN, RenderLayerKind.WEBCAM}:
                if element.radius:
                    raise AppError(
                        "RENDER_RADIUS_UNSUPPORTED",
                        "Rounded video corners are not supported by the current renderer.",
                        status_code=422,
                    )
                if element.fit == FitMode.CONTAIN and (
                    element.zoom != 1.0 or element.focal_x != 0.5 or element.focal_y != 0.5
                ):
                    raise AppError(
                        "RENDER_ZOOM_CONTAIN_UNSUPPORTED",
                        "Zoom and focal point are not supported with CONTAIN fit.",
                        status_code=422,
                    )
                input_index = inputs[RenderInputRole(kind.value)].input_index
            elif element.zoom != 1.0 or element.focal_x != 0.5 or element.focal_y != 0.5:
                raise AppError(
                    "RENDER_FRAMING_KIND_UNSUPPORTED",
                    "Zoom and focal point are supported only for screen and webcam layers.",
                    status_code=422,
                )
            layers.append(self._layer(element, kind, input_index, canvas))
        # Python's stable sort preserves editor array order for equal z-index, matching browser stacking.
        return sorted(layers, key=lambda item: item.z_index)

    @staticmethod
    def _validate_compositing_policy(layers: list[LayerPlan]) -> None:
        video_z = [item.z_index for item in layers if item.kind in {RenderLayerKind.SCREEN, RenderLayerKind.WEBCAM}]
        if not video_z:
            return
        top_video = max(video_z)
        for layer in layers:
            if layer.kind in {RenderLayerKind.CAPTIONS, RenderLayerKind.BANNER} and layer.z_index <= top_video:
                raise AppError(
                    "RENDER_TEXT_LAYER_ORDER_UNSUPPORTED",
                    "Captions and banner layers must be strictly above all video layers.",
                    status_code=422,
                )
        captions = next((item for item in layers if item.kind == RenderLayerKind.CAPTIONS), None)
        banner = next((item for item in layers if item.kind == RenderLayerKind.BANNER), None)
        if captions is not None and banner is not None and captions.z_index <= banner.z_index:
            raise AppError(
                "RENDER_TEXT_LAYER_ORDER_UNSUPPORTED",
                "Captions must be strictly above the banner in the current renderer.",
                status_code=422,
            )

    def _layer(
        self,
        element: EditorElement,
        kind: RenderLayerKind,
        input_index: int | None,
        canvas: CanvasPlan,
    ) -> LayerPlan:
        scale_x = canvas.output_width / 1080
        scale_y = canvas.output_height / 1920
        rect = NormalizedRect(
            x=round(element.x * scale_x),
            y=round(element.y * scale_y),
            width=max(1, round(element.width * scale_x)),
            height=max(1, round(element.height * scale_y)),
        )
        if rect.x + rect.width > canvas.output_width or rect.y + rect.height > canvas.output_height:
            raise AppError("RENDER_LAYER_OUTSIDE_CANVAS", "Normalized layer escapes output canvas.", status_code=422)
        return LayerPlan(
            id=element.id,
            kind=kind,
            rect=rect,
            z_index=element.z_index,
            fit=element.fit,
            opacity=element.opacity,
            border_width=round(element.border_width * scale_x),
            border_color=element.border_color.upper(),
            radius=round(element.radius * scale_x),
            padding=round(element.padding * scale_x),
            zoom=element.zoom,
            focal_x=element.focal_x,
            focal_y=element.focal_y,
            input_index=input_index,
        )

    def _captions(
        self,
        config: EditConfig,
        context: ResolvedRenderContext,
        canvas: CanvasPlan,
        clip: ClipPlan,
    ) -> CaptionPlan:
        layer = next(
            (item for item in config.elements if item.visible and item.kind == RenderLayerKind.CAPTIONS.value),
            None,
        )
        if not config.captions.enabled or layer is None:
            return CaptionPlan(enabled=False)
        transcript = context.transcript
        if transcript is None:
            raise AppError(
                "RENDER_CAPTIONS_SOURCE_MISSING",
                "Enabled captions require the candidate transcript source.",
                status_code=422,
            )
        cues = [_normalized_cue(cue, clip.duration_ms) for cue in transcript.caption_cues]
        cues = [cue for cue in cues if cue is not None]
        normalized_layer = self._layer(layer, RenderLayerKind.CAPTIONS, None, canvas)
        scale = canvas.output_height / 1920
        style = config.captions
        return CaptionPlan(
            enabled=True,
            layer_id=layer.id,
            rect=normalized_layer.rect,
            z_index=layer.z_index,
            opacity=layer.opacity,
            style=CaptionStylePlan(
                font_family=_normalized_font_family(style.font_family),
                font_size=max(1, round(style.font_size * scale)),
                color=style.color.upper(),
                weight=style.weight,
                uppercase=style.uppercase,
                outline_width=round(style.outline_width * scale),
                outline_color=style.outline_color.upper(),
                shadow=style.shadow,
                italic=style.italic,
                box_color=style.box_color.upper() if style.box_color else None,
                max_width=max(1, round(style.max_width * canvas.output_width / 1080)),
                words_per_line=style.words_per_line,
                words_per_block=style.words_per_block,
                active_word_color=style.active_word_color.upper() if style.active_word_color else None,
                gap_tolerance_ms=style.gap_tolerance_ms,
                min_display_ms=style.min_display_ms,
                hold_ms=style.hold_ms,
            ),
            cues=cast(list[CaptionCue], cues),
            timing_source=transcript.timing_source,
            transcript_id=transcript.transcript_id,
            transcript_cache_key=transcript.cache_key,
        )

    def _banner(
        self,
        config: EditConfig,
        context: ResolvedRenderContext,
        canvas: CanvasPlan,
        clip: ClipPlan,
    ) -> BannerPlan:
        layer = next(
            (item for item in config.elements if item.visible and item.kind == RenderLayerKind.BANNER.value),
            None,
        )
        if not config.banner.enabled or layer is None:
            return BannerPlan(enabled=False)
        end_ms = config.banner.end_ms or clip.duration_ms
        if config.banner.start_ms >= clip.duration_ms or end_ms > clip.duration_ms or end_ms <= config.banner.start_ms:
            raise AppError("INVALID_BANNER_RANGE", "Banner interval exceeds the render clip.", status_code=422)
        asset = context.banner_asset
        if config.banner.image_relative_path is not None:
            if asset is None or asset.relative_path != config.banner.image_relative_path:
                raise AppError(
                    "RENDER_BANNER_ASSET_MISSING",
                    "Configured banner asset was not resolved from project storage.",
                    status_code=422,
                )
            _require_regular_file(asset.path, code="RENDER_BANNER_ASSET_MISSING", label="Banner asset")
        elif asset is not None:
            raise AppError(
                "RENDER_BANNER_ASSET_UNEXPECTED",
                "Resolved banner asset does not match the editor configuration.",
                status_code=422,
            )
        normalized_layer = self._layer(layer, RenderLayerKind.BANNER, None, canvas)
        return BannerPlan(
            enabled=True,
            layer_id=layer.id,
            rect=normalized_layer.rect,
            z_index=layer.z_index,
            text=config.banner.text,
            background_color=config.banner.background_color.upper(),
            opacity=layer.opacity * config.banner.opacity,
            content_opacity=layer.opacity,
            start_ms=config.banner.start_ms,
            end_ms=end_ms,
            asset_relative_path=asset.relative_path if asset else None,
            asset_path=asset.path if asset else None,
            asset_sha256=asset.sha256.lower() if asset else None,
        )

    @staticmethod
    def _audio(config: EditConfig, context: ResolvedRenderContext, inputs: list[InputPlan]) -> AudioPlan:
        if config.audio.mode == AudioConfigMode.TRANSCRIPT_DEFAULT:
            transcript = context.transcript
            if transcript is None:
                return AudioPlan(mode=AudioMode.SILENT)
            source = next((item for item in inputs if item.media_id == transcript.media_id), None)
            if source is None or transcript.audio_stream_index not in source.audio_stream_indexes:
                raise AppError(
                    "RENDER_AUDIO_STREAM_MISSING",
                    "The transcript-selected audio stream is unavailable in project media metadata.",
                    status_code=422,
                )
            return AudioPlan(
                mode=AudioMode.SINGLE_TRACK,
                sources=[_audio_source(source, transcript.audio_stream_index, 0.0)],
            )

        sources: list[AudioSourcePlan] = []
        input_by_media_id = {item.media_id: item for item in inputs}
        for track in config.audio.tracks:
            if not track.enabled:
                continue
            source = input_by_media_id.get(track.media_id)
            if source is None:
                raise AppError(
                    "RENDER_AUDIO_INPUT_MISSING",
                    "An enabled custom audio track is not associated with a resolved render input.",
                    status_code=422,
                    details={"media_id": track.media_id, "stream_index": track.stream_index},
                )
            if track.stream_index not in source.audio_stream_indexes:
                raise AppError(
                    "RENDER_AUDIO_STREAM_MISSING",
                    "An enabled custom audio stream is unavailable in project media metadata.",
                    status_code=422,
                    details={"media_id": track.media_id, "stream_index": track.stream_index},
                )
            sources.append(_audio_source(source, track.stream_index, track.gain_db))
        if not sources:
            return AudioPlan(mode=AudioMode.SILENT)
        mode = AudioMode.SINGLE_TRACK if len(sources) == 1 else AudioMode.MIXED_TRACKS
        return AudioPlan(mode=mode, sources=sources)

    @staticmethod
    def _output(kind: RenderKind, quality: RenderQuality) -> OutputPlan:
        presets: dict[
            RenderQuality,
            tuple[Literal["ultrafast", "veryfast", "medium", "slow"], int],
        ]
        if kind == RenderKind.PREVIEW:
            presets = {
                RenderQuality.FAST: ("ultrafast", 30),
                RenderQuality.BALANCED: ("veryfast", 28),
                RenderQuality.HIGH: ("veryfast", 26),
            }
            encoder, crf = presets[quality]
            return OutputPlan(
                kind=kind,
                quality=quality,
                relative_directory="previews",
                encoder_preset=encoder,
                crf=crf,
            )
        presets = {
            RenderQuality.FAST: ("veryfast", 25),
            RenderQuality.BALANCED: ("medium", 21),
            RenderQuality.HIGH: ("slow", 18),
        }
        encoder, crf = presets[quality]
        return OutputPlan(
            kind=kind,
            quality=quality,
            relative_directory="renders",
            encoder_preset=encoder,
            crf=crf,
        )


def _audio_source(source: InputPlan, stream_index: int, gain_db: float) -> AudioSourcePlan:
    return AudioSourcePlan(
        input_index=source.input_index,
        media_id=source.media_id,
        stream_index=stream_index,
        source_start_ms=source.source_start_ms,
        source_end_ms=source.source_end_ms,
        timeline_offset_ms=source.timeline_offset_ms,
        gain_db=gain_db,
    )


def _normalized_cue(cue: CaptionCue, duration_ms: int) -> CaptionCue | None:
    start = max(0, cue.start_ms)
    end = min(duration_ms, cue.end_ms)
    if end <= start:
        return None
    words: list[dict[str, int | str]] | None = None
    if cue.words:
        words = []
        for word in cue.words:
            word_start = max(start, int(word["start_ms"]))
            word_end = min(end, int(word["end_ms"]))
            if word_end > word_start:
                words.append({"start_ms": word_start, "end_ms": word_end, "text": str(word["text"])})
    return CaptionCue(start_ms=start, end_ms=end, text=cue.text, words=words)


def _normalized_font_family(value: str) -> str:
    normalized = "".join(
        character for character in value.strip() if ord(character) >= 32 and character not in "{},\\\r\n"
    )
    return normalized[:100] or "Arial"


def _require_regular_file(path: Path, *, code: str, label: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise AppError(code, f"{label} is missing or unsafe.", status_code=422)


def _fingerprint(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
