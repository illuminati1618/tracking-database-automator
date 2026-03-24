"""
Phase 2: Anomaly Detection
Watches logs/important/ for suspicious patterns and writes alerts to alerts/alerts.jsonl
"""
import os
import re
import json
import time
import logging
import threading
from collections import deque
from datetime import datetime, timezone

log = logging.getLogger("analyzer")

# --- Config (overridable via env) ---
ALERT_DIR = os.environ.get("ALERT_DIR", "/app/alerts")
LOG_DIR = os.environ.get("LOG_DIR", "/app/logs")
IMPORTANT_DIR = os.path.join(LOG_DIR, "important")

# Sliding window: how many seconds to look back
WINDOW_SECONDS = int(os.environ.get("ANOMALY_WINDOW_SECONDS", "60"))

# Thresholds
THRESH_AUTH_FAIL = int(os.environ.get("THRESH_AUTH_FAIL", "5"))       # failed auths in window
THRESH_BULK_EDIT = int(os.environ.get("THRESH_BULK_EDIT", "5"))       # PUT /api/user in window
THRESH_BULK_DELETE = int(os.environ.get("THRESH_BULK_DELETE", "3"))   # DELETE in window

# --- Log line parsers ---
# Flask Gunicorn format:
# 2026-03-04T18:48:31.710Z 172.25.0.1 - - [04/Mar/2026:18:48:31 +0000] "POST /api/authenticate HTTP/1.0" 401 43 "..." "..."
FLASK_LINE = re.compile(
    r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.\S+\s+'   # timestamp
    r'(\S+) - - \[.*?\] '                                  # ip
    r'"(\w+) (\S+) HTTP/\S+" '                             # method path
    r'(\d{3})'                                             # status
)


def parse_flask(line: str):
    m = FLASK_LINE.search(line)
    if not m:
        return None
    ts_str, ip, method, path, status = m.groups()
    try:
        ts = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        ts = time.time()
    return {"ts": ts, "ip": ip, "method": method, "path": path, "status": int(status)}


# --- Alert deduplication ---
# Tracks last alert time per rule to avoid spam
_last_alert: dict[str, float] = {}
DEDUP_SECONDS = int(os.environ.get("ALERT_DEDUP_SECONDS", "300"))  # 5 min cooldown per rule


def _should_alert(rule: str) -> bool:
    now = time.time()
    if now - _last_alert.get(rule, 0) >= DEDUP_SECONDS:
        _last_alert[rule] = now
        return True
    return False


def emit_alert(rule: str, severity: str, detail: dict, source: str):
    if not _should_alert(rule):
        return
    os.makedirs(ALERT_DIR, exist_ok=True)
    alert = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "rule": rule,
        "severity": severity,
        "source": source,
        "detail": detail,
    }
    path = os.path.join(ALERT_DIR, "alerts.jsonl")
    with open(path, "a") as f:
        f.write(json.dumps(alert) + "\n")
    log.warning(f"[ALERT] [{severity}] {rule} — {detail}")


# --- Sliding window counters ---
class SlidingWindow:
    """Thread-safe deque of timestamps within the last WINDOW_SECONDS."""
    def __init__(self, window: int = WINDOW_SECONDS):
        self._q: deque[float] = deque()
        self._lock = threading.Lock()
        self._window = window

    def add(self, ts: float):
        with self._lock:
            self._q.append(ts)
            self._trim(ts)

    def count(self, now: float | None = None) -> int:
        with self._lock:
            self._trim(now or time.time())
            return len(self._q)

    def _trim(self, now: float):
        cutoff = now - self._window
        while self._q and self._q[0] < cutoff:
            self._q.popleft()


# One set of windows per source file
class SourceCounters:
    def __init__(self, source: str):
        self.source = source
        self.auth_fail = SlidingWindow()    # POST /api/authenticate 401 or POST /login 302
        self.bulk_edit = SlidingWindow()    # PUT /api/user 200
        self.bulk_delete = SlidingWindow()  # DELETE /users/delete/*

    def ingest(self, parsed: dict):
        method = parsed["method"]
        path = parsed["path"]
        status = parsed["status"]
        ts = parsed["ts"]

        # Auth failure: POST /api/authenticate → 401, or POST /login → 302 (bad redirect)
        if method == "POST" and ("/api/authenticate" in path or "/login" in path):
            if status in (401, 302):
                self.auth_fail.add(ts)
                n = self.auth_fail.count(ts)
                if n >= THRESH_AUTH_FAIL:
                    emit_alert(
                        rule="repeated_auth_failure",
                        severity="MEDIUM",
                        detail={"count": n, "window_s": WINDOW_SECONDS, "path": path},
                        source=self.source,
                    )

        # Bulk user edit: PUT /api/user
        elif method == "PUT" and path.startswith("/api/user"):
            self.bulk_edit.add(ts)
            n = self.bulk_edit.count(ts)
            if n >= THRESH_BULK_EDIT:
                emit_alert(
                    rule="bulk_user_edit",
                    severity="HIGH",
                    detail={"count": n, "window_s": WINDOW_SECONDS},
                    source=self.source,
                )

        # Bulk delete
        elif method == "DELETE":
            self.bulk_delete.add(ts)
            n = self.bulk_delete.count(ts)
            if n >= THRESH_BULK_DELETE:
                emit_alert(
                    rule="bulk_deletion",
                    severity="HIGH",
                    detail={"count": n, "window_s": WINDOW_SECONDS, "path": path},
                    source=self.source,
                )


# --- File tail + analyze loop ---
shutdown_event = threading.Event()

_counters: dict[str, SourceCounters] = {}


def _get_counters(source: str) -> SourceCounters:
    if source not in _counters:
        _counters[source] = SourceCounters(source)
    return _counters[source]


def tail_and_analyze(filepath: str):
    source = os.path.basename(filepath).replace("_important.log", "")
    counters = _get_counters(source)
    log.info(f"[analyzer] watching {filepath}")

    with open(filepath, "r") as f:
        f.seek(0, 2)  # seek to end
        while not shutdown_event.is_set():
            line = f.readline()
            if not line:
                time.sleep(1)
                continue
            parsed = parse_flask(line)
            if parsed:
                counters.ingest(parsed)


def watch_important_dir():
    """Spawn a tail thread for each existing important log, and poll for new ones."""
    watched: set[str] = set()
    threads: list[threading.Thread] = []

    while not shutdown_event.is_set():
        if os.path.isdir(IMPORTANT_DIR):
            for fname in os.listdir(IMPORTANT_DIR):
                if not fname.endswith(".log"):
                    continue
                fpath = os.path.join(IMPORTANT_DIR, fname)
                if fpath not in watched:
                    watched.add(fpath)
                    t = threading.Thread(
                        target=tail_and_analyze,
                        args=(fpath,),
                        name=f"analyzer-{fname}",
                        daemon=True,
                    )
                    t.start()
                    threads.append(t)
        time.sleep(10)
