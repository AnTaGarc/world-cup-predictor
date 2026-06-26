"""Per-matchup cross features built from two TeamProfiles.

Phase 3 additions: instead of feeding the 1X2 classifier with 9 aggregated
diff features, we add 12 *interaction* features that capture how each
team's strength matches against the rival's specific weakness — for
instance "attacker box-touches × defender clearances", which behaves
very differently from either feature alone.

Function design:

  * ``build_matchup_features(profile_a, profile_b) -> dict[str, float]``
    is pure and deterministic; NaNs propagate so HistGBM's native handling
    deals with missing data.
  * Two profiles are required. When a metric is missing on either side,
    the feature is NaN (HistGBM will route on the comparable cells).
  * Symmetry: we emit BOTH the (a-attacks vs b-defends) and the
    (b-attacks vs a-defends) variants so the model sees both directions.
"""
from __future__ import annotations


MATCHUP_FEATURES = (
    # Attack quality × opponent defensive load (both directions)
    "mu_xg_x_xg_conc_a",        # xg_a × xg_conceded_b
    "mu_xg_x_xg_conc_b",        # xg_b × xg_conceded_a
    "mu_box_x_def_actions_a",   # box_touches_a × defensive_actions_b
    "mu_box_x_def_actions_b",   # box_touches_b × defensive_actions_a
    "mu_shots_x_blocks_a",      # shots_a × blocks_b
    "mu_shots_x_blocks_b",      # shots_b × blocks_a
    # Set pieces (delta of corners-for vs corners-against)
    "mu_corners_diff_a",        # corners_a − corners_conceded_b
    "mu_corners_diff_b",        # corners_b − corners_conceded_a
    # Press vs build-up
    "mu_press_x_passing_a",     # tackles_a / passes_b
    "mu_press_x_passing_b",     # tackles_b / passes_a
    # Possession dominance
    "mu_pos_dominance",         # possession_a − possession_b
    # Synergistic form × production
    "mu_form_x_xg_a",           # form_diff (a−b) × (xg_a − xg_b)
)

# Metric keys aliased to keep this module independent from outcome_ml_deep.
_METRIC_XG = "resumen_del_partido.goles_esperados_xg"
_METRIC_SHOTS = "resumen_del_partido.tiros_totales"
_METRIC_BLOCKS = "tiros.tiros_bloqueados"
_METRIC_CORNERS = "resumen_del_partido.saques_de_esquina"
_METRIC_BOX = "ataque.toques_dentro_del_area"
_METRIC_POS = "resumen_del_partido.posesion_de_balon_pct"
_METRIC_PASSES = "resumen_del_partido.pases"
_METRIC_TACKLES = "defensa.tackles_totales"
_METRIC_INT = "defensa.intercepciones"
_METRIC_CLEAR = "defensa.despejes"


def _safe(value: float | None) -> float:
    return float(value) if value is not None else float("nan")


def _def_actions(profile) -> float:
    parts = [profile.get(m) for m in (_METRIC_TACKLES, _METRIC_INT, _METRIC_CLEAR)]
    parts = [p for p in parts if p is not None]
    return float(sum(parts)) if parts else float("nan")


def _safe_ratio(num: float, den: float) -> float:
    if num != num or den != den:  # NaN check
        return float("nan")
    if den <= 0:
        return float("nan")
    return num / den


def build_matchup_features(profile_a, profile_b, *, form_diff: float = 0.0) -> dict[str, float]:
    """12 interaction features between two TeamProfiles.

    ``form_diff`` comes from the Elo-derived base features (rating_diff
    style); it scales the xG diff into a single synergy signal.
    """
    xg_a = _safe(profile_a.get(_METRIC_XG))
    xg_b = _safe(profile_b.get(_METRIC_XG))
    xg_conc_a = _safe(profile_a.conceded(_METRIC_XG))
    xg_conc_b = _safe(profile_b.conceded(_METRIC_XG))
    box_a = _safe(profile_a.get(_METRIC_BOX))
    box_b = _safe(profile_b.get(_METRIC_BOX))
    def_a = _def_actions(profile_a)
    def_b = _def_actions(profile_b)
    shots_a = _safe(profile_a.get(_METRIC_SHOTS))
    shots_b = _safe(profile_b.get(_METRIC_SHOTS))
    blocks_a = _safe(profile_a.get(_METRIC_BLOCKS))
    blocks_b = _safe(profile_b.get(_METRIC_BLOCKS))
    corners_a = _safe(profile_a.get(_METRIC_CORNERS))
    corners_b = _safe(profile_b.get(_METRIC_CORNERS))
    corners_conc_a = _safe(profile_a.conceded(_METRIC_CORNERS))
    corners_conc_b = _safe(profile_b.conceded(_METRIC_CORNERS))
    pos_a = _safe(profile_a.get(_METRIC_POS))
    pos_b = _safe(profile_b.get(_METRIC_POS))
    passes_a = _safe(profile_a.get(_METRIC_PASSES))
    passes_b = _safe(profile_b.get(_METRIC_PASSES))
    tackles_a = _safe(profile_a.get(_METRIC_TACKLES))
    tackles_b = _safe(profile_b.get(_METRIC_TACKLES))

    return {
        "mu_xg_x_xg_conc_a": xg_a * xg_conc_b,
        "mu_xg_x_xg_conc_b": xg_b * xg_conc_a,
        "mu_box_x_def_actions_a": box_a * def_b,
        "mu_box_x_def_actions_b": box_b * def_a,
        "mu_shots_x_blocks_a": shots_a * blocks_b,
        "mu_shots_x_blocks_b": shots_b * blocks_a,
        "mu_corners_diff_a": corners_a - corners_conc_b,
        "mu_corners_diff_b": corners_b - corners_conc_a,
        "mu_press_x_passing_a": _safe_ratio(tackles_a, passes_b),
        "mu_press_x_passing_b": _safe_ratio(tackles_b, passes_a),
        "mu_pos_dominance": pos_a - pos_b,
        "mu_form_x_xg_a": form_diff * (xg_a - xg_b),
    }
