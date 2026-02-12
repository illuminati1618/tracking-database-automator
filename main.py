import os
import time
import signal
import logging
import threading
from datetime import datetime
from pathlib import Path

import docker

# --- Configuration ---
CONTAINER_NAMES = os.environ.get("CONTAINER_NAMES", "flask_open,java_springv1").split(",")
LOG_DIR = Path(os.environ.get("LOG_DIR", "/app/logs"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "5"))  # seconds between container discovery checks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

shutdown_event = threading.Event()


def signal_handler(sig, frame):
    log.info("Shutdown signal received, stopping log capture...")
    shutdown_event.set()


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


def log_file_for(container_name: str) -> Path:
    """Return the path to the log file for a given container name."""
    safe_name = container_name.replace("/", "_").lstrip("_")
    return LOG_DIR / f"{safe_name}.log"


def stream_container_logs(container_name: str):
    """Stream logs from a single container and write them to a file."""
    client = docker.from_env()
    log_path = log_file_for(container_name)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    log.info(f"Starting log capture for container: {container_name} -> {log_path}")

    while not shutdown_event.is_set():
        try:
            container = client.containers.get(container_name)
            with open(log_path, "a") as f:
                # Stream logs since the last captured line; tail=0 means only new lines
                for line in container.logs(stream=True, follow=True, timestamps=True):
                    if shutdown_event.is_set():
                        break
                    decoded = line.decode("utf-8", errors="replace").rstrip("\n")
                    f.write(decoded + "\n")
                    f.flush()
        except docker.errors.NotFound:
            log.warning(f"Container '{container_name}' not found, retrying in {POLL_INTERVAL}s...")
            shutdown_event.wait(POLL_INTERVAL)
        except docker.errors.APIError as e:
            log.error(f"Docker API error for '{container_name}': {e}, retrying in {POLL_INTERVAL}s...")
            shutdown_event.wait(POLL_INTERVAL)
        except Exception as e:
            log.error(f"Unexpected error for '{container_name}': {e}, retrying in {POLL_INTERVAL}s...")
            shutdown_event.wait(POLL_INTERVAL)

    log.info(f"Stopped log capture for container: {container_name}")


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    log.info(f"Log capture service starting")
    log.info(f"Watching containers: {CONTAINER_NAMES}")
    log.info(f"Log output directory: {LOG_DIR}")

    threads = []
    for name in CONTAINER_NAMES:
        name = name.strip()
        if not name:
            continue
        t = threading.Thread(target=stream_container_logs, args=(name,), name=f"capture-{name}", daemon=True)
        t.start()
        threads.append(t)

    # Keep main thread alive until shutdown
    while not shutdown_event.is_set():
        # Log a heartbeat every minute so operators know the service is running
        shutdown_event.wait(60)
        if not shutdown_event.is_set():
            active = [t.name for t in threads if t.is_alive()]
            log.info(f"Heartbeat — active capture threads: {active}")

    for t in threads:
        t.join(timeout=10)

    log.info("Log capture service stopped.")


if __name__ == "__main__":
    main()
