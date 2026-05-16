from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from faster_whisper import WhisperModel

from .audio import audio_duration_seconds
from .models import DEFAULT_LANGUAGE, DEFAULT_MODEL, get_model_spec


_LOGGER = logging.getLogger(__name__)


class SttError(RuntimeError):
    pass


@dataclass(frozen=True)
class Segment:
    index: int
    start: float
    end: float
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TranscribeResult:
    text: str
    language: str
    duration_seconds: float
    decode_seconds: float
    rtf: float
    model: str
    segments: list[Segment] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "language": self.language,
            "duration_seconds": round(self.duration_seconds, 3),
            "decode_seconds": round(self.decode_seconds, 3),
            "rtf": round(self.rtf, 3),
            "model": self.model,
            "segments": [s.to_dict() for s in self.segments],
        }


class SttEngine:
    def __init__(
        self,
        models_dir: str | Path,
        default_model: str = DEFAULT_MODEL,
        default_language: str | None = DEFAULT_LANGUAGE,
        compute_type: str = "int8",
        cpu_threads: int = 4,
        beam_size: int = 1,
        initial_prompt: str | None = None,
        vad_parameters: dict[str, Any] | None = None,
    ) -> None:
        self.models_dir = Path(models_dir)
        self.default_model = default_model
        self.default_language = default_language
        self.compute_type = compute_type
        self.cpu_threads = cpu_threads
        self.beam_size = beam_size
        self.initial_prompt = initial_prompt
        self.vad_parameters = vad_parameters

        self._cache: dict[str, WhisperModel] = {}
        self._cache_lock = threading.Lock()
        self._model_locks: dict[str, threading.Lock] = {}
        self._transcribe_lock = threading.Lock()

    def _model_lock(self, name: str) -> threading.Lock:
        with self._cache_lock:
            lock = self._model_locks.get(name)
            if lock is None:
                lock = threading.Lock()
                self._model_locks[name] = lock
            return lock

    def _get_model(self, name: str) -> WhisperModel:
        cached = self._cache.get(name)
        if cached is not None:
            return cached

        lock = self._model_lock(name)
        with lock:
            cached = self._cache.get(name)
            if cached is not None:
                return cached

            get_model_spec(name)
            self.models_dir.mkdir(parents=True, exist_ok=True)
            _LOGGER.info("loading whisper model %s", name)
            try:
                model = WhisperModel(
                    name,
                    download_root=str(self.models_dir),
                    device="cpu",
                    compute_type=self.compute_type,
                    cpu_threads=self.cpu_threads,
                )
            except Exception as exc:
                raise SttError(f"could not load model {name!r}: {exc}") from exc

            self._cache[name] = model
            return model

    def preload(self, name: str | None = None) -> None:
        self._get_model(name or self.default_model)

    def transcribe(
        self,
        wav_path: str | Path,
        model: str | None = None,
        language: str | None = None,
        initial_prompt: str | None = None,
    ) -> TranscribeResult:
        model_name = model or self.default_model
        lang = language if language is not None else self.default_language
        prompt = initial_prompt if initial_prompt is not None else self.initial_prompt

        wav = Path(wav_path)
        if not wav.exists():
            raise SttError(f"audio file not found: {wav}")

        duration = audio_duration_seconds(wav)
        whisper = self._get_model(model_name)

        with self._transcribe_lock:
            start = time.perf_counter()
            try:
                segments_iter, info = whisper.transcribe(
                    str(wav),
                    language=lang,
                    beam_size=self.beam_size,
                    initial_prompt=prompt,
                    vad_filter=self.vad_parameters is not None,
                    vad_parameters=self.vad_parameters,
                )
                segments: list[Segment] = []
                pieces: list[str] = []
                for index, seg in enumerate(segments_iter):
                    segments.append(
                        Segment(
                            index=index,
                            start=round(float(seg.start), 3),
                            end=round(float(seg.end), 3),
                            text=seg.text,
                        )
                    )
                    pieces.append(seg.text)
            except Exception as exc:
                raise SttError(f"transcription failed: {exc}") from exc
            decode_seconds = time.perf_counter() - start

        text = "".join(pieces).strip()
        detected_language = lang or getattr(info, "language", None) or ""
        rtf = decode_seconds / duration if duration > 0 else 0.0

        return TranscribeResult(
            text=text,
            language=detected_language,
            duration_seconds=duration,
            decode_seconds=decode_seconds,
            rtf=rtf,
            model=model_name,
            segments=segments,
        )
