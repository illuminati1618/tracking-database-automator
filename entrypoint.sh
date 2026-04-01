#!/bin/bash
# Dump current environment to a file cron can source
# (cron jobs don't inherit Docker container env vars)
env | grep -E '^(AWS_|RDS_|SPRING_|BACKUP_|RETENTION_|LOG_DIR|POLL_|PATH|PYTHONUNBUFFERED|AUTOMATOR_|API_PORT)' \
    | sed 's/^/export /' > /app/env.sh
chmod 644 /app/env.sh

# Grant appuser access to docker socket (runs as root here)
chmod 666 /var/run/docker.sock

# Start cron daemon in background
cron

# Start API server in background
su -s /bin/bash appuser -c "cd /app && python api.py" &

# Run log capture as appuser (foreground)
exec su -s /bin/bash appuser -c "cd /app && python main.py"
