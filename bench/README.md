# `4-stt` Phase 0 Benchmark

Validates the bench harness and characterizes faster-whisper performance for the model decision in [`../docs/context/1-STT/CHECKLIST.md`](../docs/context/1-STT/CHECKLIST.md) Phase 0.

## Layout

```
bench/
├── README.md         # this file
├── run.py            # benchmark driver
├── .venv/            # gitignored — faster-whisper + psutil
├── clips/            # reference audio (Spanish, synthesized via 3-piper)
│   ├── short_es.wav  # ~5 s
│   └── medium_es.wav # ~23 s
├── models/           # gitignored — downloaded HF model cache
└── results/
    ├── bench-<arch>.json          # machine-parseable run output
    ├── bench-x86_64-stdout.log    # local x86 baseline log
    └── RESULTS-LOCAL-X86.md       # interpretation + decision context
```

## How to run

Local re-run:

```bash
cd /home/tho/www/tho/ai/4-stt/bench
.venv/bin/python run.py
```

On the Hetzner Ampere ARM64 VPS (the numbers that actually drive the model decision):

```bash
ssh <vps>
git clone <repo> stt-bench && cd stt-bench/bench   # or rsync this folder
python3 -m venv .venv && .venv/bin/pip install faster-whisper psutil
.venv/bin/python run.py --out results/bench-aarch64.json
```

Defaults match `SPEC.md`:

- Models: `tiny-int8`, `base-int8`, `small-int8` (all under the `rhasspy/faster-whisper-*-int8` HF repo).
- `compute_type=int8`, `cpu_threads=4`, `beam_size=1`, `language=es`, VAD on, 3 runs per (model, clip) pair.

Override examples:

```bash
.venv/bin/python run.py --models rhasspy/faster-whisper-small-int8
.venv/bin/python run.py --cpu-threads 8
.venv/bin/python run.py --no-vad
.venv/bin/python run.py --repeats 5
```

## Reference clips

Both synthesized via `3-piper`'s `es_MX-ald-medium` voice (LatAm Spanish, medium quality) so the bench is deterministic and reproducible. Real child-voice clips should be added before the final production decision; see the "ARM follow-up" section in `results/RESULTS-LOCAL-X86.md`.

| Clip | Duration | Content |
|---|---|---|
| `short_es.wav` | ~4.6 s | "Hola, ¿cómo estás? Quiero aprender sobre los planetas del sistema solar." |
| `medium_es.wav` | ~23 s | Tutor-style paragraph about the solar system. |

## What the bench reports

For each (model, clip) pair:
- `load_seconds` — `WhisperModel(...)` constructor time, including download on first run.
- `decode_avg_seconds` — average of N decode passes (excludes load).
- `rtf_avg` — `decode_avg_seconds / clip_duration_seconds`.
- `first_segment_avg_seconds` — time to the *first* yielded segment (proxy for the streaming endpoint's perceived latency).
- `peak_rss_mb` — peak resident memory observed during decode.
- `transcript` — the decoded text from the last run, for spot-checking accuracy.

JSON output at `results/bench-<arch>.json` for programmatic comparison across machines.
