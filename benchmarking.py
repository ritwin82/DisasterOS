"""Measured service-crash comparisons; no generated benchmark results."""
import threading
import time
from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def latency_ms(start, end):
    return None if start is None or end is None else round((end - start) * 1000, 1)


class BenchmarkRunner:
    def __init__(self, system):
        self.system = system
        self.running = False
        self.error = None
        self.thread = None
        self.lock = threading.Lock()

    def start_pair(self):
        with self.lock:
            if self.running:
                return False
            self.running = True
            self.error = None
            self.thread = threading.Thread(target=self._run_pair, daemon=True, name="disasteros-benchmark")
            self.thread.start()
            return True

    def _run_pair(self):
        previous_dry_run = self.system.recovery.dry_run
        try:
            self.system.recovery.dry_run = False
            self._trial(False)
            self._trial(True)
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

    def _trial(self, enabled, duration_seconds=6):
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
        available, observed = 0, 1  # fault injection is a directly observed unavailable state
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
            "details": {"samples": observed, "recovery_enabled": enabled,
                        "availability_definition": "fault state plus unique monitor samples with service running",
                        "method_version": 2},
            "method_version": 2,
        }
        self.system.db.benchmark(row)
        self.system.db.event("INFO", "Benchmark trial", "Service crash", "SUCCESSFUL" if success else "UNVERIFIED",
                             details={"enabled": enabled, "detection_latency_ms": row["detection_latency_ms"],
                                      "recovery_latency_ms": row["recovery_latency_ms"]})
        self.system.stop()
        if not self.system.simulator.service_alive():
            self.system.simulator.restart_service()
        return row
