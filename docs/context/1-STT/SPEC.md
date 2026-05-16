# `4-stt` — Specification

Self-hosted Speech-to-Text service for the AI tutor pipeline. Counterpart to `3-piper` (TTS). Sits on the same Hetzner Ampere ARM64 VPS, listens on `ai-stt.thotenn.com`, transcribes child speech sent from a Raspberry Pi client, and returns text the upstream LLM engine can consume.

This document is the **what** and **why**. Architecture details are in [`PLAN.md`](PLAN.md); granular work items are in [`CHECKLIST.md`](CHECKLIST.md). Background research is in [`../0-general/`](../0-general/).

---

## 1. Scope

### In scope (v1)

- HTTP service that accepts an audio payload and returns its transcription as JSON.
- Optional NDJSON streaming endpoint that emits partial segments as the decoder produces them.
- Lightweight browser GUI for **developer testing**: record from the laptop/desktop microphone via `MediaRecorder`, send to the service, display the transcript, optionally play back the recording. Mirrors the *role* of the GUI in `3-piper` — not a production UI.
- Service modes equivalent to `3-piper`: `both` / `engine` / `gui`, controlled by `STT_SERVICE_MODE`.
- Docker + Docker Compose deployment, suitable for Coolify behind Caddy at `ai-stt.thotenn.com`.
- Latin-American Spanish (`es`) as the only configured language. English may be added later but is **not** a v1 requirement.

### Out of scope (v1)

- Real-time bidirectional websocket streaming where audio is decoded as it arrives chunk-by-chunk (true online ASR). v1 buffers the full utterance, then transcribes; the streaming endpoint emits *decoded* segments, not *received* chunks.
- Speaker diarization, alignment, word-level timestamps beyond Whisper's native segment grain.
- On-the-fly model upload / management endpoints. Model list is fixed via env at startup.
- Authentication / authorization. Public endpoint behind CORS-restricted origin, same posture as `3-piper`.
- Raspberry Pi client code. The RPi app is built elsewhere; this service only exposes the API it consumes.

### Stretch (v1.x, not v1)

