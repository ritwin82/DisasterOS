import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from config import DB_PATH


class Database:
    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, cpu REAL, ram REAL,
                    available_mb REAL, swap REAL, disk REAL, network_up REAL,
                    network_down REAL, anomaly_score REAL, is_anomaly INTEGER,
                    risk TEXT, simulated INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, severity TEXT NOT NULL,
                    scenario TEXT, action TEXT NOT NULL, result TEXT,
                    duration_ms REAL, details TEXT
                );
                CREATE TABLE IF NOT EXISTS benchmarks (
                    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, scenario TEXT NOT NULL,
                    recovery_enabled INTEGER NOT NULL, fault_ts TEXT, detection_ts TEXT,
                    action_ts TEXT, verified_ts TEXT, detection_latency_ms REAL,
                    recovery_latency_ms REAL, availability_percent REAL,
                    success INTEGER NOT NULL, false_positives INTEGER NOT NULL,
                    missed_detections INTEGER NOT NULL, details TEXT,
                    method_version INTEGER NOT NULL DEFAULT 3,
                    batch_id TEXT, trial_number INTEGER, trial_count INTEGER
                );
            """)
            existing = {row[1] for row in db.execute("PRAGMA table_info(metrics)")}
            for name, definition in {
                "measurement_source": "TEXT NOT NULL DEFAULT 'real'",
                "simulated_fields": "TEXT",
                "backend": "TEXT",
            }.items():
                if name not in existing:
                    db.execute(f"ALTER TABLE metrics ADD COLUMN {name} {definition}")
            benchmark_columns = {row[1] for row in db.execute("PRAGMA table_info(benchmarks)")}
            if "method_version" not in benchmark_columns:
                db.execute("ALTER TABLE benchmarks ADD COLUMN method_version INTEGER NOT NULL DEFAULT 1")
            for name, definition in {
                "batch_id": "TEXT",
                "trial_number": "INTEGER",
                "trial_count": "INTEGER",
            }.items():
                if name not in benchmark_columns:
                    db.execute(f"ALTER TABLE benchmarks ADD COLUMN {name} {definition}")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def metric(self, row):
        columns = ("ts", "cpu", "ram", "available_mb", "swap", "disk", "network_up", "network_down", "anomaly_score", "is_anomaly", "risk", "simulated", "measurement_source", "simulated_fields", "backend")
        with self._connect() as db:
            values = [json.dumps(row.get(key)) if key == "simulated_fields" else row.get(key) for key in columns]
            db.execute(f"INSERT INTO metrics ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", values)
            db.execute("DELETE FROM metrics WHERE id NOT IN (SELECT id FROM metrics ORDER BY id DESC LIMIT 10000)")

    def event(self, severity, action, scenario=None, result=None, duration_ms=None, details=None):
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as db:
            db.execute("INSERT INTO events (ts,severity,scenario,action,result,duration_ms,details) VALUES (?,?,?,?,?,?,?)",
                       (ts, severity, scenario, action, result, duration_ms, json.dumps(details) if isinstance(details, dict) else details))

    def events(self, limit=100):
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))]

    def metrics(self, limit=150):
        with self._connect() as db:
            rows = db.execute("SELECT * FROM metrics ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(row) for row in reversed(rows)]

    def clean_metrics(self, limit=500):
        """Return oldest-to-newest observations safe for anomaly-model training."""
        with self._connect() as db:
            rows = db.execute("""SELECT * FROM metrics
                WHERE simulated = 0 AND COALESCE(measurement_source, 'real') = 'real'
                  AND cpu IS NOT NULL AND ram IS NOT NULL AND swap IS NOT NULL AND disk IS NOT NULL
                ORDER BY id DESC LIMIT ?""", (limit,)).fetchall()
            return [dict(row) for row in reversed(rows)]

    def benchmark(self, row):
        columns = ("ts", "scenario", "recovery_enabled", "fault_ts", "detection_ts", "action_ts", "verified_ts",
                   "detection_latency_ms", "recovery_latency_ms", "availability_percent", "success",
                   "false_positives", "missed_detections", "details", "method_version",
                   "batch_id", "trial_number", "trial_count")
        with self._connect() as db:
            values = [json.dumps(row.get(key)) if key == "details" else row.get(key, 3) if key == "method_version" else row.get(key) for key in columns]
            db.execute(f"INSERT INTO benchmarks ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", values)

    def benchmarks(self, limit=50):
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM benchmarks ORDER BY id DESC LIMIT ?", (limit,))]
