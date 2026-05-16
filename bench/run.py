from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import statistics
import sys
import time
import wave
from contextlib import contextmanager
from pathlib import Path

import psutil
from faster_whisper import WhisperModel


HERE = Path(__file__).resolve().parent
CLIPS_DIR = HERE / "clips"
MODELS_DIR = HERE / "models"
RESULTS_DIR = HERE / "results"

DEFAULT_MODELS = [
    "rhasspy/faster-whisper-tiny-int8",
    "rhasspy/faster-whisper-base-int8",
    "rhasspy/faster-whisper-small-int8",
]

DEFAULT_CLIPS = ["short_es.wav", "medium_es.wav"]

DEFAULT_LANGUAGE = "es"
DEFAULT_BEAM_SIZE = 1
DEFAULT_COMPUTE_TYPE = "int8"
DEFAULT_CPU_THREADS = 4
DEFAULT_REPEATS = 3

VAD_PARAMETERS = {
    "threshold": 0.5,
    "min_speech_duration_ms": 250,
    "min_silence_duration_ms": 2000,
}


def audio_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() / float(wav.getframerate())


@contextmanager
def peak_rss_tracker():
    proc = psutil.Process(os.getpid())
    baseline_mb = proc.memory_info().rss / (1024 * 1024)
    peak = [baseline_mb]

    def sample() -> None:
        peak[0] = max(peak[0], proc.memory_info().rss / (1024 * 1024))

    yield peak, sample
    sample()


def transcribe_once(
    model: WhisperModel,
    wav_path: Path,
    language: str,
    beam_size: int,
    vad: bool,
) -> tuple[str, float, float, list[float]]:
    start = time.perf_counter()
    segments_iter, info = model.transcribe(
        str(wav_path),
        language=language,
        beam_size=beam_size,
        vad_filter=vad,
        vad_parameters=VAD_PARAMETERS if vad else None,
    )

    first_segment_t: float | None = None
    segment_decode_seconds: list[float] = []
    last_t = start
    pieces: list[str] = []
    for seg in segments_iter:
        now = time.perf_counter()
        if first_segment_t is None:
            first_segment_t = now - start
        segment_decode_seconds.append(now - last_t)
        last_t = now
        pieces.append(seg.text)

    total_decode = time.perf_counter() - start
    text = "".join(pieces).strip()
    return text, total_decode, (first_segment_t if first_segment_t is not None else total_decode), segment_decode_seconds


