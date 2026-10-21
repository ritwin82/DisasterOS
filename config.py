from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "disasteros.db"
MODEL_PATH = ROOT / "anomaly_model.joblib"
SAMPLE_SECONDS = 2.0
HISTORY_LIMIT = 300
BASELINE_SAMPLES = 500
MODEL_VERSION = 3
MODEL_CALIBRATION_FRACTION = 0.2
ANOMALY_FALSE_POSITIVE_RATE = 0.01
BENCHMARK_TRIALS = 5
SIMULATION_SECONDS = 25
MAX_MEMORY_MB = 64
SCENARIOS = ("CPU overload", "Memory pressure", "Disk pressure", "Service crash", "Network failure", "Combined disaster")
