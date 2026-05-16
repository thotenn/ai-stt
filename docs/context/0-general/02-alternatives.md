# Alternatives — STT Backends for ARM64 CPU

Realistic options for the engine that actually does the transcription, evaluated against our constraints (Ampere ARM64 CPU, ≤ 8 GB RAM, Latin-American Spanish, child speech, self-hosted).

## Summary table

| Option | Lang. quality (LatAm es) | RAM / disk | RTF on ARM CPU * | Streaming partials | License | Verdict |
|---|---|---|---|---|---|---|
| **faster-whisper (CTranslate2)** — `small-int8` | Strong | ~500 MB / ~250 MB | 0.3–0.6 | Yes (segment-level callback) | MIT | **Recommended** |
| faster-whisper — `tiny-int8` | Mediocre | ~150 MB / ~80 MB | 0.1–0.2 | Yes | MIT | Fallback / dev |
| faster-whisper — `medium-int8` | Excellent | ~1.5 GB / ~800 MB | 0.8–1.4 | Yes | MIT | Optional upgrade |
| whisper.cpp | Same as Whisper (model-dependent) | ~150 MB–1.5 GB | 0.2–0.8 (NEON-tuned) | Yes (`--no-fallback --output-srt` partials) | MIT | Strong second choice |
| sherpa-onnx + Parakeet TDT v3 (`int8`) | Unstable for `es` (mis-detects) | ~600 MB | 0.2–0.4 | Yes (true streaming recognizer) | Apache-2.0 | Skip for `es` |
| onnx-asr + GigaAM | Russian only | n/a | n/a | n/a | Apache-2.0 | Irrelevant |
| OpenAI Whisper API / Groq / Deepgram | Best, but cloud | 0 | n/a | Yes | Commercial | Violates "no third-party cloud" rule |
| Vosk (Kaldi) | Weak on conversational `es` | ~50–500 MB | 0.1–0.3 | True streaming | Apache-2.0 | Worse accuracy than Whisper-small |
| WhisperX | Whisper + alignment + diarization | Whisper + ~500 MB | 1.5–3× Whisper | No | BSD | Overkill |

\* RTF = Real-Time Factor = `decode_seconds / audio_seconds`. Lower is better. Numbers are order-of-magnitude estimates for an Ampere Altra-class CPU running int8/quantized models, drawn from published benchmarks for Raspberry Pi 5 / Apple M-series CPU paths and scaled (Ampere is comparable per-core to RPi 5 on quantized inference). **Always re-measure on the actual VPS once a candidate is installed.**

## Why faster-whisper is the right default

1. **Same model family as the Wyoming wrapper**, but importable as a normal Python library. We get the ARM-tuning knowledge from doc 01 with none of the protocol baggage.
2. **CTranslate2 backend** is the fastest CPU Whisper runtime in practice (faster than `transformers`, faster than `openai-whisper`, comparable to whisper.cpp for int8 on aarch64). It uses NEON SIMD on ARM automatically.
3. **Segment-level streaming**: `model.transcribe(...)` returns a generator of segments. We can emit them as NDJSON the moment each segment is decoded — this is the symmetric counterpart to `/speak/chunks` in 3-piper.
4. **Silero VAD integration** is built in — one boolean enables it, and it both improves accuracy on noisy/silent stretches and reduces the well-known Whisper hallucination problem on silence.
5. **HuggingFace model hub**: `rhasspy/faster-whisper-{tiny,base,small,medium}-int8` are pre-quantized and pinned by the Rhasspy team; no conversion step on our side.
6. **`int8` quantization** drops RAM/CPU cost ~4× vs fp32 with marginal accuracy loss for Whisper-small/medium on Spanish.
7. **Pure Python install** on ARM via PyPI: `ctranslate2` wheels exist for `aarch64-linux`, no source build needed.

## Why not whisper.cpp

It is genuinely excellent — arguably the most ARM-optimized Whisper implementation thanks to hand-written NEON kernels and 4-/5-/8-bit quantization variants (`ggml-small.bin`, `ggml-small-q5_1.bin`, etc.). Two reasons to prefer faster-whisper for *this* project:

- **Python integration**: faster-whisper is a `pip install` away and exposes a clean Python API. whisper.cpp would mean either shelling out to a binary (like 3-piper does with `piper`) or maintaining `pywhispercpp` / `whispercpp` bindings whose ARM wheels are flakier than `ctranslate2`'s. The shell-out path is workable but loses the segment-level callback for streaming partials.
- **Operational symmetry vs. throughput**: 3-piper shells out to `piper` because the Piper Python package on PyPI has its own onnxruntime install pains on ARM. For STT, the calculus is reversed: `ctranslate2` installs cleanly on aarch64, so we get a faster integration than shelling out.

If, once we benchmark, `faster-whisper-small-int8` is too slow on the Ampere VPS, **fall back to whisper.cpp with `ggml-small-q5_1.bin`** before reaching for a smaller model. whisper.cpp's quantization typically yields 20–40 % more throughput than faster-whisper int8 on ARM.

## Why not Parakeet / sherpa-onnx

NVIDIA's Parakeet TDT v2/v3 models are objectively faster than Whisper and have lower WER on English. Two killers for us:

- The Parakeet v3 "multilingual" model lists Spanish, but the wyoming-faster-whisper code itself comments: *"The v3 Parakeet model claims to auto detect other languages, but it doesn't work"* (`models.py:96-99`). That's a load-bearing field report from a project that actively ships this backend.
- Parakeet TDT is **streaming-only** (transducer) — the sherpa-onnx integration in wyoming-faster-whisper still feeds it the whole WAV at once, so we don't even get the streaming benefit there.

For an English-only product later we should re-evaluate Parakeet seriously. For Spanish today: skip.

## Why not a cloud API

- OpenAI Whisper API, Groq Whisper, Deepgram Nova: all faster and more accurate than anything we can run on a single Ampere core. **But** they violate the self-hosting requirement, add per-minute cost on a project aimed at children (which means many short utterances → expensive per-call overhead), and create a third-party dependency on the critical path.
- They remain a sensible **fallback** target if our local STT degrades or is overloaded. That's a v2 concern.

## Open questions worth resolving before code lands

1. **Actual RTF on the Hetzner Ampere instance** for `small-int8` and `medium-int8`. Run the benchmark from `faster-whisper`'s repo on a representative 5 s Spanish kid-voice clip and a 30 s clip. Decision threshold: target < 0.5 RTF for `small`, < 1.0 for `medium`.
2. **Concurrency target.** One child at a time = sequential. If more than one client can be transcribing at once, decide between (a) a single shared model with a mutex (simpler, what wyoming-faster-whisper does) or (b) a small worker pool (more RAM, fewer queueing stalls). Default to (a) until profiling says otherwise.
3. **Whether to keep raw audio on disk.** Privacy posture for a kids product favors *no*: write to `tempfile`, transcribe, delete. Mirror 3-piper's tempfile discipline.
4. **`initial_prompt` for child speech.** Whisper's `initial_prompt` heavily biases decoding toward in-domain vocabulary. For a tutor we can prime it with class topic + child's name, fed from the LLM. Worth a hook.
