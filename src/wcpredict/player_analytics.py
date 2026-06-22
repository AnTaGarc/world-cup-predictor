"""Per-player aggregation and impact scoring.

The Impacto score is a position-aware 0-100 percentile. Each player is
compared only against others in the same positional group, then their
weighted score in that group is converted to a percentile so the value
is intuitive (60 = top 40% of strikers; 95 = top 5% of defenders).

Two important properties:
 * The score does NOT depend on the UI's "minimum minutes" slider — that
   slider only filters the displayed list. Internally we always score
   against the same reference pool (all players with >= MIN_MINUTES_REF).
 * Per-90 rates are shrunk toward the position mean when minutes are low
   to prevent the "10-minute substitute scored 1 goal → goals/90 = 9 →
   impact 19" artefact the user spotted.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler


COUNTING = (
    "minutes", "goals", "assists", "shots", "shots_on_target", "passes",
    "yellow_cards", "tackles_won", "interceptions",
)
STYLE_FEATURES = ("goals_per90", "assists_per90", "shots_per90", "shots_on_target_per90", "passes_per90")

# Reference minutes — players with fewer total minutes than this are still
# scored (so they appear in the ranking when the slider is at 0) but their
# per-90 rates are shrunk toward the position mean for stability.
MIN_MINUTES_REF = 60
# Number of "prior matches" (90' worth) used to shrink per-90 rates.
SHRINKAGE_MATCHES = 1.5


def _position_group(position: str | None) -> str:
    """Bucket the FBref-style position string into one of 4 groups."""
    if not position:
        return "MID"
    p = str(position).upper()
    if "GK" in p:
        return "GK"
    if "DF" in p and "FW" not in p:
        return "DEF"
    if "FW" in p:
        return "ATT"
    return "MID"


# Per-position scoring rules. Each rule is (per-90 metric key, weight).
# Weights must sum to ~1 per group. Tackles + interceptions are summed into
# a single "defensive actions" key on the fly.
POSITION_WEIGHTS: dict[str, dict[str, float]] = {
    "ATT": {
        "goals_per90": 0.35,
        "assists_per90": 0.20,
        "shots_on_target_per90": 0.20,
        "shots_per90": 0.15,
        "passes_per90": 0.10,
    },
    "MID": {
        "assists_per90": 0.25,
        "passes_per90": 0.25,
        "defensive_actions_per90": 0.20,
        "goals_per90": 0.15,
        "shots_on_target_per90": 0.15,
    },
    "DEF": {
        "defensive_actions_per90": 0.45,
        "passes_per90": 0.30,
        "assists_per90": 0.10,
        "goals_per90": 0.10,
        "shots_on_target_per90": 0.05,
    },
    "GK": {
        # Save percentage is already a percentage (0-100), not a per-90 rate.
        "save_percentage": 0.55,
        "passes_per90": 0.25,
        "interceptions_per90": 0.20,
    },
}


def build_player_profiles(rows: list[dict[str, Any]], min_minutes: int = 60) -> list[dict[str, Any]]:
    """Aggregate raw per-match rows into per-player profiles with Impacto.

    Parameters
    ----------
    min_minutes : int
        Filter applied to the *returned* list (UI slider). Does NOT change
        the impact computation — that always uses the full eligible pool
        so the user sees stable scores when moving the slider.
    """
    grouped: dict[tuple[str, str], dict[str, Any]] = defaultdict(dict)
    matches: dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        key = (str(row.get("player_name") or ""), str(row.get("team_name") or ""))
        if not key[0]:
            continue
        target = grouped[key]
        target.update({"player_name": key[0], "team_name": key[1]})
        # Position can come from any row; keep the first non-empty value.
        if "position" not in target and row.get("position"):
            target["position"] = row.get("position")
        if "save_percentage" not in target and row.get("save_percentage") is not None:
            target["save_percentage"] = row.get("save_percentage")
        for metric in COUNTING:
            value = row.get(metric)
            if value is None:
                continue
            target[metric] = float(target.get(metric, 0)) + float(value)
        matches[key] += 1

    profiles = []
    for key, row in grouped.items():
        minutes = float(row.get("minutes") or 0)
        if minutes <= 0:
            continue
        profile = {**row, "matches": matches[key]}
        profile["position_group"] = _position_group(row.get("position"))
        # Raw per-90 rates (will be shrunk below).
        for metric in COUNTING[1:]:
            if metric in row:
                profile[f"{metric}_per90"] = float(row[metric]) * 90 / minutes
        # Combined defensive actions per 90 (tackles + interceptions).
        tackles = float(row.get("tackles_won") or 0)
        intercep = float(row.get("interceptions") or 0)
        profile["defensive_actions_per90"] = (tackles + intercep) * 90 / minutes
        profile["save_percentage"] = float(row.get("save_percentage") or 0)
        profiles.append(profile)
    if not profiles:
        return []

    # ---- Per-position percentile scoring ----
    # Group profiles by position for separate normalisation, but only those
    # with at least MIN_MINUTES_REF total minutes count toward the reference
    # distribution. Below that they are *scored* via shrinkage but don't
    # distort the percentile cutoffs of regular starters.
    reference_pools: dict[str, list[dict]] = defaultdict(list)
    for p in profiles:
        if p["minutes"] >= MIN_MINUTES_REF:
            reference_pools[p["position_group"]].append(p)
    # Compute per-position mean of each metric (for shrinkage + percentile).
    position_means: dict[str, dict[str, float]] = {}
    for pos, ref_list in reference_pools.items():
        means = {}
        for metric in set().union(*[POSITION_WEIGHTS[g].keys() for g in POSITION_WEIGHTS]):
            vals = [float(p.get(metric, 0)) for p in ref_list if metric in p]
            means[metric] = sum(vals) / len(vals) if vals else 0.0
        position_means[pos] = means

    # Shrink per-90 rates: stabilized = (minutes/90 * raw + SHRINKAGE_MATCHES * mean)
    #                                   / (minutes/90 + SHRINKAGE_MATCHES)
    for p in profiles:
        pos = p["position_group"]
        if pos not in position_means:
            # Fallback when nobody in this position made the reference cut.
            continue
        match_eq = p["minutes"] / 90.0
        for metric in POSITION_WEIGHTS.get(pos, {}):
            mean = position_means[pos].get(metric, 0.0)
            raw = float(p.get(metric, 0))
            stabilized = (match_eq * raw + SHRINKAGE_MATCHES * mean) / (match_eq + SHRINKAGE_MATCHES)
            p[f"{metric}_shrunk"] = stabilized

    # Compute percentile rank per position per metric using the reference pool.
    for pos, ref_list in reference_pools.items():
        for metric in POSITION_WEIGHTS.get(pos, {}):
            ref_values = sorted(p.get(f"{metric}_shrunk", 0.0) for p in ref_list)
            n = len(ref_values)
            if n == 0:
                continue
            # For all profiles in this position, rank their shrunk value vs
            # the reference distribution.
            for p in profiles:
                if p["position_group"] != pos:
                    continue
                val = p.get(f"{metric}_shrunk", 0.0)
                # Percentile = fraction of reference players this player beats.
                rank = sum(1 for v in ref_values if v < val) + 0.5 * sum(1 for v in ref_values if v == val)
                p[f"{metric}_pct"] = rank / n  # 0-1

    # Final impact = weighted sum of percentiles × 100, with discipline bonus.
    for p in profiles:
        pos = p["position_group"]
        weights = POSITION_WEIGHTS.get(pos, {})
        if not weights or pos not in position_means:
            p["impact"] = 0.0
            continue
        score = 0.0
        used_weight = 0.0
        for metric, w in weights.items():
            pct = p.get(f"{metric}_pct")
            if pct is None:
                continue
            score += w * pct
            used_weight += w
        impact_0_100 = (score / used_weight * 100.0) if used_weight else 0.0
        # Discipline penalty: subtract up to 5 points for high yellow-card rate.
        ycards = float(p.get("yellow_cards_per90") or 0)
        # Cap at 1 card/90 worth (=full -5 penalty).
        impact_0_100 -= 5.0 * min(1.0, ycards)
        # Reliability adjustment: if minutes < 90, slightly pull impact toward 50.
        if p["minutes"] < 90:
            reliability = max(0.5, p["minutes"] / 90.0)
            impact_0_100 = reliability * impact_0_100 + (1 - reliability) * 50.0
        p["impact"] = round(impact_0_100, 1)
        p["available_metrics"] = tuple(
            m.replace("_per90", "").replace("_pct", "")
            for m in weights if f"{m}_pct" in p
        )

    # ---- Apply UI filter ONLY now ----
    filtered = [p for p in profiles if p["minutes"] >= min_minutes]
    return sorted(filtered, key=lambda row: (-row["impact"], -row["minutes"], row["player_name"]))


def cluster_player_styles(profiles: list[dict[str, Any]], requested_clusters: int = 4) -> list[dict[str, Any]]:
    if len(profiles) < max(4, requested_clusters * 2):
        return []
    cluster_count = min(requested_clusters, max(2, len(profiles) // 2))
    usable_features = tuple(name for name in STYLE_FEATURES if all(name in profile for profile in profiles))
    if not usable_features:
        return []
    matrix = np.array([[float(profile[name]) for name in usable_features] for profile in profiles])
    scaled = StandardScaler().fit_transform(matrix)
    model = KMeans(n_clusters=cluster_count, random_state=42, n_init=20)
    labels = model.fit_predict(scaled)
    feature_labels = ("Finalizador", "Creador", "Rematador", "Precisión de tiro", "Distribuidor")
    names = {}
    for cluster_id, centroid in enumerate(model.cluster_centers_):
        names[cluster_id] = dict(zip(STYLE_FEATURES, feature_labels))[usable_features[int(np.argmax(centroid))]]
    return [{**profile, "style_cluster": int(label), "style_label": names[int(label)]} for profile, label in zip(profiles, labels)]
