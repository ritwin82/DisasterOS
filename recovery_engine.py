import time
from datetime import datetime, timezone


PRIORITIES = {"demo_service": "P0 Critical", "dependency": "P1 Important", "stress_worker": "P2 Non-critical"}


class RecoveryEngine:
    ALLOWED_ACTIONS = {"restart_service", "restart_systemd_service", "stop_worker", "cpu_quota", "memory_limit", "offline_mode", "disk_alert"}
    COOLDOWN_SECONDS = 30
    MAX_RETRIES = 2

    def __init__(self, db, simulator, dry_run=False):
        self.db = db
        self.backend = simulator if hasattr(simulator, "get_platform_capabilities") else None
        self.simulator = simulator.simulator if self.backend else simulator
        self.dry_run = dry_run
        self.enabled = True
        self.last_action = None
        self.completed = set()
        self.attempts = {}
        self.last_attempt = {}

    @staticmethod
    def decide(metrics, risk, service_alive=True, systemd_alive=None, capabilities=None):
        if not service_alive:
            return {"action": "restart_service", "priority": PRIORITIES["demo_service"], "reason": "Managed demo service stopped"}
        if systemd_alive is False:
            return {"action": "restart_systemd_service", "priority": "P0 Critical", "reason": "Configured user test service stopped"}
        if metrics.get("worker_alive") and capabilities:
            if metrics.get("scenario") == "CPU overload" and capabilities.get("cpu_quota", {}).get("status") == "SUPPORTED":
                return {"action": "cpu_quota", "priority": PRIORITIES["stress_worker"], "reason": "Limit owned CPU workload to 50% of one core"}
            if metrics.get("scenario") == "Memory pressure" and capabilities.get("memory_limit", {}).get("status") == "SUPPORTED":
                return {"action": "memory_limit", "priority": PRIORITIES["stress_worker"], "reason": "Limit owned memory workload to 256 MB"}
        if metrics.get("scenario") in ("CPU overload", "Memory pressure", "Combined disaster") and metrics.get("worker_alive"):
            return {"action": "stop_worker", "priority": PRIORITIES["stress_worker"], "reason": "Stop application-owned stress workload"}
        if not metrics.get("dependency_online", True) and not metrics.get("offline_mode", False):
            return {"action": "offline_mode", "priority": PRIORITIES["dependency"], "reason": "Simulated dependency unavailable"}
        if risk["resources"]["Disk"] in ("HIGH", "CRITICAL"):
            return {"action": "disk_alert", "priority": "P1 Important", "reason": "Approve temporary storage cleanup manually"}
        return {"action": "none", "priority": "—", "reason": "No recovery needed"}

    def execute(self, decision, metrics):
        action = decision["action"]
        if action == "none" or not self.enabled:
            return None
        key = (metrics.get("scenario"), action)
        if key in self.completed:
            return self.last_action
        if action not in self.ALLOWED_ACTIONS:
            raise ValueError("Action is not allowlisted")
        if self.attempts.get(key, 0) >= self.MAX_RETRIES:
            return self.last_action
        if time.monotonic() - self.last_attempt.get(key, -float("inf")) < self.COOLDOWN_SECONDS:
            return self.last_action
        start = time.monotonic()
        started_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        before = {"service_alive": self.simulator.service_alive(), "worker_alive": metrics.get("worker_alive"),
                  "dependency_online": metrics.get("dependency_online")}
        result = "UNVERIFIED"
        details = {"before": before, "reason": decision["reason"]}
        if self.dry_run:
            details["dry_run"] = True
            completed = time.monotonic()
            duration_ms = round((completed - start) * 1000, 1)
            self.last_action = {"action": action, "result": "UNVERIFIED", "duration_ms": duration_ms,
                                "priority": decision["priority"], "details": details,
                                "started_at": started_at,
                                "completed_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                                "started_monotonic": start, "completed_monotonic": completed}
            self.db.event("INFO", action, metrics.get("scenario"), "UNVERIFIED", duration_ms, details)
            self.completed.add(key)
            return self.last_action
        self.attempts[key] = self.attempts.get(key, 0) + 1
        self.last_attempt[key] = start
        try:
            if self.backend and action in ("restart_service", "restart_systemd_service", "stop_worker", "cpu_quota", "memory_limit", "offline_mode"):
                workload_id = {"restart_service": "demo_service", "restart_systemd_service": "systemd_test_service",
                               "stop_worker": "stress_worker", "cpu_quota": "stress_worker", "memory_limit": "stress_worker",
                               "offline_mode": "dependency"}[action]
                policy = {"action": action}
                if action == "cpu_quota":
                    policy["percent"] = 50
                elif action == "memory_limit":
                    policy["memory_mb"] = 256
                outcome = (self.backend.restart_managed_service(workload_id) if action in ("restart_service", "restart_systemd_service") else
                           self.backend.apply_resource_policy(workload_id, policy))
                result = outcome.verification
                details["execution"] = outcome.to_dict()
                details["after"] = self.backend.inspect_workload(workload_id)
            elif action == "restart_service":
                self.simulator.restart_service()
                result = "SUCCESSFUL" if self.simulator.service_alive() else "FAILED"
                details["after"] = {"service_alive": self.simulator.service_alive()}
            elif action == "stop_worker":
                stopped = self.simulator.stop_worker()
                result = "SUCCESSFUL" if stopped else "FAILED"
                details["after"] = {"worker_alive": not stopped}
            elif action == "offline_mode":
                self.simulator.offline_mode = True
                result = "SUCCESSFUL" if self.simulator.offline_mode else "FAILED"
                details["after"] = {"offline_mode": self.simulator.offline_mode}
            elif action == "disk_alert":
                details["after"] = "Recommendation only; no files changed"
        except Exception as exc:
            result = "FAILED"
            details["error"] = str(exc)
        completed = time.monotonic()
        duration_ms = round((completed - start) * 1000, 1)
        self.last_action = {"action": action, "result": result, "duration_ms": duration_ms,
                            "priority": decision["priority"], "details": details,
                            "started_at": started_at,
                            "completed_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                            "started_monotonic": start, "completed_monotonic": completed}
        self.db.event("INFO" if result == "SUCCESSFUL" else "WARNING", action, metrics.get("scenario"),
                      result, duration_ms, details)
        if result != "FAILED":
            self.completed.add(key)
        elif self.attempts[key] >= self.MAX_RETRIES:
            self.db.event("ERROR", "Recovery escalated", metrics.get("scenario"), "FAILED",
                          details={"action": action, "attempts": self.attempts[key]})
        return self.last_action

    def reset_episode(self):
        self.completed.clear()
        self.last_action = None
        self.attempts.clear()
        self.last_attempt.clear()
