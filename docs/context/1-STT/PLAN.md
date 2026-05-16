# `4-stt` — Implementation Plan

How we will build what [`SPEC.md`](SPEC.md) describes. This document defines the module layout, the responsibility split, the call graph for each endpoint, and the order in which work happens. Granular tasks with checkboxes live in [`CHECKLIST.md`](CHECKLIST.md).

The guiding principle is **symmetry with `3-piper`**. Anywhere `3-piper` made a choice that still fits, copy it; anywhere our problem is genuinely different (audio in vs. audio out, faster-whisper vs. piper binary), justify the deviation explicitly.

---

## 1. Repository layout

```
4-stt/
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── .env                          # gitignored
├── .gitignore
├── .dockerignore
├── README.md
├── CLAUDE.md
├── docs/
│   └── context/
│       ├── 0-general/            # research bundle (already written)
│       └── 1-STT/                # SPEC + PLAN + CHECKLIST (this folder)
├── stt_sandbox/                  # the package
│   ├── __init__.py               # re-export SttEngine, SttError, MODELS, DEFAULT_MODEL
│   ├── config.py                 # .env loader, env_bool, env_int — copy from 3-piper
│   ├── models.py                 # ModelSpec, registry, parse_model_names
│   ├── audio.py                  # decode bytes → 16 kHz mono PCM tempfile path
│   ├── engine.py                 # SttEngine: load WhisperModel, transcribe(), transcribe_stream()
│   ├── api.py                    # ThreadingHTTPServer + handler; routes
│   └── streaming.py              # NDJSON writer helper (one place that handles flushing)
├── tests/
│   ├── __init__.py
│   ├── fixtures/
│   │   ├── short_es.wav          # ~3 s "hola, ¿cómo estás?" (LatAm voice)
│   │   ├── medium_es.wav         # ~12 s
│   │   └── kid_noise_es.wav      # ~5 s child-voice clip if available
│   ├── test_models.py            # registry parsing, model_spec_from_name
│   ├── test_audio.py             # decode WAV / WebM / Opus → PCM
│   ├── test_engine.py            # transcribe a known fixture, assert text contains expected phrase
│   ├── test_transcribe_endpoint.py
│   ├── test_transcribe_stream_endpoint.py
│   └── test_gui_html.py          # GUI HTML renders + escaping is XSS-safe
└── models/                       # gitignored content (downloaded Whisper models)
    └── whisper/
```

Naming choice: package is `stt_sandbox` to mirror `piper_sandbox`. The pattern "X_sandbox" signals "small wrapper around the real thing, with test GUI included".

---

## 2. Module responsibilities

### `config.py`

Pasted from `piper_sandbox/config.py` with rename of any `PIPER_` reference to `STT_`. Hand-rolled `.env` loader (no `python-dotenv`), `env_bool`, `env_int`. `load_env` uses `os.environ.setdefault` so real env vars always win.

### `models.py`

- `@dataclass(frozen=True) ModelSpec`: `name`, `size` (`tiny|base|small|medium|large`), `quantization` (`int8|float16|...`), `language` (default), `hf_repo` (HuggingFace id).
- Naming convention: `rhasspy/faster-whisper-<size>-int8` is the canonical id. `parse_model_spec(name)` extracts `size` and `quantization` from the suffix.
- `parse_model_names(value)` accepts JSON array or comma-separated string, mirrors `3-piper`'s function.
- `MODELS: dict[str, ModelSpec]` built at import time from `STT_MODEL_NAMES`; `STT_DEFAULT_MODEL` auto-included.
- `get_model_spec(name) -> ModelSpec` with friendly KeyError listing available names.

### `audio.py`

One job: take incoming audio bytes + MIME, return a tempfile path containing 16 kHz / 16-bit / mono PCM WAV. Caller is responsible for unlinking.

- `decode_to_pcm(data: bytes, mime: str | None) -> Path`.
- Fast path: WAV-pass-through when input is already 16 kHz/mono/16-bit (sniff with `wave.open`).
- WAV resample / channel mix / sample-width adjust via `soundfile` + simple Python resampler **only when** the WAV header demands it (rare; the Pi client and the GUI both send 16 kHz mono when possible). Otherwise → ffmpeg.
- All other MIME types (WebM, Ogg, MP3, FLAC) → `ffmpeg -i <tempin> -ac 1 -ar 16000 -f wav -acodec pcm_s16le <tempout>`. argv array, no shell. Decoder errors raise `SttError`.
- `ffmpeg` binary discovered once via `shutil.which`; cached.
- `MAX_AUDIO_BYTES` enforced here as a defense in depth (the HTTP handler is the first line).

### `engine.py`

