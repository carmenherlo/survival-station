# Base image - slim for low resource usage
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# OCR dependencies (Tesseract + language packs + OpenCV runtime libs)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-spa \
    tesseract-ocr-deu \
    tesseract-ocr-ukr \
    tesseract-ocr-tur \
    tesseract-ocr-fra \
    tesseract-ocr-ita \
    tesseract-ocr-rus \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY api/ ./api/
COPY rag/ ./rag/

# Environment variables with defaults
ENV VECTOR_DIR=data/vectorstore
ENV OLLAMA_URL=http://ollama:11434
ENV LLM_MODEL=phi3:mini
ENV EMBED_MODEL=nomic-embed-text
ENV PYTHONUNBUFFERED=1

# Expose API port
EXPOSE 8000

# Start FastAPI service
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]