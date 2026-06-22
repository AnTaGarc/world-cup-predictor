"""Diagnose the systematic bias in predicted xG vs actual goals.

Builds the SAME xG_a, xG_b the production pipeline would compute for each
finished WC 2026 match, then compares against actual scoreline. Reports:
  * Mean of predicted xG vs actual goals (raw bias)
  * Distribution of (actual - predicted) errors
  * Per-team breakdown
  * Whether the bias is xG-side or dispersion-side
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wcpredict.repository import Repository
from wcpredict.team_profile import build_team_profile
from wcpredict.team_volume_markets import derive_xg_factors_from_profile
from wcpredict.advanced_form import build_xg_form_adjustment, XgFormAdjustment
from wcpredict.poisson import score_matrix_negative_binomial, expected_score
from wcpredict.ratings import build_team_ratings, MatchResult
from wcpredict.outcome_ml import match_results_to_feature_rows
from wcpredict.services import (
    expected_goals_for_match, DEFAULT_BASE_GOALS_PER_TEAM,
    DEFAULT_NB_DISPERSION, DEFAULT_DIXON_COLES_RHO,
)


def _results_list(repo, k):
    historical = repo.list_historical_rows_before(k)
    local = repo.list_match_results_before(k)
    out = []
    for r in historical + match_results_to_feature_rows(local):
        if r.get("goals_a") is None or r.get("goals_b") is None: continue
        pa = r.get("played_at_utc") or r.get("played_at") or r.get("kickoff_utc")
        if not pa: continue
        try: dt = datetime.fromisoformat(str(pa).replace("Z", "+00:00"))
        except ValueError: continue
        out.append(MatchResult(
            played_on=dt.date(),
            team_a=str(r.get("team_a_name") or r.get("team_a")),
            team_b=str(r.get("team_b_name") or r.get("team_b")),
            goals_a=int(r["goals_a"]), goals_b=int(r["goals_b"]),
            match_type=str(r.get("tournament") or "friendly"),
        ))
    return out


def main():
    repo = Repository(ROOT / "data" / "worldcup.sqlite")
    completed = [m for m in repo.list_matches()
                 if repo.get_match_result(m.id) is not None
                 and m.competition == "FIFA World Cup 2026"]
    print(f"Partidos finalizados: {len(completed)}")
    print(f"\n{'Partido':40s} {'xG_a':>5} {'xG_b':>5} {'pred_total':>10} {'real_a':>6} {'real_b':>6} {'real_total':>10}")
    print("-" * 95)
    predicted_xg_a = []
    predicted_xg_b = []
    actual_a = []
    actual_b = []
    expected_total = []
    actual_total = []
    for match in sorted(completed, key=lambda m: m.kickoff_utc):
        result = repo.get_match_result(match.id)
        if not result: continue
        a, b, k = match.team_a.name, match.team_b.name, match.kickoff_utc
        results = _results_list(repo, k)
        ratings = build_team_ratings(results, as_of=k.date())
        xg_a, xg_b = expected_goals_for_match(a, b, ratings, base_goals_per_team=DEFAULT_BASE_GOALS_PER_TEAM)
        # Apply same adjustments as production: xg_form + deep profile
        strengths = {n: {"attack": r.attack, "defense": r.defense}
                     for n, r in build_team_ratings(results, k.date()).items()}
        xg_form = build_xg_form_adjustment(a, b, repo.list_deep_volume_rows_before(k), k, team_strengths=strengths)
        deep_obs = repo.list_deep_team_metric_observations_before(k)
        sm = {n: (r.attack + r.defense) / 2 for n, r in build_team_ratings(results, k.date()).items()}
        pa_p = build_team_profile(a, deep_obs, k, opponent_strengths=sm)
        pb_p = build_team_profile(b, deep_obs, k, opponent_strengths=sm)
        if pa_p.sample_weight or pb_p.sample_weight:
            pf_a, pf_b, _ = derive_xg_factors_from_profile(pa_p, pb_p)
        else:
            pf_a = pf_b = 1.0
        xg_a_final = xg_a * xg_form.factor_a * pf_a
        xg_b_final = xg_b * xg_form.factor_b * pf_b
        ga, gb = result["goals_a"], result["goals_b"]
        predicted_xg_a.append(xg_a_final)
        predicted_xg_b.append(xg_b_final)
        actual_a.append(ga); actual_b.append(gb)
        expected_total.append(xg_a_final + xg_b_final)
        actual_total.append(ga + gb)
        print(f"{(a + ' vs ' + b):40s} {xg_a_final:>5.2f} {xg_b_final:>5.2f} {xg_a_final+xg_b_final:>10.2f} {ga:>6} {gb:>6} {ga+gb:>10}")
    n = len(predicted_xg_a)
    print(f"\n========= AGREGADOS (n={n}) =========")
    pred_mean = sum(expected_total) / n
    real_mean = sum(actual_total) / n
    print(f"Predicted total goals (mean): {pred_mean:.3f}")
    print(f"Actual total goals (mean):    {real_mean:.3f}")
    print(f"Bias (actual - predicted):    {real_mean - pred_mean:+.3f}  ({(real_mean - pred_mean) / pred_mean * 100:+.1f}%)")
    print()
    pred_a = sum(predicted_xg_a) / n
    pred_b = sum(predicted_xg_b) / n
    real_a = sum(actual_a) / n
    real_b = sum(actual_b) / n
    print(f"Home xG predicted: {pred_a:.3f}  real: {real_a:.3f}  bias: {real_a - pred_a:+.3f}")
    print(f"Away xG predicted: {pred_b:.3f}  real: {real_b:.3f}  bias: {real_b - pred_b:+.3f}")
    # Distribution of error per match
    errors = [a - p for a, p in zip(actual_total, expected_total)]
    over_pred = sum(1 for e in errors if e > 0)
    under_pred = sum(1 for e in errors if e < 0)
    exact = sum(1 for e in errors if abs(e) < 0.5)
    print(f"\nDistribución error |real - pred|:")
    print(f"  Casi exacto (|<0.5):  {exact}/{n} ({exact/n*100:.0f}%)")
    print(f"  Real > pred:          {over_pred}/{n} ({over_pred/n*100:.0f}%)")
    print(f"  Real < pred:          {under_pred}/{n} ({under_pred/n*100:.0f}%)")
    print(f"\nMAE total: {sum(abs(e) for e in errors)/n:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
