from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from .config import load_env


@dataclass(frozen=True)
class ModelSpec:
    name: str
    size: str
    quantization: str
    language: str


load_env()

DEFAULT_MODEL = os.environ.get("STT_DEFAULT_MODEL", "rhasspy/faster-whisper-tiny-int8")
DEFAULT_LANGUAGE = os.environ.get("STT_DEFAULT_LANGUAGE", "es")

DEFAULT_MODEL_NAMES = [
    "rhasspy/faster-whisper-tiny-int8",
    "rhasspy/faster-whisper-small-int8",
    "rhasspy/faster-whisper-medium-int8"
]


_RHASSPY_PATTERN = re.compile(
    r"^rhasspy/faster-whisper-(?P<size>tiny|base|small|medium|large)-(?P<quant>int8|int8_float16|int8_bfloat16|float16|bfloat16|float32)(?:\.en)?$"
)


def parse_model_names(value: str | None) -> list[str]:
    if not value:
        return list(DEFAULT_MODEL_NAMES)

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = [item.strip() for item in value.split(",")]

    if not isinstance(parsed, list):
        raise ValueError("STT_MODEL_NAMES must be a JSON array or a comma-separated list")

    names = [str(item).strip() for item in parsed if str(item).strip()]
    return names or list(DEFAULT_MODEL_NAMES)


def model_spec_from_name(name: str, language: str = DEFAULT_LANGUAGE) -> ModelSpec:
    match = _RHASSPY_PATTERN.match(name)
    if match:
        return ModelSpec(
            name=name,
            size=match.group("size"),
            quantization=match.group("quant"),
            language=language,
        )
    return ModelSpec(name=name, size="unknown", quantization="unknown", language=language)


_configured_names = parse_model_names(os.environ.get("STT_MODEL_NAMES"))
if DEFAULT_MODEL not in _configured_names:
    _configured_names = [DEFAULT_MODEL, *_configured_names]

MODELS: dict[str, ModelSpec] = {
    name: model_spec_from_name(name, DEFAULT_LANGUAGE) for name in _configured_names
}


def get_model_spec(name: str) -> ModelSpec:
    try:
        return MODELS[name]
    except KeyError as exc:
        available = ", ".join(sorted(MODELS))
        raise KeyError(f"Unknown model {name!r}. Available models: {available}") from exc
