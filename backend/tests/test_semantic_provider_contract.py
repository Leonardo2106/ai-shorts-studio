from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path

import pytest
from pydantic import ValidationError

import app.ai.service as ai_service_module
from app.ai.schemas import AIProvider, SemanticAnalysisRequest, SemanticMetrics
from app.ai.service import (
    OPENAI_SEMANTIC_SCHEMA,
    PROVIDER_MODELS,
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
        self.requests: list[SemanticAnalysisRequest] = []

    def analyze(self, payload: list[dict[str, object]], request: SemanticAnalysisRequest) -> list[dict[str, object]]:
        self.calls += 1
        self.payloads.append(payload)
        self.requests.append(request)
        if isinstance(self.result, AppError):
            raise self.result
        return self.result


def _service(tmp_path: Path, adapters: dict[AIProvider, _Provider]) -> SemanticAnalysisService:
    engine = build_engine(tmp_path / "semantic.sqlite3")
    initialize_database(engine)
    factory = build_session_factory(engine)
    with factory.begin() as session:
        session.add(ProjectModel(id=PROJECT_ID, name="Semantic contract"))
    # Provider availability must be controlled by the test double, not by a
    # developer's local .env file. Explicit init values take precedence over
    # environment settings without touching real secrets.
    settings = Settings(
        storage_root=tmp_path / "storage",
        openai_api_key=None,
        gemini_api_key=None,
        groq_api_key=None,
    )
    return SemanticAnalysisService(settings, factory, adapters=adapters)


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

    first, first_cache_hit = service._cached_or_call_with_status(PROJECT_ID, [_candidate("candidate-a")], request)
    second, second_cache_hit = service._cached_or_call_with_status(PROJECT_ID, [_candidate("candidate-a")], request)

    assert first == second
    assert first_cache_hit is False
    assert second_cache_hit is True
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
    assert result[0].effective_model == "openai/gpt-oss-120b"
    assert (primary.calls, fallback.calls) == (1, 1)
    absent = _service(tmp_path / "absent", {})
    with pytest.raises(AppError, match="not configured") as failure:
        absent._adapter(AIProvider.GEMINI)
    assert failure.value.code == "PROVIDER_NOT_CONFIGURED"


def test_fallback_keeps_a_dedicated_timeout_budget_after_slow_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [0.0]

    class _SlowFailure(_Provider):
        def analyze(
            self, payload: list[dict[str, object]], request: SemanticAnalysisRequest
        ) -> list[dict[str, object]]:
            clock[0] += request.timeout_seconds
            return super().analyze(payload, request)

    primary = _SlowFailure(AppError("PROVIDER_TIMEOUT", "timeout", status_code=504))
    fallback = _Provider(_result("candidate-budget"))
    service = _service(tmp_path, {AIProvider.OPENAI: primary, AIProvider.GROQ: fallback})
    request = SemanticAnalysisRequest(
        provider=AIProvider.OPENAI,
        model="gpt-4.1-mini",
        candidate_ids=["candidate-budget"],
        opt_in_external_processing=True,
        timeout_seconds=10,
        retries=0,
        fallback_provider=AIProvider.GROQ,
    )
    monkeypatch.setattr(ai_service_module.time, "monotonic", lambda: clock[0])

    result = service._cached_or_call(PROJECT_ID, [_candidate("candidate-budget")], request)

    assert result[0].effective_provider == AIProvider.GROQ
    assert primary.requests[0].timeout_seconds == pytest.approx(10)
    assert fallback.requests[0].timeout_seconds == pytest.approx(10)


def test_global_job_budget_caps_multichunk_retries_and_fallback(tmp_path: Path) -> None:
    service = _service(tmp_path, {AIProvider.OPENAI: _Provider([])})
    request = SemanticAnalysisRequest(
        provider=AIProvider.OPENAI,
        model="gpt-4.1-mini",
        candidate_ids=["candidate-a"],
        opt_in_external_processing=True,
        retries=3,
        fallback_provider=AIProvider.GROQ,
    )

    with pytest.raises(AppError) as failure:
        service._job_budget(4, request)

    assert failure.value.code == "SEMANTIC_JOB_TOO_LARGE"
    assert failure.value.details == {
        "chunks": 4,
        "planned_provider_calls": 32,
        "max_chunks": 20,
        "max_provider_calls": 24,
    }


def test_shared_deadline_reserves_fallback_across_multiple_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [0.0]

    class _SlowPrimary:
        requests: list[SemanticAnalysisRequest]

        def __init__(self) -> None:
            self.requests = []

        def analyze(
            self, _payload: list[dict[str, object]], request: SemanticAnalysisRequest
        ) -> list[dict[str, object]]:
            self.requests.append(request)
            clock[0] += request.timeout_seconds
            raise AppError("PROVIDER_TIMEOUT", "timeout", status_code=504)

    class _DynamicFallback:
        requests: list[SemanticAnalysisRequest]

        def __init__(self) -> None:
            self.requests = []

        def analyze(
            self, payload: list[dict[str, object]], request: SemanticAnalysisRequest
        ) -> list[dict[str, object]]:
            self.requests.append(request)
            return _result(str(payload[0]["candidate_id"]))

    primary = _SlowPrimary()
    fallback = _DynamicFallback()
    engine = build_engine(tmp_path / "global-budget.sqlite3")
    initialize_database(engine)
    factory = build_session_factory(engine)
    with factory.begin() as session:
        session.add(ProjectModel(id=PROJECT_ID, name="Global budget"))
    service = SemanticAnalysisService(
        Settings(
            storage_root=tmp_path / "storage",
            openai_api_key=None,
            gemini_api_key=None,
            groq_api_key=None,
        ),
        factory,
        adapters={AIProvider.OPENAI: primary, AIProvider.GROQ: fallback},
    )
    request = SemanticAnalysisRequest(
        provider=AIProvider.OPENAI,
        model="gpt-4.1-mini",
        candidate_ids=["chunk-a", "chunk-b"],
        opt_in_external_processing=True,
        timeout_seconds=10,
        retries=1,
        fallback_provider=AIProvider.GROQ,
    )
    monkeypatch.setattr(ai_service_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(ai_service_module.time, "sleep", lambda _seconds: None)
    budget = service._job_budget(2, request)

    first, _ = service._cached_or_call_with_budget(
        PROJECT_ID, [_candidate("chunk-a")], request, budget=budget
    )
    second, _ = service._cached_or_call_with_budget(
        PROJECT_ID, [_candidate("chunk-b")], request, budget=budget
    )

    assert [first[0].candidate_id, second[0].candidate_id] == ["chunk-a", "chunk-b"]
    assert len(primary.requests) == 4
    assert len(fallback.requests) == 2
    assert all(item.timeout_seconds == pytest.approx(10) for item in [*primary.requests, *fallback.requests])
    assert budget.calls_made == 6
    assert budget.remaining_slots == 0


def test_whitespace_provider_key_is_not_configured(tmp_path: Path) -> None:
    settings = Settings(storage_root=tmp_path, openai_api_key="  \t ")
    engine = build_engine(tmp_path / "whitespace.sqlite3")
    initialize_database(engine)
    service = SemanticAnalysisService(settings, build_session_factory(engine))

    assert settings.provider_key(AIProvider.OPENAI) is None
    assert service.configured(AIProvider.OPENAI) is False


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


def test_provider_request_bodies_use_strict_structured_output_contracts() -> None:
    requests = {
        provider: SemanticAnalysisRequest(
            provider=provider,
            model=PROVIDER_MODELS[provider][0],
            candidate_ids=["candidate-a"],
            opt_in_external_processing=True,
        )
        for provider in AIProvider
    }

    _url, _headers, openai = JsonHttpProvider(AIProvider.OPENAI, "key")._request_data(
        "prompt", requests[AIProvider.OPENAI]
    )
    assert openai["text"]["format"] == {
        "type": "json_schema",
        "name": "candidate_semantic_analysis",
        "strict": True,
        "schema": OPENAI_SEMANTIC_SCHEMA,
    }

    _url, _headers, groq = JsonHttpProvider(AIProvider.GROQ, "key")._request_data(
        "prompt", requests[AIProvider.GROQ]
    )
    assert groq["model"] == "openai/gpt-oss-120b"
    assert groq["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "candidate_semantic_analysis",
            "strict": True,
            "schema": OPENAI_SEMANTIC_SCHEMA,
        },
    }

    _url, _headers, gemini = JsonHttpProvider(AIProvider.GEMINI, "key")._request_data(
        "prompt", requests[AIProvider.GEMINI]
    )
    assert gemini["generationConfig"]["responseMimeType"] == "application/json"
    assert gemini["generationConfig"]["responseJsonSchema"] == OPENAI_SEMANTIC_SCHEMA


@pytest.mark.parametrize(
    ("provider", "response"),
    [
        (
            AIProvider.OPENAI,
            {"output": [{"content": [{"type": "output_text", "text": '{"items": []}'}]}]},
        ),
        (
            AIProvider.GEMINI,
            {"candidates": [{"content": {"parts": [{"text": '{"items": []}'}]}}]},
        ),
        (AIProvider.GROQ, {"choices": [{"message": {"content": '{"items": []}'}}]}),
    ],
)
def test_provider_transport_parses_each_supported_response_without_network(
    monkeypatch: pytest.MonkeyPatch, provider: AIProvider, response: dict[str, object]
) -> None:
    class _Response:
        headers: dict[str, str] = {}

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _size: int) -> bytes:
            return json.dumps(response).encode()

    calls: list[tuple[urllib.request.Request, float]] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        calls.append((request, timeout))
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    request = SemanticAnalysisRequest(
        provider=provider,
        model=PROVIDER_MODELS[provider][0],
        candidate_ids=["candidate-a"],
        opt_in_external_processing=True,
    )

    assert JsonHttpProvider(provider, "transport-secret").analyze([], request) == []
    assert len(calls) == 1
    assert b"transport-secret" not in (calls[0][0].data or b"")


