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

### Decision sub-step — required to lock the default

Only `small-int8` clears accuracy. At 4 threads its short-clip RTF (0.657) is **above** the SPEC §4.1 target (< 0.5). VPS has 8 vCPUs (no SMT on Ampere) — doubling threads should land us in budget.

- [ ] Run on VPS:

  ```bash
  cd /root/apps/ai-stt/bench
  .venv/bin/python run.py \
    --models rhasspy/faster-whisper-small-int8 \
    --cpu-threads 8 \
    --out results/bench-aarch64-t8.json \
    2>&1 | tee results/bench-aarch64-t8-stdout.log
  ```

- [ ] Apply decision matrix (from `arm64/RESULTS.md`):
  - RTF short ≤ 0.50 → **lock `small-int8` + `cpu_threads=8`**, no SPEC change.
  - RTF short 0.50–0.70 → lock same defaults, relax SPEC §4.1 to "< 0.7 for 5 s, < 0.5 for 30 s".
  - RTF short > 0.70 → escalate (try `compute_type=int8_float16` / `num_workers=2` / whisper.cpp backend swap). Do **not** fall back to `base-int8` — accuracy data above rules it out.

- [ ] **Decision (filled after the 8-thread run)**:
  - Default model: ________________
  - `STT_CPU_THREADS`: ________________
  - `STT_COMPUTE_TYPE`: ________________
  - Notes / SPEC amendments: ________________

### Findings to fold into PLAN / SPEC regardless of the decision

- [ ] PLAN §6 + SPEC §3.5: note that for typical 5–10 s utterances Whisper emits a single VAD-bounded segment, so `/transcribe/stream` does **not** save perceived latency on short inputs — it remains useful for long-form (≥ 30 s with pauses) only. The endpoint is still worth building (symmetric API, useful for long inputs, low cost), but should not be marketed as a latency win for kid questions.
- [ ] SPEC §5 default: `STT_CPU_THREADS=8` (was 4) once the decision row above lands the value.

---

## Phase 1 — Skeleton

### Project scaffolding

- [ ] `/home/tho/www/tho/ai/4-stt/.gitignore` (mirror 3-piper).
- [ ] `/home/tho/www/tho/ai/4-stt/.dockerignore` (mirror 3-piper).
- [ ] `/home/tho/www/tho/ai/4-stt/README.md` (placeholder; final pass in Phase 6).
- [ ] `/home/tho/www/tho/ai/4-stt/CLAUDE.md` (architecture summary for future agents; mirror 3-piper's tone).
- [ ] `/home/tho/www/tho/ai/4-stt/.env.example` matching SPEC.md §5.
- [ ] `/home/tho/www/tho/ai/4-stt/pyproject.toml` per PLAN §5.

### Package

- [ ] `stt_sandbox/__init__.py` re-exports `SttEngine`, `SttError`, `MODELS`, `DEFAULT_MODEL`, `TranscribeResult`, `__version__`.
- [ ] `stt_sandbox/config.py` ported from 3-piper, `PIPER_` → `STT_`.
- [ ] `stt_sandbox/models.py`:
  - [ ] `ModelSpec` dataclass.
  - [ ] `parse_model_names(value)` (JSON array or comma-separated).
  - [ ] `parse_model_spec(name)` derives `size` + `quantization` from `rhasspy/faster-whisper-<size>-<quant>`.
  - [ ] `MODELS` built at import time from `STT_MODEL_NAMES`; `STT_DEFAULT_MODEL` auto-included.
  - [ ] `get_model_spec(name)` with friendly KeyError.
- [ ] `stt_sandbox/audio.py`:
  - [ ] `decode_to_pcm(data, mime)` returns tempfile path.
  - [ ] WAV-passthrough when input is already 16 kHz mono 16-bit.
  - [ ] WAV resample/mix path via `soundfile` + numpy when needed.
  - [ ] Non-WAV paths raise `SttError("unsupported MIME in skeleton")` for now (Phase 2 wires ffmpeg).
- [ ] `stt_sandbox/engine.py`:
  - [ ] `class SttError(RuntimeError)`.
  - [ ] `@dataclass class TranscribeResult` and `class Segment`.
  - [ ] `class SttEngine` with `transcribe(wav_path, model, language, initial_prompt)` only.
  - [ ] Lazy `WhisperModel` cache keyed by model id.
  - [ ] VAD parameters passed through to `WhisperModel.transcribe(...)`.
  - [ ] `preload(name)` runs synchronously at startup.
  - [ ] All decoder exceptions wrapped as `SttError`.
- [ ] `stt_sandbox/api.py`:
  - [ ] CLI parser (`--mode`, `--engine-url`, `--host`, `--port`).
  - [ ] `main()` loads env, instantiates `SttEngine`, calls `engine.preload(DEFAULT_MODEL)`, starts `ThreadingHTTPServer`.
  - [ ] Request handler with `do_GET`, `do_POST`.
  - [ ] `MAX_REQUEST_BODY_BYTES` enforced before parsing.
  - [ ] CORS headers on every response.
  - [ ] Routes: `GET /health`, `GET /models`, `POST /transcribe`. No GUI yet.
  - [ ] JSON error responses (never HTML).

### Tests (Phase 1)

- [ ] `tests/__init__.py`.
- [ ] `tests/fixtures/short_es.wav` (commit a small clean clip; if rights are unclear, generate one with 3-piper at deploy time and document it).
- [ ] `tests/test_models.py`:
  - [ ] `parse_model_names` handles JSON, CSV, empty.
  - [ ] `parse_model_spec` extracts `(size, quantization)`.
  - [ ] `MODELS` includes default model.
- [ ] `tests/test_engine.py`:
  - [ ] `SttEngine.transcribe(short_es.wav)` returns non-empty text in Spanish.
  - [ ] RTF < 1.5 (loose bound; tighter in Phase 0 doc).
- [ ] `tests/test_transcribe_endpoint.py`:
  - [ ] 200 on valid multipart WAV upload.
  - [ ] 400 on empty body / missing audio field.
  - [ ] 400 on body over the size cap.

### Exit gate

- [ ] `curl -X POST http://127.0.0.1:8000/transcribe -F 'audio=@tests/fixtures/short_es.wav'` returns valid JSON with non-empty `text`.
- [ ] `pytest tests/ -q` is green.

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
