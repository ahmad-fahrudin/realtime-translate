# syntax=docker/dockerfile:1.7
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
    pulseaudio-utils \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install CPU-only PyTorch first so argostranslate/stanza does not pull multi-GB NVIDIA CUDA wheels
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --index-url https://download.pytorch.org/whl/cpu torch

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY tests ./tests

RUN mkdir -p /models/piper /models/argos /models/huggingface /models/xdg_data /models/xdg_cache

EXPOSE 8765

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765"]
