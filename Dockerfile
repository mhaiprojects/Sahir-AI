# syntax=docker/dockerfile:1
# Sahir web — targets: minimal (text/agent), full (+ Whisper/Piper voice).
FROM python:3.12-slim-bookworm AS base

LABEL com.sahir.trial=jw12

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    openssl \
    ca-certificates \
    gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-docker.txt requirements-docker-voice.txt ./
RUN pip install --no-cache-dir -r requirements-docker.txt

COPY . .

RUN chmod +x docker/entrypoint.sh scripts/setup_models.sh scripts/docker_preload_whisper.py

FROM base AS minimal

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/app/data/huggingface \
    WEB_VOICE_ENABLED=false

RUN groupadd -r -g 10001 sahir \
    && useradd -r -u 10001 -g sahir -d /app -s /usr/sbin/nologin sahir \
    && chown -R sahir:sahir /app

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
  CMD curl -fk "https://127.0.0.1:8765/api/health" || curl -f "http://127.0.0.1:8765/api/health" || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["python", "bot.py", "--web"]

FROM base AS full

ARG PRELOAD_WHISPER=0
ARG PRELOAD_PIPER=1

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/app/data/huggingface \
    PRELOAD_WHISPER=${PRELOAD_WHISPER} \
    WEB_VOICE_ENABLED=true

RUN apt-get update && apt-get install -y --no-install-recommends libportaudio2 wget \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements-docker-voice.txt

RUN if [ "$PRELOAD_PIPER" = "1" ]; then bash scripts/setup_models.sh; fi
RUN if [ "$PRELOAD_WHISPER" = "1" ]; then python scripts/docker_preload_whisper.py; fi

RUN groupadd -r -g 10001 sahir \
    && useradd -r -u 10001 -g sahir -d /app -s /usr/sbin/nologin sahir \
    && chown -R sahir:sahir /app

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=3 \
  CMD curl -fk "https://127.0.0.1:8765/api/health" || curl -f "http://127.0.0.1:8765/api/health" || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["python", "bot.py", "--web"]