# STT Research — General Context

Research bundle for the `4-stt` component of the AI tutor pipeline:

```
microphone → [STT]  →  ENGINE-LLM  →  [TTS / piper]  → speaker
            ^^^^^^                    (already live at ai-tts.thotenn.com)
```

## Project goal in one paragraph

Build an HTTP-accessible Speech-to-Text service that lives on the same Ampere ARM64 Hetzner VPS as the existing `3-piper` TTS sandbox. The service receives audio (from a browser/mobile mic in front of a child user), returns a transcript, and feeds the transcript into the upstream LLM engine (built elsewhere). Latin-American Spanish is the primary target language; English may follow later.

## Constraints

- Host: Hetzner Ampere ARM64, ~8 GB RAM free, **CPU only** (no GPU).
- Co-tenant: `3-piper` already runs on this box behind Coolify + Caddy at `ai-tts.thotenn.com`. New service should follow the same deployment pattern (Docker Compose, single subdomain, e.g. `ai-stt.thotenn.com`).
- Users: children. Inputs will be short (1–15 s), often noisy, sometimes mumbled. Latency matters more than absolute word-error-rate.
- Privacy: keep audio on our own server. No third-party cloud STT.
- Coding rules: English only in code and docs; no comments unless the file already uses them.

## Document index

1. [`01-wyoming-analysis.md`](01-wyoming-analysis.md) — Deep dive on `4-wyoming-faster-whisper`: what it is, how it works, what it costs us, and what it lacks for our use case.
2. [`02-alternatives.md`](02-alternatives.md) — Side-by-side of the realistic engine choices for ARM64 CPU (faster-whisper, whisper.cpp, sherpa-onnx/Parakeet, onnx-asr, cloud APIs).
3. [`03-recommendation.md`](03-recommendation.md) — Final conclusion: **do not adopt `wyoming-faster-whisper` as the base, do not vendor it. Build a thin HTTP service in a new repo on top of the `faster-whisper` Python library directly, mirroring the `3-piper` architecture.** Includes the proposed module layout, endpoints, default model, and rollout steps.

Read in order; each doc assumes the previous one.
