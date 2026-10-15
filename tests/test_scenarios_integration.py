from database import Database
from disaster_simulator import DisasterSimulator


def measured():
    return {
        "ts": "2026-01-01T00:00:00+00:00",
        "cpu": 10.0,
        "ram": 20.0,
        "available_mb": 4096.0,
        "swap": 0.0,
        "disk": 30.0,
        "network_up": 0.0,
        "network_down": 0.0,
    }


def test_every_scenario_is_bounded_and_reports_its_injected_fields(tmp_path):
    simulator = DisasterSimulator(Database(tmp_path / "scenarios.db"))
    try:
        simulator.start("CPU overload")
        cpu = simulator.overlay(measured())
        assert cpu["cpu"] >= 92 and cpu["simulated_fields"] == ["cpu"]
        assert simulator.stop_worker()

        simulator.start("Memory pressure")
        memory = simulator.overlay(measured())
        assert memory["ram"] >= 91 and memory["simulated_fields"] == ["ram"]

        simulator.start("Disk pressure")
        disk = simulator.overlay(measured())
        assert disk["disk"] >= 94 and disk["simulated_fields"] == ["disk"]

        simulator.start("Network failure")
        network = simulator.overlay(measured())
        assert not network["dependency_online"]
        assert network["simulated_fields"] == ["dependency_online"]

        simulator.start("Combined disaster")
        combined = simulator.overlay(measured())
        assert {"cpu", "ram", "disk", "dependency_online"} == set(combined["simulated_fields"])

        simulator.start("Service crash")
        assert not simulator.service_alive()
        assert simulator.restart_service()
        assert simulator.service_alive()
    finally:
        simulator.close()
