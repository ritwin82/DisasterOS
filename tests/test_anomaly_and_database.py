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


def test_simulated_observation_is_scored_but_not_added_to_training():
    model = AnomalyDetector(baseline_samples=4)
    for value in (30, 31, 29, 32):
        model.assess(metric(value))
    result = model.assess(metric(30), simulated=True, score_sample=metric(99))
    assert result["score"] is not None
    assert result["scored_simulated"] is True
    assert result["samples"] == 4


def test_model_persists_and_reloads_metadata(tmp_path):
    path = tmp_path / "anomaly.joblib"
    model = AnomalyDetector(baseline_samples=4, model_path=path)
    for value in (30, 31, 29, 32):
        model.assess(metric(value))
    loaded = AnomalyDetector(baseline_samples=4, model_path=path)
    result = loaded.assess(metric(30))
    assert path.exists()
    assert result["model_source"] == "loaded"
    assert result["threshold"] is not None
    assert result["feature_count"] == 12


def test_sqlite_event_persistence(tmp_path):
    path = tmp_path / "events.db"
    Database(path).event("WARNING", "Test event", "Disk pressure", "UNVERIFIED")
    rows = Database(path).events()
    assert len(rows) == 1
    assert rows[0]["action"] == "Test event"
    assert rows[0]["result"] == "UNVERIFIED"


def test_clean_metrics_excludes_simulated_rows(tmp_path):
    db = Database(tmp_path / "metrics.db")
    base = {"ts": "2026-01-01", **metric(), "available_mb": 1024,
            "network_up": 0, "network_down": 0, "is_anomaly": 0, "risk": "LOW", "backend": "test"}
    db.metric({**base, "simulated": 0, "measurement_source": "real", "simulated_fields": []})
    db.metric({**base, "simulated": 1, "measurement_source": "mixed", "simulated_fields": ["cpu"]})
    rows = db.clean_metrics()
    assert len(rows) == 1
    assert rows[0]["measurement_source"] == "real"
