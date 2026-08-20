from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import CandidateModel, ScoreProfileModel
from app.scoring.schemas import DEFAULT_RULES, ScoreRule


def score_profile(profile: ScoreProfileModel | None) -> list[ScoreRule]:
    return DEFAULT_RULES if profile is None else [ScoreRule.model_validate(rule) for rule in profile.rules]


def score_and_rank(
    session: Session,
    project_id: str,
    transcript_id: str,
    candidate_ids: list[str] | None,
    profile_id: str | None,
    top_n: int,
    max_overlap_ratio: float,
) -> list[CandidateModel]:
    profile = None
    if profile_id is not None:
        profile = session.get(ScoreProfileModel, profile_id)
        if profile is None or profile.project_id not in {None, project_id}:
            raise AppError("SCORE_PROFILE_NOT_FOUND", "Score profile was not found.", status_code=404)
    rules = score_profile(profile)
    query = select(CandidateModel).where(
        CandidateModel.project_id == project_id,
        CandidateModel.transcript_id == transcript_id,
    )
    if candidate_ids is not None:
        query = query.where(CandidateModel.id.in_(candidate_ids))
    scoped_candidates = list(session.scalars(query).all())
    if candidate_ids is not None and len(scoped_candidates) != len(candidate_ids):
        raise AppError(
            "CANDIDATE_NOT_FOUND",
            "One or more candidates are missing, rejected, or outside the requested transcript.",
            status_code=404,
        )
    candidates = [candidate for candidate in scoped_candidates if candidate.status != "REJECTED"]
    for candidate in candidates:
        breakdown: list[dict[str, object]] = []
        total = 0.0
        normalization = 0.0
        for rule in rules:
            if not rule.enabled:
                continue
            value = float(candidate.signals.get(rule.key, 0.0))
            contribution = value * rule.weight
            total += contribution
            normalization += abs(rule.weight)
            breakdown.append(
                {
                    "rule": rule.key,
                    "label": rule.label,
                    "value": value,
                    "weight": rule.weight,
                    "contribution": round(contribution, 4),
                }
            )
        candidate.score = round(50.0 + 50.0 * total / normalization, 3) if normalization else 0.0
        candidate.score = max(0.0, min(100.0, candidate.score))
        candidate.score_breakdown = breakdown
    session.flush()
    ranked = sorted(
        (candidate for candidate in candidates if 1_000 <= candidate.end_ms - candidate.start_ms <= 60_000),
        key=lambda candidate: (-(candidate.score or 0), candidate.start_ms),
    )
    selected: list[CandidateModel] = []
    remaining = list(ranked)
    diversity_counts: dict[str, int] = {}
    while remaining and len(selected) < top_n:
        eligible = [
            candidate
            for candidate in remaining
            if not any(
                _overlap_ratio(candidate, existing) > max_overlap_ratio or _same_excerpt(candidate, existing)
                for existing in selected
            )
        ]
        if not eligible:
            break
        candidate = max(
            eligible,
            key=lambda item: ((item.score or 0) - 5 * diversity_counts.get(_diversity_key(item), 0), -item.start_ms),
        )
        selected.append(candidate)
        key = _diversity_key(candidate)
        diversity_counts[key] = diversity_counts.get(key, 0) + 1
        remaining.remove(candidate)
    return sorted(selected, key=lambda candidate: (-(candidate.score or 0), candidate.start_ms))


def _overlap_ratio(left: CandidateModel, right: CandidateModel) -> float:
    overlap = max(0, min(left.end_ms, right.end_ms) - max(left.start_ms, right.start_ms))
    shorter = min(left.end_ms - left.start_ms, right.end_ms - right.start_ms)
    return overlap / shorter if shorter > 0 else 1.0


def _same_excerpt(left: CandidateModel, right: CandidateModel) -> bool:
    left_text = " ".join(str(left.context.get("text", "")).lower().split())
    right_text = " ".join(str(right.context.get("text", "")).lower().split())
    return bool(left_text) and left_text == right_text


def _diversity_key(candidate: CandidateModel) -> str:
    primary_reason = candidate.reasons[0] if candidate.reasons else "unspecified"
    return f"{candidate.context.get('source', 'UNKNOWN')}:{primary_reason}"
