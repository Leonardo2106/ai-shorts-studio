from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol, cast

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.ai.schemas import AIProvider, CandidateSemanticResult, SemanticAnalysisRequest, SemanticMetrics
from app.candidates.service import project_timeline_bounds
from app.core.errors import AppError
from app.core.settings import Settings
from app.db.models import AnalysisCacheModel, CandidateModel, ProjectModel

PROVIDER_MODELS: dict[AIProvider, list[str]] = {
    AIProvider.OPENAI: ["gpt-4.1-mini", "gpt-4.1"],
    AIProvider.GEMINI: ["gemini-2.5-flash", "gemini-2.5-pro"],
    AIProvider.GROQ: ["llama-3.3-70b-versatile"],
}
PROVIDER_PARAMETERS: dict[AIProvider, list[str]] = {
    AIProvider.OPENAI: [
        "max_output_tokens",
        "temperature",
        "top_p",
        "timeout_seconds",
        "retries",
        "fallback_provider",
        "chunk_char_limit",
    ],
    AIProvider.GEMINI: [
        "max_output_tokens",
        "temperature",
        "top_p",
        "timeout_seconds",
        "retries",
        "fallback_provider",
        "chunk_char_limit",
    ],
    AIProvider.GROQ: [
        "max_output_tokens",
        "temperature",
        "top_p",
        "timeout_seconds",
        "retries",
        "fallback_provider",
        "chunk_char_limit",
    ],
}

