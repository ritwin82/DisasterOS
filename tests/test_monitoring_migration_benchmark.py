import sqlite3
from types import SimpleNamespace

import monitoring
from benchmarking import latency_ms, summarize_benchmark_rows
from database import Database


def test_network_throughput_uses_counter_differences(monkeypatch):
    counters = iter([SimpleNamespace(bytes_sent=1000, bytes_recv=2000),
                     SimpleNamespace(bytes_sent=3048, bytes_recv=6096)])
    moments = iter([10.0, 12.0])
    monkeypatch.setattr(monitoring.time, "monotonic", lambda: next(moments))
    monkeypatch.setattr(monitoring.psutil, "net_io_counters", lambda: next(counters))
    monkeypatch.setattr(monitoring.psutil, "cpu_percent", lambda interval=None: 10.0)
    monkeypatch.setattr(monitoring.psutil, "virtual_memory", lambda: SimpleNamespace(percent=20, available=1024**3))
    monkeypatch.setattr(monitoring.psutil, "swap_memory", lambda: SimpleNamespace(percent=0))
    monkeypatch.setattr(monitoring.psutil, "disk_usage", lambda _: SimpleNamespace(percent=30))
    monitor = monitoring.Monitor()
    assert monitor.sample()["network_up"] == 0
    second = monitor.sample()
    assert second["network_up"] == 1.0
    assert second["network_down"] == 2.0


def test_existing_database_migrates_without_losing_events(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE metrics (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, cpu REAL, ram REAL,
              available_mb REAL, swap REAL, disk REAL, network_up REAL, network_down REAL,
              anomaly_score REAL, is_anomaly INTEGER, risk TEXT, simulated INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE events (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, severity TEXT NOT NULL,
              scenario TEXT, action TEXT NOT NULL, result TEXT, duration_ms REAL, details TEXT);
            INSERT INTO events (ts,severity,action,result) VALUES ('2026-01-01','INFO','historic','SUCCESSFUL');
        """)
    db = Database(path)
    assert db.events()[0]["action"] == "historic"
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(metrics)")}
    assert {"measurement_source", "simulated_fields", "backend"}.issubset(columns)
    assert db.benchmarks() == []


def test_benchmark_latency_and_persistence(tmp_path):
    assert latency_ms(10.0, 11.25) == 1250.0
    assert latency_ms(10.0, None) is None
    db = Database(tmp_path / "benchmark.db")
    db.benchmark({"ts": "2026-01-01", "scenario": "Service crash", "recovery_enabled": 1,
                  "success": 1, "false_positives": 0, "missed_detections": 0})
    assert db.benchmarks()[0]["success"] == 1
    assert db.benchmarks()[0]["method_version"] == 3


def test_repeated_benchmark_summary_reports_median_p95_and_failures():
    rows = [
        {"recovery_enabled": 1, "detection_latency_ms": 100, "recovery_latency_ms": 200,
         "availability_percent": 90, "success": 1, "false_positives": 0, "missed_detections": 0},
        {"recovery_enabled": 1, "detection_latency_ms": 300, "recovery_latency_ms": None,
         "availability_percent": 50, "success": 0, "false_positives": 1, "missed_detections": 1},
        {"recovery_enabled": 0, "detection_latency_ms": 150, "recovery_latency_ms": None,
         "availability_percent": 20, "success": 0, "false_positives": 0, "missed_detections": 0},
    ]
    summary = summarize_benchmark_rows(rows)
    assert summary[True]["detection_median_ms"] == 200
    assert summary[True]["detection_p95_ms"] == 290
    assert summary[True]["success_rate"] == 50
    assert summary[True]["missed_detections"] == 1
    assert summary[False]["availability_mean"] == 20


def test_benchmark_method_migration_marks_older_rows(tmp_path):
    path = tmp_path / "version1.db"
    Database(path)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE benchmarks")
        conn.execute("""CREATE TABLE benchmarks (
            id INTEGER PRIMARY KEY, ts TEXT NOT NULL, scenario TEXT NOT NULL,
            recovery_enabled INTEGER NOT NULL, fault_ts TEXT, detection_ts TEXT,
            action_ts TEXT, verified_ts TEXT, detection_latency_ms REAL,
            recovery_latency_ms REAL, availability_percent REAL, success INTEGER NOT NULL,
            false_positives INTEGER NOT NULL, missed_detections INTEGER NOT NULL, details TEXT)""")
        conn.execute("""INSERT INTO benchmarks
            (ts,scenario,recovery_enabled,success,false_positives,missed_detections)
            VALUES ('2026-01-01','Service crash',1,1,0,0)""")
    assert Database(path).benchmarks()[0]["method_version"] == 1
