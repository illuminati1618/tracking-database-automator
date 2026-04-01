"""
api.py — Lightweight HTTP API for triggering snapshots.

Authenticated via X-API-Key header (shared secret with Flask proxy).
"""

import os
import logging
import threading
from flask import Flask, request, jsonify
from snapshot import snapshot_aurora, snapshot_sqlite, cleanup_aurora, cleanup_sqlite

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

app = Flask(__name__)

API_KEY = os.environ.get("AUTOMATOR_API_KEY", "")
API_PORT = int(os.environ.get("API_PORT", "8586"))


def _require_api_key():
    if not API_KEY:
        return jsonify({"success": False, "message": "AUTOMATOR_API_KEY not set on automator"}), 500
    key = request.headers.get("X-API-Key", "")
    if key != API_KEY:
        return jsonify({"success": False, "message": "Invalid API key"}), 403
    return None


@app.route("/api/snapshot/aurora", methods=["POST"])
def trigger_aurora():
    err = _require_api_key()
    if err:
        return err
    log.info("API: Aurora snapshot requested")
    ok = snapshot_aurora(trigger="api")
    if ok:
        threading.Thread(target=cleanup_aurora, daemon=True).start()
        return jsonify({"success": True, "message": "Aurora snapshot initiated"})
    return jsonify({"success": False, "message": "Aurora snapshot failed — check logs"}), 500


@app.route("/api/snapshot/sqlite", methods=["POST"])
def trigger_sqlite():
    err = _require_api_key()
    if err:
        return err
    log.info("API: SQLite snapshot requested")
    ok = snapshot_sqlite(trigger="api")
    if ok:
        threading.Thread(target=cleanup_sqlite, daemon=True).start()
        return jsonify({"success": True, "message": "SQLite snapshot created"})
    return jsonify({"success": False, "message": "SQLite snapshot failed — check logs"}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


def start_api():
    """Start the API server (called from entrypoint.sh or main.py)."""
    app.run(host="0.0.0.0", port=API_PORT, debug=False)


if __name__ == "__main__":
    start_api()