OPENAI_SEMANTIC_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "string"},
                    "metrics": {
                        "type": "object",
                        "properties": {
                            key: {"type": "number", "minimum": 0, "maximum": 1}
                            for key in (
                                "hook",
                                "humor",
                                "novelty",
                                "context_completeness",
                                "standalone_quality",
                                "information_value",
                                "narrative_progression",
                                "dead_air_penalty",
                            )
                        }
                        | {
                            "recommended_start_ms": {"type": "integer", "minimum": 0},
                            "recommended_end_ms": {"type": "integer", "minimum": 1},
                        },
                        "required": [
                            "hook",
                            "humor",
                            "novelty",
                            "context_completeness",
                            "standalone_quality",
                            "information_value",
                            "narrative_progression",
                            "dead_air_penalty",
                            "recommended_start_ms",
                            "recommended_end_ms",
                        ],
                        "additionalProperties": False,
                    },
                    "reasons": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["candidate_id", "metrics", "reasons"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


class SemanticProvider(Protocol):
    def analyze(self, payload: list[dict[str, object]], request: SemanticAnalysisRequest) -> list[dict[str, Any]]: ...


class JsonHttpProvider:
    def __init__(self, provider: AIProvider, api_key: str, *, max_response_bytes: int = 2 * 1024 * 1024) -> None:
        self.provider = provider
        self.api_key = api_key
        self.max_response_bytes = max_response_bytes

    def analyze(self, payload: list[dict[str, object]], request: SemanticAnalysisRequest) -> list[dict[str, Any]]:
        prompt = (
            'Return JSON only as {"items":[...]}. For every candidate include candidate_id and metrics: '
            "hook, humor, novelty, context_completeness, standalone_quality, information_value, "
            "narrative_progression, dead_air_penalty (0..1), recommended_start_ms, recommended_end_ms, and reasons. "
            f"Candidates: {json.dumps(payload, ensure_ascii=False)}"
        )
        url, headers, body = self._request_data(prompt, request)
        http_request = urllib.request.Request(url, json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(http_request, timeout=request.timeout_seconds) as response:  # noqa: S310
                content_length = response.headers.get("Content-Length")
                if content_length is not None and int(content_length) > self.max_response_bytes:
                    raise AppError(
                        "PROVIDER_RESPONSE_TOO_LARGE", "Provider response exceeded the size limit.", status_code=502
                    )
                payload_bytes = response.read(self.max_response_bytes + 1)
                if len(payload_bytes) > self.max_response_bytes:
                    raise AppError(
                        "PROVIDER_RESPONSE_TOO_LARGE", "Provider response exceeded the size limit.", status_code=502
                    )
                raw = json.loads(payload_bytes.decode("utf-8"))
        except AppError:
            raise
        except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise AppError(
                "PROVIDER_REQUEST_FAILED", "External analysis provider request failed.", status_code=502
            ) from exc
        try:
            content = self._content(raw)
            parsed = json.loads(content) if isinstance(content, str) else content
            items = cast(list[dict[str, Any]], cast(dict[str, object], parsed)["items"])
            return items
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise AppError(
                "PROVIDER_INVALID_RESPONSE", "Provider returned an invalid structured response.", status_code=502
            ) from exc

    def _request_data(
        self, prompt: str, request: SemanticAnalysisRequest
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        headers = {"Content-Type": "application/json"}
        if self.provider == AIProvider.GEMINI:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{request.model}:generateContent"
            headers["x-goog-api-key"] = self.api_key
            generation_config: dict[str, object] = {
                "maxOutputTokens": request.max_output_tokens,
                "responseMimeType": "application/json",
            }
            if request.temperature is not None:
                generation_config["temperature"] = request.temperature
            if request.top_p is not None:
                generation_config["topP"] = request.top_p
            return (
                url,
                headers,
                {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": generation_config,
                },
            )
        headers["Authorization"] = f"Bearer {self.api_key}"
        base = (
            "https://api.openai.com/v1/responses"
            if self.provider == AIProvider.OPENAI
            else "https://api.groq.com/openai/v1/chat/completions"
        )
        if self.provider == AIProvider.OPENAI:
            body: dict[str, Any] = {
                "model": request.model,
                "input": prompt,
                "max_output_tokens": request.max_output_tokens,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "candidate_semantic_analysis",
                        "strict": True,
                        "schema": OPENAI_SEMANTIC_SCHEMA,
                    }
                },
            }
        else:
            body = {
                "model": request.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": request.max_output_tokens,
                "response_format": {"type": "json_object"},
            }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.top_p is not None:
            body["top_p"] = request.top_p
        return base, headers, body

    def _content(self, raw: dict[str, Any]) -> object:
        if self.provider == AIProvider.OPENAI:
            for output in raw["output"]:
                for content in output.get("content", []):
                    if content.get("type") == "output_text":
                        return content["text"]
            raise KeyError("Responses output contained no output_text")
        if self.provider == AIProvider.GEMINI:
            return raw["candidates"][0]["content"]["parts"][0]["text"]
        return raw["choices"][0]["message"]["content"]


class SemanticAnalysisService:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        adapters: dict[AIProvider, SemanticProvider] | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.adapters = adapters or {}

    def configured(self, provider: AIProvider) -> bool:
        return provider in self.adapters or bool(self.settings.provider_key(provider))

    def _adapter(self, provider: AIProvider) -> SemanticProvider:
        if provider in self.adapters:
            return self.adapters[provider]
        key = self.settings.provider_key(provider)
        if not key:
            raise AppError("PROVIDER_NOT_CONFIGURED", "Selected provider is not configured.", status_code=409)
        return JsonHttpProvider(provider, key, max_response_bytes=self.settings.provider_max_response_bytes)

    def analyze(
        self,
        project_id: str,
        request: SemanticAnalysisRequest,
        progress: Callable[[float], None],
        cancelled: Callable[[], bool],
    ) -> dict[str, object]:
        if not request.opt_in_external_processing:
            raise AppError(
                "EXTERNAL_AI_OPT_IN_REQUIRED", "Explicit opt-in is required for external processing.", status_code=422
            )
        if request.model not in PROVIDER_MODELS[request.provider]:
            raise AppError("UNSUPPORTED_PROVIDER_MODEL", "Model is not supported for this provider.", status_code=422)
        unsupported = (
            request.reasoning_effort is not None and "reasoning_effort" not in PROVIDER_PARAMETERS[request.provider]
        )
        if unsupported:
            raise AppError(
                "UNSUPPORTED_PROVIDER_PARAMETER", "Provider does not support reasoning_effort.", status_code=422
            )
        with self.session_factory() as session:
            project = session.get(ProjectModel, project_id)
            if project is None:
                raise AppError("PROJECT_NOT_FOUND", "Project was not found.", status_code=404)
            candidates = list(
                session.scalars(
                    select(CandidateModel).where(
                        CandidateModel.project_id == project_id, CandidateModel.id.in_(request.candidate_ids)
                    )
                ).all()
            )
            if len(candidates) != len(set(request.candidate_ids)):
                raise AppError("CANDIDATE_NOT_FOUND", "One or more candidates were not found.", status_code=404)
            lower, upper = project_timeline_bounds(session, project)
        chunks = _chunks(candidates, request.chunk_char_limit)
        deadline = time.monotonic() + min(300.0, request.timeout_seconds * (request.retries + 1))
        completed: list[str] = []
        analyses: list[dict[str, str]] = []
        for index, chunk in enumerate(chunks):
            if cancelled():
                raise AppError("JOB_CANCELLED", "Semantic analysis was cancelled.", status_code=409)
            results = self._cached_or_call(project_id, chunk, request, cancelled=cancelled, deadline=deadline)
            with self.session_factory.begin() as session:
                by_id = {
                    item.id: item
                    for item in session.scalars(
                        select(CandidateModel).where(CandidateModel.id.in_([result.candidate_id for result in results]))
                    ).all()
                }
                for result in results:
                    candidate = by_id.get(result.candidate_id)
                    if candidate is None:
                        raise AppError(
                            "PROVIDER_INVALID_RESPONSE", "Provider referenced an unknown candidate.", status_code=502
                        )
                    metrics = result.metrics
                    recommended_start, recommended_end = _validated_recommendation(candidate, metrics, lower, upper)
                    signals = dict(candidate.signals)
                    signals.update(metrics.model_dump(exclude={"recommended_start_ms", "recommended_end_ms"}))
                    signals["ai_recommended_start_ms"] = recommended_start
                    signals["ai_recommended_end_ms"] = recommended_end
                    signals["ai_recommendation_policy"] = "CLAMP_TO_CANDIDATE_WINDOW_OR_FALLBACK"
                    candidate.signals = signals
                    candidate.reasons = list(dict.fromkeys([*candidate.reasons, *result.reasons]))
                    context = dict(candidate.context)
                    if result.effective_provider is not None and result.effective_model is not None:
                        context["analysis_origin"] = f"AI_{result.effective_provider.value}"
                        context["semantic_provider"] = result.effective_provider.value
                        context["semantic_model"] = result.effective_model
                        analyses.append(
                            {
                                "candidate_id": candidate.id,
                                "provider": result.effective_provider.value,
                                "model": result.effective_model,
                            }
                        )
                    candidate.context = context
                    completed.append(candidate.id)
            progress((index + 1) / len(chunks))
        return {"candidate_ids": completed, "chunks": len(chunks), "analyses": analyses}

    def _cached_or_call(
        self,
        project_id: str,
        candidates: list[CandidateModel],
        request: SemanticAnalysisRequest,
        *,
        cancelled: Callable[[], bool] = lambda: False,
        deadline: float | None = None,
    ) -> list[CandidateSemanticResult]:
        payload = [
            {
                "candidate_id": item.id,
                "start_ms": item.start_ms,
                "end_ms": item.end_ms,
                "text": str(item.context.get("text", "")),
            }
            for item in candidates
        ]
        key_data = {
            "v": 1,
            "provider": request.provider,
            "model": request.model,
            "payload": payload,
            "max_output_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "top_p": request.top_p,
            "reasoning_effort": request.reasoning_effort,
            "fallback_provider": request.fallback_provider,
        }
        cache_key = hashlib.sha256(json.dumps(key_data, sort_keys=True).encode()).hexdigest()
        with self.session_factory() as session:
            cached = session.scalar(select(AnalysisCacheModel).where(AnalysisCacheModel.cache_key == cache_key))
            if cached is not None:
                try:
                    return [CandidateSemanticResult.model_validate(item) for item in cached.result_data["items"]]
                except (KeyError, TypeError, ValidationError) as exc:
                    raise AppError(
                        "PROVIDER_INVALID_RESPONSE", "Cached provider response is invalid.", status_code=502
                    ) from exc
        providers = [request.provider]
        if request.fallback_provider is not None and request.fallback_provider != request.provider:
            providers.append(request.fallback_provider)
        last_error: AppError | None = None
        for provider in providers:
            if cancelled():
                raise AppError("JOB_CANCELLED", "Semantic analysis was cancelled.", status_code=409)
            adapted_request = request.model_copy(update={"provider": provider})
            if adapted_request.model not in PROVIDER_MODELS[provider]:
                adapted_request = adapted_request.model_copy(update={"model": PROVIDER_MODELS[provider][0]})
            for attempt in range(request.retries + 1):
                if cancelled():
                    raise AppError("JOB_CANCELLED", "Semantic analysis was cancelled.", status_code=409)
                remaining = request.timeout_seconds if deadline is None else deadline - time.monotonic()
                if remaining <= 0:
                    raise AppError(
                        "PROVIDER_TIMEOUT", "Semantic analysis exceeded its total deadline.", status_code=504
                    )
                adapted_request = adapted_request.model_copy(
                    update={"timeout_seconds": min(adapted_request.timeout_seconds, remaining)}
                )
                try:
                    raw = self._adapter(provider).analyze(payload, adapted_request)
                    try:
                        results = [CandidateSemanticResult.model_validate(item) for item in raw]
                    except (TypeError, ValidationError) as exc:
                        raise AppError(
                            "PROVIDER_INVALID_RESPONSE", "Provider returned invalid semantic metrics.", status_code=502
                        ) from exc
                    if len(results) != len(candidates) or {item.candidate_id for item in results} != {
                        item.id for item in candidates
                    }:
                        raise AppError(
                            "PROVIDER_INVALID_RESPONSE", "Provider response did not cover the chunk.", status_code=502
                        )
                    results = [
                        item.model_copy(
                            update={"effective_provider": provider, "effective_model": adapted_request.model}
                        )
                        for item in results
                    ]
                    with self.session_factory.begin() as session:
                        session.add(
                            AnalysisCacheModel(
                                project_id=project_id,
                                kind="SEMANTIC",
                                cache_key=cache_key,
                                result_data={"items": [item.model_dump(mode="json") for item in results]},
                            )
                        )
                    return results
                except AppError as exc:
                    last_error = exc
                    if attempt < request.retries:
                        if cancelled():
                            raise AppError(
                                "JOB_CANCELLED", "Semantic analysis was cancelled.", status_code=409
                            ) from exc
                        time.sleep(min(0.25 * (2**attempt), 1.0))
        assert last_error is not None
        raise last_error


def _chunks(candidates: list[CandidateModel], char_limit: int) -> list[list[CandidateModel]]:
    chunks: list[list[CandidateModel]] = []
    current: list[CandidateModel] = []
    size = 0
    for candidate in candidates:
        candidate_size = len(str(candidate.context.get("text", ""))) + 100
        if candidate_size > char_limit:
            raise AppError(
                "CANDIDATE_CHUNK_TOO_LARGE",
                "A candidate excerpt exceeds chunk_char_limit; reduce the excerpt or increase the limit.",
                status_code=422,
                details={
                    "candidate_id": candidate.id,
                    "candidate_chars": candidate_size,
                    "chunk_char_limit": char_limit,
                },
            )
        if current and size + candidate_size > char_limit:
            chunks.append(current)
            current, size = [], 0
        current.append(candidate)
        size += candidate_size
    if current:
        chunks.append(current)
    return chunks


def _validated_recommendation(
    candidate: CandidateModel, metrics: SemanticMetrics, timeline_start: int, timeline_end: int
) -> tuple[int, int]:
    fallback_start = max(timeline_start, candidate.start_ms)
    fallback_end = min(timeline_end, candidate.end_ms, fallback_start + 60_000)
    recommended_start = max(fallback_start, metrics.recommended_start_ms)
    recommended_end = min(fallback_end, metrics.recommended_end_ms, recommended_start + 60_000)
    if recommended_end <= recommended_start:
        return fallback_start, fallback_end
    return recommended_start, recommended_end
