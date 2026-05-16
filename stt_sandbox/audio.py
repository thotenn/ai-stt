from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid
import wave
from pathlib import Path


WAV_MIME_TYPES = {"audio/wav", "audio/x-wav", "audio/wave"}
NON_WAV_MIME_TYPES = {
    "audio/webm",
    "video/webm",
    "audio/ogg",
    "application/ogg",
    "audio/opus",
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/x-m4a",
    "audio/aac",
    "audio/flac",
    "audio/x-flac",
}
SUPPORTED_MIME_TYPES = WAV_MIME_TYPES | NON_WAV_MIME_TYPES

FFMPEG_TIMEOUT_SECONDS = 30
TARGET_RATE = 16000
TARGET_CHANNELS = 1
TARGET_SAMPLE_WIDTH = 2


class AudioDecodeError(RuntimeError):
    pass


_FFMPEG_PATH_CACHE: str | None | object = ...


def _resolve_ffmpeg() -> str:
    global _FFMPEG_PATH_CACHE
    if _FFMPEG_PATH_CACHE is ...:
        env_path = os.environ.get("STT_FFMPEG_BIN")
        candidate = env_path or "ffmpeg"
        _FFMPEG_PATH_CACHE = shutil.which(candidate)
    if not _FFMPEG_PATH_CACHE:
        raise AudioDecodeError(
            "ffmpeg binary not found. Install ffmpeg or set STT_FFMPEG_BIN to its path."
        )
    return _FFMPEG_PATH_CACHE


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


def _new_tempfile(suffix: str) -> Path:
    fd, raw_path = tempfile.mkstemp(prefix=f"stt-{uuid.uuid4().hex[:8]}-", suffix=suffix)
    os.close(fd)
    return Path(raw_path)


def _suffix_for_mime(mime: str | None) -> str:
    if not mime:
        return ".bin"
    mime = mime.lower().split(";")[0].strip()
    return {
        "audio/webm": ".webm",
        "video/webm": ".webm",
        "audio/ogg": ".ogg",
        "application/ogg": ".ogg",
        "audio/opus": ".opus",
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a",
        "audio/aac": ".aac",
        "audio/flac": ".flac",
        "audio/x-flac": ".flac",
    }.get(mime, ".bin")


def _decode_with_ffmpeg(data: bytes, mime: str | None) -> Path:
    ffmpeg = _resolve_ffmpeg()
    input_path = _write_tempfile(data, suffix=_suffix_for_mime(mime))
    output_path = _new_tempfile(suffix=".wav")

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-loglevel", "error",
        "-y",
        "-i", str(input_path),
        "-vn",
        "-ac", str(TARGET_CHANNELS),
        "-ar", str(TARGET_RATE),
        "-acodec", "pcm_s16le",
        "-f", "wav",
        str(output_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        output_path.unlink(missing_ok=True)
        raise AudioDecodeError(f"ffmpeg timed out after {FFMPEG_TIMEOUT_SECONDS}s") from exc
    finally:
        input_path.unlink(missing_ok=True)

    if result.returncode != 0:
        output_path.unlink(missing_ok=True)
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise AudioDecodeError(f"ffmpeg failed: {stderr or 'unknown error'}")

    if output_path.stat().st_size == 0:
        output_path.unlink(missing_ok=True)
        raise AudioDecodeError("ffmpeg produced an empty WAV file")

    return output_path


def _validate_wav(path: Path) -> None:
    try:
        with wave.open(str(path), "rb"):
            pass
    except wave.Error as exc:
        path.unlink(missing_ok=True)
        raise AudioDecodeError(f"invalid WAV payload: {exc}") from exc


def decode_to_pcm(data: bytes, mime: str | None) -> Path:
    if not data:
        raise AudioDecodeError("empty audio payload")

    if _looks_like_wav(data, mime):
        path = _write_tempfile(data, suffix=".wav")
        _validate_wav(path)
        return path

    return _decode_with_ffmpeg(data, mime)


def audio_duration_seconds(wav_path: Path) -> float:
    with wave.open(str(wav_path), "rb") as wav:
        frames = wav.getnframes()
        rate = wav.getframerate()
    return frames / float(rate) if rate else 0.0
