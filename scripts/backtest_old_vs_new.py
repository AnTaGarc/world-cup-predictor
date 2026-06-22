"""Backtest: NEW prediction method vs OLD on the finished WC 2026 matches.

For each completed match we compare what the model would have predicted
(using only data *before* that match) under two regimes:

    OLD: Poisson volume markets, symmetric profile (own + own rival as proxy),
         fixed 80/20 ML/matrix blend.
    NEW: Negative Binomial volume markets, asymmetric profile (own + rival
         conceded), adaptive ML/matrix blend by data quality.

We then compute calibration metrics (Brier for 1X2, MAE for volume
markets) over the set of finished matches.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wcpredict.repository import Repository
from wcpredict.team_profile import build_team_profile
from wcpredict.team_volume_markets import (
    MARKET_CATALOG, _poisson_over, _neg_binomial_over,
    derive_xg_factors_from_profile,
)
from wcpredict.advanced_form import build_xg_form_adjustment, XgFormAdjustment
from wcpredict.services import predict_match_markets
from wcpredict.ratings import build_team_ratings, MatchResult
from wcpredict.outcome_ml import match_results_to_feature_rows


def _strengths(results, d):
    return {n: {"attack": r.attack, "defense": r.defense}
            for n, r in build_team_ratings(results, d).items()}


def _results_list(repo, kickoff):
    historical = repo.list_historical_rows_before(kickoff)
    local = repo.list_match_results_before(kickoff)
    out = []
    for r in historical + match_results_to_feature_rows(local):
        if r.get("goals_a") is None or r.get("goals_b") is None:
            continue
        pa = r.get("played_at_utc") or r.get("played_at") or r.get("kickoff_utc")
        if not pa:
            continue
        try:
            dt = datetime.fromisoformat(str(pa).replace("Z", "+00:00"))
        except ValueError:
            continue
        out.append(MatchResult(
            played_on=dt.date(),
            team_a=str(r.get("team_a_name") or r.get("team_a")),
            team_b=str(r.get("team_b_name") or r.get("team_b")),
            goals_a=int(r["goals_a"]),
            goals_b=int(r["goals_b"]),
            match_type=str(r.get("tournament") or "friendly"),
        ))
    return out


def _brier(probs: dict[str, float], actual: str) -> float:
    """Multi-class Brier score: sum over classes of (p_c - 1_{c=actual})^2."""
    classes = ("home", "draw", "away")
    return sum((probs.get(c, 0.0) - (1.0 if c == actual else 0.0)) ** 2 for c in classes)


def _outcome_label(goals_a: int, goals_b: int) -> str:
    if goals_a > goals_b:
        return "home"
    if goals_a < goals_b:
        return "away"
    return "draw"


def main() -> int:
    repo = Repository(ROOT / "data" / "worldcup.sqlite")
    matches = repo.list_matches()
    completed = [m for m in matches if repo.get_match_result(m.id) is not None
                 and m.competition == "FIFA World Cup 2026"]
    print(f"Partidos finalizados del Mundial 2026: {len(completed)}")
    if not completed:
        return 1

    brier_old: list[float] = []
    brier_new: list[float] = []

    # Per-team market MAE: actual minus predicted
    market_metrics: dict[str, dict[str, list[float]]] = {
        m: {"old_ae": [], "new_ae": []} for m in MARKET_CATALOG
    }

    for match in sorted(completed, key=lambda m: m.kickoff_utc):
        result = repo.get_match_result(match.id)
        if result is None:
            continue
        a, b, k = match.team_a.name, match.team_b.name, match.kickoff_utc
        results = _results_list(repo, k)
        strengths = _strengths(results, k.date())
        deep_obs = repo.list_deep_team_metric_observations_before(k)
        sm = {n: (r.attack + r.defense) / 2
              for n, r in build_team_ratings(results, k.date()).items()}
        profile_a = build_team_profile(a, deep_obs, k, opponent_strengths=sm)
        profile_b = build_team_profile(b, deep_obs, k, opponent_strengths=sm)
        xg_form_base = build_xg_form_adjustment(
            a, b, repo.list_deep_volume_rows_before(k), k, team_strengths=strengths,
        )
        if profile_a.sample_weight > 0 or profile_b.sample_weight > 0:
            pf_a, pf_b, _ = derive_xg_factors_from_profile(profile_a, profile_b)
            xg_form = XgFormAdjustment(
                factor_a=xg_form_base.factor_a * pf_a,
                factor_b=xg_form_base.factor_b * pf_b,
                sample_a=0, sample_b=0, explanation="",
            )
        else:
            xg_form = xg_form_base

        # 1X2 under OLD (80/20) and NEW (adaptive) blends
        try:
            old_preds = predict_match_markets(
                a, b, results, k.date(),
                advanced_form=xg_form, outcome_weight=0.80,
            )
            min_w = min(profile_a.sample_weight, profile_b.sample_weight)
            new_weight = 0.65 if min_w >= 15 else 0.75 if min_w >= 5 else 0.85
            new_preds = predict_match_markets(
                a, b, results, k.date(),
                advanced_form=xg_form, outcome_weight=new_weight,
            )
        except Exception as exc:
            print(f"  predict failed {a} vs {b}: {exc}")
            continue
        old_1x2 = {x.selection_name: x.probability for x in old_preds if x.market_name == "1X2"}
        new_1x2 = {x.selection_name: x.probability for x in new_preds if x.market_name == "1X2"}
        actual = _outcome_label(result["goals_a"], result["goals_b"])
        # Normalize to home/draw/away
        old_probs = {"home": old_1x2.get(a, 0.0), "draw": old_1x2.get("Draw", 0.0), "away": old_1x2.get(b, 0.0)}
        new_probs = {"home": new_1x2.get(a, 0.0), "draw": new_1x2.get("Draw", 0.0), "away": new_1x2.get(b, 0.0)}
        brier_old.append(_brier(old_probs, actual))
        brier_new.append(_brier(new_probs, actual))

        # Volume markets: compare expected vs actual (both Poisson-old and NB-new)
        actual_stats = {row["team_id"]: row for row in repo.list_team_match_stats(match.id)}
        team_a_id = repo.upsert_team(a)
        team_b_id = repo.upsert_team(b)
        actual_a = actual_stats.get(team_a_id, {})
        actual_b = actual_stats.get(team_b_id, {})
        # Map our metric key → actual column in team_match_stats
        actual_col_for = {
            "resumen_del_partido.saques_de_esquina": "corners",
            "resumen_del_partido.tarjetas_amarillas": "yellow_cards",
            "resumen_del_partido.tiros_totales": "shots",
            "tiros.tiros_a_puerta": "shots_on_target",
            # fouls/offsides aren't stored in team_match_stats, skip in MAE.
        }
        for market_id, spec in MARKET_CATALOG.items():
            metric = spec["metric"]
            col = actual_col_for.get(metric)
            if col is None:
                continue
            for actual_row, profile, other in (
                (actual_a, profile_a, profile_b),
                (actual_b, profile_b, profile_a),
            ):
                actual_val = actual_row.get(col)
                if actual_val is None:
                    continue
                own = profile.get(metric)
                # OLD: symmetric (other.get); NEW: asymmetric (other.conceded fallback to .get)
                opp_old = other.get(metric)
                opp_new = other.conceded(metric) if other.conceded(metric) is not None else other.get(metric)
                tmean = next((est.tournament_mean for est in profile.metrics.values()
                              if est.metric == metric), spec["tournament_default"])
                if not (own and opp_old and tmean):
                    continue
                lambda_old = 0.45 * own + 0.30 * opp_old + 0.25 * tmean
                lambda_new = 0.45 * own + 0.30 * opp_new + 0.25 * tmean
                market_metrics[market_id]["old_ae"].append(abs(actual_val - lambda_old))
                market_metrics[market_id]["new_ae"].append(abs(actual_val - lambda_new))

    print(f"\nMatches en backtest: {len(brier_old)}")
    print(f"\n=== Brier score 1X2 (más bajo = mejor) ===")
    if brier_old:
        avg_old = sum(brier_old) / len(brier_old)
        avg_new = sum(brier_new) / len(brier_new)
        improvement = (avg_old - avg_new) / avg_old * 100
        print(f"  OLD (blend fijo 80/20): {avg_old:.4f}")
        print(f"  NEW (blend adaptativo): {avg_new:.4f}")
        print(f"  Mejora relativa: {improvement:+.2f}%")
    print(f"\n=== Mean Absolute Error mercados por equipo ===")
    print(f"{'Mercado':25s} {'OLD MAE':>10} {'NEW MAE':>10} {'mejora':>10} {'n':>5}")
    for market_id, metrics in market_metrics.items():
        n = len(metrics["old_ae"])
        if n == 0:
            continue
        old_mae = sum(metrics["old_ae"]) / n
        new_mae = sum(metrics["new_ae"]) / n
        impro = (old_mae - new_mae) / old_mae * 100 if old_mae > 0 else 0
        print(f"{market_id:25s} {old_mae:>10.3f} {new_mae:>10.3f} {impro:>+9.2f}% {n:>5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
