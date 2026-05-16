# Recommendation

## TL;DR

**Do not adopt `wyoming-faster-whisper` as the base. Do not vendor or fork it.** Build a new lean HTTP service in `/home/tho/www/tho/ai/4-stt/` that imports `faster-whisper` directly and mirrors the architecture of `3-piper`. Treat `4-wyoming-faster-whisper` as a *reference for tuning constants only* (ARM model choice, beam size, VAD parameters, 16 kHz/mono normalization). Delete its working copy from disk once the constants are copied.

## Why this answer

The chain of reasoning is in docs 01 and 02. Compressed:

- The Wyoming protocol is the wrong API for an LLM-driven tutor: not HTTP, not browser-friendly, no Coolify-native reverse proxy story.
- The wrapper exposes only "send full WAV, get full text". We want partial transcripts, the same way 3-piper streams partial WAV chunks.
- The wrapper's ARM defaults are tuned for Raspberry Pi 4 (`tiny-int8`, beam 1). With our 8 GB headroom we should default at least to `small-int8`.
- Everything we want from the wrapper is two lines of Python on top of `faster-whisper.WhisperModel`. The protocol layer is pure overhead for our use case.

`faster-whisper` is the right backend; it just doesn't have to come bundled with Wyoming.

## Proposed architecture (mirrors `3-piper`)

```
4-stt/
├── pyproject.toml                # faster-whisper, ctranslate2, numpy, soundfile
├── Dockerfile                    # python:3.12-slim-bookworm + ARM-friendly deps
├── docker-compose.yml            # one service, mounts `stt-models` volume
├── .env.example
├── README.md
├── CLAUDE.md
├── docs/
│   └── context/
│       ├── 0-general/            # this folder
│       ├── 0-api/                # API cookbook (Python + JS clients, NDJSON)
│       └── 1-...                 # later
├── stt_sandbox/                  # package
│   ├── __init__.py
│   ├── config.py                 # tiny .env loader; mirror piper_sandbox/config.py
│   ├── models.py                 # ModelSpec + registry; parse `lang-size-quant`
│   ├── engine.py                 # WhisperEngine: load model, transcribe(), transcribe_stream()
│   ├── audio.py                  # decode incoming WAV/webm/ogg → 16k mono PCM via soundfile/ffmpeg
│   ├── chunks.py                 # (later) partial-segment emitter
│   ├── api.py                    # stdlib ThreadingHTTPServer; /health /models /transcribe /transcribe/stream /
│   └── gui.py                    # (optional) tiny browser test page, like piper_sandbox INDEX_HTML
└── tests/
    └── test_transcribe.py        # a known LatAm-es 5 s clip
```

### API surface (symmetric with 3-piper)

| Method | Path | Body | Response | Notes |
|---|---|---|---|---|
| GET  | `/health` | — | `{status, mode, engine, gui, streaming_enabled}` | Coolify check |
| GET  | `/models` | — | `[{name, language, size, quant, loaded}]` | Installed registry |
| POST | `/transcribe` | `multipart/form-data` with `audio` file **or** JSON `{audio_base64, mime, model?, language?, initial_prompt?, vad?}` | JSON `{text, language, duration_seconds, segments: [{start, end, text}], rtf}` | Whole-utterance |
| POST | `/transcribe/stream` | same as `/transcribe` | NDJSON: `meta` → many `segment` → `done` (or in-band `error`) | Emits each Whisper segment as it's decoded; flush per line; `X-Accel-Buffering: no` |
| GET  | `/` | — | Tiny HTML mic-record/upload tester | Optional, gated by `STT_SERVICE_MODE` |

The streaming endpoint is the architectural payoff over wyoming-faster-whisper. `faster_whisper.WhisperModel.transcribe(...)` returns an iterator of `Segment`; we wrap it in a generator that writes one NDJSON line per yielded segment.

### Defaults

