from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import sys

import requests


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wcpredict.historical_import import read_results_csv  # noqa: E402
from wcpredict.outcome_ml import build_training_rows, save_outcome_model, train_outcome_model  # noqa: E402
from wcpredict.repository import Repository  # noqa: E402


SOURCE_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
CACHE_PATH = ROOT / "data" / "open" / "martj42-results.csv"


def main() -> int:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(SOURCE_URL, timeout=60)
    response.raise_for_status()
    CACHE_PATH.write_bytes(response.content)
    rows = read_results_csv(CACHE_PATH, "martj42")
    repo = Repository(ROOT / "data" / "worldcup.sqlite")
    repo.initialize()
    imported = repo.import_historical_matches(rows)
    training_source = repo.list_historical_rows_before(datetime(2100, 1, 1, tzinfo=timezone.utc))
    fitted = train_outcome_model(build_training_rows(training_source), minimum_matches=60)
    if fitted.status == "ready":
        save_outcome_model(fitted, ROOT / "data" / "models" / "outcome_ml.joblib")
    retrieved = datetime.now(timezone.utc).isoformat()
    with repo.session() as con:
        con.execute(
            "INSERT INTO sources(id, source_type, source_name, source_url, retrieved_at_utc, status, notes) "
            "VALUES('martj42', 'open_dataset', 'International football results', ?, ?, 'verified', ?) "
            "ON CONFLICT(id) DO UPDATE SET source_url=excluded.source_url, retrieved_at_utc=excluded.retrieved_at_utc, status=excluded.status, notes=excluded.notes",
            (SOURCE_URL, retrieved, f"rows={len(rows)} sha256={sha256(response.content).hexdigest()}"),
        )
    print(f"Downloaded {len(response.content)} bytes; parsed {len(rows)} rows; inserted {imported} new rows; model={fitted.status} n={fitted.sample_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
