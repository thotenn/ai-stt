# `stt-sandbox` API Cookbook

Practical recipes for consuming the `stt-sandbox` HTTP API from your own applications. Each recipe is self-contained — copy, paste, adapt.

Assumes an engine is reachable at a base URL. Examples use `https://ai.stt.thotenn.com` (the production deploy). Replace with yours.

## Contents

| Recipe | What it covers |
|---|---|
| [`python_client.md`](python_client.md) | `requests` and `urllib`, multipart and JSON+base64, `initial_prompt`, error handling, timeouts, a reusable `SttClient` class. |
| [`production_ops.md`](production_ops.md) | Coolify operations, CORS pinning, log levels, model upgrade path, redeploy/rollback, troubleshooting checklist. |

## API surface at a glance

| Endpoint | Method | Purpose |
|---|---|---|
| `/health` | GET | Liveness probe; reports mode and `model_loaded` |
| `/models` | GET | List configured models with metadata |
| `/transcribe` | POST | Transcribe audio → JSON `{text, language, duration_seconds, decode_seconds, rtf, model, segments}` |
| `/` | GET | Reference web GUI (only in `both` and `gui` modes) |

The `/transcribe/stream` endpoint was cut from v1 — see [`../1-STT/SPEC.md`](../1-STT/SPEC.md) §3.5 for why.

## Request body for `/transcribe`

Two transports, same response. Pick whichever fits your transport:

**Multipart** (recommended for binary audio):

```
POST /transcribe
Content-Type: multipart/form-data; boundary=...

Field   audio    file, any of: audio/wav, audio/webm, audio/ogg, audio/mpeg, audio/mp4, audio/flac
Field   model    optional, defaults to STT_DEFAULT_MODEL on the server
Field   language optional, defaults to STT_DEFAULT_LANGUAGE (es)
Field   initial_prompt optional, biases decoding toward in-domain vocabulary
```

**JSON** (handy when you already have base64 lying around):

```json
{
  "audio_base64": "UklGRg...",
  "mime": "audio/wav",
  "model": "rhasspy/faster-whisper-tiny-int8",
  "language": "es",
  "initial_prompt": "Clase de astronomía sobre los planetas."
}
```

Body size cap: `STT_MAX_REQUEST_BODY_BYTES` (default 25 MiB ≈ 5 min of 16-bit mono PCM, ~8 min of Opus).

## Curl quickstart

```bash
# Health
curl -fsS https://ai.stt.thotenn.com/health

# Models
curl -fsS https://ai.stt.thotenn.com/models

# Transcribe a WAV file
curl -fsS -X POST https://ai.stt.thotenn.com/transcribe \
  -F 'audio=@recording.wav;type=audio/wav' \
  -F 'model=rhasspy/faster-whisper-tiny-int8'

# Transcribe a WebM/Opus blob from MediaRecorder
curl -fsS -X POST https://ai.stt.thotenn.com/transcribe \
  -F 'audio=@recording.webm;type=audio/webm'
```

Successful response:

```json
{
  "text": "Hola, ¿cómo estás? Quiero aprender sobre los planetas del sistema solar.",
  "language": "es",
  "duration_seconds": 4.621,
  "decode_seconds": 0.523,
  "rtf": 0.113,
  "model": "rhasspy/faster-whisper-tiny-int8",
  "segments": [
    {"index": 0, "start": 0.0, "end": 4.6, "text": "Hola, ¿cómo estás?..."}
  ]
}
```

Error responses (4xx, 5xx) always return `{"error": "..."}` as JSON. There are no HTML error pages.

## Choosing the right model

| Model | When | Decode (5 s clip) | RAM |
|---|---|---|---|
| `tiny-int8` *(default)* | Kid-tutor domain, simple Spanish vocab | ~0.5 s | ~250 MB |
| `small-int8` | Adversarial inputs, noise, fast speech, mixed-language | ~1.4 s | ~700 MB |
| `medium-int8` | Maximum accuracy when latency is not a concern | ~3 s | ~1.5 GB |

Pass `model=...` per request to override the default without restarting the server. See [`rpi_recipe.md`](rpi_recipe.md) §"Picking a model on the Pi" for guidance.

## What's not in v1

- **No streaming partials**. `/transcribe` returns once the whole utterance is decoded. See [`../1-STT/SPEC.md`](../1-STT/SPEC.md) §3.5 for rationale.
- **No language auto-detect**. Default is `es`. Pass `language=en` (or any Whisper language code) per request if you need it.
- **No diarization or speaker labels**.
- **No `/metrics`** Prometheus endpoint yet.

If you need any of these, open an issue or check the v1.x backlog in [`../1-STT/CHECKLIST.md`](../1-STT/CHECKLIST.md).
