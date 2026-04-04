FROM python:3.12-slim

# Security: non-root user
RUN useradd -m -u 1000 app

WORKDIR /home/app/journal

# Install dependencies first (leverages Docker layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Cloud Run injects PORT env var; default to 8080
ENV PORT=8080

# Run as non-root
USER app

# Gunicorn with 2 workers; adjust --workers based on Cloud Run CPU allocation
CMD exec gunicorn \
      --bind "0.0.0.0:${PORT}" \
      --workers 2 \
      --timeout 120 \
      --access-logfile - \
      app:app
