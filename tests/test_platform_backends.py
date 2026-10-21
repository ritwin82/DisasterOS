from pathlib import Path
from types import SimpleNamespace

import pytest

from feature_engineering import feature_vector, normalize_metrics
from platform_backends.factory import create_backend
from platform_backends.linux_backend import LinuxBackend
from platform_backends.windows_backend import WindowsBackend


def test_backend_selection_and_no_cross_host_fallback():
    assert isinstance(create_backend(host="win32", selection="windows"), WindowsBackend)
    with pytest.raises(RuntimeError, match="incompatible"):
        create_backend(host="win32", selection="linux")
    with pytest.raises(RuntimeError, match="incompatible"):
        create_backend(host="linux", selection="windows")


def test_windows_capabilities_are_explicit():
    capabilities = WindowsBackend().get_platform_capabilities()
    assert capabilities["host_metrics"]["status"] == "SUPPORTED"
    assert capabilities["systemd"]["status"] == "NOT_APPLICABLE"
    assert capabilities["cpu_quota"]["status"] == "UNSUPPORTED"


def test_normalized_metric_schema_handles_missing_optional_features():
    assert feature_vector({"cpu": 20, "ram": 30}) == [20, 30, 0, 0, 20, 0, 0, 30, 0, 0, 0, 0]
    assert normalize_metrics({"cpu": 20, "ram": 30})["network_up"] is None
    with pytest.raises(ValueError):
        feature_vector({"cpu": 101})


def linux_backend(monkeypatch, **kwargs):
    import platform_backends.linux_backend as module
    monkeypatch.setattr(module.sys, "platform", "linux")
    return LinuxBackend(**kwargs)


def test_linux_reports_unconfigured_features(monkeypatch):
    backend = linux_backend(monkeypatch)
    capabilities = backend.get_platform_capabilities()
    assert capabilities["cpu_quota"]["status"] in ("UNSUPPORTED", "NOT_CONFIGURED")
    assert capabilities["systemd"]["status"] == "NOT_CONFIGURED"
    outcome = backend.apply_resource_policy("stress_worker", {"action": "cpu_quota", "percent": 50})
    assert not outcome.succeeded
    assert outcome.verification == "FAILED"


def test_systemd_unavailable_even_when_configured(monkeypatch):
    backend = linux_backend(monkeypatch, systemd_service="disasteros-test.service")
    monkeypatch.setattr("platform_backends.linux_backend.shutil.which", lambda _: None)
    assert backend.get_platform_capabilities()["systemd"]["status"] == "UNSUPPORTED"
    assert not backend.restart_managed_service("systemd_test_service").attempted


def test_policy_bounds_and_unregistered_workload(monkeypatch):
    backend = linux_backend(monkeypatch)
    assert not backend.apply_resource_policy("stress_worker", {"action": "cpu_quota", "percent": 1}).attempted
    assert not backend.apply_resource_policy("unknown", {"action": "memory_limit", "memory_mb": 256}).succeeded


def test_controlled_cgroup_policy_and_verification(monkeypatch, tmp_path):
    import platform_backends.linux_backend as module
    mount = tmp_path / "cgroup"
    root = mount / "delegated"
    target = root / "disasteros-123"
    target.mkdir(parents=True)
    for path, value in [(mount / "cgroup.controllers", "cpu memory"),
                        (root / "cgroup.controllers", "cpu memory"),
                        (root / "cgroup.subtree_control", "cpu memory"),
                        (root / "cgroup.procs", ""), (target / "cgroup.procs", ""),
                        (target / "cpu.max", "max 100000"), (target / "memory.max", "max")]:
        path.write_text(value)
    monkeypatch.setattr(module, "CGROUP_MOUNT", mount)
    worker = SimpleNamespace(pid=123, poll=lambda: None)
    simulator = SimpleNamespace(worker=worker, service=None, offline_mode=False)
    backend = linux_backend(monkeypatch, simulator=simulator, cgroup_root=root)
    monkeypatch.setattr(backend, "_cgroup_status", lambda: ("SUPPORTED", "test delegated subtree"))
    # The Windows sandbox permits writes here but denies canonical-path lookup.
    monkeypatch.setattr(type(root), "resolve", lambda self, strict=False: self)
    outcome = backend.apply_resource_policy("stress_worker", {"action": "cpu_quota", "percent": 40})
    assert outcome.succeeded and outcome.verification == "SUCCESSFUL"
    assert (target / "cpu.max").read_text() == "40000 100000"
    assert backend.apply_resource_policy("demo_service", {"action": "memory_limit", "memory_mb": 256}).verification == "FAILED"


def test_cgroup_permission_error_is_reported(monkeypatch):
    backend = linux_backend(monkeypatch)
    monkeypatch.setattr(backend, "_controlled_cgroup", lambda _: (_ for _ in ()).throw(PermissionError("denied")))
    outcome = backend.apply_resource_policy("stress_worker", {"action": "memory_limit", "memory_mb": 256})
    assert outcome.attempted and not outcome.succeeded
    assert "Permission denied" in outcome.error
