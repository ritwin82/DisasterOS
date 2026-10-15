"""Linux execution is confined to registered child processes and an optional user service."""
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, build_opener

from platform_backends.base import PlatformBackend, result


CGROUP_MOUNT = Path("/sys/fs/cgroup")
UNIT_PATTERN = re.compile(r"^disasteros-[A-Za-z0-9_.-]+\.service$")


class LinuxBackend(PlatformBackend):
    name = "linux"

    def __init__(self, simulator=None, cgroup_root=None, systemd_service=None, health_url=None):
        if not sys.platform.startswith("linux"):
            raise RuntimeError("Linux backend cannot execute on a non-Linux host")
        super().__init__(simulator)
        self.cgroup_root = Path(cgroup_root or os.getenv("DISASTEROS_CGROUP_ROOT", "")) if (cgroup_root or os.getenv("DISASTEROS_CGROUP_ROOT")) else None
        self.systemd_service = systemd_service or os.getenv("DISASTEROS_SYSTEMD_SERVICE")
        self.health_url = health_url or os.getenv("DISASTEROS_SERVICE_HEALTH_URL")
        self._last_systemd_restart = 0.0
        self._systemd_attempts = 0
        self._owned_cgroups = set()

    def _cgroup_status(self):
        if not (CGROUP_MOUNT / "cgroup.controllers").exists():
            return "UNSUPPORTED", "cgroups v2 is unavailable"
        if self.cgroup_root is None:
            return "NOT_CONFIGURED", "Set DISASTEROS_CGROUP_ROOT to a delegated subtree"
        try:
            root = self.cgroup_root.resolve(strict=True)
            mount = CGROUP_MOUNT.resolve(strict=True)
            if root == mount or not root.is_relative_to(mount):
                return "UNSUPPORTED", "Configured root must be a dedicated subtree under /sys/fs/cgroup"
            if self.cgroup_root.is_symlink() or not (root / "cgroup.procs").exists():
                return "UNSUPPORTED", "Invalid cgroup subtree"
            available = (root / "cgroup.controllers").read_text().split()
            enabled = (root / "cgroup.subtree_control").read_text().split()
            if not {"cpu", "memory"}.issubset(available) or not {"cpu", "memory"}.issubset(enabled):
                return "UNSUPPORTED", "cpu and memory controllers must be delegated and enabled"
            if not os.access(root, os.W_OK):
                return "PERMISSION_DENIED", "Configured cgroup subtree is not writable by this user"
            return "SUPPORTED", "Dedicated delegated cgroups v2 subtree"
        except PermissionError:
            return "PERMISSION_DENIED", "Cannot inspect configured cgroup subtree"
        except (OSError, ValueError) as exc:
            return "UNSUPPORTED", str(exc)

    def _systemd_status(self):
        if not self.systemd_service:
            return "NOT_CONFIGURED", "Set DISASTEROS_SYSTEMD_SERVICE to an explicit test unit"
        if not UNIT_PATTERN.fullmatch(self.systemd_service):
            return "UNSUPPORTED", "Only disasteros-*.service user units are allowed"
        if not Path("/run/systemd/system").exists() or not shutil.which("systemctl"):
            return "UNSUPPORTED", "systemd is not active in this Linux environment"
        try:
            probe = subprocess.run(["systemctl", "--user", "show", self.systemd_service, "--property=LoadState", "--value"],
                                   capture_output=True, text=True, timeout=3, check=False)
            if probe.returncode and "permission" in probe.stderr.lower():
                return "PERMISSION_DENIED", probe.stderr.strip()[:200]
            if probe.returncode or probe.stdout.strip() != "loaded":
                return "NOT_CONFIGURED", "Configured user test service is not loaded"
            return "SUPPORTED", "Dedicated user systemd service is loaded"
        except (OSError, subprocess.TimeoutExpired) as exc:
            return "UNSUPPORTED", str(exc)

    def get_platform_capabilities(self):
        cg_state, cg_reason = self._cgroup_status()
        sd_state, sd_reason = self._systemd_status()
        return {
            "host_metrics": {"status": "SUPPORTED", "reason": "psutil with Linux process inspection"},
            "managed_process_recovery": {"status": "SUPPORTED" if self.simulator else "NOT_CONFIGURED", "reason": "Application-owned children only"},
            "cpu_quota": {"status": cg_state, "reason": cg_reason},
            "memory_limit": {"status": cg_state, "reason": cg_reason},
            "systemd": {"status": sd_state, "reason": sd_reason},
        }

    def _controlled_cgroup(self, workload_id):
        status, reason = self._cgroup_status()
        if status != "SUPPORTED":
            raise RuntimeError(f"{status}: {reason}")
        if workload_id != "stress_worker":
            raise ValueError("Resource limits are allowed only for the managed stress worker")
        process = self._owned_process(workload_id)
        if process is None or process.poll() is not None:
            raise ValueError("Managed stress worker is not running")
        root = self.cgroup_root.resolve(strict=True)
        target = root / f"disasteros-{process.pid}"
        if target.exists() and (target.is_symlink() or not target.is_dir()):
            raise ValueError("Unsafe cgroup target")
        if target.exists() and (target / "cgroup.procs").exists():
            members = (target / "cgroup.procs").read_text().split()
            if members and members != [str(process.pid)]:
                raise ValueError("Existing cgroup contains an unrelated process")
        target.mkdir(exist_ok=True)
        if target.resolve() != target or target.parent.resolve() != root:
            raise ValueError("Cgroup target escaped delegated root")
        (target / "cgroup.procs").write_text(str(process.pid))
        members = (target / "cgroup.procs").read_text().split()
        if str(process.pid) not in members:
            raise RuntimeError("Worker was not attached to its cgroup")
        self._owned_cgroups.add(target)
        return target

    def apply_resource_policy(self, workload_id, policy):
        action = policy.get("action") if isinstance(policy, dict) else None
        if action not in ("cpu_quota", "memory_limit"):
            outcome = super().apply_resource_policy(workload_id, policy)
            if action == "stop_worker":
                self.cleanup_managed_resources()
            return outcome
        amount = policy.get("percent" if action == "cpu_quota" else "memory_mb")
        valid = isinstance(amount, int) and not isinstance(amount, bool) and (10 <= amount <= 100 if action == "cpu_quota" else 128 <= amount <= 1024)
        if not valid:
            return result(action, workload_id, error="Policy value outside allowed bounds")
        try:
            target = self._controlled_cgroup(workload_id)
            filename = "cpu.max" if action == "cpu_quota" else "memory.max"
            desired = f"{amount * 1000} 100000" if action == "cpu_quota" else str(amount * 1024 * 1024)
            (target / filename).write_text(desired)
            actual = (target / filename).read_text().strip()
            verified = actual == desired and self.check_workload_health(workload_id)
            return result(action, workload_id, True, verified, "SUCCESSFUL" if verified else "FAILED",
                          None if verified else "Limit or workload health verification failed",
                          {"requested": desired, "actual": actual, "cgroup": str(target)})
        except PermissionError as exc:
            return result(action, workload_id, True, False, "FAILED", f"Permission denied: {exc}")
        except (OSError, RuntimeError, ValueError) as exc:
            return result(action, workload_id, True, False, "FAILED", str(exc))

    def cleanup_managed_resources(self):
        for target in tuple(self._owned_cgroups):
            try:
                if target.name.startswith("disasteros-") and target.parent.resolve() == self.cgroup_root.resolve() and not target.is_symlink():
                    target.rmdir()  # cgroup pseudo-files do not count as ordinary directory entries
                    self._owned_cgroups.discard(target)
            except OSError:
                pass

    def inspect_workload(self, workload_id):
        if workload_id == "systemd_test_service":
            status, reason = self._systemd_status()
            if status != "SUPPORTED":
                return {"id": workload_id, "priority": "P0", "alive": None, "status": status, "reason": reason,
                        "cpu_percent": None, "memory_mb": None, "limits": {}}
            probe = subprocess.run(["systemctl", "--user", "is-active", self.systemd_service],
                                   capture_output=True, text=True, timeout=3, check=False)
            return {"id": workload_id, "priority": "P0", "alive": probe.returncode == 0,
                    "status": probe.stdout.strip(), "cpu_percent": None, "memory_mb": None, "limits": {}}
        info = super().inspect_workload(workload_id)
        if info and workload_id == "stress_worker" and self.cgroup_root and info.get("pid"):
            target = self.cgroup_root / f'disasteros-{info["pid"]}'
            if target.is_dir() and not target.is_symlink():
                for name in ("cpu.max", "memory.max"):
                    try:
                        info["limits"][name] = (target / name).read_text().strip()
                    except OSError:
                        pass
        return info

    def list_managed_workloads(self):
        return super().list_managed_workloads() + ([self.inspect_workload("systemd_test_service")] if self.systemd_service else [])

    def _health_url_ok(self):
        if not self.health_url:
            return True
        parsed = urlparse(self.health_url)
        if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("Health URL must use loopback HTTP")
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, request, fp, code, msg, headers, newurl):
                return None

        with build_opener(NoRedirect).open(self.health_url, timeout=2) as response:
            return 200 <= response.status < 300

    def restart_managed_service(self, service_id):
        if service_id != "systemd_test_service":
            return super().restart_managed_service(service_id)
        status, reason = self._systemd_status()
        if status != "SUPPORTED":
            return result("restart_service", service_id, error=f"{status}: {reason}")
        if time.monotonic() - self._last_systemd_restart < 30 or self._systemd_attempts >= 2:
            return result("restart_service", service_id, error="Cooldown or retry limit reached")
        if self.health_url:
            try:
                self._health_url_ok()  # validates destination before restart
            except ValueError as exc:
                return result("restart_service", service_id, error=str(exc))
            except OSError:
                pass  # unhealthy is expected before recovery
        self._last_systemd_restart = time.monotonic()
        self._systemd_attempts += 1
        try:
            command = subprocess.run(["systemctl", "--user", "restart", self.systemd_service],
                                     capture_output=True, text=True, timeout=10, check=False)
            if command.returncode:
                return result("restart_service", service_id, True, False, "FAILED", command.stderr.strip()[:300])
            active = self.inspect_workload(service_id)["alive"]
            healthy = active and self._health_url_ok()
            return result("restart_service", service_id, True, bool(healthy),
                          "SUCCESSFUL" if healthy else "FAILED", None if healthy else "Service health check failed")
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            return result("restart_service", service_id, True, False, "FAILED", str(exc))
