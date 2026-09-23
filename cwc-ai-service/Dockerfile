# CWC AI service (Flask 3 / Python 3.11) - OCR, prompts, calls the LLM.
FROM python:3.11-slim

# Tesseract OCR: English text + orientation detection (used for deskew)
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-osd \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --create-home app

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /app/logs && chown app /app/logs

# Container defaults; docker-compose.yml supplies the LLM URL, key and model.
ENV PYTHONUNBUFFERED=1 \
    CWC_SERVER=waitress \
    SERVER_HOST=0.0.0.0 \
    SERVER_PORT=5002 \
    TESSERACT_CMD=/usr/bin/tesseract
USER app
EXPOSE 5002
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:5002/health', timeout=4).status == 200 else 1)"
CMD ["python", "server.py"]
