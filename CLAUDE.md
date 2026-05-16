# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Overview

`stt-sandbox` is a small Python package that wraps the [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper) library behind a stdlib HTTP server (`http.server`, no FastAPI/Flask) plus an optional browser GUI. Mirror image of [`../3-piper/`](../3-piper/) (TTS). Default model is `rhasspy/faster-whisper-small-int8` (Spanish, int8 quantized). Default language `es`.

Phase 0 (benchmark) is closed; full results in [`bench/results/arm64/RESULTS.md`](bench/results/arm64/RESULTS.md). Phase 1 (skeleton) is in progress; tracking in [`docs/context/1-STT/CHECKLIST.md`](docs/context/1-STT/CHECKLIST.md).

## Common commands

Local setup (Python 3.11 / 3.12 / 3.13; aarch64 wheels required for `ctranslate2`):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
```

Run the HTTP server:

```bash
python -m stt_sandbox.api
```

Tests:

```bash
pytest
```

Bench (Phase 0 re-run only):

```bash
cd bench
.venv/bin/python run.py
```

## Architecture

### Service modes (`STT_SERVICE_MODE`)

A single process can run in one of three modes (gated in `api.py:main`):

- `both` — GUI at `/` plus engine endpoints (`/health`, `/models`, `/transcribe`, `/transcribe/stream`).
- `engine` — engine endpoints only; `/` returns 404. Use on the production VPS.
- `gui` — only `/` and `/health`. The HTML is templated with `__ENGINE_URL__`, so the browser calls a *remote* engine for `/models` and `/transcribe[/stream]`.

`engine_enabled` / `gui_enabled` are derived from `service_mode` on `SttRequestHandler` and gate every route. Add new endpoints with the same gating.

### Module layout (`stt_sandbox/`)

- `config.py` — hand-rolled `.env` loader (no `python-dotenv`) plus `env_bool` / `env_int`. `load_env` uses `os.environ.setdefault`, so real environment variables always win over the file.
- `models.py` — `ModelSpec` dataclass and `MODELS` registry. `parse_model_names` accepts either a JSON array or a comma-separated string in `STT_MODEL_NAMES`. The registry is built **at import time** from env vars — changing `STT_MODEL_NAMES` after import has no effect.
- `audio.py` — decode incoming audio bytes to a 16 kHz / 16-bit / mono PCM tempfile path that `faster-whisper` will accept directly. Phase 1: WAV-only happy path. Phase 2 adds an `ffmpeg` shell-out for WebM / Opus / Ogg / MP3 / FLAC.
- `engine.py` — `SttEngine` holds a lazy cache of `WhisperModel` instances keyed by model id. `transcribe()` returns `TranscribeResult`. Phase 3 will add `transcribe_stream()`.
- `api.py` — stdlib `ThreadingHTTPServer` + `BaseHTTPRequestHandler`. POST bodies capped at `STT_MAX_REQUEST_BODY_BYTES`. CORS controlled by `STT_CORS_ORIGIN`.

## Gotchas

- Model names follow the `rhasspy/faster-whisper-<size>-<quant>` convention. `STT_DEFAULT_MODEL` is auto-included in the registry even if missing from `STT_MODEL_NAMES`.
- `models.py` calls `load_env()` at import. Don't add side effects that assume env vars set later in `main()` are visible to `MODELS`.
- First transcription per model triggers a HuggingFace download (~80 MB for tiny, ~250 MB for small) into `STT_MODELS_DIR`. CI / Docker should pre-warm or accept the cold-start cost on first request.
- VAD on (`STT_VAD_ENABLED=true`) reduces Whisper hallucinations on silence but also means that for short utterances (5–10 s) the whole input usually comes back as a single segment — `/transcribe/stream` is **not** a perceived-latency win in that regime. See SPEC §3.5 and §4.1.
- ARM tuning: Phase 0 measured that `cpu_threads=4` beats `cpu_threads=8` on the Hetzner CAX31 VPS. Do not set `STT_CPU_THREADS` above 4 without re-benching.

## Coding rules

- All code in English (identifiers, log messages, error strings). GUI strings are the only allowed Spanish, matching `3-piper`'s tone.
- No comments unless the file already has them. Identifier names carry the explanation.
- Tests use real audio fixtures, never mocks. The first test pulls a Whisper model from HuggingFace.
