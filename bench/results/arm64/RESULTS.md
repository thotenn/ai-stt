# Phase 0 Benchmark — Hetzner Ampere ARM64

**Date**: 2026-05-16
**Host**: `ubuntu-16gb-hel1-1` — Hetzner CAX31-class Ampere Altra, 8 vCPUs, 16 GB RAM (Ubuntu 24.04, Linux 6.8 aarch64, Python 3.12.3).
**Purpose**: Production reference. These numbers **drive the Phase 0 decision** in [`../../../docs/context/1-STT/CHECKLIST.md`](../../../docs/context/1-STT/CHECKLIST.md).

## Environment

```json
{
  "python": "3.12.3",
  "machine": "aarch64",
  "platform": "Linux-6.8.0-111-generic-aarch64-with-glibc2.39",
  "cpu_count_logical": 8,
  "cpu_threads_used": 4,
  "compute_type": "int8",
  "beam_size": 1,
  "language": "es",
  "vad": true,
  "repeats": 3
}
```

Raw artifacts: [`bench-aarch64.json`](bench-aarch64.json), [`bench-aarch64-stdout.log`](bench-aarch64-stdout.log).

## Results

| Model | Clip | RTF (avg 3) | First-segment | Peak RSS | Cold load |
|---|---|---|---|---|---|
| tiny-int8  | short_es (4.62 s)  | **0.152** | 0.70 s | 243 MB | 2.12 s |
| tiny-int8  | medium_es (23.0 s) | **0.051** | 1.18 s | 312 MB | 0.42 s (warm) |
| base-int8  | short_es           | **0.235** | 1.09 s | 382 MB | 2.11 s |
| base-int8  | medium_es          | **0.124** | 1.79 s | 468 MB | 0.41 s (warm) |
| small-int8 | short_es           | **0.657** | 3.04 s | 706 MB | 2.78 s |
| small-int8 | medium_es          | **0.217** | 4.99 s | 702 MB | 0.52 s (warm) |

## Cross-arch comparison vs. x86 baseline

| Row | x86 RTF | ARM RTF | ARM / x86 |
|---|---|---|---|
| tiny short   | 0.057 | 0.152 | 2.67× |
| tiny medium  | 0.019 | 0.051 | 2.68× |
| base short   | 0.087 | 0.235 | 2.70× |
| base medium  | 0.040 | 0.124 | 3.10× |
| small short  | 0.242 | 0.657 | 2.71× |
| small medium | 0.078 | 0.217 | 2.78× |

Consistent ~2.7–3.1× ARM penalty. Matches expectations for Ampere Altra single-core throughput on int8 CTranslate2 vs. a modern desktop x86 core.

## Accuracy spot-check (ARM transcripts)

| Model | Clip | Output | Verdict |
|---|---|---|---|
| tiny-int8  | short  | "¿Cómo estás? Quiero aprender sobre los planetas del sistema solar." | ❌ drops *Hola* |
| tiny-int8  | medium | "Me curioso es el más **secano**", "Neptuneo", "anillos→**niños**", "hielo→**hierro**" | ❌ severe content errors |
| base-int8  | short  | "**o la como estas, quiera aprender** sobre los planetas..." | ❌❌ unusable |
| base-int8  | medium | missing *Urano*, "Neptuno→**Nectunno**", "Mercurio→Me curioso" | ❌ severe |
| small-int8 | short  | "Hola como estas quiero aprender sobre los planetas del sistema solar" | ✅ all content words correct (punctuation only) |
| small-int8 | medium | "Me curió ese más cercano … Saturno, **Urano** y Neptune. Sabías que Saturno tiene **anillos de hielo** y rocas." | ✅ best ARM run — *Urano* captured (vs. "Uranus" on x86), only *Mercurio* and *Neptuno* slip |

Accuracy ranking on ARM is identical to x86: only `small-int8` is usable. `base-int8` is *worse* than tiny on the short clip ("o la como estas") — surprising at first but consistent across both archs and three repeats, so it's a genuine model artifact, not noise.

## The latency problem

For the short utterance (4.62 s) the SPEC.md §4.1 target is **RTF < 0.5**. ARM `small-int8` at 4 threads measures **RTF 0.657** — about 30 % over target. Concretely, a 5 s child utterance takes ~3.0 s to decode end-to-end, then LLM + TTS round-trip on top.

