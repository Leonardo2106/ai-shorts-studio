from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.editor.schemas import AudioTrackConfig


@pytest.mark.parametrize("gain_db", [-60.0, 0.0, 12.0])
def test_audio_track_gain_accepts_each_contract_boundary(gain_db: float) -> None:
    assert AudioTrackConfig(media_id="media", stream_index=0, gain_db=gain_db).gain_db == gain_db


@pytest.mark.parametrize("gain_db", [-60.1, 12.1])
def test_audio_track_gain_rejects_values_outside_contract_bounds(gain_db: float) -> None:
    with pytest.raises(ValidationError):
        AudioTrackConfig(media_id="media", stream_index=0, gain_db=gain_db)
