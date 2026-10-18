from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
RESULTS = ROOT / "results"
CONTRACTS = ROOT / "contracts"

for p in (RAW, PROCESSED, RESULTS):
    p.mkdir(parents=True, exist_ok=True)
