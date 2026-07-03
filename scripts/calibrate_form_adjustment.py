from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from wcpredict.repository import Repository
from wcpredict.tournament_form_adjustment import (
    _log_loss,
    build_calibration_samples,
    calibrate_alpha,
)


MODEL_VERSION = "form-adjustment-v2-stratified"
# Alpha is calibrated only on matches where the layer meaningfully acts
# (shrinkage weight >= MIN_ACTIVE_WEIGHT, i.e. both teams have >= 2
# tournament matches). Early group-stage matches carry near-zero weight by
# design, so including them dilutes the measured effect without informing it.
MIN_ACTIVE_WEIGHT = 0.25
# Activation criterion: the calibrated alpha must improve log-loss by at
# least 3% relative on the high-weight validation stratum (weight >=
# VALIDATION_WEIGHT: both teams with >= 3 matches, matching the situation
# of every prediction from here on). Otherwise alpha persists at 0.
VALIDATION_WEIGHT = 0.45
MIN_RELATIVE_IMPROVEMENT = 0.03


def run_calibration(repository: Repository, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    samples = build_calibration_samples(repository)
    active = [s for s in samples if s["weight"] >= MIN_ACTIVE_WEIGHT]
    calibration = calibrate_alpha(active)
    validation = [s for s in samples if s["weight"] >= VALIDATION_WEIGHT]
    alpha = calibration.alpha
    validation_base = _log_loss(validation, 0.0) if validation else 0.0
    validation_adjusted = _log_loss(validation, alpha) if validation else 0.0
    validation_improvement = (
        (validation_base - validation_adjusted) / validation_base
        if validation_base > 0 else 0.0
    )
    if not validation or validation_improvement < MIN_RELATIVE_IMPROVEMENT:
        alpha = 0.0
    repository.save_form_calibration(
        MODEL_VERSION, alpha, len(active),
        validation_base, validation_adjusted, now.isoformat(),
    )
    return {
        "alpha": alpha,
        "alpha_raw": calibration.alpha,
        "active_samples": len(active),
        "validation_samples": len(validation),
        "validation_log_loss_base": round(validation_base, 5),
        "validation_log_loss_adjusted": round(validation_adjusted, 5),
        "validation_improvement_pct": round(100.0 * validation_improvement, 3),
    }


if __name__ == "__main__":
    repo = Repository(Path(__file__).parents[1] / "data" / "worldcup.sqlite")
    repo.initialize()
    print(run_calibration(repo))
