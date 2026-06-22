from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from collections import defaultdict, deque

import numpy as np
import joblib
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from wcpredict.names import canonical_team_name


FEATURES = ("rating_diff", "form_diff", "goal_diff_form", "neutral_site")
CLASSES = ("home", "draw", "away")


def deduplicate_historical_rows(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple, dict[str, Any]] = {}
    for original in sorted(matches, key=lambda row: str(row["played_at_utc"])):
        row = dict(original)
        team_a = canonical_team_name(str(row["team_a"]))
        team_b = canonical_team_name(str(row["team_b"]))
        goals_a, goals_b = int(row["goals_a"]), int(row["goals_b"])
        played_on = str(row["played_at_utc"])[:10]
        if team_a <= team_b:
            key = (played_on, team_a, team_b, goals_a, goals_b)
        else:
            key = (played_on, team_b, team_a, goals_b, goals_a)
        row.update(team_a=team_a, team_b=team_b, goals_a=goals_a, goals_b=goals_b)
        unique.setdefault(key, row)
    return sorted(unique.values(), key=lambda row: str(row["played_at_utc"]))


def _outcome(goals_a: int, goals_b: int) -> str:
    return "home" if goals_a > goals_b else "away" if goals_b > goals_a else "draw"


def _replay(matches: list[dict[str, Any]], target: tuple[str, str] | None = None, neutral_site: bool = True):
    ratings: dict[str, float] = defaultdict(lambda: 1500.0)
    points: dict[str, deque] = defaultdict(lambda: deque(maxlen=5))
    goal_diffs: dict[str, deque] = defaultdict(lambda: deque(maxlen=5))
    output = []
    for match in deduplicate_historical_rows(matches):
        team_a, team_b = str(match["team_a"]), str(match["team_b"])
        features = {
            "rating_diff": (ratings[team_a] - ratings[team_b]) / 400.0,
            "form_diff": (sum(points[team_a]) / len(points[team_a]) if points[team_a] else 1.0) - (sum(points[team_b]) / len(points[team_b]) if points[team_b] else 1.0),
            "goal_diff_form": (sum(goal_diffs[team_a]) / len(goal_diffs[team_a]) if goal_diffs[team_a] else 0.0) - (sum(goal_diffs[team_b]) / len(goal_diffs[team_b]) if goal_diffs[team_b] else 0.0),
            "neutral_site": int(bool(match.get("neutral_site", True))),
        }
        goals_a, goals_b = int(match["goals_a"]), int(match["goals_b"])
        result = _outcome(goals_a, goals_b)
        output.append({**features, "played_at_utc": str(match["played_at_utc"]), "outcome": result})
        actual_a = 1.0 if result == "home" else 0.5 if result == "draw" else 0.0
        expected_a = 1 / (1 + 10 ** ((ratings[team_b] - ratings[team_a]) / 400))
        delta = 24 * (actual_a - expected_a)
        ratings[team_a] += delta
        ratings[team_b] -= delta
        points_a, points_b = (3, 0) if result == "home" else (0, 3) if result == "away" else (1, 1)
        points[team_a].append(points_a)
        points[team_b].append(points_b)
        goal_diffs[team_a].append(goals_a - goals_b)
        goal_diffs[team_b].append(goals_b - goals_a)
    if target is None:
        return output
    team_a, team_b = canonical_team_name(target[0]), canonical_team_name(target[1])
    return {
        "rating_diff": (ratings[team_a] - ratings[team_b]) / 400.0,
        "form_diff": (sum(points[team_a]) / len(points[team_a]) if points[team_a] else 1.0) - (sum(points[team_b]) / len(points[team_b]) if points[team_b] else 1.0),
        "goal_diff_form": (sum(goal_diffs[team_a]) / len(goal_diffs[team_a]) if goal_diffs[team_a] else 0.0) - (sum(goal_diffs[team_b]) / len(goal_diffs[team_b]) if goal_diffs[team_b] else 0.0),
        "neutral_site": int(neutral_site),
    }


