from recovery_engine import RecoveryEngine


class FakeSimulator:
    def __init__(self, restart_ok=True):
        self.alive = False
        self.restart_ok = restart_ok
        self.offline_mode = False
        self.restarts = 0

    def service_alive(self):
        return self.alive

    def restart_service(self):
        self.restarts += 1
        self.alive = self.restart_ok
        return self.alive

    def stop_worker(self):
        return True


class FakeDB:
    def __init__(self):
        self.events = []

    def event(self, *args, **kwargs):
        self.events.append((args, kwargs))


def sample(**kwargs):
    return {"scenario": None, "dependency_online": True, "worker_alive": False, **kwargs}


def risk(disk="LOW"):
    return {"resources": {"Disk": disk}}


def test_recovery_policy_selects_service_first():
    choice = RecoveryEngine.decide(sample(dependency_online=False), risk("HIGH"), service_alive=False)
    assert choice["action"] == "restart_service"


def test_recovery_policy_selects_owned_worker():
    choice = RecoveryEngine.decide({"scenario": "CPU overload", "worker_alive": True, "dependency_online": True}, risk())
    assert choice["action"] == "stop_worker"


def test_recovery_policy_advances_after_offline_fallback():
    metrics = sample(dependency_online=False, offline_mode=True)
    assert RecoveryEngine.decide(metrics, risk(), service_alive=True)["action"] == "none"
    assert RecoveryEngine.decide(metrics, risk("HIGH"), service_alive=True)["action"] == "disk_alert"


def test_failed_recovery_is_not_claimed_successful_or_retried_forever():
    simulator, db = FakeSimulator(restart_ok=False), FakeDB()
    engine = RecoveryEngine(db, simulator)
    decision = engine.decide(sample(), risk(), service_alive=False)
    outcome = engine.execute(decision, sample())
    assert outcome["result"] == "FAILED"
    engine.execute(decision, sample())
    assert simulator.restarts == 1
    assert db.events[0][0][3] == "FAILED"


def test_disk_recommendation_is_unverified():
    engine = RecoveryEngine(FakeDB(), FakeSimulator())
    decision = engine.decide(sample(), risk("HIGH"), service_alive=True)
    assert engine.execute(decision, sample())["result"] == "UNVERIFIED"
