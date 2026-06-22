"""Walk-forward honesto del ajuste de forma xG frente al modelo base."""

from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wcpredict.advanced_form import build_xg_form_adjustment  # noqa: E402
from wcpredict.names import same_team  # noqa: E402
from wcpredict.ratings import deduplicate_results  # noqa: E402
from wcpredict.repository import Repository  # noqa: E402
from wcpredict.services import predict_match_markets  # noqa: E402


def probabilities(rows) -> dict[str, float]:
    one_x_two = [row for row in rows if row.market_name == "1X2"]
    return {"home": one_x_two[0].probability, "draw": one_x_two[1].probability, "away": one_x_two[2].probability}


def scores(probability: dict[str, float], outcome: str) -> tuple[float, float]:
    actual = {name: float(name == outcome) for name in ("home", "draw", "away")}
    brier = sum((probability[name] - actual[name]) ** 2 for name in actual) / 3.0
    log_loss = -math.log(max(1e-12, probability[outcome]))
    return brier, log_loss


def main() -> int:
    repository = Repository(ROOT / "data" / "worldcup.sqlite")
    matches = repository.list_matches()
    all_results = deduplicate_results(
        repository.list_historical_results_before(datetime(2100, 1, 1, tzinfo=timezone.utc))
    )
    baseline_scores: list[tuple[float, float]] = []
    deep_scores: list[tuple[float, float]] = []
    enhanced = 0
    for match in matches:
        actual = next((row for row in all_results if row.played_on == match.kickoff_utc.date() and same_team(row.team_a, match.team_a.name) and same_team(row.team_b, match.team_b.name)), None)
        if actual is None:
            continue
        prior_results = [row for row in all_results if row.played_on < match.kickoff_utc.date()]
        adjustment = build_xg_form_adjustment(
            match.team_a.name, match.team_b.name,
            repository.list_deep_xg_rows_before(match.kickoff_utc), match.kickoff_utc,
        )
        base = probabilities(predict_match_markets(match.team_a.name, match.team_b.name, prior_results, match.kickoff_utc.date()))
        deep = probabilities(predict_match_markets(match.team_a.name, match.team_b.name, prior_results, match.kickoff_utc.date(), advanced_form=adjustment))
        outcome = "home" if actual.goals_a > actual.goals_b else "away" if actual.goals_b > actual.goals_a else "draw"
        baseline_scores.append(scores(base, outcome))
        deep_scores.append(scores(deep, outcome))
        enhanced += int(adjustment.sample_a > 0 or adjustment.sample_b > 0)
    if not baseline_scores:
        print("Sin partidos evaluables")
        return 2
    average = lambda values, index: sum(row[index] for row in values) / len(values)
    print(
        f"n={len(baseline_scores)} con_xg_previo={enhanced} "
        f"brier_base={average(baseline_scores, 0):.5f} brier_xg={average(deep_scores, 0):.5f} "
        f"logloss_base={average(baseline_scores, 1):.5f} logloss_xg={average(deep_scores, 1):.5f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
