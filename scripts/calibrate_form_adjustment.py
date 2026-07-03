from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from wcpredict.repository import Repository
from wcpredict.tournament_form_adjustment import (
    build_calibration_samples,
    calibrate_stratified,
)


MODEL_VERSION = "form-adjustment-v3-per-bucket"


def run_calibration(repository: Repository, now: datetime | None = None) -> dict:
    """Calibrate one alpha per matches-played bucket ("2" and "3plus").

    Each bucket activates independently when its own log-loss improvement
    clears the 3% threshold (see MIN_BUCKET_IMPROVEMENT in the module).
    Teams with fewer than 2 tournament matches never receive an adjustment.
    The `alpha` column keeps the 3plus value for backward compatibility;
    `alphas_json` carries the full per-bucket map used by the UI.
    """
    now = now or datetime.now(timezone.utc)
    samples = build_calibration_samples(repository)
    report = calibrate_stratified(samples)
    alphas = {bucket: data["alpha"] for bucket, data in report.items()}
    main = report.get("3plus", {})
    repository.save_form_calibration(
        MODEL_VERSION,
        float(main.get("alpha", 0.0)),
        int(main.get("sample_size", 0)),
        float(main.get("log_loss_base", 0.0)),
        float(main.get("log_loss_adjusted", 0.0)),
        now.isoformat(),
        alphas_json=json.dumps(alphas),
    )
    return {
        "alphas": alphas,
        "buckets": {
            bucket: {
                "alpha_raw": data["alpha_raw"],
                "n": data["sample_size"],
                "improvement_pct": round(100.0 * data["improvement"], 3),
            }
            for bucket, data in report.items()
        },
        "total_samples": len(samples),
    }


if __name__ == "__main__":
    repo = Repository(Path(__file__).parents[1] / "data" / "worldcup.sqlite")
    repo.initialize()
    print(run_calibration(repo))
