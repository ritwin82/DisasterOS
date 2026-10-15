import subprocess
import sys
import time
from pathlib import Path
from threading import RLock

from config import MAX_MEMORY_MB, SCENARIOS, SIMULATION_SECONDS


class DisasterSimulator:
    def __init__(self, db):
        self.db = db
        self.lock = RLock()
        self.scenario = None
        self.started = 0.0
        self.worker = None
        self.service = None
        self.offline_mode = False
        self._launch_service()

    def _popen(self, mode):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.Popen([sys.executable, str(Path(__file__).with_name("workload.py")), mode,
                                 "--seconds", str(SIMULATION_SECONDS), "--memory-mb", str(MAX_MEMORY_MB)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)

    def _launch_service(self):
        self.service = self._popen("service")
        return self.service

    def _stop_process(self, process):
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def start(self, scenario):
        if scenario not in SCENARIOS:
            raise ValueError("Unknown scenario")
        with self.lock:
            self.stop(log=False)
            self.scenario, self.started = scenario, time.monotonic()
            if scenario in ("CPU overload", "Memory pressure", "Combined disaster"):
                mode = {"CPU overload": "cpu", "Memory pressure": "memory", "Combined disaster": "combined"}[scenario]
                self.worker = self._popen(mode)
            if scenario == "Service crash" and self.service and self.service.poll() is None:
                self._stop_process(self.service)
            self.db.event("WARNING", "Simulation started", scenario, "ACTIVE",
                          details={"worker_pid": self.worker.pid if self.worker else None,
                                   "metrics": "injected scenario signals are labeled simulated"})

    def stop(self, log=True):
        with self.lock:
            old = self.scenario
            self._stop_process(self.worker)
            self.worker = None
            self.scenario = None
            self.offline_mode = False
            if old and log:
                self.db.event("INFO", "Simulation stopped", old, "STOPPED")

    def overlay(self, metrics):
        with self.lock:
            if self.scenario and time.monotonic() - self.started > SIMULATION_SECONDS:
                self.stop()
            result = dict(metrics)
            scenario = self.scenario
            worker_alive = self.worker is not None and self.worker.poll() is None
            if worker_alive and scenario in ("CPU overload", "Combined disaster"):
                result["cpu"] = max(result["cpu"], 92.0)
            if worker_alive and scenario in ("Memory pressure", "Combined disaster"):
                result["ram"] = max(result["ram"], 91.0)
            if scenario in ("Disk pressure", "Combined disaster"):
                result["disk"] = max(result["disk"], 94.0)
            result["dependency_online"] = scenario not in ("Network failure", "Combined disaster")
            result["simulated"] = bool(scenario)
            result["scenario"] = scenario
            result["simulated_fields"] = ([field for field in ("cpu", "ram", "disk") if result[field] != metrics[field]]
                                          + (["dependency_online"] if not result["dependency_online"] else []))
            return result

    def stop_worker(self):
        with self.lock:
            alive = self.worker is not None and self.worker.poll() is None
            if alive:
                self._stop_process(self.worker)
            return alive and self.worker.poll() is not None

    def restart_service(self):
        with self.lock:
            self._stop_process(self.service)
            self._launch_service()
            time.sleep(0.15)
            return self.service.poll() is None

    def service_alive(self):
        with self.lock:
            return self.service is not None and self.service.poll() is None

    def close(self):
        self.stop(log=False)
        with self.lock:
            self._stop_process(self.service)
