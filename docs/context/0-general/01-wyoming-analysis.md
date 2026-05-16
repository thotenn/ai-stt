# `4-wyoming-faster-whisper` — Deep Analysis

Source: <https://github.com/rhasspy/wyoming-faster-whisper> (vendored under `/home/tho/www/tho/ai/4-wyoming-faster-whisper`), version `3.1.1`, last reviewed against revision present on disk on 2026-05-15.

## What it actually is

A **Wyoming-protocol server** that wraps the [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper) Python library (CTranslate2 reimplementation of OpenAI Whisper). The Wyoming protocol is a custom event-based binary protocol designed for the [Rhasspy / Home Assistant voice pipeline](https://github.com/rhasspy/wyoming). It is **not HTTP** and **not gRPC**.

The package itself is small (≈8 source files, ≈600 LOC). The heavy lifting is delegated to:

| Backend            | Library                         | Selected via                |
| ------------------ | ------------------------------- | --------------------------- |
| **faster-whisper** | `faster-whisper>=1.2.1`         | default                     |
| transformers       | `transformers[torch]==4.52.4`   | `--stt-library transformers` |
| sherpa-onnx        | `sherpa-onnx>=1.12.19` (Parakeet) | `--stt-library sherpa`     |
| onnx-asr           | `onnx-asr` (GigaAM for Russian) | `--stt-library onnx-asr`    |

`--model auto` + `--language auto` does the following at startup (from `models.py:guess_model` and `__main__.py`):

- Detects ARM via `platform.machine()` (`arm` / `aarch`).
- On ARM with faster-whisper, defaults to `rhasspy/faster-whisper-tiny-int8` and beam size `1`.
- On x86 with faster-whisper, defaults to `rhasspy/faster-whisper-base-int8` and beam size `5`.
- For English, if `sherpa-onnx` is installed, picks `sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8`.
- For Russian with `onnx-asr`, picks `gigaam-v2-rnnt`.

## Protocol surface

CLI:
```
wyoming-faster-whisper \
  --uri tcp://0.0.0.0:10300 \
  --data-dir /data \
  [--download-dir /data] \
  [--model auto] [--language auto] \
  [--device cpu] [--compute-type default] \
  [--beam-size 0] [--cpu-threads 4] \
  [--initial-prompt "..."] \
  [--vad-filter --vad-threshold 0.5 --vad-min-speech-ms 250 --vad-min-silence-ms 2000] \
  [--stt-library auto|faster-whisper|transformers|sherpa|onnx-asr] \
  [--local-files-only] [--zeroconf]
```

Wire protocol (`dispatch_handler.py`):

1. Client sends `Describe` → server replies `Info` (advertises ASR program + model).
2. Client sends `Transcribe { language }` (optional, sets per-session language).
3. Client streams audio: `AudioStart { rate, width, channels }` → many `AudioChunk { audio bytes }` → `AudioStop`.
4. Server transcribes the **whole** buffered WAV in one shot and replies `Transcript { text }`. **No streaming/partial results.**

Audio is internally normalized to **16 kHz / 16-bit / mono PCM** via `AudioChunkConverter`. The accumulated WAV is saved to a `tempfile.TemporaryDirectory` and then handed to `faster_whisper.WhisperModel.transcribe(...)` in a worker thread (`asyncio.to_thread`).

A single TCP client = a single in-flight transcription. Concurrent clients each get their own `DispatchEventHandler` instance; the model itself is shared (cached in `ModelLoader._transcriber`), so concurrent decodes will serialize on whatever locking CTranslate2 does internally (it is thread-safe but contention will inflate latency).

## What's good

- **Battle-tested**: this is the canonical Home Assistant Whisper add-on. The `faster-whisper` wiring, the int8 quantized models hosted on the `rhasspy/` HF org, the ARM defaults, and the Silero VAD options are all production-tested across thousands of HA users.
- **ARM-aware**: actively branches on `platform.machine()` to pick smaller models and `beam_size=1`. We get this for free.
- **VAD filter built in**: `--vad-filter` enables Silero VAD on top of faster-whisper, which (per the upstream changelog) materially reduces Whisper hallucinations on silence/noise — relevant for a child speaking into a mic with long pauses.
- **Backend pluggability**: one CLI flag swaps between Whisper-family, Parakeet, and GigaAM. Useful if we ever want to A/B Spanish backends.
- **Healthy upstream**: changelog shows recent releases (3.0.x → 3.1.x added VAD knobs, multi-backend, Docker build). Not abandoned.

## What's bad **for our pipeline**

These are the points that determine whether we adopt it or build something next to it.

### 1. Wrong wire protocol

We are not building a Home Assistant satellite. The ENGINE-LLM (built in another repo) is going to call STT from Python/Node code, almost certainly over HTTP/JSON or HTTP/multipart. Wyoming's binary event protocol means:

- We would need a Wyoming **client** on the LLM side. `wyoming` is a Python package; there is no maintained JS/TS client. If the LLM engine grows browser-facing or non-Python pieces, this becomes a wall.
- Browser microphones cannot speak Wyoming directly. Any web client (the future "digital face" UI) will need a bridge.
- We can't put it directly behind Caddy/Coolify the way `ai-tts.thotenn.com` works today, because Coolify's reverse proxy is HTTP. We'd need a separate L4 forward or a shim.

The 3-piper deployment proves the pattern we actually want: stdlib HTTP server, multipart/JSON, behind Caddy on a subdomain. The Wyoming server forces us into a bridge layer we don't need.

### 2. No streaming / no partial transcripts

`dispatch_handler.py` writes the entire WAV to disk and only transcribes on `AudioStop`. For a 5–10 s utterance, latency = round-trip + whole-file decode. For a kids tutor the perceived turn-around is `STT_finalize + LLM_first_token + TTS_first_chunk`. We already designed `3-piper` to stream chunked WAV out (`/speak/chunks`). The symmetric win on STT is **partial transcripts** (faster-whisper exposes segment-level callbacks), and Wyoming does not surface them.

Note: this is a limitation of `wyoming-faster-whisper`'s wrapper, **not** of `faster-whisper` itself. If we use the library directly we can emit partial results.

### 3. ARM defaults are too conservative for our budget

The wrapper picks `tiny-int8` on ARM. `tiny` is famously weak on Spanish (especially Latin-American accents and child speech). We have **8 GB of free RAM**, which is enormous compared to the Raspberry Pi 4 / 5 (1–4 GB) the ARM defaults are tuned for. We can comfortably run `small-int8` (~500 MB resident, ~250–400 MB on disk) and possibly `medium-int8` (~1.5 GB). Adopting the wrapper means living with its defaults or fighting them via CLI flags. Easier to set our own.

### 4. Docker image is Debian + apt + Torch CPU wheels

The provided `Dockerfile` is `debian:bookworm-slim` + `pip install torch==2.6.0 --extra-index-url .../whl/cpu` plus `transformers`, `sherpa-onnx`, `onnx-asr`. That pulls in **PyTorch CPU on ARM** (≈400 MB), plus three optional ASR backends we will not use. The resulting image is huge and slow to build under Coolify. For our use case (Spanish, faster-whisper only) we can produce a much leaner image: `python:3.12-slim` + `faster-whisper` + `ctranslate2` + `numpy` ≈ 250 MB total, no Torch.

### 5. Multi-tenancy / API surface

We will likely want:
- `GET /health` (Coolify health check).
- `GET /models` (list installed models, mirror of 3-piper).
- `POST /transcribe` (multipart WAV or JSON-base64 → JSON `{text, language, duration_s, segments[]}`).
- `POST /transcribe/stream` (NDJSON with partial segments, mirroring `/speak/chunks`).
- Optional `GET /` GUI for manual testing, like 3-piper has.

None of this exists in `wyoming-faster-whisper`. Building it all on top means we'd either fork the wrapper or run **two** processes (Wyoming server + HTTP shim). Both options are worse than a single HTTP service that imports `faster-whisper` directly.

### 6. License / vendoring posture

MIT — no legal blocker. But vendoring a third-party server we'd then need to maintain in lockstep with upstream HA changes is overhead with no payoff for our scenario.

## Honest accounting

If our target had been "satellite for Home Assistant" or "drop-in replacement for an existing Wyoming pipeline", I would adopt `wyoming-faster-whisper` as-is. It is the right tool for that. **For our tutor pipeline it is the wrong layer**: too much protocol, too little API, and the only thing we actually want from it — the ARM tuning of `faster-whisper` — is two lines of code we can write ourselves.

The real reusable artifact in this repo is the **knowledge encoded in `models.py:guess_model` and the CLI defaults**: which model+beam size+VAD settings work on ARM CPUs. We will copy those numbers, not the wrapper.

## Concrete dependencies we'd actually keep

From `pyproject.toml`:
- `faster-whisper>=1.2.1,<2` — yes, this is the core engine.
- `wyoming>=1.8,<2` — **no**, drop entirely.
- `transformers`, `sherpa-onnx`, `onnx-asr` — no, none needed for Spanish.

From the wrapper's behavior:
- Silero VAD wiring (`vad_filter=True`, `vad_parameters={threshold, min_speech_duration_ms, min_silence_duration_ms}`) — copy.
- 16 kHz / 16-bit / mono PCM normalization — copy.
- ARM beam-size = 1 heuristic — copy.
- `int8` compute type on CPU — copy.
