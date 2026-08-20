from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import CandidateModel, MediaModel, MediaRole, ProjectModel, ScoreProfileModel, TranscriptModel
from app.db.session import build_engine, build_session_factory, initialize_database
from app.scoring.service import score_and_rank

PROJECT_ID = "2ef83f41-3d17-4cf1-a09b-8ca04882dd0f"
MEDIA_ID = "2c4d3443-a8f1-4d75-befb-0dab0a3b37e9"
TRANSCRIPT_ID = "c8effc6b-676b-45fd-8e8e-34eb97c3641c"
SECOND_TRANSCRIPT_ID = "d8effc6b-676b-45fd-8e8e-34eb97c3641c"


def _factory_with_rankable_candidates(tmp_path: Path):
    engine = build_engine(tmp_path / "score.sqlite3")
    initialize_database(engine)
    factory = build_session_factory(engine)
    with factory.begin() as session:
        session.add(ProjectModel(id=PROJECT_ID, name="Scores"))
        session.flush()
        session.add(
            MediaModel(
                id=MEDIA_ID,
                project_id=PROJECT_ID,
                role=MediaRole.SCREEN,
                relative_path=f"projects/{PROJECT_ID}/screen.mp4",
                original_filename="screen.mp4",
                size_bytes=1,
                sha256="a" * 64,
                probe_data={"duration_ms": 60_000, "audio_streams": []},
            )
        )
        session.flush()
        session.add(
            TranscriptModel(
                id=TRANSCRIPT_ID,
                project_id=PROJECT_ID,
                media_id=MEDIA_ID,
                cache_key="b" * 64,
                relative_path="transcripts/score.json",
                language="pt",
                duration_ms=60_000,
            )
        )
        session.add(
            TranscriptModel(
                id=SECOND_TRANSCRIPT_ID,
                project_id=PROJECT_ID,
                media_id=MEDIA_ID,
                cache_key="c" * 64,
                relative_path="transcripts/second.json",
                language="pt",
                duration_ms=60_000,
            )
        )
        session.flush()
        for candidate_id, start, end, hook, dead_air in [
            ("candidate-a", 0, 20_000, 1.0, 0.0),
            ("candidate-b", 5_000, 25_000, 0.9, 0.0),
            ("candidate-c", 25_000, 45_000, 0.4, 0.0),
        ]:
            session.add(
                CandidateModel(
                    id=candidate_id,
                    project_id=PROJECT_ID,
                    transcript_id=TRANSCRIPT_ID,
                    start_ms=start,
                    end_ms=end,
                    title=candidate_id,
                    reasons=[],
                    context={"text": candidate_id},
                    signals={"hook": hook, "dead_air_penalty": dead_air, "ignored": 1.0},
                )
            )
        profile = ScoreProfileModel(
            id="profile",
            project_id=PROJECT_ID,
            name="Contract profile",
            is_default=False,
            rules=[
                {"key": "hook", "label": "Hook", "weight": 2.0, "enabled": True},
                {"key": "dead_air_penalty", "label": "Dead air", "weight": -1.0, "enabled": True},
                {"key": "ignored", "label": "Ignored", "weight": 10.0, "enabled": False},
            ],
        )
        session.add(profile)
        session.add_all(
            [
                CandidateModel(
                    id="candidate-rejected",
                    project_id=PROJECT_ID,
                    transcript_id=TRANSCRIPT_ID,
                    start_ms=45_000,
                    end_ms=55_000,
                    title="Rejected",
                    status="REJECTED",
                    reasons=[],
                    context={"text": "rejected"},
                    signals={"hook": 1.0},
                ),
                CandidateModel(
                    id="candidate-other-transcript",
                    project_id=PROJECT_ID,
                    transcript_id=SECOND_TRANSCRIPT_ID,
                    start_ms=45_000,
                    end_ms=55_000,
                    title="Other transcript",
                    reasons=[],
                    context={"text": "other transcript"},
                    signals={"hook": 1.0},
                ),
            ]
        )
    return factory


def test_scoring_persists_explainable_weighted_breakdown_and_respects_disabled_rules(tmp_path: Path) -> None:
    factory = _factory_with_rankable_candidates(tmp_path)

    with factory.begin() as session:
        selected = score_and_rank(session, PROJECT_ID, TRANSCRIPT_ID, None, "profile", top_n=2, max_overlap_ratio=0.5)
        assert [candidate.id for candidate in selected] == ["candidate-a", "candidate-c"]
        candidate = session.get(CandidateModel, "candidate-a")
        assert candidate is not None
        assert candidate.score == 83.333
        assert candidate.score_breakdown is not None
        assert [line["rule"] for line in candidate.score_breakdown] == ["hook", "dead_air_penalty"]
        assert candidate.score_breakdown[0]["contribution"] == 2.0
        assert candidate.score_breakdown[1]["contribution"] == 0.0

    with factory() as session:
        restored = session.get(CandidateModel, "candidate-a")
        assert restored is not None
        assert restored.score_breakdown is not None
        assert len(list(session.scalars(select(ScoreProfileModel)))) == 1


def test_ranking_is_scoped_to_transcript_excludes_rejected_and_validates_requested_selection(tmp_path: Path) -> None:
    factory = _factory_with_rankable_candidates(tmp_path)

    with factory.begin() as session:
        selected = score_and_rank(
            session, PROJECT_ID, TRANSCRIPT_ID, ["candidate-a", "candidate-b"], "profile", 10, 0.5
        )
        assert [candidate.id for candidate in selected] == ["candidate-a"]
        all_for_transcript = score_and_rank(session, PROJECT_ID, TRANSCRIPT_ID, None, "profile", 10, 0.5)
        assert "candidate-rejected" not in [candidate.id for candidate in all_for_transcript]
        assert "candidate-other-transcript" not in [candidate.id for candidate in all_for_transcript]

        with pytest.raises(AppError) as failure:
            score_and_rank(
                session,
                PROJECT_ID,
                TRANSCRIPT_ID,
                ["candidate-a", "candidate-other-transcript"],
                "profile",
                10,
                0.5,
            )
        assert failure.value.code == "CANDIDATE_NOT_FOUND"
