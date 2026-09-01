from __future__ import annotations

from pathlib import Path

from app.rendering.filter_graph import FilterGraph
from app.rendering.schemas import RenderPlan


class FFmpegCommandBuilder:
    def __init__(self, executable: str = "ffmpeg", *, max_output_bytes: int = 1024**3) -> None:
        self.executable = executable
        self.max_output_bytes = max_output_bytes

    def build(self, plan: RenderPlan, graph: FilterGraph, output_path: Path) -> list[str]:
        command = [self.executable, "-nostdin", "-hide_banner", "-loglevel", "warning"]
        for item in plan.inputs:
            # Input-scoped seeking avoids decoding everything from timestamp zero.
            # RenderPlan has already resolved synchronization into source_start_ms.
            command.extend(
                [
                    "-ss",
                    f"{item.source_start_ms / 1000:.3f}",
                    "-t",
                    f"{plan.clip.duration_ms / 1000:.3f}",
                    "-i",
                    str(item.path),
                ]
            )
        if plan.banner.asset_path is not None:
            command.extend(["-loop", "1", "-i", str(plan.banner.asset_path)])
        command.extend(
            [
                "-filter_complex",
                graph.value,
                "-map",
                f"[{graph.video_output}]",
            ]
        )
        if graph.audio_output is not None:
            command.extend(["-map", f"[{graph.audio_output}]", "-c:a", plan.output.audio_codec, "-b:a", "160k"])
        else:
            command.append("-an")
        command.extend(
            [
                "-c:v",
                plan.output.video_codec,
                "-preset",
                plan.output.encoder_preset,
                "-crf",
                str(plan.output.crf),
                "-pix_fmt",
                plan.output.pixel_format,
                "-r",
                str(plan.canvas.fps),
                "-t",
                f"{plan.clip.duration_ms / 1000:.3f}",
                "-movflags",
                "+faststart",
                "-fs",
                str(self.max_output_bytes),
                "-progress",
                "pipe:1",
                "-stats_period",
                "0.25",
                "-n",
                str(output_path),
            ]
        )
        return command
