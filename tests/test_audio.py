from __future__ import annotations

import pytest

from stt_sandbox.audio import AudioDecodeError, audio_duration_seconds, decode_to_pcm


def test_decode_wav_happy_path(short_es_wav):
    data = short_es_wav.read_bytes()
    out = decode_to_pcm(data, "audio/wav")
    try:
        assert out.exists()
        assert out.read_bytes() == data
        assert audio_duration_seconds(out) > 0
    finally:
        out.unlink(missing_ok=True)


def test_decode_wav_sniff_without_mime(short_es_wav):
    data = short_es_wav.read_bytes()
    out = decode_to_pcm(data, None)
    try:
        assert out.exists()
    finally:
        out.unlink(missing_ok=True)


def test_decode_empty_payload_raises():
    with pytest.raises(AudioDecodeError):
        decode_to_pcm(b"", "audio/wav")


def test_decode_non_wav_raises_with_phase2_hint():
    with pytest.raises(AudioDecodeError) as exc:
        decode_to_pcm(b"\x1aE\xdf\xa3 not a wav", "audio/webm")
    assert "Phase 2" in str(exc.value)


def test_decode_garbage_wav_mime_raises():
    with pytest.raises(AudioDecodeError):
        decode_to_pcm(b"RIFFxxxxWAVEgarbage", "audio/wav")
