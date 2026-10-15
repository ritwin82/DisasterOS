from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "disasteros.db"
SAMPLE_SECONDS = 2.0
HISTORY_LIMIT = 300
BASELINE_SAMPLES = 20
SIMULATION_SECONDS = 25
MAX_MEMORY_MB = 64
SCENARIOS = ("CPU overload", "Memory pressure", "Disk pressure", "Service crash", "Network failure", "Combined disaster")
