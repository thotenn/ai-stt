from __future__ import annotations

import os
import tempfile
import uuid
import wave
from pathlib import Path


WAV_MIME_TYPES = {"audio/wav", "audio/x-wav", "audio/wave"}
SUPPORTED_MIME_TYPES = WAV_MIME_TYPES


class AudioDecodeError(RuntimeError):
    pass


def _looks_like_wav(data: bytes, mime: str | None) -> bool:
    if mime and mime.lower() in WAV_MIME_TYPES:
        return True
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def _write_tempfile(data: bytes, suffix: str) -> Path:
    fd, raw_path = tempfile.mkstemp(prefix=f"stt-{uuid.uuid4().hex[:8]}-", suffix=suffix)
    path = Path(raw_path)
    try:
        with os.fdopen(fd, "wb") as fp:
            fp.write(data)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def decode_to_pcm(data: bytes, mime: str | None) -> Path:
    if not data:
        raise AudioDecodeError("empty audio payload")

    if not _looks_like_wav(data, mime):
        raise AudioDecodeError(
            f"unsupported audio MIME {mime!r}. Phase 1 accepts WAV only; "
            "non-WAV decoding (WebM/Opus/Ogg/MP3/FLAC) lands in Phase 2."
        )

    path = _write_tempfile(data, suffix=".wav")
    try:
        with wave.open(str(path), "rb"):
            pass
    except wave.Error as exc:
        path.unlink(missing_ok=True)
        raise AudioDecodeError(f"invalid WAV payload: {exc}") from exc

    return path


def audio_duration_seconds(wav_path: Path) -> float:
    with wave.open(str(wav_path), "rb") as wav:
        frames = wav.getnframes()
        rate = wav.getframerate()
    return frames / float(rate) if rate else 0.0
