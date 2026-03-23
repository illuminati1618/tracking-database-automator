"""
api_server.py — Lightweight Flask API for triggering snapshots on demand.

Runs inside the tracking-database-automator container on port 8586.
Only accepts requests with a valid X-API-Key header (shared secret with Flask backend).
"""

import os
import logging
import threading

from flask import Flask, request, jsonify
from snapshot import snapshot_aurora, snapshot_sqlite

log = logging.getLogger("api_server")

app = Flask(__name__)

AUTOMATOR_API_KEY = os.environ.get("AUTOMATOR_API_KEY", "")
API_PORT = int(os.environ.get("API_PORT", "8586"))

# Serialize snapshot requests to prevent concurrent runs
_snapshot_lock = threading.Lock()

# Injected by main.py
shutdown_event = threading.Event()


@app.before_request
def check_api_key():
    if not AUTOMATOR_API_KEY:
        return jsonify({"success": False, "message": "AUTOMATOR_API_KEY not configured"}), 500
    key = request.headers.get("X-API-Key", "")
    if key != AUTOMATOR_API_KEY:
        return jsonify({"success": False, "message": "Unauthorized"}), 401


@app.route("/api/snapshot/aurora", methods=["POST"])
def trigger_aurora():
    acquired = _snapshot_lock.acquire(blocking=False)
    if not acquired:
        return jsonify({"success": False, "message": "A snapshot is already in progress"}), 429
    try:
        ok = snapshot_aurora(trigger="manual-api")
        if ok:
            return jsonify({"success": True, "message": "Aurora snapshot initiated"})
        else:
            return jsonify({"success": False, "message": "Aurora snapshot failed — check logs"}), 500
    finally:
        _snapshot_lock.release()


@app.route("/api/snapshot/sqlite", methods=["POST"])
def trigger_sqlite():
    acquired = _snapshot_lock.acquire(blocking=False)
    if not acquired:
        return jsonify({"success": False, "message": "A snapshot is already in progress"}), 429
    try:
        ok = snapshot_sqlite(trigger="manual-api")
        if ok:
            return jsonify({"success": True, "message": "SQLite snapshot created"})
        else:
            return jsonify({"success": False, "message": "SQLite snapshot failed — check logs"}), 500
    finally:
        _snapshot_lock.release()


def start_api_server():
    """Entry point called from main.py as a daemon thread."""
    log.info(f"API server starting on port {API_PORT}")
    app.run(host="0.0.0.0", port=API_PORT, threaded=True, use_reloader=False)
