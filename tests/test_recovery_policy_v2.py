from recovery_engine import RecoveryEngine


class DB:
    def __init__(self):
        self.records = []

    def event(self, *args, **kwargs):
        self.records.append((args, kwargs))


class Simulator:
    def __init__(self):
        self.restarts = 0

    def service_alive(self):
        return False

    def restart_service(self):
        self.restarts += 1
        return False


def choice(action="restart_service"):
    return {"action": action, "priority": "P0 Critical", "reason": "test"}


def test_dry_run_does_not_execute():
    sim, db = Simulator(), DB()
    engine = RecoveryEngine(db, sim, dry_run=True)
    assert engine.execute(choice(), {"scenario": "test"})["result"] == "UNVERIFIED"
    assert sim.restarts == 0
    assert db.records[0][0][5]["dry_run"]


def test_action_carries_source_timing_for_benchmarks():
    sim, db = Simulator(), DB()
    engine = RecoveryEngine(db, sim)
    outcome = engine.execute(choice(), {"scenario": "test"})
    assert outcome["started_at"].endswith("+00:00")
    assert outcome["completed_at"].endswith("+00:00")
    assert outcome["completed_monotonic"] >= outcome["started_monotonic"]
    elapsed = (outcome["completed_monotonic"] - outcome["started_monotonic"]) * 1000
    assert abs(outcome["duration_ms"] - elapsed) <= 0.1


def test_cooldown_and_retry_limit():
    sim, db = Simulator(), DB()
    engine = RecoveryEngine(db, sim)
    engine.execute(choice(), {"scenario": "test"})
    engine.execute(choice(), {"scenario": "test"})
    assert sim.restarts == 1
    engine.last_attempt[("test", "restart_service")] -= 31
    engine.execute(choice(), {"scenario": "test"})
    assert sim.restarts == 2
    assert any(args[1] == "Recovery escalated" for args, _ in db.records)
    engine.execute(choice(), {"scenario": "test"})
    assert sim.restarts == 2


def test_unallowlisted_action_is_rejected():
    engine = RecoveryEngine(DB(), Simulator())
    try:
        engine.execute(choice("shell_command"), {})
        assert False
    except ValueError:
        pass


def test_configured_systemd_failure_selects_dedicated_recovery():
    decision = RecoveryEngine.decide({"dependency_online": True}, {"resources": {"Disk": "LOW"}},
                                     service_alive=True, systemd_alive=False)
    assert decision["action"] == "restart_systemd_service"


def test_linux_capability_selects_less_disruptive_limit():
    metrics = {"scenario": "CPU overload", "worker_alive": True, "dependency_online": True}
    risk = {"resources": {"Disk": "LOW"}}
    caps = {"cpu_quota": {"status": "SUPPORTED"}}
    assert RecoveryEngine.decide(metrics, risk, capabilities=caps)["action"] == "cpu_quota"
    assert RecoveryEngine.decide(metrics, risk)["action"] == "stop_worker"
