"""Compare xG/score-matrix calibrations using the WC 2026 results.

For each finished WC 2026 match we precompute the per-match base xG_a and
xG_b (with all the production adjustments) ONCE, then evaluate several
candidate (base_goals, dispersion, rho) combinations against the actual
scoreline. This is ~7x faster than recomputing the profiles for every
config.
"""
from __future__ import annotations
import math, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wcpredict.repository import Repository
from wcpredict.team_profile import build_team_profile
from wcpredict.team_volume_markets import derive_xg_factors_from_profile
from wcpredict.advanced_form import build_xg_form_adjustment
from wcpredict.poisson import score_matrix_negative_binomial, summarize_score_matrix, most_probable_score
from wcpredict.ratings import build_team_ratings, MatchResult, expected_goals_for_match
from wcpredict.outcome_ml import match_results_to_feature_rows


def _results_list(repo, k):
    rows = repo.list_historical_rows_before(k) + match_results_to_feature_rows(repo.list_match_results_before(k))
    out = []
    for r in rows:
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


# Precomputed cache: list of dicts with raw_xg_a, raw_xg_b (no base factor),
# actual_a, actual_b. Then each calibration just scales by base.
def precompute(repo):
    completed = [m for m in repo.list_matches()
                 if repo.get_match_result(m.id) is not None
                 and m.competition == "FIFA World Cup 2026"]
    items = []
    for i, match in enumerate(completed, 1):
        if i % 10 == 0:
            print(f"  precomputed {i}/{len(completed)}")
        result = repo.get_match_result(match.id)
        if not result: continue
        a, b, k = match.team_a.name, match.team_b.name, match.kickoff_utc
        results = _results_list(repo, k)
        ratings = build_team_ratings(results, as_of=k.date())
        # xG with base=1.0 so we can re-scale by any candidate base later.
        xg_a_unit, xg_b_unit = expected_goals_for_match(a, b, ratings, base_goals_per_team=1.0)
        strengths = {n: {"attack": r.attack, "defense": r.defense}
                     for n, r in ratings.items()}
        xg_form = build_xg_form_adjustment(a, b, repo.list_deep_volume_rows_before(k), k, team_strengths=strengths)
        deep_obs = repo.list_deep_team_metric_observations_before(k)
        sm = {n: (r.attack + r.defense) / 2 for n, r in ratings.items()}
        pa_p = build_team_profile(a, deep_obs, k, opponent_strengths=sm)
        pb_p = build_team_profile(b, deep_obs, k, opponent_strengths=sm)
        if pa_p.sample_weight or pb_p.sample_weight:
            pf_a, pf_b, _ = derive_xg_factors_from_profile(pa_p, pb_p)
        else:
            pf_a = pf_b = 1.0
        items.append({
            "label": f"{a} vs {b}",
            "raw_a": xg_a_unit * xg_form.factor_a * pf_a,
            "raw_b": xg_b_unit * xg_form.factor_b * pf_b,
            "ga": result["goals_a"],
            "gb": result["goals_b"],
        })
    return items


CONFIGS = {
    "baseline_1.35":      {"base": 1.35, "dispersion": 0.08, "rho": -0.10},
    "raise_1.45":         {"base": 1.45, "dispersion": 0.08, "rho": -0.10},
    "raise_1.50":         {"base": 1.50, "dispersion": 0.08, "rho": -0.10},
    "raise_1.55":         {"base": 1.55, "dispersion": 0.08, "rho": -0.10},
    "raise_1.60":         {"base": 1.60, "dispersion": 0.08, "rho": -0.10},
    "raise_1.60_disp.20": {"base": 1.60, "dispersion": 0.20, "rho": -0.10},
    "raise_1.60_disp.30": {"base": 1.60, "dispersion": 0.30, "rho": -0.10},
    "raise_1.55_disp.15": {"base": 1.55, "dispersion": 0.15, "rho": -0.10},
}


def evaluate(items, base, dispersion, rho):
    pred_totals, actual_totals = [], []
    exact_hits = 0
    btts_ll = 0.0
    over25_ll = 0.0
    btts_n = 0
    for it in items:
        xg_a = it["raw_a"] * base
        xg_b = it["raw_b"] * base
        ga, gb = it["ga"], it["gb"]
        pred_totals.append(xg_a + xg_b)
        actual_totals.append(ga + gb)
        try:
            matrix = score_matrix_negative_binomial(xg_a, xg_b, dispersion=dispersion, rho=rho, max_goals=10)
            mps = most_probable_score(matrix)
            if mps.team_a_goals == ga and mps.team_b_goals == gb:
                exact_hits += 1
            s = summarize_score_matrix(matrix, total_line=2.5)
            ab = 1 if (ga > 0 and gb > 0) else 0
            ao = 1 if (ga + gb > 2.5) else 0
            btts_ll += -(ab * math.log(max(s.both_teams_to_score, 1e-9)) + (1 - ab) * math.log(max(1 - s.both_teams_to_score, 1e-9)))
            over25_ll += -(ao * math.log(max(s.over_total, 1e-9)) + (1 - ao) * math.log(max(1 - s.over_total, 1e-9)))
            btts_n += 1
        except Exception:
            pass
    n = len(pred_totals)
    return {
        "mae_total": sum(abs(p - a) for p, a in zip(pred_totals, actual_totals)) / n,
        "bias_total": sum(actual_totals) / n - sum(pred_totals) / n,
        "exact_pct": exact_hits / n * 100,
        "btts_ll": btts_ll / btts_n if btts_n else float("nan"),
        "over25_ll": over25_ll / btts_n if btts_n else float("nan"),
    }


def main():
    repo = Repository(ROOT / "data" / "worldcup.sqlite")
    print("Precomputando perfiles...")
    items = precompute(repo)
    print(f"Listo: {len(items)} partidos.\n")
    print(f"{'Configuracion':22s} {'MAE':>5} {'bias':>6} {'exact%':>7} {'BTTS LL':>8} {'O2.5 LL':>8}")
    print("-" * 65)
    for name, cfg in CONFIGS.items():
        r = evaluate(items, **cfg)
        print(f"{name:22s} {r['mae_total']:>5.2f} {r['bias_total']:>+6.2f} {r['exact_pct']:>6.1f}% {r['btts_ll']:>8.4f} {r['over25_ll']:>8.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
