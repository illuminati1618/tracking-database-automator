"""
filter.py — Log filtering stage

Reads raw log files from LOG_DIR and writes only security-relevant lines
to LOG_DIR/important/<container>.log in real-time.

Flask log format (Gunicorn access log):
  <docker_ts> <ip> - - [<date>] "<METHOD> <path> HTTP/x.x" <status> <bytes> "<referer>" "<ua>"

Spring log format:
  <docker_ts> <app_ts> <LEVEL> <pid> --- [<thread>] <logger> : <message>
"""

import os
import re
import time
import signal
import logging
import threading
from pathlib import Path

LOG_DIR = Path(os.environ.get("LOG_DIR", "/app/logs"))
IMPORTANT_DIR = LOG_DIR / "important"
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "5"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# shutdown_event is injected by main.py when used as a module;
# falls back to a local event when run standalone
shutdown_event = threading.Event()


def _standalone_signal_handler(sig, frame):
    shutdown_event.set()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _standalone_signal_handler)
    signal.signal(signal.SIGINT, _standalone_signal_handler)


# ---------------------------------------------------------------------------
# Flask filter rules
# Match lines that contain security-relevant paths or non-2xx status codes
# ---------------------------------------------------------------------------

# Paths that are always important regardless of status
FLASK_IMPORTANT_PATHS = re.compile(
    r'"(?:POST|PUT|DELETE|PATCH) (?:'
    r'/users/reset_password/\d+'       # password reset
    r'|/users/delete/\d+'             # user deletion
    r'|/delete_user/[^"]*'            # kasm user deletion
    r'|/update_user/[^"]*'            # user update
    r'|/api/user'                     # user creation
    r'|/login'                        # login attempts
    r')'
)

# Any response that is 4xx or 5xx is worth logging
FLASK_ERROR_STATUS = re.compile(r'" [45]\d\d ')


def is_flask_important(line: str) -> bool:
    return bool(FLASK_IMPORTANT_PATHS.search(line) or FLASK_ERROR_STATUS.search(line))


# ---------------------------------------------------------------------------
# Spring filter rules
# ---------------------------------------------------------------------------

SPRING_IMPORTANT_PATTERNS = re.compile(
    r'(?:'
    r'ERROR'                          # any ERROR level log
    r'|password'                      # password-related operations
    r'|/api/person.*(?:POST|PUT|DELETE)'  # user CRUD
    r'|delete'                        # deletion operations
    r'|migration'                     # schema migrations
    r'|Exception'                     # exceptions
    r'|WARN.*(?:auth|login|token|jwt|forbidden|unauthorized)',  # auth warnings
    re.IGNORECASE
)


def is_spring_important(line: str) -> bool:
    return bool(SPRING_IMPORTANT_PATTERNS.search(line))


# ---------------------------------------------------------------------------
# Per-source filter dispatch
# ---------------------------------------------------------------------------

FILTERS = {
    "flask": is_flask_important,
    "spring": is_spring_important,
}


def detect_source(filename: str) -> str:
    name = filename.lower()
    if "flask" in name:
        return "flask"
    if "spring" in name or "java" in name:
        return "spring"
    return "unknown"


def filter_source_matches(line: str) -> bool:
    """Fallback: keep ERROR/WARN lines from any unknown source."""
    return bool(re.search(r'\b(?:ERROR|WARN|Exception)\b', line))


# ---------------------------------------------------------------------------
# File tail + filter loop
# ---------------------------------------------------------------------------

def tail_and_filter(raw_log: Path):
    source = detect_source(raw_log.name)
    is_important = FILTERS.get(source, filter_source_matches)

    out_path = IMPORTANT_DIR / raw_log.name
    out_path.parent.mkdir(parents=True, exist_ok=True)

    log.info(f"Filtering {raw_log.name} ({source}) -> {out_path}")

    # Wait for the raw log to exist
    while not raw_log.exists() and not shutdown_event.is_set():
        shutdown_event.wait(POLL_INTERVAL)

    with open(raw_log, "r") as infile, open(out_path, "a") as outfile:
        # Seek to end so we only process new lines going forward
        infile.seek(0, 2)

        while not shutdown_event.is_set():
            line = infile.readline()
            if not line:
                shutdown_event.wait(0.2)
                continue
            if is_important(line):
                outfile.write(line)
                outfile.flush()


def watch_for_new_logs():
    """Watch LOG_DIR for new *.log files and spin up filter threads for them."""
    known: set[str] = set()
    threads: list[threading.Thread] = []

    while not shutdown_event.is_set():
        current = {p.name for p in LOG_DIR.glob("*.log")}
        new = current - known
        for name in new:
            raw_log = LOG_DIR / name
            t = threading.Thread(
                target=tail_and_filter,
                args=(raw_log,),
                name=f"filter-{name}",
                daemon=True,
            )
            t.start()
            threads.append(t)
            known.add(name)
        shutdown_event.wait(POLL_INTERVAL)

    for t in threads:
        t.join(timeout=5)


if __name__ == "__main__":
    IMPORTANT_DIR.mkdir(parents=True, exist_ok=True)
    log.info(f"Filter service starting — watching {LOG_DIR} for *.log files")
    watch_for_new_logs()
    log.info("Filter service stopped.")
