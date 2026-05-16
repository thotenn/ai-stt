FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STT_HOST=0.0.0.0 \
    STT_PORT=8000 \
    STT_SERVICE_MODE=both \
    STT_ENGINE_URL= \
    STT_CORS_ORIGIN=* \
    STT_MODELS_DIR=/app/models/whisper \
    STT_DEFAULT_MODEL=rhasspy/faster-whisper-tiny-int8 \
    STT_MODEL_NAMES='["rhasspy/faster-whisper-tiny-int8","rhasspy/faster-whisper-small-int8"]' \
    STT_DEFAULT_LANGUAGE=es \
    STT_COMPUTE_TYPE=int8 \
    STT_CPU_THREADS=4 \
    STT_BEAM_SIZE=1 \
    STT_VAD_ENABLED=true \
    STT_VAD_THRESHOLD=0.5 \
    STT_VAD_MIN_SPEECH_MS=250 \
    STT_VAD_MIN_SILENCE_MS=2000 \
    STT_MAX_REQUEST_BODY_BYTES=26214400 \
    STT_LOG_TRANSCRIPTS=false

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY stt_sandbox ./stt_sandbox

RUN pip install --upgrade pip && pip install .

EXPOSE 8000

CMD ["python", "-m", "stt_sandbox.api"]
