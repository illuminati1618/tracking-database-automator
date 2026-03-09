"""
snapshot.py — Automated database snapshot system

Handles two database types:
  1. Aurora/RDS (Flask production) — uses boto3 to create AWS snapshots
  2. SQLite (Spring production) — copies the .db file to a timestamped backup

Can be run via cron, manually, or imported as a module (for future API use).
"""

import os
import sys
import shutil
import logging
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# --- Configuration ---
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")
RDS_INSTANCE_ID = os.environ.get("RDS_INSTANCE_ID", "")
SPRING_SQLITE_PATH = Path(os.environ.get("SPRING_SQLITE_PATH", "/spring-volumes/sqlite.db"))
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/app/backups"))

RETENTION_DAILY = int(os.environ.get("RETENTION_DAILY", "7"))
RETENTION_WEEKLY = int(os.environ.get("RETENTION_WEEKLY", "4"))
RETENTION_MONTHLY = int(os.environ.get("RETENTION_MONTHLY", "3"))


# ---------------------------------------------------------------------------
# Aurora/RDS snapshots
# ---------------------------------------------------------------------------

def snapshot_aurora(trigger: str = "scheduled") -> bool:
    """Create an RDS snapshot with metadata tags."""
    if not RDS_INSTANCE_ID:
        log.warning("RDS_INSTANCE_ID not set, skipping Aurora snapshot")
        return False

    try:
        import boto3
    except ImportError:
        log.error("boto3 not installed, cannot create Aurora snapshot")
        return False

    now = datetime.utcnow()
    snapshot_id = f"{RDS_INSTANCE_ID}-{now.strftime('%Y%m%d-%H%M%S')}"

    try:
        rds = boto3.client("rds", region_name=AWS_REGION)
        log.info(f"Creating RDS snapshot: {snapshot_id}")

        rds.create_db_snapshot(
            DBSnapshotIdentifier=snapshot_id,
            DBInstanceIdentifier=RDS_INSTANCE_ID,
            Tags=[
                {"Key": "trigger", "Value": trigger},
                {"Key": "created_by", "Value": "db-automator"},
                {"Key": "timestamp", "Value": now.isoformat()},
            ],
        )
        log.info(f"Aurora snapshot '{snapshot_id}' creation initiated")
        return True

    except Exception as e:
        log.error(f"Failed to create Aurora snapshot: {e}")
        return False


def cleanup_aurora():
    """Delete Aurora snapshots beyond retention policy."""
    if not RDS_INSTANCE_ID:
        return

    try:
        import boto3
    except ImportError:
        return

    try:
        rds = boto3.client("rds", region_name=AWS_REGION)
        response = rds.describe_db_snapshots(
            DBInstanceIdentifier=RDS_INSTANCE_ID,
            SnapshotType="manual",
        )
        snapshots = response.get("DBSnapshots", [])

        # Only manage snapshots created by us
        our_snapshots = []
        for snap in snapshots:
            tags_resp = rds.list_tags_for_resource(ResourceName=snap["DBSnapshotArn"])
            tags = {t["Key"]: t["Value"] for t in tags_resp.get("TagList", [])}
            if tags.get("created_by") == "db-automator":
                our_snapshots.append({
                    "id": snap["DBSnapshotIdentifier"],
                    "time": snap["SnapshotCreateTime"],
                })

        our_snapshots.sort(key=lambda s: s["time"], reverse=True)
        _apply_retention(our_snapshots, delete_fn=lambda s: _delete_aurora_snapshot(rds, s["id"]))

    except Exception as e:
        log.error(f"Aurora cleanup failed: {e}")


def _delete_aurora_snapshot(rds, snapshot_id: str):
    log.info(f"Deleting Aurora snapshot: {snapshot_id}")
    rds.delete_db_snapshot(DBSnapshotIdentifier=snapshot_id)


# ---------------------------------------------------------------------------
# SQLite snapshots
# ---------------------------------------------------------------------------

