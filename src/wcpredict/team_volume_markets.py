"""Per-team predictions for volume markets (corners, cards, shots, …).

Distribution choice (Poisson vs Negative Binomial)
--------------------------------------------------
For each metric we model the count for one team as either Poisson(λ) or
Negative Binomial(λ, α) depending on the empirical overdispersion of the
metric in international football:

  * Yellow cards & fouls: variance/mean ≈ 2.0-2.5 → NB strongly preferred
  * Corners: variance/mean ≈ 1.3-1.8 → NB clearly better
  * Shots / shots on target: variance/mean ≈ 1.0-1.2 → Poisson acceptable
  * Offsides: variance/mean ≈ 1.5 → NB ligero

Mean construction
-----------------
We build λ as a weighted blend of three signals:

* What the team itself *creates* (its own rate for that metric)
* What the opponent *concedes* (the opponent's "against" rate, NOT what
  they themselves create — that's the asymmetric refinement added on top
  of the original symmetric blend that used the opponent's own rate as
  a proxy)
* The tournament-wide mean (prior, important when per-team samples are tiny)

Then we report ``over/under`` probabilities at the common bookmaker lines.

Used by services.predict_match_markets and rendered as the "Estadísticas
estimadas por equipo" panel in the Predicción y Valor page.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from wcpredict.team_profile import TeamProfile


# Maps user-facing market id → metric/lines/default + the count distribution.
# ``dispersion_prior`` is α in the NB parameterisation var = μ + α·μ²; 0.0
# falls back to Poisson. Literature suggests cards/corners are overdispersed
# in club football (α ≈ 0.2-0.5), but our WC26 over/under backtest (40 matches)
# showed Poisson edged out NB by 0.7% on Brier — international tournaments
# seem to have less variance than club football, possibly because referees
# normalise discipline across teams. Defaults are 0.0 (Poisson) until we have
# enough data to estimate α reliably. The NB code path stays in place so we
# can switch back when sample sizes warrant it.
MARKET_CATALOG: dict[str, dict] = {
    "corners": {
        "metric": "resumen_del_partido.saques_de_esquina",
        "label": "Corners",
        "lines": (2.5, 3.5, 4.5, 5.5),
        "tournament_default": 4.5,
        "dispersion_prior": 0.0,
    },
    "yellow_cards": {
        "metric": "resumen_del_partido.tarjetas_amarillas",
        "label": "Tarjetas amarillas",
        "lines": (0.5, 1.5, 2.5, 3.5),
        "tournament_default": 1.8,
        "dispersion_prior": 0.0,
    },
    "shots_total": {
        "metric": "resumen_del_partido.tiros_totales",
        "label": "Tiros totales",
        "lines": (7.5, 9.5, 11.5, 13.5),
        "tournament_default": 11.0,
        "dispersion_prior": 0.0,
    },
    "shots_on_target": {
        "metric": "tiros.tiros_a_puerta",
        "label": "Tiros a puerta",
        "lines": (2.5, 3.5, 4.5, 5.5),
        "tournament_default": 4.0,
        "dispersion_prior": 0.0,
    },
    "fouls": {
        "metric": "resumen_del_partido.faltas",
        "label": "Faltas",
        "lines": (9.5, 11.5, 13.5, 15.5),
        "tournament_default": 12.5,
        "dispersion_prior": 0.0,
    },
    "offsides": {
        "metric": "ataque.fueras_de_juego",
        "label": "Fueras de juego",
        "lines": (0.5, 1.5, 2.5),
        "tournament_default": 1.8,
        "dispersion_prior": 0.0,
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
    upto = int(math.floor(line))
    cdf = 0.0
    term = math.exp(-lambd)
    cdf += term
    for k in range(1, upto + 1):
        term *= lambd / k
        cdf += term
    return max(0.0, min(1.0, 1.0 - cdf))


def _neg_binomial_over(lambd: float, alpha: float, line: float) -> float:
    """P(X > line) for Negative Binomial parameterised by (mean=λ, dispersion=α).

    The NB distribution here has variance = λ + α·λ². For α → 0 the
    distribution converges to Poisson(λ); for α > 0 the right tail is
    heavier than Poisson, which is what we want for corners / cards /
    fouls (where the empirical variance/mean ratio is > 1).
    """
    if alpha <= 0:
        return _poisson_over(lambd, line)
    if lambd <= 0:
        return 0.0
    r = 1.0 / alpha
    p = r / (r + lambd)
    upto = int(math.floor(line))
    cdf = 0.0
    for k in range(0, upto + 1):
        # PMF: gamma(k+r)/(k! gamma(r)) * p^r * (1-p)^k
        log_coeff = math.lgamma(k + r) - math.lgamma(k + 1) - math.lgamma(r)
        cdf += math.exp(log_coeff + r * math.log(p) + k * math.log(1.0 - p))
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

    The expected count (λ) for team A's metric M is:
        λ_A = own_weight × A.created(M)
            + opp_weight × B.conceded(M)        # ← asymmetric: what B *allows*
            + tournament_weight × tournament_mean(M)

    "Conceded" is the rival's value of that metric measured against B's
    opponents. Falling back to B.created(M) when conceded isn't available
    keeps the model robust on the few teams without rival-side data yet.

    Over/under probabilities are computed via Negative Binomial when the
    metric is overdispersed (corners, cards, fouls), and via Poisson
    otherwise. The dispersion is taken from MARKET_CATALOG priors that
    encode the typical international-football overdispersion of each metric.
    """
    out: list[TeamMarketLine] = []
    for market_id, spec in MARKET_CATALOG.items():
        metric = spec["metric"]
        label = spec["label"]
        lines = spec["lines"]
        default = float(spec["tournament_default"])
        alpha = float(spec.get("dispersion_prior", 0.0))
        for team_profile, other_profile, team_name in (
            (profile_a, profile_b, profile_a.team_name),
            (profile_b, profile_a, profile_b.team_name),
        ):
            own = team_profile.get(metric)
            opp_conceded = other_profile.conceded(metric)
            opp_created = other_profile.get(metric)
            tmean = next(
                (est.tournament_mean for est in team_profile.metrics.values() if est.metric == metric),
                default,
            )
            if tmean <= 0:
                tmean = default
            own_val = own if own is not None else tmean
            opp_val = (
                opp_conceded if opp_conceded is not None
                else (opp_created if opp_created is not None else tmean)
            )
            lambd = (
                own_weight * own_val
                + opp_weight * opp_val
                + tournament_weight * tmean
            )
            sample = team_profile.metrics.get(metric).sample_size if team_profile.metrics.get(metric) else 0.0
            conf = _confidence_for(sample)
            for line in lines:
                over_prob = _neg_binomial_over(lambd, alpha, line)
                out.append(TeamMarketLine(
                    market=market_id,
                    label=label,
                    team_name=team_name,
                    line=float(line),
                    expected=lambd,
                    over_probability=over_prob,
                    confidence=conf,
                    sample_size=sample,
                ))
    return out


def derive_xg_factors_from_profile(
    profile_a: TeamProfile,
    profile_b: TeamProfile,
    *,
    max_factor: float = 1.20,
    min_factor: float = 0.83,
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
    # Toned-down coefficients (was 0.30/0.20/0.15) so the deep profile nudges
    # the xG instead of overpowering the Elo-derived base. With caps at 0.83
    # and 1.20 the typical adjustment is now ±5-15% rather than ±25%.
    delta_a = 0.18 * off_a - 0.12 * def_b - 0.08 * gk_b
    delta_b = 0.18 * off_b - 0.12 * def_a - 0.08 * gk_a

    factor_a = max(min_factor, min(max_factor, math.exp(delta_a)))
    factor_b = max(min_factor, min(max_factor, math.exp(delta_b)))
    explanation = (
        f"Perfil profundo: {profile_a.team_name} ataque {off_a:+.2f}, defensa {def_a:+.2f}, portería {gk_a:+.2f}; "
        f"{profile_b.team_name} ataque {off_b:+.2f}, defensa {def_b:+.2f}, portería {gk_b:+.2f}. "
        f"xG ×{factor_a:.2f} / ×{factor_b:.2f}."
    )
    return factor_a, factor_b, explanation
