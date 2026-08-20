from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.ai.schemas import AIProvider, SemanticAnalysisRequest, SemanticMetrics
from app.ai.service import (
    PROVIDER_PARAMETERS,
    JsonHttpProvider,
    SemanticAnalysisService,
    _chunks,
    _validated_recommendation,
)
from app.core.errors import AppError
from app.core.settings import Settings
from app.db.models import CandidateModel, ProjectModel
from app.db.session import build_engine, build_session_factory, initialize_database

PROJECT_ID = "2ef83f41-3d17-4cf1-a09b-8ca04882dd0f"


class _Provider:
    def __init__(self, result: list[dict[str, object]] | AppError) -> None:
        self.result = result
        self.calls = 0
        self.payloads: list[list[dict[str, object]]] = []

    def analyze(self, payload: list[dict[str, object]], _request: SemanticAnalysisRequest) -> list[dict[str, object]]:
        self.calls += 1
        self.payloads.append(payload)
        if isinstance(self.result, AppError):
            raise self.result
        return self.result


def _service(tmp_path: Path, adapters: dict[AIProvider, _Provider]) -> SemanticAnalysisService:
    engine = build_engine(tmp_path / "semantic.sqlite3")
    initialize_database(engine)
    factory = build_session_factory(engine)
    with factory.begin() as session:
        session.add(ProjectModel(id=PROJECT_ID, name="Semantic contract"))
    return SemanticAnalysisService(Settings(storage_root=tmp_path / "storage"), factory, adapters=adapters)


def _candidate(candidate_id: str) -> CandidateModel:
    return CandidateModel(
        id=candidate_id,
        project_id=PROJECT_ID,
        transcript_id="transcript",
        start_ms=1_000,
        end_ms=6_000,
        title="Candidate",
        reasons=[],
        context={"text": "only this excerpt leaves the device"},
        signals={},
    )


def _result(candidate_id: str) -> list[dict[str, object]]:
    return [
        {
            "candidate_id": candidate_id,
            "metrics": {
                "hook": 0.5,
                "humor": 0,
                "novelty": 0,
                "context_completeness": 1,
                "standalone_quality": 1,
                "information_value": 1,
                "narrative_progression": 1,
                "dead_air_penalty": 0,
                "recommended_start_ms": 1_000,
                "recommended_end_ms": 6_000,
            },
        }
    ]


def test_provider_cache_uses_local_double_and_sends_only_candidate_excerpt(tmp_path: Path) -> None:
    provider = _Provider(_result("candidate-a"))
    service = _service(tmp_path, {AIProvider.OPENAI: provider})
    request = SemanticAnalysisRequest(
        provider=AIProvider.OPENAI, model="gpt-4.1-mini", candidate_ids=["candidate-a"], opt_in_external_processing=True
    )

    first = service._cached_or_call(PROJECT_ID, [_candidate("candidate-a")], request)
    second = service._cached_or_call(PROJECT_ID, [_candidate("candidate-a")], request)

    assert first == second
    assert provider.calls == 1
    assert provider.payloads == [
        [
            {
                "candidate_id": "candidate-a",
                "start_ms": 1_000,
                "end_ms": 6_000,
                "text": "only this excerpt leaves the device",
            }
        ]
    ]


def test_provider_falls_back_with_double_and_absent_provider_is_predictable(tmp_path: Path) -> None:
    primary = _Provider(AppError("PROVIDER_REQUEST_FAILED", "timeout", status_code=502))
    fallback = _Provider(_result("candidate-b"))
    service = _service(tmp_path, {AIProvider.OPENAI: primary, AIProvider.GROQ: fallback})
    request = SemanticAnalysisRequest(
        provider=AIProvider.OPENAI,
        model="gpt-4.1-mini",
        candidate_ids=["candidate-b"],
        opt_in_external_processing=True,
        retries=0,
        fallback_provider=AIProvider.GROQ,
    )

    result = service._cached_or_call(PROJECT_ID, [_candidate("candidate-b")], request)

    assert result[0].candidate_id == "candidate-b"
    assert result[0].effective_provider == AIProvider.GROQ
    assert result[0].effective_model == "llama-3.3-70b-versatile"
    assert (primary.calls, fallback.calls) == (1, 1)
    absent = _service(tmp_path / "absent", {})
    with pytest.raises(AppError, match="not configured") as failure:
        absent._adapter(AIProvider.GEMINI)
    assert failure.value.code == "PROVIDER_NOT_CONFIGURED"


