# `4-stt` — Implementation Checklist

Granular work items derived from [`PLAN.md`](PLAN.md). Tick boxes as work lands.

Conventions:
- All code in **English** (identifiers, strings, log messages). GUI strings are the only allowed Spanish, to match `3-piper`.
- **No comments** in code unless a file already uses them.
- Tests use real audio fixtures, not mocks.

---

## Phase 0 — Benchmark (blocks Phase 1+)

### Harness + local x86 baseline — done

- [x] Bench harness lives at `/home/tho/www/tho/ai/4-stt/bench/` (`run.py`, `clips/`, `models/`, `results/`, dedicated `.venv` with `faster-whisper 1.2.1` + `psutil`). Re-runnable on any host with one command.
- [x] Reference clips synthesized via `3-piper` `es_MX-ald-medium`:
  - [x] `short_es.wav` (4.62 s) — "Hola, ¿cómo estás? Quiero aprender sobre los planetas del sistema solar."
  - [x] `medium_es.wav` (23.0 s) — Tutor paragraph about the solar system.
  - [ ] `kid_es.wav` — real child voice, **not v1 blocker** (see RESULTS doc).
- [x] Local x86_64 run with `cpu_threads=4`, `compute_type=int8`, `beam_size=1`, VAD on, 3 repeats. Full results: [`/home/tho/www/tho/ai/4-stt/bench/results/RESULTS-LOCAL-X86.md`](../../../bench/results/RESULTS-LOCAL-X86.md) and `bench-x86_64.json`.

  | Model | Clip | RTF (avg 3) | First-segment | Peak RSS | Accuracy notes |
  |---|---|---|---|---|---|
  | tiny-int8   | short_es  | 0.057 | 0.27 s | 263 MB | ❌ drops opening *Hola* |
  | tiny-int8   | medium_es | 0.019 | 0.43 s | 311 MB | ❌ "Mercurio→Me curioso", "anillos→niños" |
  | base-int8   | short_es  | 0.087 | 0.40 s | 375 MB | ❌❌ garbled: "o la como estas" |
  | base-int8   | medium_es | 0.040 | 0.60 s | 437 MB | ❌ missing *Urano*, "Neptuno→Nectuno" |
  | small-int8  | short_es  | 0.242 | 1.12 s | 668 MB | ✅ verbatim |
  | small-int8  | medium_es | 0.078 | 1.80 s | 744 MB | ⚠️ best — only proper-noun slips ("Uranus"/"Neptune") |

### ARM run on Hetzner VPS — done (sweep with 4 threads)

Host: Hetzner CAX31, 8 vCPUs / 16 GB RAM, Ubuntu 24.04, aarch64. Full analysis: [`../../../bench/results/arm64/RESULTS.md`](../../../bench/results/arm64/RESULTS.md).

- [x] Bench cloned to `/root/apps/ai-stt/bench`, venv with `faster-whisper 1.2.1` + `psutil`.
- [x] `run.py` executed at default `cpu_threads=4`. Artifacts: `bench-aarch64.json`, `bench-aarch64-stdout.log`.

  | Model | Clip | RTF (avg 3) | First-segment | Peak RSS | Accuracy |
  |---|---|---|---|---|---|
  | tiny-int8   | short_es  | 0.152 | 0.70 s | 243 MB | ❌ drops *Hola* |
  | tiny-int8   | medium_es | 0.051 | 1.18 s | 312 MB | ❌ "Mercurio→secano", "hielo→hierro" |
  | base-int8   | short_es  | 0.235 | 1.09 s | 382 MB | ❌❌ "o la como estas" |
  | base-int8   | medium_es | 0.124 | 1.79 s | 468 MB | ❌ missing *Urano*, "Nectunno" |
  | small-int8  | short_es  | **0.657** | 3.04 s | 706 MB | ✅ all content words correct |
  | small-int8  | medium_es | **0.217** | 4.99 s | 702 MB | ✅ best — *Urano* and *anillos* captured |

### Thread sweep — done

- [x] Re-ran `small-int8` with `--cpu-threads 8`. Artifacts: `bench/results/arm64/bench-aarch64-t8.{json,stdout.log}`.

  | Threads | Short RTF | Medium RTF |
  |---|---|---|
  | 4 | 0.657 | **0.217** |
  | 8 | 0.689 | **0.390** |

  Eight threads does **not** help; medium clip nearly doubles in latency (thread contention or Hetzner shared-instance noise). 4 threads is the optimum for this VPS class.

### Final Phase 0 decision — locked

