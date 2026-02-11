# Use official Python image as base image
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies and clean up apt cache
RUN apt-get update && apt-get install -y --no-install-recommends \
    git && \
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
    chmod -R 555 /app/main.py && \
    # Ensure log directory will be writable
    mkdir -p /app/logs && \
    chmod -R 755 /app/logs && \
    chown -R appuser:appuser /app/logs

# Switch to non-privileged user
USER appuser

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Expose no HTTP port — this is a background worker service
# Logs are written to /app/logs (mount a volume there)

# Start log capture service
CMD ["python", "main.py"]
