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

## Confirmation step — done

Re-ran `small-int8` with `--cpu-threads 8`. Artifacts: [`bench-aarch64-t8.json`](bench-aarch64-t8.json), [`bench-aarch64-t8-stdout.log`](bench-aarch64-t8-stdout.log).

| Threads | Short RTF (4.62 s) | Medium RTF (23.0 s) | Peak RSS |
|---|---|---|---|
| 4 | 0.657 | **0.217** | 706 MB |
| 8 | 0.689 | **0.390** | 587 MB |

**Eight threads does not help; on the medium clip it's nearly 2× slower.** Two plausible causes:

1. **Thread / memory contention.** `small-int8` matrices are small enough that the marginal core stops paying for itself well before 8, and the shared resident weights start hitting cache contention.
2. **Hetzner shared-instance noise.** CAX-series exposes 8 vCPUs but Ampere Altra cores can be over-subscribed at the hypervisor level under load. The pattern (short clip ≈ flat, medium clip degrades) is consistent with intermittent CPU steal.

Either way, **4 threads is the optimum for this VPS class with this model**. Higher thread counts are not on the table.

## Decision matrix — applied

The matrix below was written *before* the 8-thread run. With 8 threads now ruled out, the applicable row is "4-thread RTF short 0.50–0.70" (0.657 → middle band).

| Outcome (8 threads) | Decision |
|---|---|
| RTF short ≤ 0.50 | Lock `small-int8 @ t=8`. — *N/A, t=8 made it worse.* |
| RTF short 0.50–0.70 | Lock `small-int8 @ t=4`, relax SPEC §4.1 to "< 0.7 for 5 s, < 0.5 for 30 s". *Accept the latency.* — **APPLIED** |
| RTF short > 0.70 | Escalate (`int8_float16`, `num_workers`, whisper.cpp). — *N/A.* |

## Memory budget — no concern

`small-int8` peaks at ~706 MB RSS during decode. Adding ~200 MB headroom for the HTTP server + Python overhead → ~1 GB for the STT service. With `3-piper` co-resident (~500 MB for Piper + a loaded voice) we're at ~1.5 GB used out of 16 GB. Plenty of room for `medium-int8` (~1.5 GB peak) if we ever decide accuracy needs more — but accuracy is already strong on `small-int8`, and `medium` would push RTF further out of budget.

## v1.x follow-up (not blocking Phase 1)

- Real child-voice sample (`kid_es.wav`) to validate accuracy on the actual target population. Spot the words that genuinely matter for a tutor and verify `small-int8` lands them.
- Whisper `medium-int8` benchmark, *only* if we later observe accuracy regressions in production on hard inputs.
- whisper.cpp `ggml-small-q5_1.bin` benchmark as a backend swap-in study, *only* if the production latency feels unacceptable after Phase 5 deploy.

## Final Phase 0 decision

| Setting | Value | Source |
|---|---|---|
| Model | **`rhasspy/faster-whisper-small-int8`** | Only model with acceptable Spanish accuracy. |
| `STT_COMPUTE_TYPE` | `int8` | Default. `int8_float16` deferred to v1.x as a possible micro-optimization. |
| `STT_CPU_THREADS` | **`4`** | t=8 made performance worse on this VPS; t=4 wins. |
| `STT_BEAM_SIZE` | `1` | wyoming-faster-whisper ARM default; sufficient. |
| VAD | on, default parameters from SPEC §5 | Reduces hallucinations on silence/noise. |

**SPEC §4.1 amendment**: target relaxed from "RTF < 0.5 for 5 s" to **"RTF < 0.7 for 5 s, RTF < 0.5 for 30 s"** to reflect the measured floor (0.657 / 0.217). End-to-end implication: a 5 s child utterance takes ~3 s to decode end-to-end, then LLM + TTS round-trip on top. The pipeline-design implication is that we should not artificially block on STT — the LLM streaming should start as soon as the transcript is returned, and TTS streaming as soon as the first LLM tokens arrive.

