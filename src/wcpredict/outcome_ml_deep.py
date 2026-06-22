"""Gradient-boosted 1X2 classifier trained on deep-stats features.

Why a second model
------------------
The original ``outcome_ml`` model trains on ~50k historical matches using
only 4 lightweight features (rating_diff, form_diff, goal_diff_form,
neutral_site). The deep-stat profile (~50 metrics per team, recency- and
opponent-weighted, including xG, shots, possession, defensive actions and
goalkeeper) is currently injected only as a multiplicative xG factor on
the score matrix — a heuristic patch that backtesting showed actually
*degrades* 1X2 Brier by ~1.2%.

The fix recommended by the literature is to feed those signals directly
into a classifier and let it learn how much each one matters. This module
trains a ``HistGradientBoostingClassifier`` on the subset of matches that
have a deep profile available for both sides (~3000 with the StatsBomb +
eatpizzanot backfill). The model is small but the features are dense, so
it complements the ~50k-match Elo classifier rather than replacing it.

At inference time both models are combined as a weighted ensemble in
``services.predict_match_markets`` so the rich profile only kicks in for
matches where the data warrants it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import joblib
import numpy as np

# HistGradientBoostingClassifier handles NaN natively, which matters because
# many international friendlies in the back-fill have partial stats.
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.preprocessing import StandardScaler

from wcpredict.names import canonical_team_name
from wcpredict.team_profile import build_team_profile


DEEP_FEATURES = (
    "rating_diff",           # carried over from outcome_ml
    "form_diff",             # carried over
    "goal_diff_form",        # carried over
    "neutral_site",          # carried over
    "xg_created_diff",       # A.xg_created − B.xg_created
    "xg_conceded_diff",      # A.xg_conceded − B.xg_conceded  (lower = better)
    "shots_diff",
    "shots_on_target_diff",
    "possession_diff",
    "corners_diff",
    "cards_diff",
    "defensive_actions_diff",  # tackles + interceptions + clearances
    "sample_min",              # min of both teams' effective profile samples
)
CLASSES = ("home", "draw", "away")


# Metric keys (Spanish, matching METRIC_CATALOG) used to build the feature
# vector from a TeamProfile.
_METRIC_XG = "resumen_del_partido.goles_esperados_xg"
_METRIC_SHOTS = "resumen_del_partido.tiros_totales"
_METRIC_SOT = "tiros.tiros_a_puerta"
_METRIC_POS = "resumen_del_partido.posesion_de_balon_pct"
_METRIC_CORNERS = "resumen_del_partido.saques_de_esquina"
_METRIC_CARDS = "resumen_del_partido.tarjetas_amarillas"
_METRIC_TACKLES = "defensa.tackles_totales"
_METRIC_INT = "defensa.intercepciones"
_METRIC_CLEAR = "defensa.despejes"


def _safe(val: float | None, default: float = float("nan")) -> float:
    return float(val) if val is not None else default


def _defensive_actions(profile) -> float:
    parts = [profile.get(m) for m in (_METRIC_TACKLES, _METRIC_INT, _METRIC_CLEAR)]
    parts = [p for p in parts if p is not None]
    return sum(parts) if parts else float("nan")


def build_deep_features(
    base_features: dict[str, float],
    profile_a,
    profile_b,
) -> dict[str, float]:
    """Combine the base Elo features with deep-profile differential features."""
    xg_a, xg_b = _safe(profile_a.get(_METRIC_XG)), _safe(profile_b.get(_METRIC_XG))
    xg_conc_a, xg_conc_b = _safe(profile_a.conceded(_METRIC_XG)), _safe(profile_b.conceded(_METRIC_XG))
    return {
        "rating_diff": float(base_features.get("rating_diff", 0.0)),
        "form_diff": float(base_features.get("form_diff", 0.0)),
        "goal_diff_form": float(base_features.get("goal_diff_form", 0.0)),
        "neutral_site": float(base_features.get("neutral_site", 1)),
        "xg_created_diff": xg_a - xg_b,
        "xg_conceded_diff": xg_conc_a - xg_conc_b,
        "shots_diff": _safe(profile_a.get(_METRIC_SHOTS)) - _safe(profile_b.get(_METRIC_SHOTS)),
        "shots_on_target_diff": _safe(profile_a.get(_METRIC_SOT)) - _safe(profile_b.get(_METRIC_SOT)),
        "possession_diff": _safe(profile_a.get(_METRIC_POS)) - _safe(profile_b.get(_METRIC_POS)),
        "corners_diff": _safe(profile_a.get(_METRIC_CORNERS)) - _safe(profile_b.get(_METRIC_CORNERS)),
        "cards_diff": _safe(profile_a.get(_METRIC_CARDS)) - _safe(profile_b.get(_METRIC_CARDS)),
        "defensive_actions_diff": _defensive_actions(profile_a) - _defensive_actions(profile_b),
        "sample_min": float(min(profile_a.sample_weight, profile_b.sample_weight)),
    }


@dataclass
class FittedDeepOutcomeModel:
    status: str
    model: HistGradientBoostingClassifier | None = None
    training_cutoff_utc: str | None = None
    validation_cutoff_utc: str | None = None
    sample_size: int = 0
    validation_brier: float | None = None
    reason: str | None = None

    def predict(self, features: dict[str, float]) -> dict[str, float]:
        if self.status != "ready" or self.model is None:
            raise ValueError(self.reason or "Deep outcome model is not ready")
        # Replace NaN-aware: HistGradientBoostingClassifier handles it itself,
        # but the user may pass real values; we feed in feature order.
        x = np.array([[features.get(name, float("nan")) for name in DEEP_FEATURES]])
        proba = self.model.predict_proba(x)[0]
        return {
            str(cls): float(p)
            for cls, p in zip(self.model.classes_, proba)
        }


def save_deep_model(model: FittedDeepOutcomeModel, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    joblib.dump(model, tmp)
    tmp.replace(path)


def load_deep_model(path) -> FittedDeepOutcomeModel:
    obj = joblib.load(path)
    if not isinstance(obj, FittedDeepOutcomeModel):
        raise ValueError("Invalid deep outcome model artifact")
    return obj


def _brier_multiclass(model: HistGradientBoostingClassifier, X: np.ndarray, y: np.ndarray) -> float:
    proba = model.predict_proba(X)
    one_hot = np.zeros_like(proba)
    indexes = {label: i for i, label in enumerate(model.classes_)}
    for i, label in enumerate(y):
        one_hot[i, indexes[label]] = 1.0
    return float(((proba - one_hot) ** 2).sum(axis=1).mean())


def train_deep_outcome_model(
    rows: list[dict[str, Any]],
    minimum_matches: int = 150,
) -> FittedDeepOutcomeModel:
    """Train a HistGradientBoostingClassifier on (features, outcome) rows.

    Each input row must contain the keys in DEEP_FEATURES plus 'outcome'
    (one of 'home', 'draw', 'away') and 'played_at_utc' for chronological
    splitting.
    """
    ordered = sorted(rows, key=lambda r: str(r.get("played_at_utc") or ""))
    if len(ordered) < minimum_matches:
        return FittedDeepOutcomeModel(
            "insufficient_data", sample_size=len(ordered),
            reason=f"Need at least {minimum_matches} matches with deep features",
        )
    outcomes = {r["outcome"] for r in ordered}
    if outcomes != {"home", "draw", "away"}:
        return FittedDeepOutcomeModel(
            "insufficient_data", sample_size=len(ordered),
            reason="Training set missing one of the 1X2 classes",
        )
    split = max(1, int(len(ordered) * 0.80))
    split = min(split, len(ordered) - 5)
    training, validation = ordered[:split], ordered[split:]

    def _xy(seq):
        X = np.array([[r.get(name, float("nan")) for name in DEEP_FEATURES] for r in seq])
        y = np.array([r["outcome"] for r in seq])
        return X, y

    X_train, y_train = _xy(training)
    X_val, y_val = _xy(validation)
    model = HistGradientBoostingClassifier(
        max_iter=400,
        learning_rate=0.05,
        max_depth=6,
        l2_regularization=1.0,
        random_state=42,
    )
    model.fit(X_train, y_train)
    val_brier = _brier_multiclass(model, X_val, y_val)
    return FittedDeepOutcomeModel(
        status="ready",
        model=model,
        training_cutoff_utc=str(training[-1].get("played_at_utc") or ""),
        validation_cutoff_utc=str(validation[-1].get("played_at_utc") or ""),
        sample_size=len(ordered),
        validation_brier=val_brier,
    )