def test_gpt_4_1_models_reject_reasoning_effort_and_use_responses_structured_output(tmp_path: Path) -> None:
    service = _service(tmp_path, {AIProvider.OPENAI: _Provider([])})
    request = SemanticAnalysisRequest(
        provider=AIProvider.OPENAI,
        model="gpt-4.1-mini",
        candidate_ids=["candidate-a"],
        opt_in_external_processing=True,
        reasoning_effort="low",
    )

    assert "reasoning_effort" not in PROVIDER_PARAMETERS[AIProvider.OPENAI]
    with pytest.raises(AppError) as failure:
        service.analyze(PROJECT_ID, request, lambda _value: None, lambda: False)
    assert failure.value.code == "UNSUPPORTED_PROVIDER_PARAMETER"

    provider = JsonHttpProvider(AIProvider.OPENAI, "not-sent")
    compatible = request.model_copy(update={"reasoning_effort": None})
    _url, _headers, body = provider._request_data("prompt", compatible)
    assert "reasoning" not in body
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["text"]["format"]["strict"] is True
    assert (
        provider._content(
            {"output": [{"type": "message", "content": [{"type": "output_text", "text": '{"items": []}'}]}]}
        )
        == '{"items": []}'
    )


def test_invalid_provider_schema_becomes_predictable_provider_error(tmp_path: Path) -> None:
    provider = _Provider([{"candidate_id": "candidate-invalid", "metrics": {"hook": "not-a-number"}}])
    service = _service(tmp_path, {AIProvider.OPENAI: provider})
    request = SemanticAnalysisRequest(
        provider=AIProvider.OPENAI,
        model="gpt-4.1-mini",
        candidate_ids=["candidate-invalid"],
        opt_in_external_processing=True,
        retries=0,
    )

    with pytest.raises(AppError, match="invalid semantic metrics") as failure:
        service._cached_or_call(PROJECT_ID, [_candidate("candidate-invalid")], request)

    assert failure.value.code == "PROVIDER_INVALID_RESPONSE"


def test_semantic_request_rejects_duplicate_ids_and_oversized_single_chunk() -> None:
    with pytest.raises(ValidationError, match="unique"):
        SemanticAnalysisRequest(
            provider=AIProvider.OPENAI,
            model="gpt-4.1-mini",
            candidate_ids=["duplicate", "duplicate"],
            opt_in_external_processing=True,
        )

    candidate = _candidate("candidate-large")
    candidate.context = {"text": "x" * 600}
    with pytest.raises(AppError) as failure:
        _chunks([candidate], 500)
    assert failure.value.code == "CANDIDATE_CHUNK_TOO_LARGE"


def test_invalid_ai_timestamps_fallback_to_at_most_sixty_seconds() -> None:
    candidate = _candidate("legacy-long")
    candidate.start_ms = 10_000
    candidate.end_ms = 100_000
    metrics = SemanticMetrics(
        hook=0,
        humor=0,
        novelty=0,
        context_completeness=0,
        standalone_quality=0,
        information_value=0,
        narrative_progression=0,
        dead_air_penalty=0,
        recommended_start_ms=200_000,
        recommended_end_ms=210_000,
    )

    start_ms, end_ms = _validated_recommendation(candidate, metrics, 0, 120_000)

    assert (start_ms, end_ms) == (10_000, 70_000)
    assert end_ms - start_ms <= 60_000
