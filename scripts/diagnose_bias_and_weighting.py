"""Diagnostico:
  1) Bias home/away — ¿el modelo da sistemáticamente más probabilidad a team_a?
  2) Peso de los partidos del Mundial en el perfil — ¿cuánto cuenta cada uno?
"""
from __future__ import annotations
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wcpredict.repository import Repository
from wcpredict.team_profile import build_team_profile, _competition_weight, _recency_weight, METRIC_CATALOG
from wcpredict.team_volume_markets import derive_xg_factors_from_profile
from wcpredict.advanced_form import build_xg_form_adjustment, XgFormAdjustment
from wcpredict.services import predict_match_markets
from wcpredict.ratings import build_team_ratings, MatchResult
from wcpredict.outcome_ml import current_match_features, load_outcome_model, match_results_to_feature_rows
from wcpredict.outcome_ml_deep import build_deep_features, load_deep_model


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


def main():
    repo = Repository(ROOT / "data" / "worldcup.sqlite")
    ml_model = load_outcome_model(ROOT / "data" / "models" / "outcome_ml.joblib")
    deep_model = load_deep_model(ROOT / "data" / "models" / "outcome_ml_deep.joblib")
    completed = [m for m in repo.list_matches()
                 if repo.get_match_result(m.id) is not None
                 and m.competition == "FIFA World Cup 2026"]

    # ===== 1) Home/Away bias =====
    print("===== 1) Sesgo team_a vs team_b en predicciones del modelo =====\n")
    sum_p_home = sum_p_away = sum_p_draw = 0.0
    actual_home = actual_away = actual_draw = 0
    n = 0
    for match in completed:
        result = repo.get_match_result(match.id)
        if not result: continue
        a, b, k = match.team_a.name, match.team_b.name, match.kickoff_utc
        results = _results_list(repo, k)
        strengths = {nm: {"attack": r.attack, "defense": r.defense}
                     for nm, r in build_team_ratings(results, k.date()).items()}
        xg_base = build_xg_form_adjustment(a, b, repo.list_deep_volume_rows_before(k), k, team_strengths=strengths)
        deep_obs = repo.list_deep_team_metric_observations_before(k)
        sm = {nm: (r.attack + r.defense) / 2 for nm, r in build_team_ratings(results, k.date()).items()}
        pa_p = build_team_profile(a, deep_obs, k, opponent_strengths=sm)
        pb_p = build_team_profile(b, deep_obs, k, opponent_strengths=sm)
        if pa_p.sample_weight or pb_p.sample_weight:
            pf_a, pf_b, _ = derive_xg_factors_from_profile(pa_p, pb_p)
            xg_form = XgFormAdjustment(xg_base.factor_a*pf_a, xg_base.factor_b*pf_b, 0, 0, "")
        else:
            xg_form = xg_base
        feats = current_match_features(
            repo.list_historical_rows_before(k) + match_results_to_feature_rows(repo.list_match_results_before(k)),
            a, b, match.neutral_site,
        )
        ml_probs = ml_model.predict(feats)
        deep_probs = None; deep_w = 0.0
        if pa_p.sample_weight >= 3 and pb_p.sample_weight >= 3:
            df = build_deep_features(feats, pa_p, pb_p)
            try:
                deep_probs = deep_model.predict(df)
                mn = min(pa_p.sample_weight, pb_p.sample_weight)
                deep_w = min(0.25, max(0.0, (mn - 3.0) / 48.0))
            except Exception:
                pass
        mn_w = min(pa_p.sample_weight, pb_p.sample_weight)
        ow = 0.65 if mn_w >= 15 else 0.75 if mn_w >= 5 else 0.85
        try:
            preds = predict_match_markets(a, b, results, k.date(), advanced_form=xg_form,
                                          outcome_probabilities=ml_probs, outcome_weight=ow,
                                          deep_outcome_probabilities=deep_probs, deep_outcome_weight=deep_w)
        except Exception:
            continue
        probs = {x.selection_name: x.probability for x in preds if x.market_name == "1X2"}
        sum_p_home += probs.get(a, 0)
        sum_p_draw += probs.get("Draw", 0)
        sum_p_away += probs.get(b, 0)
        ga, gb = result["goals_a"], result["goals_b"]
        if ga > gb: actual_home += 1
        elif ga < gb: actual_away += 1
        else: actual_draw += 1
        n += 1
    print(f"Partidos: {n}")
    print(f"\n             {'PREDICHO promedio':>18s}  {'REAL frecuencia':>17s}")
    print(f"  team_a:    {sum_p_home/n*100:>17.1f}%  {actual_home/n*100:>16.1f}%")
    print(f"  draw:      {sum_p_draw/n*100:>17.1f}%  {actual_draw/n*100:>16.1f}%")
    print(f"  team_b:    {sum_p_away/n*100:>17.1f}%  {actual_away/n*100:>16.1f}%")
    print(f"\n  bias team_a: {sum_p_home/n*100 - actual_home/n*100:+.1f}pp")
    print(f"  bias team_b: {sum_p_away/n*100 - actual_away/n*100:+.1f}pp")

    # ===== 2) Peso de partidos WC en el perfil =====
    print("\n\n===== 2) % del peso del perfil que viene de partidos del Mundial 2026 =====\n")
    as_of = datetime(2026, 6, 23, tzinfo=timezone.utc)
    all_obs = repo.list_deep_team_metric_observations_before(as_of)
    half_life = 540
    teams_to_check = ['Spain', 'France', 'Argentina', 'Brazil', 'Germany', 'England',
                       'Morocco', 'Japan', 'Mexico', 'USA', 'New Zealand', 'Cape Verde']
    print(f"{'Equipo':15s} {'muestra':>8s} {'%WC':>6s}  {'partidos_WC':>11s} {'partidos_otros':>15s}")
    for team in teams_to_check:
        own_rows = [r for r in all_obs if str(r.get('team_name') or '').lower() == team.lower()]
        if not own_rows: continue
        # Build a small per-match aggregate to see weight by competition
        per_match = {}
        for r in own_rows:
            metric = str(r.get('metric') or '')
            if metric not in METRIC_CATALOG: continue
            ko = str(r.get('kickoff_utc') or '')
            if not ko: continue
            played = datetime.fromisoformat(ko.replace('Z', '+00:00'))
            if played.tzinfo is None: played = played.replace(tzinfo=timezone.utc)
            w_rec = _recency_weight(played, as_of, half_life)
            w_comp = _competition_weight(str(r.get('competition') or ''))
            w = w_rec * w_comp
            per_match.setdefault(ko, {'comp': str(r.get('competition') or ''), 'w': w})
        total_w = sum(m['w'] for m in per_match.values())
        wc_w = sum(m['w'] for m in per_match.values() if 'World Cup 2026' in m['comp'])
        n_wc = sum(1 for m in per_match.values() if 'World Cup 2026' in m['comp'])
        n_other = len(per_match) - n_wc
        pct = wc_w / total_w * 100 if total_w else 0
        print(f"{team:15s} {total_w:>8.2f} {pct:>5.1f}% {n_wc:>11d} {n_other:>15d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
