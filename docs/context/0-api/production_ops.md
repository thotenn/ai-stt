# Production Operations

How to keep `stt-sandbox` healthy in production at `https://ai.stt.thotenn.com`. Companion to [`../1-STT/DEPLOY.md`](../1-STT/DEPLOY.md) — that doc covers the first-time setup, this one covers running it.

## Health probing

`GET /health` is the canonical liveness check. Coolify already polls it via the `docker-compose.yml` healthcheck (Python `urllib` hitting `/health`, 30 s interval, 120 s start grace). External monitors should also poll it:

```bash
curl -fsS https://ai.stt.thotenn.com/health
```

Response:

```json
{
  "status": "ok",
  "mode": "both",
  "engine": true,
  "gui": true,
  "model_loaded": "rhasspy/faster-whisper-tiny-int8",
  "language": "es"
}
```

If `status != "ok"`, the engine failed to load on boot. Logs will say why.

A 502/504 from Coolify's proxy means the container is up but the app isn't responding — usually a Python crash. `docker logs stt-sandbox` on the VPS.

## Logs

```bash
ssh root@<vps>
docker logs -f stt-sandbox
```

Useful lines:

- `stt-sandbox listening on http://0.0.0.0:8000 (mode=both, supported MIMEs=[...])` — boot success
- `INFO faster_whisper: Processing audio with duration 00:04.621` — request being decoded
- `INFO faster_whisper: VAD filter removed 00:00.000 of audio` — VAD pass-through (no silence trimmed)
- `INFO stt_sandbox.api: <ip> - "POST /transcribe HTTP/1.1" 200 -` — request completed
- `WARNING ... could not load model ...` — HuggingFace fetch failure (network / rate limit)
- `ERROR ... ffmpeg failed: ...` — audio decode failed on the input

### Transcript logging (privacy-sensitive)

`STT_LOG_TRANSCRIPTS=false` (default) — transcripts are **never** written to logs.

`STT_LOG_TRANSCRIPTS=true` — DEBUG-level log lines include the transcript text. Only flip this for debugging, behind controlled access.

To enable on the live deploy: set in Coolify env vars + redeploy + immediately revert when done. **Do not leave on in production for a child-facing service.**

## CORS pinning

Default is `STT_CORS_ORIGIN=*` for testing. Pin to the actual origins once known:

```env
STT_CORS_ORIGIN=https://your-engine.example,https://tutor.your-domain.com
```

The server takes a single value but most browsers respect a comma-separated list; if not, configure the upstream Coolify/Caddy layer to write the `Access-Control-Allow-Origin` based on the request `Origin`.

For RPi-only clients (which don't enforce CORS — that's a browser concept), `*` is irrelevant and safe.

## Model upgrade path

Switching the default model is an env-var change + redeploy. Four places to keep in sync (CLAUDE.md documents this rule):

1. `stt_sandbox/models.py` — `DEFAULT_MODEL` and `DEFAULT_MODEL_NAMES`
2. `Dockerfile` — `STT_DEFAULT_MODEL` and `STT_MODEL_NAMES` env defaults
3. `docker-compose.yml` — same vars in the `environment:` block (fallbacks)
4. `.env.example` — documented defaults

The Coolify "Environment Variables" panel **overrides all four** at runtime. You can ship a Docker image with `tiny` as the default but force the deployed instance to `small` by setting `STT_DEFAULT_MODEL=rhasspy/faster-whisper-small-int8` in Coolify and redeploying.

For temporary A/B testing without a redeploy: pass `model=...` per request, no server change needed.

To add a new model to the registry without code change:

```env
STT_MODEL_NAMES=["rhasspy/faster-whisper-tiny-int8","rhasspy/faster-whisper-small-int8","openai/whisper-large-v3"]
```

The pattern `rhasspy/faster-whisper-<size>-<quant>` is parsed for the `size`/`quantization` metadata; other repo IDs (like `openai/whisper-large-v3`) work too but display as `size=unknown` in `/models`.

## Volume management

The `stt-models` named volume holds downloaded Whisper weights. Lifecycle:

- **First start of each model**: HuggingFace pull, ~80 MB (`tiny`), ~250 MB (`small`), ~800 MB (`medium`).
- **Restart with same image**: weights reused, ~1 s cold start.
- **`docker volume rm stt-models`**: forces re-download on next boot. Use after `pip install` of a new `faster-whisper` version that requires different weight formats.

Don't bind-mount a host path unless you have a reason — Coolify's named volumes survive `docker compose down` / `up` cycles and the deploy redeploy flow.

## Redeploy / rollback

### Forward (deploy a new commit)

1. `git push origin main` from your dev machine.
2. Coolify auto-redeploys (if webhook is wired) or click **Deploy** manually.
3. Coolify rebuilds the image, stops the old container, starts the new one, waits for healthcheck.
4. Old volume reattaches automatically.

Wall time: ~3–5 min (mostly rebuild). The site is down for ~10 s during container swap. Coolify can do rolling updates with two replicas, but for a single-VPS deploy the swap downtime is usually acceptable.

### Backward (rollback)

