# stt-sandbox

Small Python library, HTTP API, and browser GUI for testing `faster-whisper` Speech-to-Text. Counterpart to [`3-piper`](../3-piper/) (TTS) in the AI tutor pipeline.

This is Phase 1 (skeleton). Full feature set lives in [`docs/context/1-STT/SPEC.md`](docs/context/1-STT/SPEC.md); implementation plan in [`docs/context/1-STT/PLAN.md`](docs/context/1-STT/PLAN.md); progress in [`docs/context/1-STT/CHECKLIST.md`](docs/context/1-STT/CHECKLIST.md).

## Quickstart (local dev)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
python -m stt_sandbox.api
```

In another terminal:

```bash
curl -X POST http://127.0.0.1:8000/transcribe \
  -F 'audio=@tests/fixtures/short_es.wav'
```

## Endpoints (Phase 1)

| Method | Path | Body | Response |
|---|---|---|---|
| `GET`  | `/health`     | — | `{status, mode, engine, gui, model_loaded, language}` |
| `GET`  | `/models`     | — | `{default, models: [...]}` |
| `POST` | `/transcribe` | `multipart/form-data` field `audio`, optional `model` / `language` | `{text, language, duration_seconds, rtf, model, segments}` |

`/transcribe/stream` and the browser GUI ship in Phase 3 / Phase 4 respectively.

## Tests

```bash
pytest
```

Tests use a real Spanish fixture WAV at `tests/fixtures/short_es.wav` (synthesized via `3-piper`). First run downloads the default Whisper model (~250 MB).
