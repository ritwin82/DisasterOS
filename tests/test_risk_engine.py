from risk_engine import RiskEngine


def metrics(cpu=20, ram=35, disk=40):
    return {"cpu": cpu, "ram": ram, "disk": disk}


def test_normal_usage():
    result = RiskEngine().assess(metrics())
    assert result["category"] == "LOW"
    assert all(level == "LOW" for level in result["resources"].values())


def test_critical_memory_pressure():
    result = RiskEngine().assess(metrics(ram=97))
    assert result["category"] == "CRITICAL"
    assert result["resources"]["Memory"] == "CRITICAL"


def test_cpu_overload():
    assert RiskEngine().assess(metrics(cpu=89))["resources"]["CPU"] == "HIGH"


def test_disk_pressure():
    assert RiskEngine().assess(metrics(disk=93))["resources"]["Disk"] == "HIGH"


def test_combined_pressure_escalates():
    assert RiskEngine().assess(metrics(cpu=88, ram=89))["category"] == "CRITICAL"
