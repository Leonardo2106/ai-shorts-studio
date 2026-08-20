from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.ai.schemas import AIProvider, SemanticAnalysisRequest
from app.ai.service import PROVIDER_MODELS, PROVIDER_PARAMETERS, SemanticAnalysisService
from app.api.routes import router
from app.candidates.schemas import CandidateGenerationRequest
from app.candidates.service import CandidateService
from app.core.errors import install_error_handlers
from app.core.middleware import LocalRequestGuardMiddleware
from app.core.settings import Settings, get_settings
from app.db.session import build_engine, build_session_factory, initialize_database
from app.jobs.runner import LocalJobRunner
from app.media.importer import MediaImporter
from app.media.probe import FFprobeService
from app.projects.storage import ProjectStorage
from app.transcription.schemas import WHISPER_LANGUAGE_CODES
from app.transcription.service import TranscriptionService
from app.vision.schemas import VisionAnalysisRequest
from app.vision.service import VisionService


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        storage = ProjectStorage(configured.resolved_storage_root)
        engine = build_engine(configured.resolved_database_path)
        initialize_database(engine)
        storage.make_private(configured.resolved_database_path)
        session_factory = build_session_factory(engine)
        prober = FFprobeService(
            configured.ffprobe_binary,
            configured.ffprobe_timeout_seconds,
            max_duration_ms=configured.max_media_duration_ms,
            max_width=configured.max_video_width,
            max_height=configured.max_video_height,
            allowed_formats=configured.allowed_media_formats,
        )
        probe_executor = ThreadPoolExecutor(max_workers=configured.probe_workers, thread_name_prefix="ai-shorts-probe")
        importer = MediaImporter(
            storage,
            prober,
            max_bytes=configured.max_upload_bytes,
            chunk_bytes=configured.upload_chunk_bytes,
            min_free_bytes=configured.min_free_space_bytes,
            max_concurrent=configured.max_concurrent_uploads,
            probe_executor=probe_executor,
        )
        transcription = TranscriptionService(configured, storage, session_factory)
        runner = LocalJobRunner(
            session_factory,
            transcription,
            workers=configured.job_workers,
            max_active_jobs=configured.max_active_jobs,
            shutdown_timeout_seconds=configured.job_shutdown_timeout_seconds,
        )
        candidates = CandidateService(storage, session_factory)
        semantic_analysis = SemanticAnalysisService(configured, session_factory)
        vision = VisionService(storage, session_factory, timeout_seconds=configured.vision_timeout_seconds)
        runner.register_handler(
            "CANDIDATE_GENERATION",
            lambda project_id, data, progress, cancelled: candidates.generate(
                project_id, CandidateGenerationRequest.model_validate(data), progress, cancelled
            ),
        )
        runner.register_handler(
            "SEMANTIC_ANALYSIS",
            lambda project_id, data, progress, cancelled: semantic_analysis.analyze(
                project_id, SemanticAnalysisRequest.model_validate(data), progress, cancelled
            ),
        )
        runner.register_handler(
            "VISION_ANALYSIS",
            lambda project_id, data, progress, cancelled: vision.analyze(
                project_id, VisionAnalysisRequest.model_validate(data), progress, cancelled
            ),
        )
        runner.reconcile_interrupted()
        app.state.settings = configured
        app.state.storage = storage
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.prober = prober
        app.state.importer = importer
        app.state.job_runner = runner
        app.state.transcription = transcription
        app.state.candidates = candidates
        app.state.semantic_analysis = semantic_analysis
        app.state.vision = vision
        app.state.capabilities = {
            "ffprobe": {"available": prober.available_path() is not None, "version": prober.version()},
            "ffmpeg": {"available": shutil.which(configured.ffmpeg_binary) is not None},
            "faster_whisper": {"available": transcription.is_available()},
            "transcription_presets": ["ECONOMY", "BALANCED", "QUALITY", "MAXIMUM_QUALITY"],
            "transcription_languages": sorted(WHISPER_LANGUAGE_CODES),
            "advanced_options": [],
            "ai_providers": [
                {
                    "provider": provider.value,
                    "configured": semantic_analysis.configured(provider),
                    "models": PROVIDER_MODELS[provider],
                    "parameters": PROVIDER_PARAMETERS[provider],
                }
                for provider in AIProvider
            ],
            "vision": {"available": vision.is_available(), "analyzer_version": 1},
            "editor": {"canvas": {"width": 1080, "height": 1920}, "schema_version": 1},
        }
        yield
        runner.shutdown()
        probe_executor.shutdown(wait=False, cancel_futures=True)
        engine.dispose()

    app = FastAPI(title=configured.app_name, version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=configured.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(
        LocalRequestGuardMiddleware,
        max_bytes=configured.max_request_bytes,
        allowed_origins=set(configured.cors_origins),
        max_concurrent_uploads=configured.max_concurrent_uploads,
        body_timeout_seconds=configured.request_body_timeout_seconds,
        min_free_space_bytes=configured.min_free_space_bytes,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=configured.allowed_hosts)
    install_error_handlers(app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(router, prefix=configured.api_prefix)
    return app


app = create_app()
