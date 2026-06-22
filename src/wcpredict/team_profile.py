"""Aggregate deep team metrics into stable per-team profiles for prediction.

The deep-stats JSON gives us ~70 metrics per team per match (xG, shots, passes,
duels, defensive actions, goalkeeper stats, etc.). Used naively, each metric
alone is noisy when a team only has 1-2 World Cup matches played.

This module builds a *TeamProfile* per team by:

1. Pulling every deep observation we have for that team across competitions.
2. Weighting each match by recency (exponential decay) and competition
   importance (World Cup > continental > friendly).
3. Bayesian-shrinking each metric toward the tournament-wide mean so a team
   with 2 matches doesn't dominate the estimate.
4. Grouping metrics into four dimensions: ``offense``, ``defense``,
   ``goalkeeper``, ``style``.

The resulting ``TeamProfile`` is consumed by services.predict_match_markets
(to refine xG estimates) and by team_volume_markets (to predict corners,
cards, shots, etc. for each team).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math


# Metric → (dimension, "rate per 90 min" hint).
# The hint is used when the JSON value is a per-match count we want to scale.
# Metrics already expressed as percentages or per-90 keep rate_per_90=False.
METRIC_CATALOG: dict[str, tuple[str, bool]] = {
    # Offense
    "resumen_del_partido.goles_esperados_xg": ("offense", True),
    "resumen_del_partido.tiros_totales": ("offense", True),
    "tiros.tiros_totales": ("offense", True),
    "tiros.tiros_a_puerta": ("offense", True),
    "tiros.tiros_dentro_del_area_penal": ("offense", True),
    "tiros.tiros_fuera_del_area": ("offense", True),
    "tiros.tiros_al_palo": ("offense", True),
    "ataque.toques_dentro_del_area": ("offense", True),
    "ataque.ocasiones_claras_realizadas": ("offense", True),
    "ataque.ocasiones_claras_falladas": ("offense", True),
    "ataque.pases_en_profundidad": ("offense", True),
    "ataque.faltas_recibidas_en_el_tercio_final": ("offense", True),
    "resumen_del_partido.ocasiones_claras": ("offense", True),
    "resumen_del_partido.saques_de_esquina": ("offense", True),
    "pases.pases_al_ultimo_tercio": ("offense", True),
    "pases.pases_en_el_ultimo_tercio.completados": ("offense", True),
    "pases.pases_en_el_ultimo_tercio.porcentaje": ("offense", False),
    "pases.centros.completados": ("offense", True),
    "pases.centros.porcentaje": ("offense", False),
    "duelos.regates.completados": ("offense", True),
    "duelos.regates.porcentaje": ("offense", False),
    # Defense
    "defensa.tackles_totales": ("defense", True),
    "defensa.intercepciones": ("defense", True),
    "defensa.recuperaciones": ("defense", True),
    "defensa.despejes": ("defense", True),
    "defensa.entradas_ganadas_pct": ("defense", False),
    "defensa.errores_que_llevan_a_disparo": ("defense", True),
    "defensa.errores_que_llevan_a_gol": ("defense", True),
    "duelos.duelos_ganados_pct_del_total": ("defense", False),
    "duelos.duelos_aereos.ganados": ("defense", True),
    "duelos.duelos_aereos.porcentaje": ("defense", False),
    "duelos.duelos_en_el_suelo.ganados": ("defense", True),
    "duelos.duelos_en_el_suelo.porcentaje": ("defense", False),
    "tiros.tiros_bloqueados": ("defense", True),
    # Goalkeeper
    "porteria.paradas": ("goalkeeper", True),
    "porteria.grandes_paradas": ("goalkeeper", True),
    "porteria.goles_evitados": ("goalkeeper", True),
    "porteria.despejes_por_alto": ("goalkeeper", True),
    "porteria.con_los_punos": ("goalkeeper", True),
    "porteria.saques_de_puerta": ("goalkeeper", True),
    # Style / volume / discipline
    "resumen_del_partido.posesion_de_balon_pct": ("style", False),
    "resumen_del_partido.pases": ("style", True),
    "pases.pases_precisos": ("style", True),
    "pases.pases_largos.intentados": ("style", True),
    "pases.pases_largos.porcentaje": ("style", False),
    "resumen_del_partido.faltas": ("style", True),
    "resumen_del_partido.tarjetas_amarillas": ("style", True),
    "resumen_del_partido.tarjetas_rojas": ("style", True),
    "resumen_del_partido.distancia_recorrida_km": ("style", False),
    "resumen_del_partido.numero_de_sprints": ("style", True),
    "duelos.perdidas": ("style", True),
    "ataque.fueras_de_juego": ("style", True),
}


@dataclass(frozen=True)
class MetricEstimate:
    """A single metric for a single team: shrunk mean + sample weight."""
    metric: str
    dimension: str
    value: float           # per-match rate (or % if metric is already a ratio)
    sample_size: float     # effective weighted samples (decay-weighted matches)
    tournament_mean: float


@dataclass(frozen=True)
class TeamProfile:
    """Stable per-team summary built from every available deep observation."""
    team_name: str
    metrics: dict[str, MetricEstimate]
    sample_weight: float = 0.0

    def get(self, metric: str) -> float | None:
        est = self.metrics.get(metric)
        return est.value if est else None

    def dimension_score(self, dimension: str) -> float:
        """Average z-score of this team's metrics in a given dimension, vs
        tournament mean. Positive = above average; negative = below."""
        # Build z-scores against per-metric tournament dispersion. We don't
        # have stdev cheaply, so we use absolute value vs mean as a proxy.
        scores = []
        for est in self.metrics.values():
            if est.dimension != dimension or est.tournament_mean <= 0:
                continue
            scores.append((est.value - est.tournament_mean) / max(est.tournament_mean, 1e-3))
        return sum(scores) / len(scores) if scores else 0.0


def _recency_weight(played_at_utc: datetime, as_of_utc: datetime, half_life_days: float) -> float:
    """Exponential decay: a match played `half_life_days` ago contributes 0.5×."""
    if played_at_utc >= as_of_utc:
        return 0.0
    age_days = (as_of_utc - played_at_utc).total_seconds() / 86400.0
    return math.pow(0.5, age_days / max(half_life_days, 1.0))


def build_team_profile(
    team_name: str,
    deep_rows: list[dict],
    as_of_utc: datetime,
    *,
    half_life_days: float = 365.0,
    shrinkage_prior_matches: float = 4.0,
) -> TeamProfile:
    """Build a TeamProfile for ``team_name`` using every deep observation
    in ``deep_rows``.

    Parameters
    ----------
    deep_rows : list[dict]
        Rows from ``repository.list_deep_team_metric_observations_before``.
        Each row has keys: kickoff_utc, team_name, metric, value_number.
    as_of_utc : datetime
        The reference time — only matches strictly before this are used.
    half_life_days : float
        Recency decay. 365 days = one year ago contributes half weight.
    shrinkage_prior_matches : float
        Bayesian prior strength expressed as "equivalent matches" pulled
        toward the tournament mean. With prior=4 and only 2 actual matches,
        the team estimate is roughly 1/3 own observations + 2/3 mean.
    """
    if as_of_utc.tzinfo is None:
        as_of_utc = as_of_utc.replace(tzinfo=timezone.utc)

    # Compute per-metric tournament means using ALL rows (not just this team).
    metric_totals: dict[str, list[float]] = {}
    for row in deep_rows:
        metric = str(row.get("metric") or "")
        value = row.get("value_number")
        if metric not in METRIC_CATALOG or value is None:
            continue
        metric_totals.setdefault(metric, []).append(float(value))

    tournament_means = {
        metric: (sum(values) / len(values)) if values else 0.0
        for metric, values in metric_totals.items()
    }

    # Now accumulate this team's weighted observations.
    own_rows = [r for r in deep_rows if _matches_team(r, team_name)]
    weighted_sums: dict[str, tuple[float, float]] = {}  # metric → (sum, weight)
    total_weight = 0.0
    for row in own_rows:
        metric = str(row.get("metric") or "")
        value = row.get("value_number")
        if metric not in METRIC_CATALOG or value is None:
            continue
        played = _parse_dt(row.get("kickoff_utc"))
        if played is None:
            continue
        w = _recency_weight(played, as_of_utc, half_life_days)
        if w <= 0:
            continue
        s, ws = weighted_sums.get(metric, (0.0, 0.0))
        weighted_sums[metric] = (s + float(value) * w, ws + w)
        total_weight = max(total_weight, ws + w)

    # Apply Bayesian shrinkage toward tournament mean.
    metrics: dict[str, MetricEstimate] = {}
    for metric, (catalog_dim, _) in METRIC_CATALOG.items():
        tmean = tournament_means.get(metric, 0.0)
        s, w = weighted_sums.get(metric, (0.0, 0.0))
        if w <= 0 and tmean <= 0:
            continue
        # Effective mean = (w * observed + prior * tmean) / (w + prior)
        own_mean = s / w if w > 0 else tmean
        prior = shrinkage_prior_matches
        shrunk = (w * own_mean + prior * tmean) / (w + prior)
        metrics[metric] = MetricEstimate(
            metric=metric,
            dimension=catalog_dim,
            value=shrunk,
            sample_size=w,
            tournament_mean=tmean,
        )

    return TeamProfile(team_name=team_name, metrics=metrics, sample_weight=total_weight)


def _matches_team(row: dict, team_name: str) -> bool:
    from wcpredict.names import same_team
    return same_team(str(row.get("team_name") or ""), team_name)


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
