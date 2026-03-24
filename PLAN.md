# Tracking Database Automator — Implementation Plan

## Problem We Are Solving
Flask/Spring applications have experienced data loss (e.g., mass password resets) due to race conditions, unsafe migration scripts, and no automated monitoring or backups. We need a system that prevents, detects, and recovers from data loss.

---

## System Layers

```
[ Flask / Spring containers ]
         │ logs
         ▼
[ Phase 1: Log Capture ]  ← DONE
         │ log files
         ▼
[ Phase 2: Anomaly Detection ]  ← NEXT
         │ alerts
         ▼
[ Phase 3: Alerting (Slack/email) ]
         │
         ▼
[ Phase 4: Control Panel (GitHub Pages) ]
         │
         ▼
[ Phase 5: Aurora Snapshots & Recovery ]  ← DONE
```

---

## Phases

### Phase 1 — Log Capture ✅ DONE
- [x] Docker-based Python service
- [x] Streams logs from `flask_web_1` and `spring-web-1` via Docker socket
- [x] Writes timestamped logs to `./logs/*.log`
- [x] Auto-retries on container restart, heartbeat every 60s

---

### Phase 2 — Anomaly Detection 🔜 NEXT
**Goal**: Parse captured logs in real-time and flag suspicious operations.

**Rules to detect:**
| Pattern | Condition | Severity |
|---|---|---|
| Mass password reset | >N password changes in T seconds, not from admin | HIGH |
| Bulk user creation | >N new users in T seconds, no override mode | MEDIUM |
| Bulk deletion | >N DELETE operations in T seconds | HIGH |
| Migration execution | Any schema migration outside maintenance window | MEDIUM |
| Repeated auth failures | >N failed logins from same IP in T seconds | MEDIUM |

**Design:**
- Add an `analyzer.py` module that reads from the same log files (or receives lines via queue from `main.py`)
- Sliding window counters per rule (e.g., 10 password changes in 60 seconds = alert)
- Track "override mode" flag in logs — suppress alerts when admin override is active
- Emit structured alert events (JSON) to an alerts queue/file

**Key questions to resolve:**
- What thresholds make sense? (N, T per rule)
- How do we distinguish admin vs. non-admin operations in the logs?
- What does "override mode" look like in current Flask logs?

---

### Phase 3 — Alerting
**Goal**: Notify the team when anomalies are detected.

- Slack webhook notification (primary)
- Email fallback (optional)
- Alert includes: timestamp, rule triggered, log excerpt, container source
- Deduplication — don't spam the same alert repeatedly
- Alert log stored in `./alerts/alerts.jsonl` for audit trail

---

### Phase 4 — Control Panel (GitHub Pages)
**Goal**: Single human-operated interface for the whole system.

- Static frontend on GitHub Pages
- Triggers GitHub Actions workflows via `workflow_dispatch` API
- Features:
  - View recent anomaly alerts
  - Trigger/view Aurora snapshots
  - Approve/reject pending migrations
  - Initiate point-in-time restore

**Branch strategy** (per jm1021 feedback):
- PRs/code changes → `main` branch
- Deployable Actions → `production` branch (only tested builds promoted here)

---

### Phase 5 — Aurora Snapshots & Recovery ✅ DONE
**Goal**: Automated backups with configurable retention.

- Cron-based snapshots (daily minimum, configurable)
- Pre-migration automatic snapshot
- Retention policy: 7 daily, 4 weekly, 3 monthly
- Snapshot tagged with: timestamp, trigger type (scheduled/manual/pre-migration), app version
- Restore interface in Control Panel

---

## Operator KSA (Knowledge, Skills, Abilities)
The system should support operators at three levels:

**Knowledge** the system teaches:
- What each alert means and what caused it
- Current system state (normal / degraded / blocked / override)
- RPO/RTO boundaries for each backup tier

**Skills** the system supports:
- Reading and interpreting anomaly reports
- Safely executing restore and rollback procedures
- Approving or rejecting migrations with full context

**Abilities** the system builds:
- Situational awareness under pressure (clear dashboard, no information overload)
- Judgment in override decisions (audit trail of who approved what and why)
- Post-incident review from audit logs

---

## Password Handling Note
On backup/restore, hashed passwords are preserved as-is. The backend should accept either:
- `"password"` key → hash before storing
- `"hashed_password"` key → store directly (used during restore)

This avoids double-hashing during restore operations.

---

## Success Metrics
- Zero data loss incidents after full implementation
- < 15 minute detection time for anomalous operations
- 100% migration success rate with rollback capability via snapshots