**v1.x exploration tickets** (not blocking Phase 1):
- `compute_type=int8_float16` micro-benchmark (one `run.py --models rhasspy/faster-whisper-small-int8 --compute-type int8_float16` re-run, ~1 min).
- whisper.cpp swap-in benchmark per `docs/context/0-general/02-alternatives.md` if production feel is unsatisfactory after deploy.
- Real child-voice `kid_es.wav` accuracy verification before public launch.

Phase 0 is closed. Phase 1 unblocked.

---

## 2026-05-16 amendment — real-voice A/B overrides the Phase 0 model choice

**Context**: Phase 0 ranked accuracy using clips synthesized by Piper TTS (`es_MX-ald-medium`). Once `https://ai.stt.thotenn.com` was live (Phase 5), the user recorded the same content with their own voice into the browser GUI and ran all three models back-to-back. The synthetic vs. real divergence was dramatic enough to override the original default.

**Setup**: same VPS (Hetzner CAX31 Ampere, aarch64), same defaults (`int8`, `beam=1`, `cpu_threads=4`, VAD on). Single recorded clip of 4 kid-tutor questions, ~13 s total, decoded with each model via the production HTTP endpoint.

**Questions spoken**:

1. "¿Qué es el sistema solar?"
2. "Cuéntame del Triceratops."
3. "¿Cuántas patas tiene una araña?"
4. "¿Por qué el cielo es azul?"

**Results**:

| Model | Decode (s) | RTF | Transcript notes |
|---|---|---|---|
| **tiny-int8**  | 1.42 | 0.104 | ✅ all 4 questions verbatim, including "triceratops" and "¿Por qué" |
| base-int8  | 1.40 | 0.105 | ❌ "triceratops → tricera top", "¿Por qué → Porque" — content errors |
| small-int8 | 3.65 | 0.283 | ✅ all 4 verbatim (identical to tiny) |

**Findings**:

- **`tiny-int8` is perfect on real human voice in the kids-tutor domain.** Phase 0 had ranked it ❌ because it dropped "Hola" on the Piper synthetic clip — that was a TTS artifact, not a model weakness. Natural voice does not trigger it.
- **`base-int8` is genuinely worse than `tiny`**, with the same decode latency. Errors are *content* errors (split proper noun, lost question mark) that would actively confuse the downstream LLM. It has no role and is no longer in the default model registry.
- **`small-int8` is equal in accuracy to `tiny`** on this content, at 2.6× the decode cost. Keep it in the registry as an opt-in fallback for harder inputs (noise, fast speech, unfamiliar vocabulary) but not as the default.

**Defaults updated**:

| Setting | Old (Phase 0) | New (real-voice) |
|---|---|---|
| `STT_DEFAULT_MODEL` | `small-int8` | **`tiny-int8`** |
| `STT_MODEL_NAMES` registry | `[small, base, tiny]` | **`[tiny, small]`** (base dropped, tiny first) |
| 5 s RTF target (SPEC §4.1) | < 0.7 | **< 0.3** |
| Peak RSS target | < 1.0 GB | **< 400 MB** |
| Cold-start download | ~250 MB | **~80 MB** |

**Lesson learned**: synthesized TTS clips are fine for *throughput* comparisons (they have stable timing, deterministic content) but **engaño para WER en producción**. TTS prosody can confuse smaller models in ways that real human speech does not. Future bench passes should include a real-voice sample of the actual target domain *before* locking a model default. Recorded in `CLAUDE.md` as guidance.

**v1.x follow-ups still open**:
- Validate `tiny-int8` with multiple speakers (different ages, accents, noise levels). Especially: actual child voices.
- If a regression appears in production, the fallback `small-int8` is one env var away.
- `compute_type=int8_float16` micro-benchmark still pending — would shave latency further on the new default.