- Whisper `initial_prompt` injected per request (for biasing toward class topic / child's name supplied by the LLM).
- Optional `audio_keep` flag that retains the WAV for debugging.
- Multi-language: keep `es` as default, allow per-request `language` override.

---

## 2. Users and clients

| Client | Where | What it sends | What it expects back |
|---|---|---|---|
| **Raspberry Pi tutor app** (Python) | RPi at the child's house | Audio captured from local mic, encoded as WAV or WebM/Opus, posted as `multipart/form-data` to `/transcribe` | JSON `{text, language, duration_seconds, segments, rtf}` |
| **Developer browser GUI** | Any laptop with a mic | Same endpoint as the RPi, but recorded via `MediaRecorder` (WebM/Opus) | Same response, rendered in the page |
| **(Future) ENGINE-LLM** | Same server or elsewhere | Could call directly if it wants to do its own audio capture, but the canonical flow has the RPi mediating | Same response |

The RPi is the **primary** client. The browser GUI exists to validate the service without needing the RPi in the loop.

---

## 3. Functional requirements

### 3.1 Endpoints

| Method | Path | Modes | Purpose |
|---|---|---|---|
| `GET`  | `/health` | all | Liveness/readiness for Coolify and the GUI |
| `GET`  | `/models` | both, engine | List installed transcription models |
| `POST` | `/transcribe` | both, engine | Whole-utterance transcription |
| `POST` | `/transcribe/stream` | both, engine — gated by `STT_STREAM_ENABLED` | Segment-by-segment NDJSON stream |
| `GET`  | `/` | both, gui | Browser GUI (record mic, send, show text) |

### 3.2 `GET /health`

Response:

```json
{
  "status": "ok",
  "mode": "both",
  "engine": true,
  "gui": true,
  "stream_enabled": true,
  "model_loaded": "rhasspy/faster-whisper-small-int8",
  "language": "es"
}
```

`stream_enabled` is `true` only when the engine is active *and* `STT_STREAM_ENABLED=true`. Browser GUI reads this on load to decide whether to call `/transcribe` or `/transcribe/stream`. Mirrors `3-piper`'s `chunks_enabled` field.

### 3.3 `GET /models`

```json
{
  "default": "rhasspy/faster-whisper-small-int8",
  "models": [
    {
      "name": "rhasspy/faster-whisper-small-int8",
      "size": "small",
      "quantization": "int8",
      "language": "es",
      "loaded": true
    },
    {
      "name": "rhasspy/faster-whisper-base-int8",
      "size": "base",
      "quantization": "int8",
      "language": "es",
      "loaded": false
    }
  ]
}
```

Only the `default` model is preloaded at startup. Other entries are descriptors; first request against them triggers a lazy `WhisperModel(...)` load and HuggingFace download.

### 3.4 `POST /transcribe`

**Request** — one of:

- `multipart/form-data` with field `audio` (file), optional fields `model`, `language`, `initial_prompt`, `vad` (`true`/`false`).
- `application/json` body `{audio_base64, mime, model?, language?, initial_prompt?, vad?}`.

Accepted MIME types: `audio/wav`, `audio/x-wav`, `audio/webm`, `audio/ogg`, `audio/flac`, `audio/mpeg`. Internally normalized to 16 kHz / 16-bit / mono PCM.

**Response 200** — `application/json`:

```json
{
  "text": "hola, quiero aprender sobre los planetas",
  "language": "es",
  "duration_seconds": 3.47,
  "rtf": 0.41,
  "model": "rhasspy/faster-whisper-small-int8",
  "segments": [
    {"start": 0.0,  "end": 1.62, "text": "hola, quiero aprender"},
    {"start": 1.62, "end": 3.47, "text": " sobre los planetas"}
  ]
}
```

**Response 400** — invalid JSON, empty body, unknown model, unsupported MIME, body > `STT_MAX_REQUEST_BODY_BYTES`. Body: `{error: string}`.

**Response 500** — decoder failure. Body: `{error: string}`.

### 3.5 `POST /transcribe/stream`

Same request schema as `/transcribe`. Returns `501 Not Implemented` if `STT_STREAM_ENABLED=false`.

**Response 200** — `application/x-ndjson`, headers include `X-Accel-Buffering: no` (mirrors `/speak/chunks` in `3-piper`). One JSON object per line, flushed immediately:

```text
{"type":"meta","model":"...","language":"es","duration_seconds":3.47}
{"type":"segment","index":0,"start":0.0,"end":1.62,"text":"hola, quiero aprender","decode_seconds":0.41}
{"type":"segment","index":1,"start":1.62,"end":3.47,"text":" sobre los planetas","decode_seconds":0.38}
{"type":"done","text":"hola, quiero aprender sobre los planetas","rtf":0.23}
```

Mid-stream failure emits an in-band event and closes:

```text
{"type":"error","index":1,"message":"..."}
```

Pre-stream failures return HTTP 4xx/5xx before any NDJSON is written.

### 3.6 `GET /` — Browser GUI

Single inlined HTML page (same pattern as `3-piper`'s `INDEX_HTML`). Features:

- Mic permission prompt + start/stop recording button (uses `MediaRecorder`, `audio/webm;codecs=opus`).
- Visual recording indicator (timer + level meter, level meter is optional in v1).
- After stop: posts the recorded blob to `/transcribe` (or `/transcribe/stream` when `stream_enabled`).
- Displays the transcript progressively (streaming mode) or all at once (non-streaming).
- Model selector populated from `/models`.
- Optional playback `<audio>` element of the recorded clip for sanity checking what the server received.
- Status text equivalent to 3-piper's `status` element: `Listo` / `Grabando 0:03` / `Transcribiendo...` / `Listo`.
- Localized in Spanish, matching `3-piper`'s tone.

GUI is **for developer testing only**. No analytics, no auth, no storage.

### 3.7 Audio handling

- **Accepted formats**: WAV (PCM 16-bit), WebM/Opus (browser default), Ogg/Opus, FLAC, MP3.
- **Internal format**: 16 kHz / 16-bit / mono PCM. Decoder shells out to `ffmpeg` when the input is not directly decodable by `soundfile` (e.g. WebM). Decoded audio lives only in a `tempfile.NamedTemporaryFile`; deleted in `finally`.
- **Size cap**: `STT_MAX_REQUEST_BODY_BYTES` (default `25 * 1024 * 1024` = 25 MiB ≈ 5 min of 16-bit mono PCM, ~8 min of Opus at typical bitrates).
- **No raw audio retained on disk** after a successful response. Mirrors `3-piper`'s tempfile discipline.

### 3.8 Service modes

| Mode | What's exposed |
|---|---|
| `both`   | GUI at `/`, engine endpoints (`/health`, `/models`, `/transcribe`, `/transcribe/stream`) |
| `engine` | Engine endpoints only, `/` returns 404 |
| `gui`    | `/` and `/health` only; GUI calls remote engine via `STT_ENGINE_URL` |

Same model as `3-piper` (`PIPER_SERVICE_MODE`). Code: `engine_enabled` / `gui_enabled` derived from `service_mode` on the request handler, every route gated.

CLI flags `--mode {both,engine,gui}`, `--engine-url URL` (only honoured in `gui` mode).

---

## 4. Non-functional requirements

### 4.1 Performance targets

For a 5 s LatAm-Spanish utterance at `STT_DEFAULT_MODEL = rhasspy/faster-whisper-small-int8` on the Ampere VPS:

- **RTF**: < 0.5 (decode in under 2.5 s).
- **First segment latency (streaming)**: < 1.5 s after `AudioStop`.
- **Cold start** (model not yet loaded): < 30 s including download (~250 MB) on first request, < 5 s on subsequent restarts (model on disk).
- **Peak RSS**: < 1.0 GB with `small-int8` model resident.

For a 30 s utterance: RTF < 0.6.

Targets are checkpoints, not contracts. If `small-int8` misses them, fall back order is `base-int8` → `tiny-int8`. If `small-int8` clears them comfortably (< 0.3), promote to `medium-int8`. Decision recorded in [`CHECKLIST.md`](CHECKLIST.md) under "Bench".

### 4.2 Concurrency

- Single shared `WhisperModel` instance per (library, model) tuple, gated by an `asyncio.Lock`. Two simultaneous transcription requests serialize on the lock. Matches `wyoming-faster-whisper`'s `ModelLoader._transcriber_lock` pattern.
- HTTP server is `ThreadingHTTPServer` (stdlib), same as `3-piper`. Threads handle parsing and response framing; the model call runs synchronously on the request thread.
- No worker pool in v1. Revisit only if the RPi or future LLM clients drive sustained parallel load.

### 4.3 Reliability

- All decoder failures raise a typed `SttError`; the HTTP layer maps it to a 4xx/5xx with a structured JSON body. Never returns an HTML error page.
- Temp files unlinked in `finally` regardless of outcome.
- Streaming endpoint emits an in-band `error` event on mid-stream failure; client knows the stream did not complete.

### 4.4 Privacy / data handling

- Audio is processed in memory and a tempfile; deleted after the response is written.
- No logging of transcript text at INFO level. Transcripts logged only at DEBUG, behind `STT_LOG_TRANSCRIPTS=false` default.
- Standard request log lines (method, path, status, duration) at INFO.

### 4.5 Observability

- `GET /health` returns enough state for Coolify, the GUI, and a future status dashboard.
- Each response includes `duration_seconds` and `rtf` for client-side perf tracking.
- Optional `STT_METRICS_ENABLED=true` env exposing a Prometheus-style `/metrics` endpoint is a v1.x stretch, not v1.

### 4.6 Security

- CORS controlled by `STT_CORS_ORIGIN`. Default `*` for testing. Production: pin to the LLM/RPi origins.
- Request body capped to prevent OOM via `MAX_REQUEST_BODY_BYTES` (mirrors 3-piper).
- `ffmpeg` invocation uses argv arrays (never shell strings); input path is a tempfile we own, not a user-supplied filename.
- No SSRF surface: there is no endpoint that fetches a URL on behalf of the caller.

### 4.7 Portability

- Must build and run on `linux/arm64`. CI step (if any) tests at least one transcription on `arm64`.
- Local dev should also work on `linux/amd64` and macOS (Apple Silicon).

---

## 5. Configuration

`.env.example` (mirrors `3-piper` naming conventions):

```env
STT_HOST=127.0.0.1
STT_PORT=8000
STT_HOST_PORT=8000
STT_CONTAINER_NAME=stt-sandbox

STT_SERVICE_MODE=both
STT_ENGINE_URL=
STT_CORS_ORIGIN=*

STT_MODELS_DIR=models/whisper
STT_DEFAULT_MODEL=rhasspy/faster-whisper-small-int8
STT_MODEL_NAMES=["rhasspy/faster-whisper-small-int8","rhasspy/faster-whisper-base-int8","rhasspy/faster-whisper-tiny-int8"]

STT_DEFAULT_LANGUAGE=es
STT_COMPUTE_TYPE=int8
STT_CPU_THREADS=4
STT_BEAM_SIZE=1
STT_INITIAL_PROMPT=

STT_VAD_ENABLED=true
STT_VAD_THRESHOLD=0.5
STT_VAD_MIN_SPEECH_MS=250
STT_VAD_MIN_SILENCE_MS=2000

STT_STREAM_ENABLED=true
STT_MAX_REQUEST_BODY_BYTES=26214400

STT_LOG_TRANSCRIPTS=false
STT_HF_BASE=https://huggingface.co/
```

Notes:
- `STT_HOST_PORT` is for Docker Compose host:container mapping, like `PIPER_HOST_PORT`.
- `STT_MODEL_NAMES` accepts a JSON array **or** a comma-separated list (parse-compatible with `parse_model_names` in `3-piper/models.py`).
- `STT_DEFAULT_MODEL` is auto-included in the registry even if missing from `STT_MODEL_NAMES`.
- Real environment variables win over `.env` (same `os.environ.setdefault` pattern).

---

## 6. Deliverables

- Working Docker image deployed to Coolify at `https://ai-stt.thotenn.com`.
- `/transcribe` and `/transcribe/stream` callable from a Python `requests` client and from a browser.
- Browser GUI usable from any laptop with a mic.
- Test suite covering: model registry parsing, audio decode path, `/transcribe` happy path, `/transcribe/stream` event order, GUI HTML render.
- API cookbook under `docs/context/0-api/` (Python client, JS/browser client, RPi-specific recipe), to be authored after v1 ships — **not** part of v1 scope.

---

## 7. Glossary

- **STT** — Speech-to-Text.
- **VAD** — Voice Activity Detection. Silero VAD model bundled by faster-whisper.
- **RTF** — Real-Time Factor: `decode_seconds / audio_seconds`. Lower is better. < 1.0 = faster than realtime.
- **int8** — Integer quantization. Drops Whisper memory/CPU ~4× vs fp32 with marginal accuracy loss.
- **Segment** — Whisper's native output unit: a span of audio (seconds) with a decoded text string. A 30 s utterance typically yields 3–8 segments.
- **NDJSON** — Newline-delimited JSON. One JSON object per line, flushed independently. Lets a client process events as they arrive without parsing the whole response.