- `class SttError(RuntimeError): ...`
- `class SttEngine`:
  - `__init__(models_dir, default_model, default_language, compute_type, cpu_threads, beam_size, initial_prompt, vad_parameters)`.
  - `_models: dict[str, WhisperModel]` — lazy cache.
  - `_load_locks: dict[str, asyncio.Lock]` — one lock per model id, prevents concurrent loads of the same model.
  - `def preload(self, name: str) -> None` — synchronous preload at startup so the first request isn't slow.
  - `def transcribe(self, wav_path: Path, *, model: str, language: str | None, initial_prompt: str | None) -> TranscribeResult` — returns `{text, language, duration_seconds, rtf, segments: [{start, end, text}], model}`.
  - `def transcribe_stream(self, wav_path: Path, *, model: str, language: str | None, initial_prompt: str | None) -> Iterator[StreamEvent]` — yields `meta`, then one `segment` per faster-whisper segment, then `done`. Iterates the generator returned by `WhisperModel.transcribe(...)` — this is what gives us partial output.
- VAD parameters passed straight to `WhisperModel.transcribe(...vad_filter=True, vad_parameters={...})`.
- One `SttEngine` instance per process, created in `api.main()`.

### `streaming.py`

Tiny helper. `class NdjsonWriter(wfile)`: `write(obj: dict)` serializes to UTF-8 + `\n`, calls `wfile.flush()`. Centralizing the flush avoids a 3-piper-style copy-paste bug where one path forgets to flush.

### `api.py`

`ThreadingHTTPServer` + `BaseHTTPRequestHandler`. Same shape as `piper_sandbox/api.py`, just with new routes.

- `INDEX_HTML` constant: inlined GUI, no external assets.
- `__ENGINE_URL_JSON__` placeholder replaced at request time, XSS-safe (`json.dumps` + escape `<`). Same pattern as 3-piper.
- `MAX_REQUEST_BODY_BYTES` enforced before parsing.
- CORS headers on every response.
- Routes table dispatched in `do_GET` / `do_POST`.
- `engine_enabled` / `gui_enabled` derived from `service_mode`; every route gated.
- `main()` parses CLI, loads env, builds `SttEngine`, preloads default model, starts server.

### `__init__.py`

Re-export `SttEngine`, `SttError`, `MODELS`, `DEFAULT_MODEL`, `TranscribeResult`.

---

## 3. Endpoint call graphs

### `POST /transcribe`

```
do_POST("/transcribe")
  → _check_size(content_length)
  → _parse_body() → bytes audio + mime + optional fields
  → audio.decode_to_pcm(bytes, mime) → wav_path (tempfile)
  → engine.transcribe(wav_path, model, language, initial_prompt)
  → _write_json(200, result)
  finally: wav_path.unlink(missing_ok=True)
```

### `POST /transcribe/stream`

```
do_POST("/transcribe/stream")
  → if not STT_STREAM_ENABLED: 501
  → _check_size(content_length)
  → _parse_body() → bytes audio + mime + fields
  → audio.decode_to_pcm(...) → wav_path
  → _start_ndjson_response(200)  # Content-Type, Transfer-Encoding, X-Accel-Buffering: no
  → writer = NdjsonWriter(wfile)
  → for event in engine.transcribe_stream(wav_path, ...):
        writer.write(event)
  → writer.write({"type":"done", ...})
  except SttError as e (mid-stream):
      writer.write({"type":"error", ...})
  finally: wav_path.unlink(missing_ok=True)
```

### `GET /models`, `GET /health`, `GET /`

Identical pattern to 3-piper. `/` substitutes `__ENGINE_URL_JSON__` and serves `INDEX_HTML`.

---

## 4. Browser GUI (`INDEX_HTML`)

The GUI is one HTML+CSS+JS string, inlined into `api.py` like `3-piper` does. Approximate structure:

```text
+----------------------------------------------------+
|  STT Sandbox                                       |
|  Presiona el boton y habla.                        |
|                                                    |
|  [ Modelo: rhasspy/faster-whisper-small-int8 v]    |
|                                                    |
|  ( ●  Grabar  )    Listo                           |
|                                                    |
|  Transcripcion:                                    |
|  +--------------------------------------------+    |
|  |  ...                                       |    |
|  +--------------------------------------------+    |
|                                                    |
|  > [audio playback of the last recording]         |
+----------------------------------------------------+
```

### Recording flow (JS)

```text
1. Click "Grabar"
   - navigator.mediaDevices.getUserMedia({audio: {channelCount:1, sampleRate:16000}})
   - MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' })
   - mediaRecorder.start()
   - button label → "■ Detener"
   - status → "Grabando 0:00" with ticking timer
2. Click "Detener"
   - mediaRecorder.stop() → ondataavailable → Blob (audio/webm)
   - status → "Transcribiendo..."
   - if (stream_enabled) POST /transcribe/stream (NDJSON, append per segment)
     else                 POST /transcribe       (JSON, render once)
   - audio.src = URL.createObjectURL(blob)   # let dev replay what was sent
   - status → "Listo"
```

