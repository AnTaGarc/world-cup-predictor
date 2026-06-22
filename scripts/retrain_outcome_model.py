"""Reentrena el contraste ML desde el histórico local canónicamente deduplicado."""

from datetime import datetime, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wcpredict.outcome_ml import build_training_rows, save_outcome_model, train_outcome_model  # noqa: E402
from wcpredict.repository import Repository  # noqa: E402


def main() -> int:
    repository = Repository(ROOT / "data" / "worldcup.sqlite")
    repository.initialize()
    raw = repository.list_historical_rows_before(datetime(2100, 1, 1, tzinfo=timezone.utc))
    rows = build_training_rows(raw)
    fitted = train_outcome_model(rows, minimum_matches=60)
    if fitted.status != "ready":
        print(f"modelo={fitted.status} motivo={fitted.reason}")
        return 2
    save_outcome_model(fitted, ROOT / "data" / "models" / "outcome_ml.joblib")
    print(f"filas_brutas={len(raw)} filas_canónicas={len(rows)} modelo=ready n={fitted.sample_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
