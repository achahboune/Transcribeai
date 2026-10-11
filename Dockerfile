# TranscribeAI Orchestrator — lightweight image for Render.
# Transcription runs via Groq's cloud-hosted Whisper (free tier) —
# no dependency on any local machine being on.

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl ca-certificates fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir -U "yt-dlp[default]" curl_cffi

COPY app ./app
COPY frontend ./frontend

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
