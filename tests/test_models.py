from __future__ import annotations

import pytest

from stt_sandbox.models import (
    DEFAULT_MODEL,
    MODELS,
    get_model_spec,
    model_spec_from_name,
    parse_model_names,
)


def test_default_model_in_registry():
    assert DEFAULT_MODEL in MODELS


def test_parse_model_names_json_array():
    names = parse_model_names('["rhasspy/faster-whisper-small-int8","rhasspy/faster-whisper-base-int8"]')
    assert names == [
        "rhasspy/faster-whisper-small-int8",
        "rhasspy/faster-whisper-base-int8",
    ]


def test_parse_model_names_comma_separated():
    names = parse_model_names("rhasspy/faster-whisper-small-int8, rhasspy/faster-whisper-base-int8")
    assert names == [
        "rhasspy/faster-whisper-small-int8",
        "rhasspy/faster-whisper-base-int8",
    ]


def test_parse_model_names_empty_returns_defaults():
    assert parse_model_names(None)
    assert parse_model_names("")


def test_parse_model_names_invalid_type():
    with pytest.raises(ValueError):
        parse_model_names('{"not": "a list"}')


def test_model_spec_from_rhasspy_name_extracts_size_and_quant():
    spec = model_spec_from_name("rhasspy/faster-whisper-small-int8")
    assert spec.size == "small"
    assert spec.quantization == "int8"


def test_model_spec_from_unknown_name_falls_back():
    spec = model_spec_from_name("openai/whisper-large-v3")
    assert spec.size == "unknown"
    assert spec.quantization == "unknown"


def test_get_model_spec_unknown_raises_with_available_list():
    with pytest.raises(KeyError) as exc:
        get_model_spec("does-not-exist")
    assert "Available models" in str(exc.value)