Two important nuances about *first-segment latency*:

- For both clips, `first_segment == decode_seconds`. faster-whisper with VAD on emits one segment per VAD-bounded speech window, and both fixtures are continuous adult speech, so the whole utterance comes back as a single segment.
- This means our planned `/transcribe/stream` endpoint provides **no perceived-latency win for typical short tutor utterances** — first segment = total decode. The streaming endpoint still matters for long-form input (≥ 30 s with natural pauses), but for the 5–10 s child-question case it does not save time. Worth recording in PLAN §6 / SPEC.

## Why 4 threads might be leaving performance on the table

The VPS has **8 logical cores** (`cpu_count_logical=8`). The bench used `cpu_threads=4` (default copied from `wyoming-faster-whisper`, which was tuned for 2–4 core Raspberry Pis). Ampere Altra has no SMT, so 8 logical = 8 physical cores. CTranslate2 int8 scales close-to-linearly up to physical-core count for Whisper-small.

Expected: doubling threads from 4 → 8 should drop `small-int8` short RTF from 0.657 to roughly **0.35–0.45**, putting us inside the SPEC target.

**This needs one short confirmation run before we lock the default.** See "Confirmation step" below.

## Confirmation step — required to lock the decision

Run on the VPS:

```bash
cd /root/apps/ai-stt/bench
.venv/bin/python run.py \
  --models rhasspy/faster-whisper-small-int8 \
  --cpu-threads 8 \
  --out results/bench-aarch64-t8.json \
  2>&1 | tee results/bench-aarch64-t8-stdout.log
```

(~1 min wall time. Model already cached from the first run.)

Goal: see whether `small-int8` short_es RTF drops below 0.5 with 8 threads. If it does → lock `small-int8` + `cpu_threads=8` as defaults, done. If it doesn't, we have a real choice to make (see decision matrix below).

## Decision matrix (after the 8-thread run)

| Outcome | Decision |
|---|---|
| `small-int8 @ t=8` RTF short ≤ 0.50 | **Default: `small-int8`, `cpu_threads=8`.** Update SPEC §4.1, PLAN §8, CHECKLIST. Move to Phase 1. |
| `small-int8 @ t=8` RTF short 0.50–0.70 | **Still default to `small-int8 @ t=8`** but relax SPEC §4.1 to "< 0.7 for 5 s, < 0.5 for 30 s" and accept the latency. Add a v1.x ticket to explore whisper.cpp as a future swap-in. |
| `small-int8 @ t=8` RTF short > 0.70 | **Escalate**: try `compute_type=int8_float16` (sometimes faster than pure int8 on Ampere), or downgrade `WhisperModel(..., num_workers=2)` parallel decoding. If still no, **switch backend** to whisper.cpp per `docs/context/0-general/02-alternatives.md`. Do NOT fall back to `base-int8` — accuracy data above shows it is unusable for production. |

## Memory budget — no concern

`small-int8` peaks at ~706 MB RSS during decode. Adding ~200 MB headroom for the HTTP server + Python overhead → ~1 GB for the STT service. With `3-piper` co-resident (~500 MB for Piper + a loaded voice) we're at ~1.5 GB used out of 16 GB. Plenty of room for `medium-int8` (~1.5 GB peak) if we ever decide accuracy needs more — but accuracy is already strong on `small-int8`, and `medium` would push RTF further out of budget.

## v1.x follow-up (not blocking Phase 1)

- Real child-voice sample (`kid_es.wav`) to validate accuracy on the actual target population. Spot the words that genuinely matter for a tutor and verify `small-int8` lands them.
- Whisper `medium-int8` benchmark, *only* if we later observe accuracy regressions in production on hard inputs.
- whisper.cpp `ggml-small-q5_1.bin` benchmark as a backend swap-in study, *only* if the production latency feels unacceptable after Phase 5 deploy.

## Provisional decision

**Pending the 8-thread confirmation run, the default will be:**

- Model: `rhasspy/faster-whisper-small-int8`
- `STT_COMPUTE_TYPE=int8`
- `STT_CPU_THREADS=8`
- `STT_BEAM_SIZE=1`
- VAD: on with the wyoming-faster-whisper defaults already in SPEC.md §5

If the 8-thread run lands above the matrix's escalation row, this doc will be amended with the actual chosen path and SPEC/PLAN updated accordingly.