Streaming consumption uses the Fetch API's `ReadableStream` + `TextDecoderStream` and processes one line at a time, exactly like a Node/Python NDJSON consumer.

### Browser compatibility

- Chrome/Edge/Firefox on desktop: supported. Safari `MediaRecorder` lacks Opus, supports `audio/mp4`; we accept either MIME server-side.
- iOS Safari: requires user gesture; supported.
- The GUI is internal dev tooling, so we don't ship polyfills for ancient browsers.

---

## 5. Dependencies

`pyproject.toml`:

```toml
[project]
name = "stt-sandbox"
version = "0.1.0"
description = "Small Python library, API, and GUI for testing faster-whisper STT."
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
  "faster-whisper>=1.2.1,<2",
  "soundfile>=0.12",
  "numpy>=1.26",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23"]

[project.scripts]
stt-sandbox-api = "stt_sandbox.api:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

Notes:
- `faster-whisper` pulls in `ctranslate2` (the real engine), `tokenizers`, `huggingface-hub`, `onnxruntime` (for Silero VAD). All have aarch64 wheels.
- `soundfile` for WAV/FLAC/OGG decode. WebM/Opus → ffmpeg system binary.
- **No PyTorch**, **no transformers**, **no sherpa-onnx**. Image stays small (~250–300 MB).

### Dockerfile

Mirror `3-piper`'s, plus `ffmpeg`:

```dockerfile
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    STT_HOST=0.0.0.0 STT_PORT=8000 STT_SERVICE_MODE=both \
    STT_MODELS_DIR=/app/models/whisper \
    STT_DEFAULT_MODEL=rhasspy/faster-whisper-small-int8 \
    STT_DEFAULT_LANGUAGE=es STT_COMPUTE_TYPE=int8 STT_CPU_THREADS=4 STT_BEAM_SIZE=1 \
    STT_VAD_ENABLED=true STT_STREAM_ENABLED=true

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY stt_sandbox ./stt_sandbox
RUN pip install --upgrade pip && pip install .

EXPOSE 8000
CMD ["python", "-m", "stt_sandbox.api"]
```

The image MUST build on `linux/arm64`. Verify with `docker buildx build --platform linux/arm64 .` before pushing.

### docker-compose.yml

Copy from `3-piper/docker-compose.yml` with rename: `piper-sandbox` → `stt-sandbox`, `PIPER_*` → `STT_*`, volume `piper-models` → `stt-models` mounted at `/app/models`. Same healthcheck pattern (Python urllib hitting `/health`).

---

## 6. Implementation order

Phases are sequential; checkboxes within a phase can be parallelized.

### Phase 0 — Benchmark (blocks everything downstream)

Run on the Hetzner Ampere VPS (or a representative aarch64 environment) **before** writing service code.

1. `pip install faster-whisper` in a throwaway venv.
2. Transcribe one 5 s and one 30 s LatAm-Spanish clip with `small-int8`, `base-int8`, `tiny-int8` (each, in turn). Capture RTF, peak RSS, first-segment latency.
3. Record numbers in [`CHECKLIST.md`](CHECKLIST.md).
4. **Decision**: default model = the largest one that clears RTF < 0.5 for the 5 s clip and RTF < 0.7 for the 30 s clip.

Why this is phase 0: every line of service code assumes a specific default model. Picking it wrong means a deploy that's either too slow (user-perceived lag) or too inaccurate (kids' speech misread → bad LLM output).

### Phase 1 — Skeleton (vertical slice, no streaming, no GUI)

Smallest thing that proves the service works end-to-end.

- `config.py`, `models.py` ported from 3-piper with rename.
- `audio.py` happy-path: WAV-only decode. WebM/Opus → 501 for now.
- `engine.py` with `transcribe(...)` only (no streaming method).
- `api.py` with `/health`, `/models`, `/transcribe`. No GUI route. CLI runs the server.
- Tests: `test_models.py`, `test_engine.py` (one fixture clip), `test_transcribe_endpoint.py` (happy path + 400 on empty body).

Exit criterion: `curl -X POST http://127.0.0.1:8000/transcribe -F 'audio=@tests/fixtures/short_es.wav'` returns valid JSON with non-empty `text`.

### Phase 2 — Decoder for non-WAV inputs

- Add `ffmpeg` shell-out path in `audio.py`.
- Accept WebM, Ogg, MP3, FLAC.
- Test: `test_audio.py` covers each decode path, asserts output is 16 kHz mono 16-bit.

Exit criterion: posting a `.webm` blob from a browser via curl returns valid transcript.

