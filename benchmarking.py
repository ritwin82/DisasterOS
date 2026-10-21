"""Repeatable measured recovery benchmarks; no generated benchmark results."""
import statistics
import threading
import time
import uuid
from datetime import datetime, timezone

from config import BENCHMARK_TRIALS


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def latency_ms(start, end):
    return None if start is None or end is None else round((end - start) * 1000, 1)


def percentile(values, percent):
    clean = sorted(float(value) for value in values if value is not None)
    if not clean:
        return None
    position = (len(clean) - 1) * percent / 100
    lower, upper = int(position), min(int(position) + 1, len(clean) - 1)
    fraction = position - lower
    return clean[lower] + (clean[upper] - clean[lower]) * fraction


def summarize_benchmark_rows(rows):
    """Aggregate repeated trials without hiding failures or missing measurements."""
    summary = {}
    for enabled in (False, True):
        group = [row for row in rows if bool(row.get("recovery_enabled")) is enabled]
        if not group:
            continue
        detection = [row.get("detection_latency_ms") for row in group]
        recovery = [row.get("recovery_latency_ms") for row in group]
        availability = [row.get("availability_percent") for row in group]
        summary[enabled] = {
            "runs": len(group),
            "detection_median_ms": percentile(detection, 50),
            "detection_p95_ms": percentile(detection, 95),
            "recovery_median_ms": percentile(recovery, 50),
            "recovery_p95_ms": percentile(recovery, 95),
            "availability_mean": statistics.fmean(value for value in availability if value is not None)
            if any(value is not None for value in availability) else None,
            "availability_median": percentile(availability, 50),
            "success_rate": 100 * sum(int(row.get("success", 0)) for row in group) / len(group),
            "false_positives": sum(int(row.get("false_positives", 0)) for row in group),
            "missed_detections": sum(int(row.get("missed_detections", 0)) for row in group),
        }
    return summary


class BenchmarkRunner:
    def __init__(self, system):
        self.system = system
        self.running = False
        self.error = None
        self.thread = None
        self.lock = threading.Lock()
        self.batch_id = None
        self.completed_runs = 0
        self.total_runs = 0

    @property
    def progress(self):
        return self.completed_runs / self.total_runs if self.total_runs else 0.0

    def start_suite(self, trials=BENCHMARK_TRIALS):
        trials = max(1, int(trials))
        with self.lock:
            if self.running:
                return False
            self.running = True
            self.error = None
            self.batch_id = uuid.uuid4().hex
            self.completed_runs = 0
            self.total_runs = trials * 2
            self.thread = threading.Thread(target=self._run_suite, args=(trials,), daemon=True,
                                           name="disasteros-benchmark")
            self.thread.start()
            return True

    def start_pair(self):
        """Compatibility entry point for callers that need one A/B comparison."""
        return self.start_suite(1)

    def _run_suite(self, trials):
        previous_dry_run = self.system.recovery.dry_run
        try:
            self.system.recovery.dry_run = False
            for trial_number in range(1, trials + 1):
                # Alternate ordering to reduce warm-up/order bias.
                order = (False, True) if trial_number % 2 else (True, False)
                for enabled in order:
                    self._trial(enabled, batch_id=self.batch_id, trial_number=trial_number,
                                trial_count=trials)
                    self.completed_runs += 1
        except Exception as exc:
            self.error = str(exc)
            self.system.db.event("ERROR", "Benchmark failed", "Service crash", "FAILED", details=str(exc))
        finally:
            self.system.recovery.enabled = True
            self.system.recovery.dry_run = previous_dry_run
            self.system.stop()
            if not self.system.simulator.service_alive():
                self.system.simulator.restart_service()
            with self.lock:
                self.running = False

    def _trial(self, enabled, duration_seconds=6, batch_id=None, trial_number=1, trial_count=1):
        self.system.stop()
        if not self.system.simulator.service_alive():
            self.system.simulator.restart_service()
        baseline_deadline = time.monotonic() + 4
        pre_fault = {}
        while time.monotonic() < baseline_deadline:
            pre_fault, _, _ = self.system.snapshot()
            if pre_fault.get("observed", {}).get("scenario") is None and pre_fault.get("service_alive"):
                break
            time.sleep(0.1)
        false_positives = int(pre_fault.get("observed", {}).get("scenario") is None and
                              pre_fault.get("decision", {}).get("action") == "restart_service")
        self.system.recovery.enabled = enabled
        self.system.start("Service crash")
        if self.system.simulator.service_alive():
            raise RuntimeError("Fault injection did not stop the managed service")
        fault_ts = utc_now()
        fault_clock = time.monotonic()
        detection_clock = action_clock = verified_clock = None
        detection_ts = action_ts = verified_ts = None
        available, observed = 0, 1
        last_sample_ts = None
        deadline = fault_clock + duration_seconds
        while time.monotonic() < deadline:
            snap, _, _ = self.system.snapshot()
            sample = snap.get("observed", {})
            if sample.get("scenario") == "Service crash" and sample.get("ts") != last_sample_ts:
                last_sample_ts = sample.get("ts")
                observed += 1
                available += int(bool(snap.get("service_alive")))
                if snap.get("decision", {}).get("action") == "restart_service" and detection_clock is None:
                    detection_clock = snap.get("decision_monotonic") or time.monotonic()
                    detection_ts = snap.get("decision_at") or utc_now()
                action = snap.get("action")
                if enabled and action and action.get("action") == "restart_service" and action_clock is None:
                    action_clock = action.get("started_monotonic") or time.monotonic()
                    action_ts = action.get("started_at") or utc_now()
                    if action.get("result") == "SUCCESSFUL" and snap.get("service_alive"):
                        verified_clock = action.get("completed_monotonic") or time.monotonic()
                        verified_ts = action.get("completed_at") or utc_now()
            if enabled and verified_clock is not None:
                break
            time.sleep(0.1)
        success = int(verified_clock is not None)
        row = {
            "ts": utc_now(), "scenario": "Service crash", "recovery_enabled": int(enabled),
            "fault_ts": fault_ts, "detection_ts": detection_ts, "action_ts": action_ts, "verified_ts": verified_ts,
            "detection_latency_ms": latency_ms(fault_clock, detection_clock),
            "recovery_latency_ms": latency_ms(fault_clock, verified_clock),
            "availability_percent": round(100 * available / observed, 1) if observed else None,
            "success": success, "false_positives": false_positives,
            "missed_detections": int(detection_clock is None),
            "batch_id": batch_id, "trial_number": trial_number, "trial_count": trial_count,
            "details": {"samples": observed, "recovery_enabled": enabled,
                        "availability_definition": "fault state plus unique monitor samples with service running",
                        "order_is_alternated": True, "method_version": 3},
            "method_version": 3,
        }
        self.system.db.benchmark(row)
        self.system.db.event("INFO", "Benchmark trial", "Service crash", "SUCCESSFUL" if success else "UNVERIFIED",
                             details={"enabled": enabled, "batch_id": batch_id, "trial_number": trial_number,
                                      "detection_latency_ms": row["detection_latency_ms"],
                                      "recovery_latency_ms": row["recovery_latency_ms"]})
        self.system.stop()
        if not self.system.simulator.service_alive():
            self.system.simulator.restart_service()
        return row
