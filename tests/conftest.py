from __future__ import annotations

import os
from pathlib import Path

import pytest


FIXTURES_DIR = Path(__file__).parent / "fixtures"
SHORT_ES_WAV = FIXTURES_DIR / "short_es.wav"

DEFAULT_TEST_MODEL = os.environ.get(
    "STT_TEST_MODEL", "rhasspy/faster-whisper-tiny-int8"
)
TEST_MODELS_DIR = Path(
    os.environ.get("STT_TEST_MODELS_DIR", str(Path(__file__).parent.parent / "models" / "whisper"))
)


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def short_es_wav() -> Path:
    if not SHORT_ES_WAV.exists():
        pytest.skip(f"missing fixture {SHORT_ES_WAV}")
    return SHORT_ES_WAV


@pytest.fixture(scope="session")
def test_model_name() -> str:
    return DEFAULT_TEST_MODEL


@pytest.fixture(scope="session")
def test_models_dir() -> Path:
    TEST_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    return TEST_MODELS_DIR


@pytest.fixture(scope="session")
def shared_engine(test_model_name, test_models_dir):
    from stt_sandbox.engine import SttEngine

    engine = SttEngine(
        models_dir=test_models_dir,
        default_model=test_model_name,
        default_language="es",
        compute_type="int8",
        cpu_threads=4,
        beam_size=1,
        vad_parameters={
            "threshold": 0.5,
            "min_speech_duration_ms": 250,
            "min_silence_duration_ms": 2000,
        },
    )
    engine.preload(test_model_name)
    return engine