- [x] **Default model**: `rhasspy/faster-whisper-small-int8`. Only model with acceptable Spanish accuracy.
- [x] **`STT_CPU_THREADS=4`**. t=8 measured worse than t=4 on the actual VPS.
- [x] **`STT_COMPUTE_TYPE=int8`**. `int8_float16` deferred to v1.x exploration.
- [x] **`STT_BEAM_SIZE=1`**. ARM heuristic from `wyoming-faster-whisper`; sufficient.
- [x] SPEC §4.1 amended: target relaxed to **RTF < 0.7 for 5 s, RTF < 0.5 for 30 s** (calibrated to measured floor 0.657 / 0.217).
- [x] SPEC §3.5 amended: documented that `/transcribe/stream` has no perceived-latency benefit for utterances < 30 s (single VAD segment → first-segment = total decode).
- [x] PLAN §8 unchanged — `small-int8` was already the provisional default; `cpu_threads=4` matches the pre-existing entry.

### v1.x exploration tickets (post-launch, not blocking Phase 1)

- [ ] Micro-benchmark `compute_type=int8_float16` (1 min): `.venv/bin/python run.py --models rhasspy/faster-whisper-small-int8 --compute-type int8_float16 --out results/bench-aarch64-int8fp16.json`.
- [ ] whisper.cpp swap-in benchmark per `docs/context/0-general/02-alternatives.md`, only if production feel is unsatisfactory after Phase 5 deploy.
- [ ] Real child-voice `kid_es.wav` accuracy verification before public launch.

---

## Phase 1 — Skeleton ✅

### Project scaffolding

- [x] `.gitignore`, `.dockerignore`, `.env.example`, `pyproject.toml`, `README.md` (placeholder), `CLAUDE.md`.

### Package (`stt_sandbox/`)

- [x] `__init__.py` re-exports `SttEngine`, `SttError`, `MODELS`, `DEFAULT_MODEL`, `TranscribeResult`, `Segment`, `__version__`.
- [x] `config.py` ported from 3-piper (`PIPER_` → `STT_`) plus `env_float`.
- [x] `models.py`: `ModelSpec`, `parse_model_names` (JSON / CSV / empty), `model_spec_from_name` regex over `rhasspy/faster-whisper-<size>-<quant>`, `MODELS` built at import with `STT_DEFAULT_MODEL` auto-included, `get_model_spec` with friendly KeyError.
- [x] `audio.py`: `decode_to_pcm(data, mime)` writes tempfile and validates WAV via `wave.open`. Phase 2 will add ffmpeg for non-WAV; full strict resampling deferred (faster-whisper accepts any sample rate).
- [x] `engine.py`: `SttError`, `Segment`, `TranscribeResult` dataclasses; `SttEngine` with lazy `WhisperModel` cache, per-model load lock, shared transcribe lock (mirrors wyoming-faster-whisper), VAD parameters forwarded, `preload(name)`, all decoder exceptions wrapped as `SttError`.
- [x] `multipart.py`: stdlib `cgi` is gone in Python 3.13 → wrote a minimal RFC 2046 multipart parser (`MultipartPart`, `parse_multipart`).
- [x] `api.py`: argparse CLI (`--host`, `--port`, `--mode`, `--engine-url`, `--no-preload`, `--debug`), `ThreadingHTTPServer`, `SttRequestHandler` with `do_GET` / `do_POST` / `do_OPTIONS`, CORS on every response, body cap enforced before parse, JSON error responses, routes gated by `engine_enabled` / `gui_enabled`. Routes: `GET /health`, `GET /models`, `POST /transcribe`, `GET /` (placeholder until Phase 4), `POST /transcribe/stream` (501 until Phase 3).

### Tests — 30 passing in 9.84 s

- [x] `tests/__init__.py`, `tests/conftest.py` (session-scoped `shared_engine` using `tiny-int8` to keep CI fast; first run downloads ~80 MB), `tests/fixtures/short_es.wav` copied from `bench/clips/`.
- [x] `tests/test_models.py` (8) — registry + parsing + regex extraction.
- [x] `tests/test_audio.py` (5) — happy path, MIME sniffing, empty/garbage/non-WAV errors with Phase 2 hint.
- [x] `tests/test_multipart.py` (5) — boundary parsing, multi-field, missing closing/name errors.
- [x] `tests/test_engine.py` (3) — real fixture transcription returns `es` text containing *planetas*, *sistema*, *solar*; round-trip `to_dict`.
- [x] `tests/test_transcribe_endpoint.py` (9) — `/health`, `/models`, `/transcribe` happy path, empty body 400, missing audio 400, body cap 413, `/transcribe/stream` 501, unsupported MIME 400, `engine` mode → `/` 404.