def build_training_rows(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _replay(matches)


def current_match_features(matches: list[dict[str, Any]], team_a: str, team_b: str, neutral_site: bool = True) -> dict[str, float]:
    return _replay(matches, (team_a, team_b), neutral_site)


def match_results_to_feature_rows(results: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "played_at_utc": result.played_on.isoformat(),
            "team_a": result.team_a,
            "team_b": result.team_b,
            "goals_a": result.goals_a,
            "goals_b": result.goals_b,
            "tournament": result.match_type,
            "neutral_site": 1,
        }
        for result in results
    ]


@dataclass
class FittedOutcomeModel:
    status: str
    model: LogisticRegression | None = None
    scaler: StandardScaler | None = None
    temperature: float = 1.0
    training_cutoff_utc: str | None = None
    validation_cutoff_utc: str | None = None
    sample_size: int = 0
    reason: str | None = None

    def predict(self, features: dict[str, Any]) -> dict[str, float]:
        if self.status != "ready" or self.model is None or self.scaler is None:
            raise ValueError(self.reason or "Outcome model is not ready")
        x = self.scaler.transform([[float(features[name]) for name in FEATURES]])
        logits = self.model.decision_function(x)
        if logits.ndim == 1:
            logits = np.column_stack([-logits, logits])
        logits = logits[0] / self.temperature
        probabilities = np.exp(logits - np.max(logits))
        probabilities = probabilities / probabilities.sum()
        mapped = {str(name): float(value) for name, value in zip(self.model.classes_, probabilities)}
        return {name: mapped.get(name, 0.0) for name in CLASSES}


def save_outcome_model(model: FittedOutcomeModel, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    joblib.dump(model, temporary)
    temporary.replace(path)


def load_outcome_model(path) -> FittedOutcomeModel:
    model = joblib.load(path)
    if not isinstance(model, FittedOutcomeModel):
        raise ValueError("Invalid outcome model artifact")
    return model


def _negative_log_likelihood(logits: np.ndarray, y: np.ndarray, classes: np.ndarray, temperature: float) -> float:
    scaled = logits / temperature
    scaled -= scaled.max(axis=1, keepdims=True)
    probabilities = np.exp(scaled)
    probabilities /= probabilities.sum(axis=1, keepdims=True)
    indexes = {label: index for index, label in enumerate(classes)}
    selected = np.array([probabilities[i, indexes[label]] for i, label in enumerate(y)])
    return float(-np.log(np.clip(selected, 1e-12, 1)).mean())


def train_outcome_model(rows: list[dict[str, Any]], minimum_matches: int = 60) -> FittedOutcomeModel:
    ordered = sorted(rows, key=lambda row: str(row["played_at_utc"]))
    if len(ordered) < minimum_matches or len({row.get("outcome") for row in ordered}) < 3:
        return FittedOutcomeModel("insufficient_data", sample_size=len(ordered), reason="Se necesitan más partidos cronológicos y las tres clases 1X2")
    split = max(1, int(len(ordered) * 0.75))
    split = min(split, len(ordered) - 3)
    training, validation = ordered[:split], ordered[split:]
    if len({row["outcome"] for row in training}) < 3:
        return FittedOutcomeModel("insufficient_data", sample_size=len(ordered), reason="El tramo de entrenamiento no contiene las tres clases")
    scaler = StandardScaler()
    x_train = scaler.fit_transform([[float(row[name]) for name in FEATURES] for row in training])
    y_train = np.array([row["outcome"] for row in training])
    model = LogisticRegression(max_iter=1000, random_state=42)
    model.fit(x_train, y_train)
    x_validation = scaler.transform([[float(row[name]) for name in FEATURES] for row in validation])
    logits = model.decision_function(x_validation)
    if logits.ndim == 1:
        logits = np.column_stack([-logits, logits])
    y_validation = np.array([row["outcome"] for row in validation])
    temperatures = np.linspace(0.5, 3.0, 101)
    temperature = min(temperatures, key=lambda value: _negative_log_likelihood(logits.copy(), y_validation, model.classes_, float(value)))
    return FittedOutcomeModel(
        "ready", model, scaler, float(temperature),
        str(training[-1]["played_at_utc"]), str(validation[-1]["played_at_utc"]), len(ordered),
    )