def bench_model_on_clip(
    model_id: str,
    cache_dir: Path,
    compute_type: str,
    cpu_threads: int,
    clip_path: Path,
    language: str,
    beam_size: int,
    vad: bool,
    repeats: int,
) -> dict:
    print(f"\n[load] {model_id}", flush=True)
    load_start = time.perf_counter()
    with peak_rss_tracker() as (peak_after_load, sample_load):
        model = WhisperModel(
            model_id,
            download_root=str(cache_dir),
            device="cpu",
            compute_type=compute_type,
            cpu_threads=cpu_threads,
        )
        sample_load()
    load_seconds = time.perf_counter() - load_start
    print(f"  loaded in {load_seconds:.2f}s, RSS after load {peak_after_load[0]:.0f} MB", flush=True)

    duration = audio_duration_seconds(clip_path)
    runs: list[dict] = []
    text_sample = ""

    for i in range(repeats):
        gc.collect()
        with peak_rss_tracker() as (peak, sample):
            text, total_decode, first_seg, _ = transcribe_once(
                model, clip_path, language, beam_size, vad
            )
            sample()
        rtf = total_decode / duration if duration > 0 else float("inf")
        runs.append({
            "run": i + 1,
            "decode_seconds": round(total_decode, 3),
            "first_segment_seconds": round(first_seg, 3),
            "rtf": round(rtf, 3),
            "peak_rss_mb": round(peak[0], 1),
        })
        text_sample = text
        print(f"  run {i+1}: decode={total_decode:.2f}s rtf={rtf:.3f} first_seg={first_seg:.2f}s peak_rss={peak[0]:.0f}MB", flush=True)

    decode_avg = statistics.mean(r["decode_seconds"] for r in runs)
    rtf_avg = statistics.mean(r["rtf"] for r in runs)
    first_seg_avg = statistics.mean(r["first_segment_seconds"] for r in runs)
    peak_rss_max = max(r["peak_rss_mb"] for r in runs)

    del model
    gc.collect()

    return {
        "model": model_id,
        "clip": clip_path.name,
        "clip_duration_seconds": round(duration, 3),
        "load_seconds": round(load_seconds, 3),
        "decode_avg_seconds": round(decode_avg, 3),
        "rtf_avg": round(rtf_avg, 3),
        "first_segment_avg_seconds": round(first_seg_avg, 3),
        "peak_rss_mb": round(peak_rss_max, 1),
        "runs": runs,
        "transcript": text_sample,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="faster-whisper STT benchmark")
    parser.add_argument("--models", nargs="*", default=DEFAULT_MODELS)
    parser.add_argument("--clips", nargs="*", default=DEFAULT_CLIPS)
    parser.add_argument("--clips-dir", default=str(CLIPS_DIR))
    parser.add_argument("--models-dir", default=str(MODELS_DIR))
    parser.add_argument("--language", default=DEFAULT_LANGUAGE)
    parser.add_argument("--beam-size", type=int, default=DEFAULT_BEAM_SIZE)
    parser.add_argument("--compute-type", default=DEFAULT_COMPUTE_TYPE)
    parser.add_argument("--cpu-threads", type=int, default=DEFAULT_CPU_THREADS)
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--no-vad", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    clips_dir = Path(args.clips_dir)
    models_dir = Path(args.models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    clip_paths = []
    for name in args.clips:
        p = clips_dir / name
        if not p.exists():
            print(f"[skip] missing clip: {p}", file=sys.stderr)
            continue
        clip_paths.append(p)
    if not clip_paths:
        print("no clips found", file=sys.stderr)
        return 2

    environment = {
        "python": sys.version.split()[0],
        "machine": platform.machine(),
        "platform": platform.platform(),
        "cpu_count": psutil.cpu_count(logical=True),
        "cpu_threads_used": args.cpu_threads,
        "compute_type": args.compute_type,
        "beam_size": args.beam_size,
        "language": args.language,
        "vad": not args.no_vad,
        "repeats": args.repeats,
    }
    print("[env]", json.dumps(environment, indent=2))

    results: list[dict] = []
    for model_id in args.models:
        for clip_path in clip_paths:
            try:
                row = bench_model_on_clip(
                    model_id=model_id,
                    cache_dir=models_dir,
                    compute_type=args.compute_type,
                    cpu_threads=args.cpu_threads,
                    clip_path=clip_path,
                    language=args.language,
                    beam_size=args.beam_size,
                    vad=not args.no_vad,
                    repeats=args.repeats,
                )
                results.append(row)
            except Exception as exc:
                print(f"[error] {model_id} on {clip_path.name}: {exc}", file=sys.stderr)
                results.append({"model": model_id, "clip": clip_path.name, "error": str(exc)})

    print("\n=== summary ===")
    header = f"{'model':<48} {'clip':<16} {'dur':>6} {'load':>6} {'rtf':>6} {'1st':>6} {'rss_mb':>7}"
    print(header)
    print("-" * len(header))
    for r in results:
        if "error" in r:
            print(f"{r['model']:<48} {r['clip']:<16} ERROR: {r['error']}")
            continue
        print(
            f"{r['model']:<48} {r['clip']:<16} "
            f"{r['clip_duration_seconds']:>6.2f} {r['load_seconds']:>6.2f} "
            f"{r['rtf_avg']:>6.3f} {r['first_segment_avg_seconds']:>6.2f} {r['peak_rss_mb']:>7.0f}"
        )

    payload = {"environment": environment, "results": results}
    out_path = Path(args.out) if args.out else RESULTS_DIR / f"bench-{platform.machine()}.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\n[wrote] {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