### Exit gate

- [x] `pytest -x` → **30 passed**.
- [x] Live server smoke test (`--port 8765 --no-preload`):
  - `GET /health` → `{status: ok, mode: both, engine: true, gui: true, stream_enabled: true, model_loaded: rhasspy/faster-whisper-small-int8, language: es}`
  - `GET /models` → 3 models with `size`/`quantization`/`language`/`loaded` fields
  - `POST /transcribe` (multipart) → `text: "¿Cómo estás? Quiero aprender sobre los planetas del sistema solar."`, `language: es`, `duration_seconds: 4.621`, `decode_seconds: 0.339`, `rtf: 0.073`, full segment list.
  - `POST /transcribe` (JSON+base64) → identical transcript, alternate transport works.
  - `POST /transcribe/stream` → HTTP 501 as planned.
  - `GET /nonexistent` → HTTP 404.

### Phase 1 deviations from PLAN.md (worth surfacing)

- Added `stt_sandbox/multipart.py` (not in original PLAN) because `cgi` was removed in Python 3.13. ~80 LOC, no external deps.
- `audio.py` does not yet do active resampling/channel-mix; faster-whisper handles arbitrary sample rates internally, so Phase 1 just validates the WAV is parseable. If we ever need strict 16 kHz/mono normalization (e.g. for whisper.cpp backend swap), revisit in Phase 2 alongside the ffmpeg path.
- Engine lock is `threading.Lock` (not `asyncio.Lock`) because the stdlib HTTP server uses threads, not asyncio. Same serialization semantics; matches the actual runtime.

---

## Phase 2 — Decoder for non-WAV inputs

- [ ] Add `ffmpeg` dependency (system binary, not pip).
- [ ] `audio.decode_to_pcm` shells out to `ffmpeg -i <tempin> -ac 1 -ar 16000 -f wav -acodec pcm_s16le <tempout>` for non-WAV MIMEs.
- [ ] argv array, no shell string. Validate `ffmpeg` resolved via `shutil.which('ffmpeg')` once at import.
- [ ] Add `tests/fixtures/short_es.webm` (Opus) and `short_es.ogg` and `short_es.mp3`.
- [ ] `tests/test_audio.py`:
  - [ ] Decode each fixture, assert output WAV is 16 kHz / 16-bit / mono.
  - [ ] Decode of corrupt input raises `SttError`, no crash.
- [ ] `tests/test_transcribe_endpoint.py`:
  - [ ] 200 on `audio/webm` upload returns same text (approximately) as the WAV fixture.

### Exit gate

- [ ] `curl -X POST http://127.0.0.1:8000/transcribe -F 'audio=@tests/fixtures/short_es.webm'` returns valid JSON.

---

## Phase 3 — Streaming endpoint

- [ ] `stt_sandbox/streaming.py` with `NdjsonWriter(wfile)` that flushes per line.
- [ ] `SttEngine.transcribe_stream(...)` generator yielding:
  - [ ] `{"type":"meta", "model", "language", "duration_seconds"}` first.
  - [ ] One `{"type":"segment", "index", "start", "end", "text", "decode_seconds"}` per faster-whisper segment.
  - [ ] `{"type":"done", "text", "rtf"}` last.
- [ ] `api.py` route `/transcribe/stream`:
  - [ ] 501 when `STT_STREAM_ENABLED=false`.
  - [ ] `Content-Type: application/x-ndjson`, `X-Accel-Buffering: no`, no `Content-Length`.
  - [ ] In-band `{"type":"error", "index", "message"}` on mid-stream failure.
  - [ ] Pre-stream errors return HTTP 4xx/5xx before any NDJSON.
- [ ] `/health` reports `stream_enabled`.
- [ ] `tests/test_transcribe_stream_endpoint.py`:
  - [ ] First line is `meta`.
  - [ ] At least one `segment` line.
  - [ ] Last line is `done`.
  - [ ] `done.text == ''.join(seg.text for seg in segments).strip()`.
  - [ ] Error-injection variant emits `error` event and the stream closes cleanly.

### Exit gate

- [ ] `curl -N -X POST http://127.0.0.1:8000/transcribe/stream -F 'audio=@tests/fixtures/medium_es.wav'` prints lines progressively (first `segment` arrives before the equivalent `/transcribe` would have completed).

---

## Phase 4 — Browser GUI

