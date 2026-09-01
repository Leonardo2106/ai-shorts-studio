from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.ai.schemas import (
    AIProvider,
    AnalysisEstimate,
    CandidateSemanticResult,
    SemanticAnalysisRequest,
    SemanticMetrics,
)
from app.candidates.service import project_timeline_bounds
from app.core.errors import AppError
from app.core.settings import Settings
from app.db.models import AnalysisCacheModel, CandidateModel, ProjectModel

PROVIDER_MODELS: dict[AIProvider, list[str]] = {
    AIProvider.OPENAI: ["gpt-4.1-mini", "gpt-4.1"],
    AIProvider.GEMINI: ["gemini-2.5-flash", "gemini-2.5-pro"],
    AIProvider.GROQ: ["openai/gpt-oss-120b"],
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

_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def _safe_request_id(value: object) -> str | None:
    candidate = str(value).strip()
    return candidate if _SAFE_REQUEST_ID.fullmatch(candidate) else None


@dataclass
class _ProviderCallBudget:
    deadline: float
    remaining_slots: int
    calls_made: int = 0

    def next_timeout(self, requested_timeout: float) -> float:
        remaining_time = self.deadline - time.monotonic()
        if self.remaining_slots <= 0:
            raise AppError(
                "PROVIDER_CALL_BUDGET_EXHAUSTED",
                "Semantic analysis exhausted its provider call budget.",
                status_code=422,
            )
        if remaining_time < 1:
            raise AppError(
                "PROVIDER_TIMEOUT", "Semantic analysis exceeded its total deadline.", status_code=504
            )
        timeout = min(requested_timeout, remaining_time / self.remaining_slots)
        self.remaining_slots -= 1
        self.calls_made += 1
        return max(1.0, timeout)

    def release(self, unused_slots: int) -> None:
        self.remaining_slots = max(0, self.remaining_slots - max(0, unused_slots))


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
        except urllib.error.HTTPError as exc:
            request_id = _safe_request_id(exc.headers.get("x-request-id") or exc.headers.get("request-id"))
            details: dict[str, object] = {
                "provider": self.provider.value,
                "provider_status": exc.code,
                "category": "HTTP_ERROR",
            }
            if request_id:
                details["request_id"] = request_id
            exc.close()
            raise AppError(
                "PROVIDER_REQUEST_FAILED",
                f"{self.provider.value} analysis request failed.",
                status_code=502,
                details=details,
            ) from exc
        except AppError:
            raise
        except TimeoutError as exc:
            raise AppError(
                "PROVIDER_TIMEOUT",
                f"{self.provider.value} analysis request timed out.",
                status_code=504,
                details={"provider": self.provider.value},
            ) from exc
        except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise AppError(
                "PROVIDER_REQUEST_FAILED",
                f"{self.provider.value} analysis request failed.",
                status_code=502,
                details={"provider": self.provider.value},
            ) from exc
        try:
            content = self._content(raw)
            parsed = json.loads(content) if isinstance(content, str) else content
            items = cast(list[dict[str, Any]], cast(dict[str, object], parsed)["items"])
            return items
        except (AttributeError, IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
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
                "responseJsonSchema": OPENAI_SEMANTIC_SCHEMA,
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
                "max_completion_tokens": request.max_output_tokens,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "candidate_semantic_analysis",
                        "strict": True,
                        "schema": OPENAI_SEMANTIC_SCHEMA,
                    },
                },
            }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.top_p is not None:
            body["top_p"] = request.top_p
        return base, headers, body

    def _content(self, raw: dict[str, Any]) -> object:
        if self.provider == AIProvider.OPENAI:
            output_items = raw.get("output")
            if not isinstance(output_items, list):
                raise KeyError("Responses output is missing")
            for output in output_items:
                if not isinstance(output, dict):
                    continue
                for content in output.get("content", []):
                    if isinstance(content, dict) and content.get("type") == "output_text":
                        return content["text"]
            raise KeyError("Responses output contained no output_text")
        if self.provider == AIProvider.GEMINI:
            candidates = raw.get("candidates")
            if not isinstance(candidates, list) or not candidates:
                raise KeyError("Gemini response contained no candidates")
            candidate = candidates[0]
            if not isinstance(candidate, dict):
                raise KeyError("Gemini candidate is invalid")
            content = candidate.get("content")
            if not isinstance(content, dict):
                raise KeyError("Gemini candidate content is missing")
            parts = content.get("parts")
            if not isinstance(parts, list) or not parts or not isinstance(parts[0], dict):
                raise KeyError("Gemini candidate parts are missing")
            return parts[0]["text"]
        choices = raw.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise KeyError("Groq response contained no choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise KeyError("Groq response message is missing")
        return message["content"]


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

    def validate_request(self, request: SemanticAnalysisRequest) -> None:
        if request.model not in PROVIDER_MODELS[request.provider]:
            raise AppError("UNSUPPORTED_PROVIDER_MODEL", "Model is not supported for this provider.", status_code=422)
        if request.reasoning_effort is not None and "reasoning_effort" not in PROVIDER_PARAMETERS[request.provider]:
            raise AppError(
                "UNSUPPORTED_PROVIDER_PARAMETER", "Provider does not support reasoning_effort.", status_code=422
            )
        if not self.configured(request.provider):
            raise AppError("PROVIDER_NOT_CONFIGURED", "Selected provider is not configured.", status_code=409)
        fallback = request.fallback_provider
        if fallback is not None and fallback != request.provider and not self.configured(fallback):
            raise AppError(
                "FALLBACK_PROVIDER_NOT_CONFIGURED",
                "Selected fallback provider is not configured.",
                status_code=409,
            )

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
        self.validate_request(request)
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
        budget = self._job_budget(len(chunks), request)
        completed: list[str] = []
        analyses: list[dict[str, str]] = []
        cached_chunks = 0
        for index, chunk in enumerate(chunks):
            if cancelled():
                raise AppError("JOB_CANCELLED", "Semantic analysis was cancelled.", status_code=409)
            results, cache_hit = self._cached_or_call_with_status(
                project_id, chunk, request, cancelled=cancelled, budget=budget
            )
            cached_chunks += int(cache_hit)
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
        return {
            "candidate_ids": completed,
            "chunks": len(chunks),
            "analyses": analyses,
            "cache_hit": cached_chunks == len(chunks),
            "cached_chunks": cached_chunks,
        }

    def _job_budget(self, chunk_count: int, request: SemanticAnalysisRequest) -> _ProviderCallBudget:
        planned_calls = self._validate_job_shape(chunk_count, request)
        total_timeout = min(self.settings.provider_job_timeout_seconds, request.timeout_seconds * planned_calls)
        return _ProviderCallBudget(time.monotonic() + total_timeout, planned_calls)

    def _validate_job_shape(self, chunk_count: int, request: SemanticAnalysisRequest) -> int:
        providers_per_chunk = 1 + int(
            request.fallback_provider is not None and request.fallback_provider != request.provider
        )
        calls_per_chunk = providers_per_chunk * (request.retries + 1)
        planned_calls = chunk_count * calls_per_chunk
        if (
            chunk_count > self.settings.provider_max_chunks_per_job
            or planned_calls > self.settings.provider_max_calls_per_job
        ):
            raise AppError(
                "SEMANTIC_JOB_TOO_LARGE",
                "Semantic analysis exceeds the configured local provider budget.",
                status_code=422,
                details={
                    "chunks": chunk_count,
                    "planned_provider_calls": planned_calls,
                    "max_chunks": self.settings.provider_max_chunks_per_job,
                    "max_provider_calls": self.settings.provider_max_calls_per_job,
                },
            )
        return planned_calls

    def estimate(self, candidates: list[CandidateModel], request: SemanticAnalysisRequest) -> AnalysisEstimate:
        self.validate_request(request)
        chunks = _chunks(candidates, request.chunk_char_limit)
        planned_calls = self._validate_job_shape(len(chunks), request)
        input_chars = sum(len(str(item.context.get("text", ""))) + 100 for item in candidates)
        return AnalysisEstimate(
            chunks=len(chunks),
            estimated_input_tokens=(input_chars + 3) // 4,
            candidates=len(candidates),
            planned_provider_calls=planned_calls,
            max_provider_calls=self.settings.provider_max_calls_per_job,
        )

    def _cached_or_call(
        self,
        project_id: str,
        candidates: list[CandidateModel],
        request: SemanticAnalysisRequest,
        *,
        cancelled: Callable[[], bool] = lambda: False,
        deadline: float | None = None,
    ) -> list[CandidateSemanticResult]:
        results, _cache_hit = self._cached_or_call_with_status(
            project_id,
            candidates,
            request,
            cancelled=cancelled,
            deadline=deadline,
        )
        return results

    def _cached_or_call_with_status(
        self,
        project_id: str,
        candidates: list[CandidateModel],
        request: SemanticAnalysisRequest,
        *,
        cancelled: Callable[[], bool] = lambda: False,
        deadline: float | None = None,
        budget: _ProviderCallBudget | None = None,
    ) -> tuple[list[CandidateSemanticResult], bool]:
        return self._cached_or_call_with_budget(
            project_id,
            candidates,
            request,
            cancelled=cancelled,
            deadline=deadline,
            budget=budget,
        )

    def _cached_or_call_with_budget(
        self,
        project_id: str,
        candidates: list[CandidateModel],
        request: SemanticAnalysisRequest,
        *,
        cancelled: Callable[[], bool] = lambda: False,
        deadline: float | None = None,
        budget: _ProviderCallBudget | None = None,
    ) -> tuple[list[CandidateSemanticResult], bool]:
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
            "v": 2,
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
        providers = [request.provider]
        if request.fallback_provider is not None and request.fallback_provider != request.provider:
            providers.append(request.fallback_provider)
        attempts_per_provider = request.retries + 1
        chunk_call_slots = len(providers) * attempts_per_provider
        with self.session_factory() as session:
            cached = session.scalar(select(AnalysisCacheModel).where(AnalysisCacheModel.cache_key == cache_key))
            if cached is not None:
                try:
                    if budget is not None:
                        budget.release(chunk_call_slots)
                    return (
                        [CandidateSemanticResult.model_validate(item) for item in cached.result_data["items"]],
                        True,
                    )
                except (KeyError, TypeError, ValidationError) as exc:
                    raise AppError(
                        "PROVIDER_INVALID_RESPONSE", "Cached provider response is invalid.", status_code=502
                    ) from exc
        if budget is None:
            effective_deadline = deadline or (time.monotonic() + min(300.0, request.timeout_seconds * chunk_call_slots))
            budget = _ProviderCallBudget(effective_deadline, chunk_call_slots)
        chunk_calls_made = 0
        last_error: AppError | None = None
        for provider in providers:
            if cancelled():
                raise AppError("JOB_CANCELLED", "Semantic analysis was cancelled.", status_code=409)
            adapted_request = request.model_copy(update={"provider": provider})
            if adapted_request.model not in PROVIDER_MODELS[provider]:
                adapted_request = adapted_request.model_copy(update={"model": PROVIDER_MODELS[provider][0]})
            for attempt in range(attempts_per_provider):
                if cancelled():
                    raise AppError("JOB_CANCELLED", "Semantic analysis was cancelled.", status_code=409)
                attempt_timeout = budget.next_timeout(request.timeout_seconds)
                adapted_request = adapted_request.model_copy(update={"timeout_seconds": attempt_timeout})
                chunk_calls_made += 1
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
                    budget.release(chunk_call_slots - chunk_calls_made)
                    return results, False
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
