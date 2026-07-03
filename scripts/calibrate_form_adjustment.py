from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from wcpredict.repository import Repository
from wcpredict.tournament_form_adjustment import (
    _log_loss,
    build_calibration_samples,
    calibrate_alpha,
)


MODEL_VERSION = "form-adjustment-v4-global-continuous"
# A single global alpha applies to every match where both teams have played
# at least MIN_MATCHES tournament games. The continuous shrinkage weight
# n/(n+3) then grows the effective shift with every extra match played:
# 3 games -> 0.50, 4 -> 0.57, 5 -> 0.625, 6 -> 0.67...
MIN_MATCHES = 2
# Activation: the calibrated alpha must improve log-loss by at least 2%
# relative on the n>=3 validation stratum (user decision 2026-07-03,
# lowering the original 3% given 30+ validation samples).
MIN_RELATIVE_IMPROVEMENT = 0.02


def run_calibration(repository: Repository, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    samples = build_calibration_samples(repository)
    active = [s for s in samples if int(s.get("matches_min", 0)) >= MIN_MATCHES]
    calibration = calibrate_alpha(active, grid_max=2.5)
    validation = [s for s in samples if int(s.get("matches_min", 0)) >= 3]
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
        alphas_json=json.dumps({"global": alpha}),
    )
    return {
        "alpha": alpha,
        "alpha_raw": calibration.alpha,
        "active_samples": len(active),
        "validation_samples": len(validation),
        "validation_improvement_pct": round(100.0 * validation_improvement, 3),
    }


if __name__ == "__main__":
    repo = Repository(Path(__file__).parents[1] / "data" / "worldcup.sqlite")
    repo.initialize()
    print(run_calibration(repo))