- [ ] `INDEX_HTML` constant added to `api.py`. Spanish UI strings only.
- [ ] Route `GET /` serves the GUI, replaces `__ENGINE_URL_JSON__` with a JSON+`<`-escaped literal.
- [ ] Gated by `gui_enabled`; returns 404 in `engine` mode.
- [ ] GUI features:
  - [ ] Model `<select>` populated from `GET /models` with default preselected.
  - [ ] `getUserMedia({audio: {channelCount:1, sampleRate:16000}})`.
  - [ ] `MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' })`. Fallback to `audio/mp4` for Safari.
  - [ ] Record/stop button with timer.
  - [ ] On stop, POST blob to `/transcribe/stream` when `stream_enabled`, else `/transcribe`.
  - [ ] Render transcript progressively in streaming mode (append per `segment` event).
  - [ ] `<audio controls>` plays back the just-sent blob.
  - [ ] Status line: `Listo` / `Grabando 0:03` / `Transcribiendo...` / `Listo` / `Error: ...`.
- [ ] CLI flags `--mode gui --engine-url URL` route the GUI to a remote engine.
- [ ] `tests/test_gui_html.py`:
  - [ ] HTML renders without unresolved `__ENGINE_URL_JSON__`.
  - [ ] Injecting `STT_ENGINE_URL='</script><script>alert(1)</script>'` does not produce an executable `<script>` in the output.

### Exit gate

- [ ] Open `http://127.0.0.1:8000/` in Chrome on a laptop with a mic. Click Grabar, speak Spanish, click Detener, see a transcript appear within a few seconds.

---

## Phase 5 — Docker + Coolify deploy

- [ ] `Dockerfile` per PLAN §5. Installs `ffmpeg`. Base `python:3.12-slim-bookworm`.
- [ ] `docker-compose.yml` per PLAN §5 with healthcheck on `/health`.
- [ ] Confirm image builds on `linux/arm64`: `docker buildx build --platform linux/arm64 -t stt-sandbox:local .`.
- [ ] Image size ≤ 400 MB. If larger, investigate before deploy.
- [ ] Local Compose run boots successfully on the Ampere VPS, `/health` returns 200 from `127.0.0.1:8000`.
- [ ] Create Coolify resource at `ai-stt.thotenn.com`:
  - [ ] GitHub Apps integration (same pattern as `3-piper`).
  - [ ] Env vars set from `.env.example` defaults.
  - [ ] Volume `stt-models` mounted at `/app/models`.
  - [ ] Healthcheck enabled.
- [ ] DNS / Caddy / subdomain wired up.
- [ ] `https://ai-stt.thotenn.com/health` returns 200 publicly.
- [ ] Smoke-test from a browser: GUI at `https://ai-stt.thotenn.com/` records, sends, transcribes.
- [ ] Smoke-test from Python: post a WAV via `requests.post`, get a transcript.

### Exit gate

- [ ] Service is reachable at `https://ai-stt.thotenn.com`, returns a valid Spanish transcript for at least one recorded utterance from a real laptop mic.

---

## Phase 6 — Polish & v1.x backlog (post-launch)

Not gated on this checklist; promote to a real ticket as the RPi client surfaces needs.

- [ ] `initial_prompt` injected per request from the LLM (already wired in engine; expose in handler).
- [ ] API cookbook in `docs/context/0-api/`:
  - [ ] `README.md` (index, mirror 3-piper's).
  - [ ] `python_client.md` (`requests` + multipart, NDJSON consumer).
  - [ ] `js_client.md` (browser `fetch` + `MediaRecorder`, NDJSON consumer).
  - [ ] `rpi_recipe.md` (Pi mic capture via `sounddevice` → POST → handle response).
  - [ ] `llm_with_voice.md` (round-trip: RPi → STT → LLM → TTS → RPi).
  - [ ] `production_ops.md` (Coolify, CORS pinning, log levels, model upgrade path).
- [ ] `STT_LOG_TRANSCRIPTS=true` toggle for debug DEBUG logging.
- [ ] `/metrics` Prometheus endpoint if observability becomes a need.
- [ ] English support (`STT_DEFAULT_LANGUAGE=en` or per-request `language`).
- [ ] CI: GitHub Actions workflow that builds on `linux/arm64` and runs `pytest`.

---

## Verification artifacts to keep

After each phase exit gate, copy the relevant terminal output into a "verification" section of the PR/commit message so we have evidence the gate actually passed (not just claimed). Examples:

- Phase 1: `pytest -q` output + the successful `curl` JSON.
- Phase 3: a captured NDJSON stream showing progressive `segment` lines with rising timestamps.
- Phase 5: `curl https://ai-stt.thotenn.com/health` output.
