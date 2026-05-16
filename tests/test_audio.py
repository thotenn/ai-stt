from __future__ import annotations

import shutil
import wave

import pytest

from stt_sandbox.audio import (
    AudioDecodeError,
    TARGET_CHANNELS,
    TARGET_RATE,
    TARGET_SAMPLE_WIDTH,
    audio_duration_seconds,
    decode_to_pcm,
)


HAS_FFMPEG = shutil.which("ffmpeg") is not None
ffmpeg_required = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")


def _assert_target_pcm(path) -> None:
    with wave.open(str(path), "rb") as wav:
        assert wav.getnchannels() == TARGET_CHANNELS
        assert wav.getsampwidth() == TARGET_SAMPLE_WIDTH
        assert wav.getframerate() == TARGET_RATE


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


def test_decode_garbage_wav_mime_raises():
    with pytest.raises(AudioDecodeError):
        decode_to_pcm(b"RIFFxxxxWAVEgarbage", "audio/wav")


@ffmpeg_required
@pytest.mark.parametrize(
    ("filename", "mime"),
    [
        ("short_es.webm", "audio/webm"),
        ("short_es.ogg", "audio/ogg"),
        ("short_es.mp3", "audio/mpeg"),
        ("short_es.flac", "audio/flac"),
    ],
)
def test_decode_non_wav_through_ffmpeg(fixtures_dir, filename, mime):
    path = fixtures_dir / filename
    if not path.exists():
        pytest.skip(f"missing fixture {path}")
    data = path.read_bytes()

    out = decode_to_pcm(data, mime)
    try:
        assert out.exists()
        _assert_target_pcm(out)
        assert audio_duration_seconds(out) > 0
    finally:
        out.unlink(missing_ok=True)


@ffmpeg_required
def test_decode_corrupt_non_wav_raises():
    with pytest.raises(AudioDecodeError):
        decode_to_pcm(b"\x1aE\xdf\xa3 not really an audio file", "audio/webm")


@ffmpeg_required
def test_decode_webm_without_mime_uses_ffmpeg(fixtures_dir):
    path = fixtures_dir / "short_es.webm"
    if not path.exists():
        pytest.skip(f"missing fixture {path}")

    out = decode_to_pcm(path.read_bytes(), None)
    try:
        assert out.exists()
        _assert_target_pcm(out)
    finally:
        out.unlink(missing_ok=True)
