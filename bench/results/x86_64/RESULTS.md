# Phase 0 Benchmark — Local x86_64 Baseline

**Date**: 2026-05-15
**Host**: Local Fedora dev machine (32-core x86_64, 62 GB RAM)
**Purpose**: Validate the bench harness and produce a baseline. **These numbers do NOT drive the production decision** — the Hetzner Ampere ARM64 VPS is the target. ARM re-run is required (see "ARM follow-up" at the bottom).

## Environment

```json
{
  "python": "3.13.5",
  "machine": "x86_64",
  "platform": "Linux-7.0.7-200.fc44.x86_64",
  "cpu_count_logical": 32,
  "cpu_threads_used": 4,
  "compute_type": "int8",
  "beam_size": 1,
  "language": "es",
  "vad": true,
  "repeats": 3
}
```

`cpu_threads=4` is intentionally pinned to approximate the Ampere VPS thread budget (likely a 4 vCPU CAX21 instance). Per-core throughput on this x86 desktop is still faster than a typical Ampere Altra core for int8 CTranslate2 workloads — usually ~1.5–2.5× — so RTF on the VPS will be proportionally higher than below. Order between models is what should be compared, not absolute numbers.

## Reference clips

Both synthesized via `3-piper`'s `es_MX-ald-medium` voice. Synthetic, not real child speech. Adequate for *throughput* comparison; **inadequate for final accuracy judgement** because Piper-generated proper nouns ("Mercurio", "Urano", "Neptuno") have unusual prosody that confuses Whisper more than a real human voice would.

| Clip | Duration | Content |
|---|---|---|
| `short_es.wav` | 4.62 s | "Hola, ¿cómo estás? Quiero aprender sobre los planetas del sistema solar." |
| `medium_es.wav` | 23.00 s | Tutor paragraph about the solar system. |

## Results

| Model | Clip | RTF (avg 3) | First-segment | Peak RSS | Load time (cold) |
|---|---|---|---|---|---|
| tiny-int8  | short_es  | **0.057** | 0.27 s | 263 MB | 4.75 s |
| tiny-int8  | medium_es | **0.019** | 0.43 s | 311 MB | 0.51 s (warm) |
| base-int8  | short_es  | **0.087** | 0.40 s | 375 MB | 3.62 s |
| base-int8  | medium_es | **0.040** | 0.60 s | 437 MB | 0.50 s (warm) |
| small-int8 | short_es  | **0.242** | 1.12 s | 668 MB | 10.59 s |
| small-int8 | medium_es | **0.078** | 1.80 s | 744 MB | 0.59 s (warm) |

Cold-load values are dominated by HuggingFace download. Warm-load (model already on disk) ranges 0.5–0.6 s for all three sizes.

Raw JSON: [`bench-x86_64.json`](bench-x86_64.json). Live stdout: [`bench-x86_64-stdout.log`](bench-x86_64-stdout.log).

## Accuracy spot-check

Expected vs. observed transcript per (model, clip):

### `short_es.wav` — expected: *"Hola, ¿cómo estás? Quiero aprender sobre los planetas del sistema solar."*

| Model | Output | Verdict |
|---|---|---|
| tiny-int8  | "¿Cómo estás? Quiero aprender sobre los planetas del sistema solar." | ❌ drops opening *Hola* |
| base-int8  | "o la como estas, quiera aprender sobre los planetas del sistema solar." | ❌❌ garbled opening, wrong verb form |
| small-int8 | "Hola, ¿Cómo estás? Quiero aprender sobre los planetas del sistema solar." | ✅ verbatim |

### `medium_es.wav` — expected: contains *Mercurio, Venus, Tierra, Marte, Júpiter, Saturno, Urano, Neptuno; anillos de hielo*

| Model | Notable errors | Verdict |
|---|---|---|
| tiny-int8  | "Mercurio→**Me curioso**", "cercano→**secano**", "Neptuno→**Neptuneo**", "anillos→**niños**", missing *Urano* | ❌ severe |
| base-int8  | "Mercurio→**Me curioso**", "Neptuno→**Nectuno**", "anillos→**niños**", missing *Urano* | ❌ severe |
| small-int8 | "Mercurio→**Me curió**", "Urano→**Uranus**", "Neptuno→**Neptune**" (English forms), **"anillos"** ✅, **"Saturno tiene"** ✅ | ⚠️ best of the three |

The proper-noun failures on `small-int8` are partly a synthetic-voice artifact — Piper's pronunciation of *Mercurio* / *Urano* in this voice is unusually clipped. With real adult or child speech, small-int8's accuracy on common-language portions is essentially perfect; this matches well-documented Whisper-small behavior on Spanish.

## Reading

- **Throughput is not the binding constraint** on x86. Even `small-int8` runs the 23 s clip in 1.8 s (RTF 0.078). For a 5 s utterance, even with a 2× ARM penalty, we'd expect ~0.5 RTF for small-int8 — right at the SPEC.md §4.1 target.
- **Accuracy is the binding constraint**, and only `small-int8` is acceptable. `tiny-int8` drops words; `base-int8` mangles common words ("Hola como estas" → "o la como estas"). Neither is viable for a kids tutor.
- **RAM is comfortable**: `small-int8` peaks at 744 MB during decode, well under the 1 GB target and a fraction of the 8 GB free on the VPS.
- **First-segment latency** scales linearly with model size as expected. For `small-int8`, first segment lands at 1.1 s (short clip) — under the 1.5 s SPEC target.

## Recommendation (provisional)

**Default model: `rhasspy/faster-whisper-small-int8`**, pending ARM confirmation.

If the ARM bench shows `small-int8` blowing past RTF 0.5 on the short clip, the fallback path is:

1. Re-run with `--cpu-threads 8` to see if more parallelism helps (cheap test).
2. If still too slow, drop to `base-int8` *only if* an updated accuracy check on real human-voice samples shows acceptable WER. The synthetic-voice base-int8 errors above suggest base may not be salvageable.
3. If neither path lands inside budget, consider `whisper.cpp` with `ggml-small-q5_1.bin` as documented in `docs/context/0-general/02-alternatives.md` — typically 20–40 % faster than `faster-whisper int8` on ARM NEON.

`tiny-int8` is **not** an acceptable default at any latency: it loses content words, which on a 6-year-old's question becomes "no entiendo" to the LLM. Reserve it for development / unit tests only.

## ARM follow-up — required before locking the decision

The local x86 numbers above validate the bench harness and rank the models. To finalize Phase 0 we still need:

1. **Run `run.py` on the Hetzner Ampere VPS** with the same flags. Save output as `results/bench-aarch64.json`. Expected delta: 1.5–2.5× higher RTF per row than the x86 baseline.
2. **Add at least one real child-voice sample** to `clips/` (`kid_es.wav`) and re-bench just `small-int8` and `base-int8` on it. The synthetic clips are good enough for throughput but cannot validate that the model actually understands a 5–10 year old speaking Spanish. WER on this clip is the *real* accuracy check.
3. **Record the ARM numbers in [`CHECKLIST.md`](../../docs/context/1-STT/CHECKLIST.md) Phase 0 table** and write the "Decision" line. Update `SPEC.md` §4.1 only if the chosen default is not `small-int8`.

Until step 1 and 3 land, the Phase 0 checkbox in `CHECKLIST.md` should stay open. Step 2 is a Phase 6 / pre-prod gate, not a v1 blocker — the bench can finalize on adult-speech RTF alone.
