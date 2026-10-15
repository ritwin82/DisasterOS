from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import psutil

from monitoring import Monitor


@dataclass
class ActionResult:
    action: str
    workload_id: str
    attempted: bool
    succeeded: bool
    verification: str = "UNVERIFIED"
    error: str | None = None
    timestamp: str = ""
    details: dict[str, Any] | None = None

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def to_dict(self):
        return asdict(self)


def result(action, workload_id, attempted=False, succeeded=False, verification="UNVERIFIED", error=None, details=None):
    return ActionResult(action, workload_id, attempted, succeeded, verification, error, details=details)


class PlatformBackend(ABC):
    name = "base"

    def __init__(self, simulator=None):
        self.simulator = simulator
        self.monitor = Monitor()

    def collect_metrics(self):
        return self.monitor.sample()

    def _owned_process(self, workload_id):
        if self.simulator is None:
            return None
        if workload_id == "demo_service":
            return self.simulator.service
        if workload_id == "stress_worker":
            return self.simulator.worker
        return None

    def inspect_workload(self, workload_id):
        if workload_id == "dependency" and self.simulator is not None:
            return {"id": workload_id, "priority": "P1", "alive": True, "offline_mode": self.simulator.offline_mode,
                    "cpu_percent": None, "memory_mb": None, "limits": {}}
        process = self._owned_process(workload_id)
        if process is None:
            return None
        alive = process.poll() is None
        cpu = memory = None
        if alive:
            try:
                inspected = psutil.Process(process.pid)
                cpu = inspected.cpu_percent(interval=None)
                memory = inspected.memory_info().rss / 1024**2
            except (psutil.Error, OSError):
                pass
        return {"id": workload_id, "priority": "P0" if workload_id == "demo_service" else "P2",
                "pid": process.pid, "alive": alive, "cpu_percent": cpu, "memory_mb": memory, "limits": {}}

    def list_managed_workloads(self):
        return [info for name in ("demo_service", "stress_worker", "dependency")
                if (info := self.inspect_workload(name)) is not None]

    def check_workload_health(self, workload_id):
        info = self.inspect_workload(workload_id)
        return bool(info and info["alive"])

    def restart_managed_service(self, service_id):
        if service_id != "demo_service" or self.simulator is None or self.simulator.service is None:
            return result("restart_service", service_id, error="Service is not registered")
        try:
            self.simulator.restart_service()
            healthy = self.check_workload_health(service_id)
            return result("restart_service", service_id, True, healthy,
                          "SUCCESSFUL" if healthy else "FAILED", None if healthy else "Health check failed")
        except Exception as exc:
            return result("restart_service", service_id, True, False, "FAILED", str(exc))

    def apply_resource_policy(self, workload_id, policy):
        action = policy.get("action") if isinstance(policy, dict) else None
        if action == "stop_worker" and workload_id == "stress_worker" and self._owned_process(workload_id):
            if not self.check_workload_health(workload_id):
                return result(action, workload_id, error="Managed worker is already stopped")
            try:
                self.simulator.stop_worker()
                stopped = not self.check_workload_health(workload_id)
                return result(action, workload_id, True, stopped, "SUCCESSFUL" if stopped else "FAILED")
            except Exception as exc:
                return result(action, workload_id, True, False, "FAILED", str(exc))
        if action == "offline_mode" and workload_id == "dependency" and self.simulator is not None:
            self.simulator.offline_mode = True
            verified = self.simulator.offline_mode is True
            return result(action, workload_id, True, verified, "SUCCESSFUL" if verified else "FAILED")
        return result(str(action), workload_id, error="Unsupported action or unregistered workload")

    @abstractmethod
    def get_platform_capabilities(self):
        """Return feature -> {status, reason}, with explicit status boundaries."""
