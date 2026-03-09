# Use official Python image as base image
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies and clean up apt cache
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    cron && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy application code into the container
COPY . /app

# Upgrade pip and install dependencies
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Create non-privileged user and set file permissions
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app && \
    chmod -R 755 /app && \
    # Make code read-only for appuser
    chmod -R 555 /app/main.py /app/filter.py /app/snapshot.py && \
    # Ensure log and backup directories will be writable
    mkdir -p /app/logs /app/backups && \
    chmod -R 755 /app/logs /app/backups && \
    chown -R appuser:appuser /app/logs /app/backups

# Install crontab for appuser
RUN crontab -u appuser /app/crontab

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Entrypoint: dump env vars for cron, start cron, then run log capture as appuser
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod 755 /app/entrypoint.sh
CMD ["/app/entrypoint.sh"]
