# Tracking Database Automator

Protecting data while automating database management tasks is crucial for maintaining data integrity and security. This project implements best practices that ensure data protection during migration processes.

---

## What This Service Does (Phase 1: Log Capture)

This is a lightweight Python background service that connects to the Docker daemon and streams logs from sibling containers (`flask-tracking`, `spring-tracking`) to local log files. Captured logs are the foundation for the next phase: anomaly detection.

**Architecture overview:**

```
[flask_open container]  ──► docker.sock ──► [log-capture container] ──► ./logs/flask_open.log
[java_springv1 container] ──►               [log-capture container] ──► ./logs/java_springv1.log
```

The service:
- Connects to `/var/run/docker.sock` to read Docker container logs
- Streams logs in real-time with timestamps
- Writes to `./logs/<container_name>.log` on the host
- Automatically retries if a container is down or not yet started
- Logs a heartbeat every 60 seconds so you can confirm it's running

---

## Quick Start

### 1. Copy the environment file

```bash
cp .env.example .env
```

Edit `.env` if your container names differ from the defaults (`flask_open`, `java_springv1`).

### 2. Build and run

```bash
docker-compose up --build -d
```

### 3. Confirm it's running

```bash
docker-compose logs -f log-capture
```

You should see lines like:

```
Starting log capture for container: flask_open -> /app/logs/flask_open.log
Starting log capture for container: java_springv1 -> /app/logs/java_springv1.log
```

### 4. View captured logs

```bash
tail -f logs/flask_open.log
tail -f logs/java_springv1.log
```

---

## Configuration (`.env`)

| Variable | Default | Description |
|---|---|---|
| `CONTAINER_NAMES` | `flask_open,java_springv1` | Comma-separated Docker container names to watch |
| `LOG_DIR` | `/app/logs` | Directory inside container where logs are written |
| `POLL_INTERVAL` | `5` | Seconds to wait before retrying a missing container |

---

## Project Structure

```
tracking-database-automator/
├── main.py              # Log capture service (entry point)
├── requirements.txt     # Python dependencies (docker SDK)
├── Dockerfile           # Production container image
├── docker-compose.yml   # Service orchestration
├── .env.example         # Environment variable template
├── .gitignore
└── ReadME.md
```

---

## Roadmap

- **Phase 1** (current): Docker log capture to files
- **Phase 2**: Anomaly detection — parse logs to flag suspicious operations (mass password resets, bulk deletions)
- **Phase 3**: Alerting — Slack/email notifications on anomalies
- **Phase 4**: Control panel — GitHub Pages frontend for snapshots, migrations, and restore operations

---

## Prerequisites

- Docker and Docker Compose installed
- The `flask-tracking` and/or `spring-tracking` containers must be running (or the service will keep retrying until they start)
- The user running Docker Compose must have access to `/var/run/docker.sock`

---

## Security Note

Mounting `/var/run/docker.sock` grants this container significant access to the Docker daemon. Keep this service on a private network and do not expose it publicly.
