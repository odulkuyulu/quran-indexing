FROM python:3.11-slim

# Install ffmpeg for audio slice extraction
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Install Python dependencies first (layer cache)
COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt

# Copy application code
COPY app/ ./app/
COPY src/ ./src/

# Pre-create data directories (empty in demo; mount Azure Files here later)
RUN mkdir -p data/output data/audio/raw data/audio/normalized

ENV PORT=5000
ENV OUTPUT_DIR=/workspace/data/output
ENV AUDIO_DIR=/workspace/data/audio
ENV PYTHONUNBUFFERED=1

EXPOSE 5000

# 2 workers is enough for a demo; increase via env var if needed
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "60", "app.demo:app"]
