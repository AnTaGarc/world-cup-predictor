"""Backtest: ML clásico vs ensemble ML+DEEP en los WC2026 finalizados."""
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
from wcpredict.outcome_ml import current_match_features, load_outcome_model, match_results_to_feature_rows
from wcpredict.outcome_ml_deep import build_deep_features, load_deep_model


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


def _outcome(ga, gb): return "home" if ga > gb else "away" if gb > ga else "draw"
def _brier(p, a): return sum((p.get(c, 0.0) - (1.0 if c == a else 0.0)) ** 2 for c in ("home", "draw", "away"))


def main():
    repo = Repository(ROOT / "data" / "worldcup.sqlite")
    ml_model = load_outcome_model(ROOT / "data" / "models" / "outcome_ml.joblib")
    deep_model = load_deep_model(ROOT / "data" / "models" / "outcome_ml_deep.joblib")
    completed = [m for m in repo.list_matches()
                 if repo.get_match_result(m.id) is not None
                 and m.competition == "FIFA World Cup 2026"]
    print(f"Partidos: {len(completed)}")
    brier_ml = []
    brier_ensemble = []
    hits_ml = 0
    hits_ensemble = 0
    flips = 0
    for match in sorted(completed, key=lambda m: m.kickoff_utc):
        result = repo.get_match_result(match.id)
        if not result: continue
        a, b, k = match.team_a.name, match.team_b.name, match.kickoff_utc
        results = _results_list(repo, k)
        strengths = {n: {"attack": r.attack, "defense": r.defense}
                     for n, r in build_team_ratings(results, k.date()).items()}
        xg_base = build_xg_form_adjustment(a, b, repo.list_deep_volume_rows_before(k), k, team_strengths=strengths)
        deep_obs = repo.list_deep_team_metric_observations_before(k)
        sm = {n: (r.attack + r.defense) / 2 for n, r in build_team_ratings(results, k.date()).items()}
        pa_p = build_team_profile(a, deep_obs, k, opponent_strengths=sm)
        pb_p = build_team_profile(b, deep_obs, k, opponent_strengths=sm)
        if pa_p.sample_weight > 0 or pb_p.sample_weight > 0:
            pf_a, pf_b, _ = derive_xg_factors_from_profile(pa_p, pb_p)
            xg_form = XgFormAdjustment(xg_base.factor_a*pf_a, xg_base.factor_b*pf_b, 0, 0, "")
        else:
            xg_form = xg_base

        feats = current_match_features(
            repo.list_historical_rows_before(k) + match_results_to_feature_rows(repo.list_match_results_before(k)),
            a, b, match.neutral_site,
        )
        ml_probs = ml_model.predict(feats)
        deep_probs = None
        deep_w = 0.0
        if pa_p.sample_weight >= 3 and pb_p.sample_weight >= 3:
            deep_feats = build_deep_features(feats, pa_p, pb_p)
            try:
                deep_probs = deep_model.predict(deep_feats)
                mn = min(pa_p.sample_weight, pb_p.sample_weight)
                deep_w = min(0.25, max(0.0, (mn - 3.0) / 48.0))
            except Exception:
                pass
        mn_w = min(pa_p.sample_weight, pb_p.sample_weight)
        if mn_w >= 15: ow = 0.65
        elif mn_w >= 5: ow = 0.75
        else: ow = 0.85
        try:
            ml_only = predict_match_markets(a, b, results, k.date(), advanced_form=xg_form,
                                            outcome_probabilities=ml_probs, outcome_weight=ow)
            ensemble = predict_match_markets(a, b, results, k.date(), advanced_form=xg_form,
                                             outcome_probabilities=ml_probs, outcome_weight=ow,
                                             deep_outcome_probabilities=deep_probs, deep_outcome_weight=deep_w)
        except Exception as exc:
            print(f"  fail: {exc}")
            continue
        ml1 = {x.selection_name: x.probability for x in ml_only if x.market_name == "1X2"}
        en1 = {x.selection_name: x.probability for x in ensemble if x.market_name == "1X2"}
        actual = _outcome(result["goals_a"], result["goals_b"])
        ml_p = {"home": ml1.get(a, 0), "draw": ml1.get("Draw", 0), "away": ml1.get(b, 0)}
        en_p = {"home": en1.get(a, 0), "draw": en1.get("Draw", 0), "away": en1.get(b, 0)}
        brier_ml.append(_brier(ml_p, actual))
        brier_ensemble.append(_brier(en_p, actual))
        if max(ml_p, key=ml_p.get) == actual: hits_ml += 1
        if max(en_p, key=en_p.get) == actual: hits_ensemble += 1
        ml_pick = max(ml_p, key=ml_p.get)
        en_pick = max(en_p, key=en_p.get)
        if ml_pick != en_pick:
            flips += 1
            mark = "OK" if en_pick == actual else "NO" if ml_pick == actual else "??"
            print(f"  FLIP[{mark}] {a} vs {b}: ML={ml_pick}({ml_p[ml_pick]*100:.0f}%) -> ENS={en_pick}({en_p[en_pick]*100:.0f}%) | real={actual} | deep_w={deep_w:.2f}")
    n = len(brier_ml)
    print(f"\nBacktest n={n}, flips={flips}")
    print(f"  Brier ML solo:     {sum(brier_ml)/n:.4f}  hits={hits_ml}/{n} ({hits_ml/n*100:.1f}%)")
    print(f"  Brier ML+DEEP ens: {sum(brier_ensemble)/n:.4f}  hits={hits_ensemble}/{n} ({hits_ensemble/n*100:.1f}%)")
    impro = (sum(brier_ml) - sum(brier_ensemble)) / sum(brier_ml) * 100
    print(f"  Mejora Brier: {impro:+.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