### Phase 3 — Streaming endpoint

- `streaming.py` with `NdjsonWriter`.
- `engine.transcribe_stream(...)` yielding `meta` → many `segment` → `done`.
- `api.py` route `/transcribe/stream`, gated by `STT_STREAM_ENABLED`.
- `STT_STREAM_ENABLED=true` reflected in `/health`.
- Test: `test_transcribe_stream_endpoint.py` consumes the NDJSON, asserts event order and that `text` of `done` equals concatenation of segments.

Exit criterion: `curl -N -X POST .../transcribe/stream -F 'audio=@medium_es.wav'` prints lines progressively, first `segment` arrives sooner than the full `/transcribe` response would.

### Phase 4 — Browser GUI

- `INDEX_HTML` constant in `api.py`.
- Route `/` serving the GUI with `__ENGINE_URL_JSON__` substitution.
- Mode gating: `gui_enabled` controls `/`; in `gui` mode, GUI calls remote engine via `STT_ENGINE_URL`.
- Localized in Spanish (matches 3-piper tone).
- Test: `test_gui_html.py` renders the GUI, asserts the `__ENGINE_URL_JSON__` token is replaced, asserts no unescaped `<script>` from an injected `STT_ENGINE_URL`.

Exit criterion: open `http://127.0.0.1:8000/` in Chrome, click Grabar, speak, click Detener, see transcript.

### Phase 5 — Docker + Coolify deploy

- `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `.env.example`.
- Build on `linux/arm64`, push to whatever registry 3-piper uses (or build from GitHub via Coolify).
- Create the Coolify resource at `ai-stt.thotenn.com`, point at this repo, copy env vars.
- Verify `/health` returns 200 from public URL.
- Smoke-test by recording from the browser GUI and confirming a Spanish transcript.

### Phase 6 — Polish & v1.x backlog

After v1 is live and the RPi client is consuming the API, these become real:

- `initial_prompt` injected per request from the LLM.
- API cookbook in `docs/context/0-api/` (Python client, JS client, RPi recipe).
- Latency probe / `/metrics` endpoint.
- Optional `STT_LOG_TRANSCRIPTS` toggle for debugging.

---

## 7. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `small-int8` too slow on Ampere | Medium | High | Phase 0 benchmark; pre-decided fallback to `base-int8` then `tiny-int8`. |
| `ctranslate2` aarch64 wheel breaks on `python:3.12-slim-bookworm` | Low | High | Have a `python:3.11-slim-bookworm` fallback ready; both Python lines have aarch64 wheels in `ctranslate2 4.x`. |
| `ffmpeg` apt install bloats image past Coolify limits | Low | Medium | `ffmpeg` is ~30 MB on slim-bookworm; well within bounds. |
| Browser MediaRecorder produces a MIME the server can't decode (Safari WebM lack) | Low | Medium | Accept both `audio/webm` and `audio/mp4` server-side. |
| Whisper hallucinates on silence at start/end of kid recordings | Medium | Medium | VAD filter on by default; verified to mitigate this in upstream changelog and field reports. |
| Concurrent transcription requests block on the asyncio lock and back up | Low (single RPi v1) | Medium | Documented limitation; revisit with a worker pool only when profiling shows it matters. |
| GUI XSS via crafted `STT_ENGINE_URL` | Low | High | Same `json.dumps`-and-escape pattern as 3-piper; verified by `test_gui_html.py`. |
| Whisper Spanish accuracy on child speech worse than expected | Medium | Medium | Phase 0 must include at least one kid-voice clip in the benchmark; if WER is unacceptable, escalate the model size before reaching for cloud APIs. |

---

## 8. Out-of-the-way decisions

Recorded once so they don't keep getting re-litigated mid-implementation:

- **Package name**: `stt_sandbox` (mirrors `piper_sandbox`).
- **Default model**: `rhasspy/faster-whisper-small-int8` (subject to Phase 0 outcome).
- **Default language**: `es`, **not** `auto`. Spanish-only product; language detection is wasted compute.
- **No PyTorch**, no `transformers`, no Wyoming, no sherpa, no GigaAM. Slim deps.
- **Subdomain**: `ai-stt.thotenn.com`, symmetric with `ai-tts.thotenn.com`.
- **Library name in `__init__.py`**: `SttEngine` (not `WhisperEngine` — keeps engine-agnostic naming if we ever swap backends).
- **Audio normalization**: always 16 kHz / 16-bit / mono PCM before Whisper. Non-negotiable; Whisper expects this and resampling outside is cheaper and more deterministic than letting Whisper do it implicitly.
- **Tests use real fixtures**, not mocks. Mocked Whisper output would mask the exact category of issue we care about (model loading, segment iteration, decode path).
