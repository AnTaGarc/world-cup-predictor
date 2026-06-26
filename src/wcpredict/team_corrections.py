"""Per-team / per-market calibration corrections from past residuals.

After Phase 5 the pipeline tracks how much each team's predictions
systematically over- or under-shoot reality, and applies a small shift
(Bayesian-shrunk) at inference time. The shifts are kept TINY by design:
prediction snapshots are noisy with N<=3-5 matches, so without strong
shrinkage we would chase noise.

Inputs come from ``backtest_runs`` rows persisted by
``scripts.backtest_replay`` (one row per match × market × selection).

Public surface:

  * ``compute_team_market_shifts(rows, *, prior_strength=5.0, min_n=3)``
    returns ``{(team_name, market): shrunk_shift}``.
  * ``team_outcome_logit_shifts(rows, ...)`` returns per-team 1X2 log-prob
    shifts ready to feed ``apply_outcome_shifts``.
  * ``describe_team_shifts(...)`` builds human-readable lines for the UI.
"""
from __future__ import annotations

from collections import defaultdict
import math


def _shrink(measured: float, n: int, prior_strength: float) -> float:
    if n <= 0:
        return 0.0
    weight = n / (n + prior_strength)
    return measured * weight


def compute_team_market_shifts(
    rows: list[dict],
    *,
    prior_strength: float = 5.0,
    min_n: int = 3,
    market_filter: tuple[str, ...] | None = None,
) -> dict[tuple[str, str], float]:
    """Mean residual (prob_predicted − outcome_observed) per team×market,
    shrunk toward zero with a Bayesian prior.

    Each row is expected to contain ``team_a, team_b, market, selection,
    prob_predicted, outcome_observed``. For 1X2 we attribute the
    residual to the team named in ``selection``; for symmetric markets
    (BTTS, O/U) we credit BOTH teams equally.
    """
    by_team_market_local: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        market = str(row.get("market") or "")
        if market_filter and market not in market_filter:
            continue
        prob = float(row.get("prob_predicted") or 0.0)
        outcome = int(row.get("outcome_observed") or 0)
        residual = prob - outcome
        team_a = str(row.get("team_a") or "")
        team_b = str(row.get("team_b") or "")
        selection = str(row.get("selection") or "")
        if market == "1X2":
            # The residual belongs to whichever team was named.
            target = selection
            if target in (team_a, team_b):
                by_team_market_local[(target, market)].append(residual)
        else:
            for team in (team_a, team_b):
                if team:
                    by_team_market_local[(team, market)].append(residual)

    shifts: dict[tuple[str, str], float] = {}
    for key, residuals in by_team_market_local.items():
        n = len(residuals)
        if n < min_n:
            continue
        mean = sum(residuals) / n
        shifts[key] = _shrink(mean, n, prior_strength)
    return shifts


def team_outcome_logit_shifts(
    rows: list[dict],
    *,
    prior_strength: float = 5.0,
    min_n: int = 3,
) -> dict[str, dict[str, float]]:
    """1X2 log-prob shifts per team derived from ``compute_team_market_shifts``.

    Returns ``{team_name: {home: s_home, draw: s_draw, away: s_away}}``
    using a simple mapping: positive residual = overestimating that team
    on home/away → negative log shift. ``draw`` shifts are inferred from
    the residual of the team's own draw row when present, otherwise 0.
    """
    raw = compute_team_market_shifts(
        rows, prior_strength=prior_strength, min_n=min_n,
        market_filter=("1X2",),
    )
    per_team: dict[str, dict[str, float]] = defaultdict(lambda: {"home": 0.0, "draw": 0.0, "away": 0.0})
    for (team, _market), shift in raw.items():
        # If team's win prob over-predicts (shift>0) we lower its win logit.
        # Same residual is split between home/away depending on context; here
        # we keep a single team-level adjustment and let the runtime resolve
        # which slot to apply (the caller knows if team is home or away).
        per_team[team]["__own__"] = -shift  # negative residual means upshift
    return dict(per_team)


def describe_team_shifts(shifts: dict[tuple[str, str], float], *, top: int = 6) -> list[str]:
    """Human-readable summary lines for the UI."""
    ranked = sorted(shifts.items(), key=lambda kv: -abs(kv[1]))[:top]
    out: list[str] = []
    for (team, market), shift in ranked:
        if abs(shift) < 1e-3:
            continue
        direction = "↓" if shift > 0 else "↑"
        out.append(f"{team} · {market}: modelo {direction}{abs(shift)*100:.1f}pp")
    return out