@pytest.mark.parametrize(
    ("provider", "response"),
    [
        (AIProvider.GEMINI, {"candidates": [], "promptFeedback": {"blockReason": "private fragment"}}),
        (AIProvider.GROQ, {"choices": []}),
    ],
)
def test_empty_provider_envelopes_are_sanitized_structured_errors(
    monkeypatch: pytest.MonkeyPatch, provider: AIProvider, response: dict[str, object]
) -> None:
    class _Response:
        headers: dict[str, str] = {}

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _size: int) -> bytes:
            return json.dumps(response).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda _request, timeout: _Response())
    request = SemanticAnalysisRequest(
        provider=provider,
        model=PROVIDER_MODELS[provider][0],
        candidate_ids=["candidate-a"],
        opt_in_external_processing=True,
    )

    with pytest.raises(AppError) as failure:
        JsonHttpProvider(provider, "secret").analyze([], request)

    assert failure.value.code == "PROVIDER_INVALID_RESPONSE"
    assert failure.value.details == {}
    assert "private fragment" not in failure.value.message


def test_empty_gemini_envelope_activates_groq_fallback_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Response:
        headers: dict[str, str] = {}

        def __init__(self, response: dict[str, object]) -> None:
            self.response = response

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _size: int) -> bytes:
            return json.dumps(self.response).encode()

    groq_item = _result("candidate-envelope")[0]

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        del timeout
        if "generativelanguage.googleapis.com" in request.full_url:
            return _Response({"candidates": [], "promptFeedback": {"blockReason": "private fragment"}})
        return _Response({"choices": [{"message": {"content": json.dumps({"items": [groq_item]})}}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    engine = build_engine(tmp_path / "envelope-fallback.sqlite3")
    initialize_database(engine)
    factory = build_session_factory(engine)
    with factory.begin() as session:
        session.add(ProjectModel(id=PROJECT_ID, name="Envelope fallback"))
    service = SemanticAnalysisService(
        Settings(storage_root=tmp_path / "storage", gemini_api_key="gemini-key", groq_api_key="groq-key"),
        factory,
    )
    request = SemanticAnalysisRequest(
        provider=AIProvider.GEMINI,
        model="gemini-2.5-flash",
        candidate_ids=["candidate-envelope"],
        opt_in_external_processing=True,
        retries=0,
        fallback_provider=AIProvider.GROQ,
    )

    result = service._cached_or_call(PROJECT_ID, [_candidate("candidate-envelope")], request)

    assert result[0].effective_provider == AIProvider.GROQ
    assert result[0].effective_model == "openai/gpt-oss-120b"


def test_provider_http_error_exposes_only_allowlisted_metadata_and_never_reads_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "provider-super-secret"
    prompt_echo = "only this excerpt leaves the device"
    headers = Message()
    headers["x-request-id"] = "req-safe-123"

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> None:
        del timeout
        # Providers may echo only a fragment of user input. Exact-string redaction is
        # insufficient, so arbitrary response bodies must never reach Job.error_details.
        error = json.dumps({"error": f"bad Bearer {secret}; fragment={prompt_echo[:12]}"}).encode()
        raise urllib.error.HTTPError(request.full_url, 429, "rate limited", headers, io.BytesIO(error))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    provider = JsonHttpProvider(AIProvider.OPENAI, secret)
    request = SemanticAnalysisRequest(
        provider=AIProvider.OPENAI,
        model="gpt-4.1-mini",
        candidate_ids=["candidate-a"],
        opt_in_external_processing=True,
    )

    with pytest.raises(AppError) as failure:
        provider.analyze([{"candidate_id": "candidate-a", "text": prompt_echo}], request)

    assert failure.value.code == "PROVIDER_REQUEST_FAILED"
    assert failure.value.details == {
        "provider": "OPENAI",
        "provider_status": 429,
        "category": "HTTP_ERROR",
        "request_id": "req-safe-123",
    }
    exposed = json.dumps(failure.value.details)
    assert secret not in exposed
    assert prompt_echo[:12] not in exposed


def test_provider_request_id_rejects_arbitrary_or_oversized_header(monkeypatch: pytest.MonkeyPatch) -> None:
    headers = Message()
    headers["x-request-id"] = "transcript fragment with spaces " + "x" * 200

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> None:
        del timeout
        raise urllib.error.HTTPError(request.full_url, 500, "failure", headers, io.BytesIO(b"private body"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    request = SemanticAnalysisRequest(
        provider=AIProvider.OPENAI,
        model="gpt-4.1-mini",
        candidate_ids=["candidate-a"],
        opt_in_external_processing=True,
    )

    with pytest.raises(AppError) as failure:
        JsonHttpProvider(AIProvider.OPENAI, "secret").analyze([], request)

    assert failure.value.details == {
        "provider": "OPENAI",
        "provider_status": 500,
        "category": "HTTP_ERROR",
    }


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


def test_estimate_reuses_chunking_and_reports_exact_provider_call_budget(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        {AIProvider.OPENAI: _Provider([]), AIProvider.GROQ: _Provider([])},
    )
    first = _candidate("estimate-a")
    second = _candidate("estimate-b")
    first.context = {"text": "a" * 350}
    second.context = {"text": "b" * 350}
    request = SemanticAnalysisRequest(
        provider=AIProvider.OPENAI,
        model="gpt-4.1-mini",
        candidate_ids=[first.id, second.id],
        opt_in_external_processing=True,
        retries=1,
        fallback_provider=AIProvider.GROQ,
        chunk_char_limit=500,
    )

    estimate = service.estimate([first, second], request)

    assert estimate.model_dump() == {
        "chunks": 2,
        "estimated_input_tokens": 225,
        "candidates": 2,
        "planned_provider_calls": 8,
        "max_provider_calls": 24,
    }

    first.context = {"text": "x" * 401}
    with pytest.raises(AppError) as failure:
        service.estimate([first], request.model_copy(update={"candidate_ids": [first.id]}))
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
