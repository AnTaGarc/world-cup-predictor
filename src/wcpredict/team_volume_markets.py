"""Per-team Poisson predictions for volume markets (corners, cards, shots, …).

For each (team, metric) we build the Poisson mean as a weighted blend of:

* The team's own historical rate (offensive/creative bias, e.g. "Spain creates
  many corners").
* The opponent's "conceded" rate for that metric (defensive bias of the rival).
* The tournament-wide mean (strong prior since per-team samples are tiny).

Then we report ``over/under`` probabilities at common lines.

Used by services.predict_match_markets and rendered as a new "Mercados por
equipo" panel in the Predicción y Valor page.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from wcpredict.team_profile import TeamProfile


# Maps user-facing market id → (metric in team_profile, default lines).
# Lines are tuned to common bookmaker offerings for international football.
MARKET_CATALOG: dict[str, dict] = {
    "corners": {
        "metric": "resumen_del_partido.saques_de_esquina",
        "label": "Corners",
        "lines": (2.5, 3.5, 4.5, 5.5),
        "tournament_default": 4.5,
    },
    "yellow_cards": {
        "metric": "resumen_del_partido.tarjetas_amarillas",
        "label": "Tarjetas amarillas",
        "lines": (0.5, 1.5, 2.5, 3.5),
        "tournament_default": 1.8,
    },
    "shots_total": {
        "metric": "resumen_del_partido.tiros_totales",
        "label": "Tiros totales",
        "lines": (7.5, 9.5, 11.5, 13.5),
        "tournament_default": 11.0,
    },
    "shots_on_target": {
        "metric": "tiros.tiros_a_puerta",
        "label": "Tiros a puerta",
        "lines": (2.5, 3.5, 4.5, 5.5),
        "tournament_default": 4.0,
    },
    "fouls": {
        "metric": "resumen_del_partido.faltas",
        "label": "Faltas",
        "lines": (9.5, 11.5, 13.5, 15.5),
        "tournament_default": 12.5,
    },
    "offsides": {
        "metric": "ataque.fueras_de_juego",
        "label": "Fueras de juego",
        "lines": (0.5, 1.5, 2.5),
        "tournament_default": 1.8,
    },
}


@dataclass(frozen=True)
class TeamMarketLine:
    market: str
    label: str
    team_name: str
    line: float
    expected: float
    over_probability: float
    confidence: str    # "high" | "medium" | "low"
    sample_size: float


def _poisson_over(lambd: float, line: float) -> float:
    """P(X > line) for Poisson(lambd). Lines like 2.5 mean "more than 2.5"."""
    if lambd <= 0:
        return 0.0
    # Sum probabilities for k <= floor(line), then 1 - that.
    upto = int(math.floor(line))
    cdf = 0.0
    term = math.exp(-lambd)
    cdf += term
    for k in range(1, upto + 1):
        term *= lambd / k
        cdf += term
    return max(0.0, min(1.0, 1.0 - cdf))


def _confidence_for(sample: float) -> str:
    if sample >= 6:
        return "high"
    if sample >= 2:
        return "medium"
    return "low"


def predict_team_volume_markets(
    profile_a: TeamProfile,
    profile_b: TeamProfile,
    *,
    own_weight: float = 0.45,
    opp_weight: float = 0.30,
    tournament_weight: float = 0.25,
) -> list[TeamMarketLine]:
    """Return predicted volume markets for both teams of a match.

    The Poisson mean for team A's metric M is:
        λ_A = own_weight   * profile_A.get(M)
            + opp_weight   * profile_B.get(M)        # rival's typical rate
            + tournament_weight * tournament_mean(M)

    Why blend with the opponent's *own* rate rather than its "conceded" rate?
    Conceded rates per metric aren't stored separately yet. As an approximation,
    teams that play high-corner / high-tackle styles tend to push opponents
    into similar rhythms — so the opponent's own rate is a usable proxy.
    """
    out: list[TeamMarketLine] = []
    for market_id, spec in MARKET_CATALOG.items():
        metric = spec["metric"]
        label = spec["label"]
        lines = spec["lines"]
        default = float(spec["tournament_default"])
        for team_profile, other_profile, team_name in (
            (profile_a, profile_b, profile_a.team_name),
            (profile_b, profile_a, profile_b.team_name),
        ):
            own = team_profile.get(metric)
            opp = other_profile.get(metric)
            tmean = next(
                (est.tournament_mean for est in team_profile.metrics.values() if est.metric == metric),
                default,
            )
            if tmean <= 0:
                tmean = default
            own_val = own if own is not None else tmean
            opp_val = opp if opp is not None else tmean
            lambd = (
                own_weight * own_val
                + opp_weight * opp_val
                + tournament_weight * tmean
            )
            sample = team_profile.metrics.get(metric).sample_size if team_profile.metrics.get(metric) else 0.0
            conf = _confidence_for(sample)
            for line in lines:
                out.append(TeamMarketLine(
                    market=market_id,
                    label=label,
                    team_name=team_name,
                    line=float(line),
                    expected=lambd,
                    over_probability=_poisson_over(lambd, line),
                    confidence=conf,
                    sample_size=sample,
                ))
    return out


def derive_xg_factors_from_profile(
    profile_a: TeamProfile,
    profile_b: TeamProfile,
    *,
    max_factor: float = 1.30,
    min_factor: float = 0.77,
) -> tuple[float, float, str]:
    """Compute multiplicative xG adjustments from the offense vs defense
    dimensions of two team profiles.

    A team that scores high on offense AND faces a weak defense gets factor > 1.
    A team that scores low on offense AND faces a strong defense gets factor < 1.
    Returns (factor_a, factor_b, human-readable explanation).
    """
    off_a = profile_a.dimension_score("offense")
    off_b = profile_b.dimension_score("offense")
    def_a = profile_a.dimension_score("defense")
    def_b = profile_b.dimension_score("defense")
    gk_a = profile_a.dimension_score("goalkeeper")
    gk_b = profile_b.dimension_score("goalkeeper")

    # Strong defense / GK = harder to score against → lower xG for opponent.
    # Score deltas are roughly in [-0.5, 0.5]; we map ±0.4 → ±25% xG.
    delta_a = 0.30 * off_a - 0.20 * def_b - 0.15 * gk_b
    delta_b = 0.30 * off_b - 0.20 * def_a - 0.15 * gk_a

    factor_a = max(min_factor, min(max_factor, math.exp(delta_a)))
    factor_b = max(min_factor, min(max_factor, math.exp(delta_b)))
    explanation = (
        f"Perfil profundo: {profile_a.team_name} ataque {off_a:+.2f}, defensa {def_a:+.2f}, portería {gk_a:+.2f}; "
        f"{profile_b.team_name} ataque {off_b:+.2f}, defensa {def_b:+.2f}, portería {gk_b:+.2f}. "
        f"xG ×{factor_a:.2f} / ×{factor_b:.2f}."
    )
    return factor_a, factor_b, explanation