def snapshot_sqlite(trigger: str = "scheduled") -> bool:
    """Copy the Spring SQLite database to a timestamped backup."""
    if not SPRING_SQLITE_PATH.exists():
        log.warning(f"SQLite file not found at {SPRING_SQLITE_PATH}, skipping")
        return False

    now = datetime.utcnow()
    dest_dir = BACKUP_DIR / "spring"
    dest_dir.mkdir(parents=True, exist_ok=True)

    filename = f"sqlite_{now.strftime('%Y%m%d_%H%M%S')}.db"
    dest = dest_dir / filename

    try:
        log.info(f"Copying SQLite database: {SPRING_SQLITE_PATH} -> {dest}")
        shutil.copy2(SPRING_SQLITE_PATH, dest)

        # Also copy WAL and SHM files if they exist (for consistency)
        for suffix in ["-wal", "-shm"]:
            wal = SPRING_SQLITE_PATH.parent / (SPRING_SQLITE_PATH.name + suffix)
            if wal.exists():
                shutil.copy2(wal, dest_dir / (filename + suffix))

        size_mb = dest.stat().st_size / (1024 * 1024)
        log.info(f"SQLite snapshot saved: {dest} ({size_mb:.1f} MB)")
        return True

    except Exception as e:
        log.error(f"Failed to create SQLite snapshot: {e}")
        return False


def cleanup_sqlite():
    """Delete old SQLite backups beyond retention policy."""
    backup_dir = BACKUP_DIR / "spring"
    if not backup_dir.exists():
        return

    backups = sorted(backup_dir.glob("sqlite_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)

    snapshots = [{"id": p.name, "time": datetime.fromtimestamp(p.stat().st_mtime), "path": p} for p in backups]

    def delete_sqlite_backup(snap):
        path = snap["path"]
        log.info(f"Deleting old SQLite backup: {path.name}")
        path.unlink()
        # Clean up associated WAL/SHM files
        for suffix in ["-wal", "-shm"]:
            companion = path.parent / (path.name + suffix)
            if companion.exists():
                companion.unlink()

    _apply_retention(snapshots, delete_fn=delete_sqlite_backup)


# ---------------------------------------------------------------------------
# Retention logic
# ---------------------------------------------------------------------------

def _apply_retention(snapshots: list[dict], delete_fn):
    """
    Keep the most recent RETENTION_DAILY snapshots,
    plus one per week for RETENTION_WEEKLY weeks,
    plus one per month for RETENTION_MONTHLY months.
    Delete the rest.
    """
    if not snapshots:
        return

    now = datetime.utcnow()
    keep = set()

    # Keep the N most recent (daily)
    for snap in snapshots[:RETENTION_DAILY]:
        keep.add(snap["id"])

    # Keep one per week for the last N weeks
    for weeks_ago in range(RETENTION_WEEKLY):
        week_start = now - timedelta(weeks=weeks_ago + 1)
        week_end = now - timedelta(weeks=weeks_ago)
        for snap in snapshots:
            t = snap["time"]
            # Handle timezone-aware datetimes
            if hasattr(t, 'tzinfo') and t.tzinfo is not None:
                t = t.replace(tzinfo=None)
            if week_start <= t < week_end:
                keep.add(snap["id"])
                break

    # Keep one per month for the last N months
    for months_ago in range(RETENTION_MONTHLY):
        month_start = now - timedelta(days=30 * (months_ago + 1))
        month_end = now - timedelta(days=30 * months_ago)
        for snap in snapshots:
            t = snap["time"]
            if hasattr(t, 'tzinfo') and t.tzinfo is not None:
                t = t.replace(tzinfo=None)
            if month_start <= t < month_end:
                keep.add(snap["id"])
                break

    # Delete everything not in the keep set
    for snap in snapshots:
        if snap["id"] not in keep:
            delete_fn(snap)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_all(trigger: str = "scheduled"):
    """Run all snapshot + cleanup operations."""
    log.info(f"=== Snapshot run starting (trigger={trigger}) ===")

    aurora_ok = snapshot_aurora(trigger=trigger)
    sqlite_ok = snapshot_sqlite(trigger=trigger)

    cleanup_aurora()
    cleanup_sqlite()

    log.info(f"=== Snapshot run complete (aurora={'OK' if aurora_ok else 'SKIP'}, sqlite={'OK' if sqlite_ok else 'SKIP'}) ===")
    return aurora_ok or sqlite_ok


if __name__ == "__main__":
    trigger = sys.argv[1] if len(sys.argv) > 1 else "manual"
    run_all(trigger=trigger)