```bash
git revert <bad-commit>
git push origin main
```

Coolify redeploys the reverted code. Volume contents persist.

If you need a fast rollback to a specific previous deploy, Coolify keeps tagged build history — use its UI to redeploy a specific tag.

## Memory & CPU under load

On the Hetzner CAX31 (8 vCPU / 16 GB) running `tiny-int8`:

| State | RAM (RSS) | CPU |
|---|---|---|
| Idle (model preloaded) | ~250 MB | 0 % |
| Decoding 1 request | ~315 MB | 4 vCPUs at ~95 % for ~0.5 s |
| 2 concurrent requests | ~330 MB | second waits ~0.5 s on `_transcribe_lock` |

`stt-sandbox` serializes decode on a single shared `WhisperModel` (`threading.Lock` in `engine.py`). For two RPi clients in parallel, the second request adds the first's decode time to its own latency. For >2 sustained concurrent clients, consider a worker pool (v1.x — not yet built).

Co-resident with `3-piper` (~300 MB resident), combined steady-state is ~600 MB out of 16 GB. Plenty of headroom for `small-int8` (~700 MB) or even `medium-int8` (~1.5 GB) if accuracy needs grow.

## Cold-start cost

First `/transcribe` after container boot blocks until the model downloads. Two mitigations:

### Mitigation A — Pre-warm at startup (current)

`main()` calls `engine.preload(DEFAULT_MODEL)` synchronously before the server starts listening. The container only reports as `up` after preload completes. Coolify's 120 s start grace covers the HuggingFace download.

Downside: container boot is ~30 s instead of ~3 s on a cold pull.

### Mitigation B — Bake model into image

Add to `Dockerfile`:

```dockerfile
RUN python -c "from faster_whisper import WhisperModel; \
    WhisperModel('rhasspy/faster-whisper-tiny-int8', download_root='/app/models/whisper')"
```

Image grows to ~1.1 GB but cold start drops to ~3 s. Trade-off: every new image rebuild re-downloads (Docker layer caching helps).

Default v1 posture: Mitigation A (no bake). Switch to B if container restart frequency becomes a UX problem.

## Capacity planning

A single Hetzner CAX31 handles:

- **~1 sustained client** comfortably (RPi running the tutor loop with 2–3 s turns).
- **~2 clients in light usage** (one decoding while one buffers next request).
- **Stops scaling** past 2–3 because of the single-model serialization. Adding more CPU cores doesn't help — see Phase 0 results (8 threads measured worse than 4 on this VPS).

For more concurrent users (classroom deployment, multi-Pi setups), the realistic options are:

1. **Vertical**: move to a bigger Hetzner instance (CAX41 = 16 vCPU). Adds a workers pool config (v1.x).
2. **Horizontal**: run N stt-sandbox containers behind a round-robin load balancer (Coolify supports multi-replica). Each container = ~315 MB RAM + 4 vCPUs.

## Troubleshooting checklist

| Symptom | First check | Likely cause |
|---|---|---|
| 502 Bad Gateway from Coolify | `docker ps` shows container stopped | Python crash; `docker logs` for stack trace |
| 502 transient | Healthcheck failing during preload | Wait, or increase `start_period` in compose |
| 200 but text is `""` | `audio.duration_seconds` in response | VAD removed everything; silent input or wrong sample rate |
| 400 `unsupported audio MIME ...` | `Content-Type` header on the request | Set `audio/wav`, `audio/webm`, etc.; default `application/octet-stream` fails |
| 413 Request Entity Too Large | Audio file size vs. `STT_MAX_REQUEST_BODY_BYTES` | Increase env or split audio |
| 500 `ffmpeg failed: ...` | Audio bytes are valid | Corrupt input; usually a buffering bug client-side |
| Slow first request after deploy | `decode_seconds` in response | Cold start; subsequent requests warm |
| Two requests serialize unexpectedly | Single shared model lock | This is by design (v1); plan multi-worker for v1.x |
| `model_loaded` empty in `/health` | Container booted but engine failed | Look for `ERROR ... could not load model` in logs; usually HuggingFace fetch fail (transient or rate-limit) |
| Transcripts mangled for kid names | Compare without `initial_prompt` | Add `initial_prompt` per request with the current topic |

## Backup / recovery

There is no state to back up. The model cache volume can be regenerated by hitting `/transcribe` once with each model after deploy. Config lives entirely in env vars + the git repo.

For disaster recovery: the entire production state is `git pull && docker compose up -d --build`. The whole VPS could blow up and you'd be back online in 10 minutes on fresh hardware (assuming DNS + Coolify already set up).

## What v1.x would add

(Tracked in [`../1-STT/CHECKLIST.md`](../1-STT/CHECKLIST.md) Phase 6 backlog. None are blocking the RPi client.)

- **`/metrics`** Prometheus endpoint for request rate / latency histograms.
- **Worker pool** for >2 concurrent clients without queue blocking.
- **Auth** if the service is ever exposed beyond known clients.
- **English support** (already 1-line per request, doc-only for now).
- **whisper.cpp backend swap** if latency feel ever degrades.
