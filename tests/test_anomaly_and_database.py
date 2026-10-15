from anomaly_detector import AnomalyDetector
from database import Database


def metric(value=30):
    return {"cpu": value, "ram": value, "swap": 0, "disk": 40}


def test_insufficient_training_data():
    model = AnomalyDetector(baseline_samples=4)
    result = model.assess(metric())
    assert result["status"] == "LEARNING"
    assert result["score"] is None


def test_anomaly_fallback_on_invalid_metrics():
    result = AnomalyDetector().assess({"cpu": None})
    assert result["status"] == "UNAVAILABLE"
    assert not result["is_anomaly"]


def test_model_trains_without_simulation_contamination():
    model = AnomalyDetector(baseline_samples=4)
    model.assess(metric(90), simulated=True)
    for value in (30, 31, 29, 32):
        result = model.assess(metric(value))
    assert result["status"] in ("NORMAL", "UNUSUAL")
    assert result["samples"] == 4


def test_sqlite_event_persistence(tmp_path):
    path = tmp_path / "events.db"
    Database(path).event("WARNING", "Test event", "Disk pressure", "UNVERIFIED")
    rows = Database(path).events()
    assert len(rows) == 1
    assert rows[0]["action"] == "Test event"
    assert rows[0]["result"] == "UNVERIFIED"
