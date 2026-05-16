from __future__ import annotations

__version__ = "0.1.0"

from .engine import Segment, SttEngine, SttError, TranscribeResult
from .models import DEFAULT_MODEL, MODELS, ModelSpec, get_model_spec

__all__ = [
    "__version__",
    "DEFAULT_MODEL",
    "MODELS",
    "ModelSpec",
    "Segment",
    "SttEngine",
    "SttError",
    "TranscribeResult",
    "get_model_spec",
]
