from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler


COUNTING = ("minutes", "goals", "assists", "shots", "shots_on_target", "passes", "yellow_cards")
STYLE_FEATURES = ("goals_per90", "assists_per90", "shots_per90", "shots_on_target_per90", "passes_per90")


def build_player_profiles(rows: list[dict[str, Any]], min_minutes: int = 180) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = defaultdict(dict)
    matches: dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        key = (str(row.get("player_name") or ""), str(row.get("team_name") or ""))
        if not key[0]:
            continue
        target = grouped[key]
        target.update({"player_name": key[0], "team_name": key[1]})
        for metric in COUNTING:
            value = row.get(metric)
            if value is None:
                continue
            target[metric] = float(target.get(metric, 0)) + float(value)
        matches[key] += 1
    profiles = []
    for key, row in grouped.items():
        minutes = float(row.get("minutes") or 0)
        if minutes <= 0 or minutes < min_minutes:
            continue
        profile = {**row, "matches": matches[key]}
        for metric in COUNTING[1:]:
            if metric in row:
                profile[f"{metric}_per90"] = float(row[metric]) * 90 / minutes
        profiles.append(profile)
    if not profiles:
        return []
    impact_weights = {
        "goals_per90": 0.40,
        "assists_per90": 0.25,
        "shots_on_target_per90": 0.20,
        "passes_per90": 0.15,
    }
    standardized: dict[str, dict[int, float]] = {}
    for feature in impact_weights:
        available = [(index, float(profile[feature])) for index, profile in enumerate(profiles) if feature in profile]
        if not available:
            continue
        values = np.array([value for _, value in available], dtype=float)
        mean = float(values.mean())
        std = float(values.std()) or 1.0
        standardized[feature] = {index: (value - mean) / std for index, value in available}
    for index, profile in enumerate(profiles):
        available_weights = [weight for feature, weight in impact_weights.items() if index in standardized.get(feature, {})]
        weighted = sum(
            standardized[feature][index] * weight
            for feature, weight in impact_weights.items()
            if index in standardized.get(feature, {})
        )
        discipline = float(profile.get("yellow_cards_per90") or 0)
        profile["impact"] = float(weighted / (sum(available_weights) or 1.0) - 0.05 * discipline)
        profile["available_metrics"] = tuple(
            feature.removesuffix("_per90") for feature in impact_weights if feature in profile
        )
    return sorted(profiles, key=lambda row: (-row["impact"], -row["minutes"], row["player_name"]))


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
