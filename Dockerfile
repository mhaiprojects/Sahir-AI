# Sahir — web UI + STT/TTS (CPU). Ollama runs in a separate compose service.
FROM python:3.12-slim-bookworm

ARG PRELOAD_WHISPER=0
ARG PRELOAD_PIPER=1

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/app/data/huggingface \
    PRELOAD_WHISPER=${PRELOAD_WHISPER}

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libportaudio2 \
    wget \
    curl \
    openssl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt

COPY . .

RUN chmod +x docker/entrypoint.sh scripts/setup_models.sh scripts/docker_preload_whisper.py

# Optional: bake Piper voice into image (~61 MB)
RUN if [ "$PRELOAD_PIPER" = "1" ]; then bash scripts/setup_models.sh; fi

# Optional: bake Whisper weights into image (several hundred MB)
RUN if [ "$PRELOAD_WHISPER" = "1" ]; then python scripts/docker_preload_whisper.py; fi

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
  CMD curl -fk "https://127.0.0.1:8765/api/health" || curl -f "http://127.0.0.1:8765/api/health" || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["python", "bot.py", "--web"]
