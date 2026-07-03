from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from wcpredict.repository import Repository
from wcpredict.tournament_form_adjustment import (
    build_calibration_samples,
    calibrate_alpha,
)


MODEL_VERSION = "form-adjustment-v1"
# Spec hard criterion: the layer only activates when the calibrated alpha
# improves the pre-match snapshot log-loss by at least 3% relative. Below
# that, alpha persists as 0 and the layer stays inert until more closed
# matches provide stronger evidence.
MIN_RELATIVE_IMPROVEMENT = 0.03


def run_calibration(repository: Repository, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    samples = build_calibration_samples(repository)
    calibration = calibrate_alpha(samples)
    alpha = calibration.alpha
    if calibration.log_loss_base > 0:
        improvement = (
            calibration.log_loss_base - calibration.log_loss_adjusted
        ) / calibration.log_loss_base
        if improvement < MIN_RELATIVE_IMPROVEMENT:
            alpha = 0.0
    repository.save_form_calibration(
        MODEL_VERSION, alpha, calibration.sample_size,
        calibration.log_loss_base, calibration.log_loss_adjusted,
        now.isoformat(),
    )
    return {
        "alpha": alpha,
        "alpha_raw": calibration.alpha,
        "sample_size": calibration.sample_size,
        "log_loss_base": round(calibration.log_loss_base, 5),
        "log_loss_adjusted": round(calibration.log_loss_adjusted, 5),
        "improvement_pct": round(
            100.0 * (calibration.log_loss_base - calibration.log_loss_adjusted)
            / calibration.log_loss_base, 3,
        ) if calibration.log_loss_base else 0.0,
    }


if __name__ == "__main__":
    repo = Repository(Path(__file__).parents[1] / "data" / "worldcup.sqlite")
    repo.initialize()
    print(run_calibration(repo))
