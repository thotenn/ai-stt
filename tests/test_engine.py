from __future__ import annotations

from stt_sandbox.engine import SttEngine, TranscribeResult


def test_transcribe_returns_spanish_text(shared_engine: SttEngine, short_es_wav):
    result = shared_engine.transcribe(short_es_wav)
    assert isinstance(result, TranscribeResult)
    assert result.text, "transcript should not be empty"
    assert result.duration_seconds > 0
    assert result.decode_seconds > 0
    assert result.rtf > 0
    assert result.model == shared_engine.default_model
    assert result.language == "es"
    assert result.segments, "at least one segment expected"


def test_transcribe_text_contains_expected_words(shared_engine: SttEngine, short_es_wav):
    result = shared_engine.transcribe(short_es_wav)
    lowered = result.text.lower()
    must_have = ["planetas", "sistema", "solar"]
    missing = [word for word in must_have if word not in lowered]
    assert not missing, f"missing expected words {missing} in transcript: {result.text!r}"


def test_to_dict_round_trip(shared_engine: SttEngine, short_es_wav):
    result = shared_engine.transcribe(short_es_wav)
    payload = result.to_dict()
    assert set(payload).issuperset({
        "text", "language", "duration_seconds", "decode_seconds", "rtf", "model", "segments"
    })
    assert isinstance(payload["segments"], list)
    if payload["segments"]:
        seg = payload["segments"][0]
        assert set(seg).issuperset({"index", "start", "end", "text"})
