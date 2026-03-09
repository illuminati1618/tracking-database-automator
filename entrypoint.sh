#!/bin/bash
# Dump current environment to a file cron can source
# (cron jobs don't inherit Docker container env vars)
env | grep -E '^(AWS_|RDS_|SPRING_|BACKUP_|RETENTION_|LOG_DIR|POLL_|PATH|PYTHONUNBUFFERED)' \
    | sed 's/^/export /' > /app/env.sh
chmod 644 /app/env.sh

# Start cron daemon in background
cron

# Run log capture as appuser (foreground)
exec su -s /bin/bash appuser -c "cd /app && python main.py"
