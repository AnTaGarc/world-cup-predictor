"""Backtest: does the deep-stats profile factor actually help the 1X2?

Compares two regimes over the finished WC 2026 matches:

  BASE: only the simple xg_form (Elo + 9-metric xg history). No factor
        from the rich 50+ metric profile.
  DEEP: BASE multiplied by derive_xg_factors_from_profile (our new ~3000-
        match backfill of asymmetric per-team stats).

Brier score on 1X2 (lower = better), plus an accuracy proxy (matches where
the highest-probability outcome was the actual one).
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
from wcpredict.services import predict_match_markets
from wcpredict.ratings import build_team_ratings, MatchResult
from wcpredict.outcome_ml import match_results_to_feature_rows


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
            goals_a=int(r["goals_a"]), goals_b=int(r["goals_b"]),
            match_type=str(r.get("tournament") or "friendly"),
        ))
    return out


def _outcome_label(ga: int, gb: int) -> str:
    if ga > gb: return "home"
    if ga < gb: return "away"
    return "draw"


def _brier(probs: dict[str, float], actual: str) -> float:
    return sum((probs.get(c, 0.0) - (1.0 if c == actual else 0.0)) ** 2
               for c in ("home", "draw", "away"))


def main() -> int:
    repo = Repository(ROOT / "data" / "worldcup.sqlite")
    completed = [m for m in repo.list_matches()
                 if repo.get_match_result(m.id) is not None
                 and m.competition == "FIFA World Cup 2026"]
    print(f"Partidos: {len(completed)}")
    base_brier: list[float] = []
    deep_brier: list[float] = []
    base_hits = 0
    deep_hits = 0
    for match in sorted(completed, key=lambda m: m.kickoff_utc):
        result = repo.get_match_result(match.id)
        if not result:
            continue
        a, b, k = match.team_a.name, match.team_b.name, match.kickoff_utc
        results = _results_list(repo, k)
        strengths = {n: {"attack": r.attack, "defense": r.defense}
                     for n, r in build_team_ratings(results, k.date()).items()}
        xg_base = build_xg_form_adjustment(
            a, b, repo.list_deep_volume_rows_before(k), k,
            team_strengths=strengths,
        )
        deep_obs = repo.list_deep_team_metric_observations_before(k)
        sm = {n: (r.attack + r.defense) / 2
              for n, r in build_team_ratings(results, k.date()).items()}
        profile_a = build_team_profile(a, deep_obs, k, opponent_strengths=sm)
        profile_b = build_team_profile(b, deep_obs, k, opponent_strengths=sm)
        # DEEP path
        if profile_a.sample_weight > 0 or profile_b.sample_weight > 0:
            pf_a, pf_b, _ = derive_xg_factors_from_profile(profile_a, profile_b)
            xg_deep = XgFormAdjustment(
                factor_a=xg_base.factor_a * pf_a,
                factor_b=xg_base.factor_b * pf_b,
                sample_a=0, sample_b=0, explanation="",
            )
        else:
            xg_deep = xg_base
        try:
            base_preds = predict_match_markets(a, b, results, k.date(), advanced_form=xg_base)
            deep_preds = predict_match_markets(a, b, results, k.date(), advanced_form=xg_deep)
        except Exception:
            continue
        base_1x2 = {x.selection_name: x.probability for x in base_preds if x.market_name == "1X2"}
        deep_1x2 = {x.selection_name: x.probability for x in deep_preds if x.market_name == "1X2"}
        actual = _outcome_label(result["goals_a"], result["goals_b"])
        base_p = {"home": base_1x2.get(a, 0), "draw": base_1x2.get("Draw", 0), "away": base_1x2.get(b, 0)}
        deep_p = {"home": deep_1x2.get(a, 0), "draw": deep_1x2.get("Draw", 0), "away": deep_1x2.get(b, 0)}
        base_brier.append(_brier(base_p, actual))
        deep_brier.append(_brier(deep_p, actual))
        if max(base_p, key=base_p.get) == actual:
            base_hits += 1
        if max(deep_p, key=deep_p.get) == actual:
            deep_hits += 1
        # Show only the matches where the prediction flipped
        base_pick = max(base_p, key=base_p.get)
        deep_pick = max(deep_p, key=deep_p.get)
        if base_pick != deep_pick:
            print(f"  FLIP {a} vs {b}: BASE={base_pick} ({base_p[base_pick]*100:.0f}%) -> DEEP={deep_pick} ({deep_p[deep_pick]*100:.0f}%) | real={actual}")
    n = len(base_brier)
    print(f"\nBacktest n={n}")
    print(f"  Brier BASE (sin deep profile): {sum(base_brier)/n:.4f}")
    print(f"  Brier DEEP (con deep profile): {sum(deep_brier)/n:.4f}")
    print(f"  Mejora: {(sum(base_brier) - sum(deep_brier)) / sum(base_brier) * 100:+.2f}%")
    print(f"  Aciertos del 'pick' (max prob): BASE {base_hits}/{n} = {base_hits/n*100:.1f}%  DEEP {deep_hits}/{n} = {deep_hits/n*100:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
