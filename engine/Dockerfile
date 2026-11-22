FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MODELS_DIR=/models \
    HF_HOME=/models/huggingface \
    ARGOS_PACKAGES_DIR=/models/argos \
    XDG_DATA_HOME=/models/xdg_data \
    XDG_CACHE_HOME=/models/xdg_cache

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    espeak-ng \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

RUN mkdir -p /models/piper /models/argos /models/huggingface /models/xdg_data /models/xdg_cache

EXPOSE 8765

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765"]

