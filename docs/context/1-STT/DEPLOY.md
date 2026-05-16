# Deployment — `4-stt` to Hetzner Ampere via Coolify

End-to-end runbook for getting `stt-sandbox` live at `https://ai-stt.thotenn.com`, symmetric with the `3-piper` deployment at `ai-tts.thotenn.com`.

## Pre-flight (on your local machine)

```bash
git push origin main          # Coolify pulls from GitHub
```

The repo must be reachable from Coolify's GitHub App.

## Path A — Coolify with GitHub App (recommended, matches 3-piper)

This is the same flow you used for `3-piper`. Coolify clones the repo on the VPS, builds the image natively for `aarch64`, and proxies the configured subdomain to the container.

### 1. Coolify resource setup

1. Open Coolify → **New Resource** → **Public Repository** (or **GitHub Apps** if the org is connected).
2. Repository: `https://github.com/thotenn/ai-stt` (your fork/origin).
3. Branch: `main`.
4. **Build Pack**: `Docker Compose`.
5. **Docker Compose File**: `docker-compose.yml` (default).
6. **Domain**: `https://ai-stt.thotenn.com`.
7. **Port** (proxy target): `8000` (must match `STT_PORT`).

### 2. Environment variables

Paste these into Coolify's "Environment Variables" section. Most are already covered by `docker-compose.yml` defaults — only set what you want to override.

Minimum recommended for production:

```env
STT_SERVICE_MODE=both
STT_DEFAULT_MODEL=rhasspy/faster-whisper-small-int8
STT_CPU_THREADS=4
STT_CORS_ORIGIN=*
```

When the RPi client and `ENGINE-LLM` origins are known, tighten CORS:

```env
STT_CORS_ORIGIN=https://your-engine.example
```

Full list with explanations: see [`SPEC.md` §5](SPEC.md).

### 3. Volume

The `docker-compose.yml` declares a named volume `stt-models` at `/app/models`. Coolify will materialize it automatically. **Do not bind-mount a host path** unless you want to lose model cache on container recreation. The first `/transcribe` call downloads `~250 MB` of `small-int8` weights from HuggingFace into this volume; subsequent restarts skip the download.

### 4. Healthcheck

Already wired in `docker-compose.yml` (Python `urllib` hitting `/health`, 30 s interval, 120 s grace period to cover model preload). Coolify reads this and only routes traffic once the container reports `healthy`.

### 5. Deploy

Click **Deploy**. First build takes ~3–5 min (Debian apt + pip install with onnxruntime/ctranslate2/av wheels). First request to `/transcribe` after boot triggers the model download (~30–60 s on Hetzner DC bandwidth); subsequent requests warm.

### 6. Verify from any browser

```bash
curl -fsS https://ai-stt.thotenn.com/health
# → {"status":"ok","mode":"both","engine":true,"gui":true,...}
```

Open `https://ai-stt.thotenn.com/` in Chrome with a mic, click Grabar, speak Spanish, click Detener, transcript should appear within ~3 s.

## Path B — Manual build on the VPS (fallback)

If Coolify is unavailable or you want to test before wiring the resource:

```bash
ssh root@<vps>
cd /root/apps
git clone https://github.com/thotenn/ai-stt.git
cd ai-stt

# Build natively for aarch64 — no buildx / qemu needed
docker build -t stt-sandbox:local .

# Run with the same env defaults docker-compose would use
docker run -d --restart unless-stopped --name stt-sandbox \
  -p 8000:8000 \
  -v stt-models:/app/models \
  stt-sandbox:local

# Smoke test
sleep 60   # allow model preload on first boot
curl -fsS http://127.0.0.1:8000/health
```

Or compose it:

```bash
docker compose up -d --build
```

To put it behind your existing Caddy/Traefik manually, route `ai-stt.thotenn.com` → `http://127.0.0.1:8000`.

## Image size

The image lands at **~995 MB** (Python 3.12 slim + ffmpeg + onnxruntime + ctranslate2 + numpy + transitive deps). This **exceeds** the `≤ 400 MB` target written in `PLAN.md §5` — that target was optimistic and ignored the real cost of CPU-only inference deps on Python. Treat 1 GB as the v1 floor. Optimization ideas (not blocking deploy):

- **Static ffmpeg binary**: replace the ~150 MB apt-installed ffmpeg with a ~30 MB statically compiled one (e.g., from `johnvansickle/ffmpeg-release`). Saves ~120 MB.
- **`python:3.12-alpine`**: saves ~50 MB on the base, but `ctranslate2`/`onnxruntime` lack musl wheels and would need a source build. Bad trade-off for the savings.
- **Multi-stage build**: limited gain, since most weight is runtime deps not build-time tooling.

Not worth doing unless a deploy target enforces a hard image-size cap.

## Operations

### First-request cold start

When the container starts fresh (no `stt-models` volume yet), the first transcription request blocks ~30–60 s while HuggingFace serves the `small-int8` weights. The `start_period: 120s` in the healthcheck covers this, but the *first* user-facing request will still feel slow. Two ways to mitigate:

1. **Pre-warm in CI/deploy**: add a post-deploy step that hits `/transcribe` once with a small WAV. Coolify supports this via post-deployment scripts.
2. **Bake model into image**: add a `RUN python -c "from faster_whisper import WhisperModel; WhisperModel('rhasspy/faster-whisper-small-int8', download_root='/app/models/whisper')"` in the Dockerfile. Pushes image to ~1.25 GB but eliminates cold start entirely.

Default v1 posture: accept the first-request cost. Revisit if it becomes a UX issue.

### Logs

```bash
docker logs -f stt-sandbox
```

Look for:
- `stt-sandbox listening on http://0.0.0.0:8000 (mode=both, ...)` — boot success
- `INFO faster_whisper: Processing audio with duration ...` — request being served
- `INFO stt_sandbox.api: <ip> - "POST /transcribe HTTP/1.1" 200 -` — successful response

`STT_LOG_TRANSCRIPTS=true` adds DEBUG-level lines with the transcript text. Default off for privacy.

### Restart / redeploy

Coolify: click **Deploy** again, it rebuilds and rolls.

Manual:

```bash
cd /root/apps/ai-stt
git pull
docker compose up -d --build
```

The `stt-models` volume survives both flows.

### Resource usage in production

Expected on the Hetzner CAX31 (8 vCPU / 16 GB):

- Idle: ~600 MB RSS (the preloaded `small-int8` model).
- During decode: ~700 MB RSS, 4 vCPUs at ~100 % for ~3 s per request.
- Concurrent requests serialize on `SttEngine._transcribe_lock`. Two simultaneous requests = the second waits for the first to finish.

Coexists comfortably with `3-piper` (which uses ~300 MB resident). Combined steady-state ~1 GB out of 16.

### Rollback

```bash
git revert <bad-commit>
git push
# Coolify redeploys automatically
```

There is no DB to migrate, no shared state besides the cached model files in the volume (which are immutable per model name).

## What you still need to do (checklist)

- [ ] `git push origin main` (or your remote)
- [ ] Create the Coolify resource pointing at the repo + branch
- [ ] Set the `Domain` to `ai-stt.thotenn.com` and `Port` to `8000`
- [ ] Confirm DNS A record for `ai-stt.thotenn.com` points at the VPS
- [ ] Click Deploy
- [ ] Wait for `healthy` status
- [ ] `curl https://ai-stt.thotenn.com/health` returns 200
- [ ] Open the URL in a browser, record a Spanish utterance, see the transcript
- [ ] (Optional) Tighten `STT_CORS_ORIGIN` once the RPi/LLM origins are known