| Setting | Value | Source |
|---|---|---|
| `STT_DEFAULT_MODEL` | `rhasspy/faster-whisper-small-int8` | doc 02 §"Why faster-whisper" |
| `STT_DEFAULT_LANGUAGE` | `es` | Project scope |
| `STT_COMPUTE_TYPE` | `int8` | Wyoming wrapper ARM default |
| `STT_CPU_THREADS` | `4` | Wyoming wrapper default; tune after benchmark |
| `STT_BEAM_SIZE` | `1` | Wyoming wrapper ARM heuristic |
| `STT_VAD_ENABLED` | `true` | Reduces hallucinations on child silence |
| `STT_VAD_THRESHOLD` | `0.5` | Wyoming default |
| `STT_VAD_MIN_SPEECH_MS` | `250` | Wyoming default |
| `STT_VAD_MIN_SILENCE_MS` | `2000` | Wyoming default |
| `STT_MODELS_DIR` | `models/whisper` | Mirror 3-piper's `models/piper` |
| `STT_MAX_REQUEST_BODY_BYTES` | `25 * 1024 * 1024` (25 MiB) | Cover ~5 min of 16-bit mono PCM |
| `STT_CORS_ORIGIN` | `*` initially, restrict in prod | Same posture as 3-piper |

### Audio input format

- Accept: WAV (PCM 16-bit), WebM/Opus (browser MediaRecorder default), Ogg/Opus, FLAC, MP3.
- Internally normalize to 16 kHz / 16-bit / mono PCM before handing to `WhisperModel.transcribe`.
- Decode path: `soundfile` for WAV/FLAC/OGG/MP3; shell out to `ffmpeg` for WebM if `soundfile` can't handle it. `ffmpeg` is small enough on `python:3.12-slim` (add via `apt-get install -y ffmpeg`).

### Deployment

- One Coolify resource at `ai-stt.thotenn.com`, same pattern as `ai-tts.thotenn.com`.
- `STT_HOST=0.0.0.0`, `STT_PORT=8000`, behind Coolify's proxy.
- Volume `stt-models` for `models/whisper/`.
- First request triggers model download (~250 MB for `small-int8`); we can also pre-pull at build time if cold-start matters.

### Sizing on the Ampere VPS

- `small-int8`: ~500 MB RSS at idle + working set, ~200–400 MB peak during decode. Fits with `3-piper` (Piper voices are ~60 MB each in RAM) inside 8 GB free with comfortable margin.
- Concurrency: start with **single shared `WhisperModel` + asyncio lock**, exactly the pattern wyoming-faster-whisper uses. Two concurrent requests serialize. Revisit only after we observe contention.

## What we explicitly do **not** build in v1

- No speaker diarization, no alignment (no WhisperX).
- No language detection — pin `language="es"`. Re-evaluate when English support is added.
- No model registry HTTP endpoint for downloading new models on the fly. Set the list at startup via env, like 3-piper does.
- No Wyoming protocol compatibility shim. We don't need it.

## Rollout / step order

1. **Bench first.** Spin up `python:3.12-slim` ARM container on the Hetzner VPS, `pip install faster-whisper`, transcribe one ~5 s and one ~30 s LatAm-es clip with `small-int8` and `medium-int8`. Record RTF and peak RAM. **This determines our default model.** Estimated effort: 30 min.
2. **Skeleton** (`config.py`, `models.py`, `engine.py`, single `POST /transcribe` endpoint, no streaming, no GUI). Pattern matches `piper_sandbox` 1:1 — fastest path to a working service.
3. **Audio decoding** for non-WAV inputs (multipart + WebM/Opus via ffmpeg).
4. **Streaming endpoint** `/transcribe/stream` — NDJSON, segment-by-segment.
5. **Browser test GUI** (mic record + send + show text) — same posture as piper_sandbox's `INDEX_HTML`.
6. **Dockerize + Coolify deploy** under `ai-stt.thotenn.com`.
7. **API cookbook** (`docs/context/0-api/`), symmetric to 3-piper's, covering Python + JS clients including a MediaRecorder example.

## Final answer to the original question

> ¿Es factible utilizar este repositorio como base, o crear uno nuevo teniendo en cuenta o como base 4-wyoming, o si hay una alternativa mucho mejor?

**Create a new repo.** Use `4-wyoming-faster-whisper` only as documentation — specifically, as proof that `faster-whisper` + Silero VAD on int8-quantized models is the right ARM-CPU engine, and as a source of well-tuned defaults. The actual code should be a small HTTP service that imports `faster-whisper` directly, structured like `3-piper`, deployed the same way at `ai-stt.thotenn.com`.

If a single sentence: **Wyoming-faster-whisper is the right engine in the wrong wrapper. Keep the engine, drop the wrapper.**
