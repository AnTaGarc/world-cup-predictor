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

from dataclasses import dataclass, field, field
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
    """Stable per-team summary built from every available deep observation.

    ``metrics`` holds what the team *creates* (its own per-match averages).
    ``conceded_metrics`` holds what its rivals scored against it (the team's
    per-match against averages). Both share the same weighting machinery
    (recency × competition × opponent strength).
    """
    team_name: str
    metrics: dict[str, MetricEstimate]
    sample_weight: float = 0.0
    conceded_metrics: dict[str, MetricEstimate] = field(default_factory=dict)

    def get(self, metric: str) -> float | None:
        est = self.metrics.get(metric)
        return est.value if est else None

    def conceded(self, metric: str) -> float | None:
        """How much this team allows the rival to produce of ``metric``."""
        est = self.conceded_metrics.get(metric)
        return est.value if est else None

    def dimension_score(self, dimension: str) -> float:
        """Average z-score of this team's metrics in a given dimension, vs
        tournament mean. Positive = above average; negative = below."""
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


def _competition_weight(competition: str) -> float:
    """Importance multiplier per match type.

    The ongoing tournament gets the strongest boost because each match is
    the most direct evidence we have of current form against the same field
    we're predicting. The diagnostic on 12 sample teams showed Mundial 2026
    matches were contributing only 4-15% of the profile weight before this
    boost; doubling them brings top teams' WC weight to ~15-25%, which is
    much more in line with what the user (and football intuition) expects.

      * 2.00 — Current World Cup 2026 ("FIFA World Cup 2026" exactly)
      * 1.00 — Other top tournaments (past World Cups, AFCON, Copa América,
               Asian Cup, Euro, Gold Cup, OFC Nations Cup)
      * 0.85 — Qualifiers, Nations League, Euro Qualifiers
      * 0.50 — International friendlies
      * 0.70 — Unknown competition (fallback)
    """
    if not competition:
        return 0.70
    c = competition.lower()
    if "world cup 2026" in c:
        return 2.00
    if "friendly" in c or "amistos" in c:
        return 0.50
    if "qualif" in c or "qualific" in c or "nations league" in c or "eliminator" in c:
        return 0.85
    if any(token in c for token in (
        "world cup", "afcon", "africa cup", "copa america", "asian cup",
        "euro", "gold cup", "ofc nations cup", "confederations",
    )):
        return 1.00
    return 0.70


@dataclass(frozen=True)
class _TeamProfileBuildContext:
    tournament_means: dict[str, float]
    by_match_metric: dict[tuple[str, str], list[tuple[str, float]]]
    teams_by_match: dict[str, tuple[str, ...]]
    rows_by_team: dict[str, tuple[dict, ...]]


def _build_profile_context(deep_rows: list[dict]) -> _TeamProfileBuildContext:
    metric_totals: dict[str, list[float]] = {}
    by_match_metric: dict[tuple[str, str], list[tuple[str, float]]] = {}
    teams_by_match: dict[str, set[str]] = {}
    rows_by_team: dict[str, list[dict]] = {}
    for row in deep_rows:
        kickoff_key = str(row.get("kickoff_utc") or "")
        team_name = str(row.get("team_name") or "")
        if kickoff_key and team_name:
            teams_by_match.setdefault(kickoff_key, set()).add(team_name)
        if team_name:
            rows_by_team.setdefault(canonical_team_name(team_name), []).append(row)

        metric = str(row.get("metric") or "")
        value = row.get("value_number")
        if metric not in METRIC_CATALOG or value is None:
            continue
        numeric = float(value)
        metric_totals.setdefault(metric, []).append(numeric)
        if kickoff_key:
            by_match_metric.setdefault((kickoff_key, metric), []).append((team_name, numeric))

    tournament_means = {
        metric: (sum(values) / len(values)) if values else 0.0
        for metric, values in metric_totals.items()
    }
    return _TeamProfileBuildContext(
        tournament_means=tournament_means,
        by_match_metric=by_match_metric,
        teams_by_match={key: tuple(values) for key, values in teams_by_match.items()},
        rows_by_team={key: tuple(values) for key, values in rows_by_team.items()},
    )


def build_team_profiles(
    team_names: list[str] | tuple[str, ...],
    deep_rows: list[dict],
    as_of_utc: datetime,
    *,
    half_life_days: float = 540.0,
    shrinkage_prior_matches: float = 2.0,
    opponent_strengths: dict[str, float] | None = None,
) -> dict[str, TeamProfile]:
    context = _build_profile_context(deep_rows)
    return {
        team_name: _build_team_profile_from_context(
            team_name,
            deep_rows,
            as_of_utc,
            context,
            half_life_days=half_life_days,
            shrinkage_prior_matches=shrinkage_prior_matches,
            opponent_strengths=opponent_strengths,
        )
        for team_name in team_names
    }


def build_team_profile(
    team_name: str,
    deep_rows: list[dict],
    as_of_utc: datetime,
    *,
    half_life_days: float = 540.0,
    shrinkage_prior_matches: float = 2.0,
    opponent_strengths: dict[str, float] | None = None,
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
        Recency decay. Default 240 = a match 8 months ago weighs 0.5×, one
        year ago ~0.35×, eighteen months ago ~0.16×. The deliberately short
        half-life implicitly accounts for squad turnover — we don't track
        which players were on the pitch, but old fixtures are downweighted
        fast enough that pre-rotation form fades out within ~12-18 months.
    shrinkage_prior_matches : float
        Bayesian prior strength expressed as "equivalent matches" pulled
        toward the tournament mean. With prior=2 and a back-filled FBref
        history of ~15 matches per team, the team's own data dominates
        but the prior still pulls outliers in.
    opponent_strengths : dict[str, float] | None
        Optional mapping ``canonical_team_name -> strength_index`` (Elo-derived
        or similar). When provided, each observation is reweighted by
        ``opponent_strength / mean_strength`` so metrics produced against
        strong sides count for more than the same metric against weak ones.
        Default treats every opponent equally.
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

    # Mean opponent strength used to normalize per-match weighting (so a
    # metric against a strong rival contributes more than the same metric
    # against a weak one). Normalize the lookup dict to canonical keys so
    # callers can pass whatever casing they have.
    normalized_strengths: dict[str, float] = {}
    if opponent_strengths:
        for raw_name, value in opponent_strengths.items():
            normalized_strengths[canonical_team_name(str(raw_name))] = float(value)
    mean_strength = (
        sum(normalized_strengths.values()) / len(normalized_strengths)
        if normalized_strengths else 1.0
    ) or 1.0

    # Now accumulate this team's weighted observations. We need to know who
    # the *opponent* was to apply the strength reweighting — that requires
    # pairing rows from the same match.
    own_rows = [r for r in deep_rows if _matches_team(r, team_name)]
    # Build a per-match opponent lookup from the full deep_rows set.
    match_opponents: dict[str, str] = {}
    if normalized_strengths:
        for r in deep_rows:
            key = str(r.get("kickoff_utc") or "")
            if not key:
                continue
            other_team = str(r.get("team_name") or "")
            if other_team and not _matches_team(r, team_name):
                match_opponents.setdefault(key, other_team)

    # Pre-index rows by (kickoff_utc, metric) → list of (team_name, value)
    # so we can find the "other team's" value for each (match, metric) cheaply.
    by_match_metric: dict[tuple[str, str], list[tuple[str, float]]] = {}
    for r in deep_rows:
        metric = str(r.get("metric") or "")
        value = r.get("value_number")
        if metric not in METRIC_CATALOG or value is None:
            continue
        key = (str(r.get("kickoff_utc") or ""), metric)
        if not key[0]:
            continue
        by_match_metric.setdefault(key, []).append(
            (str(r.get("team_name") or ""), float(value))
        )

    weighted_sums: dict[str, tuple[float, float]] = {}  # created → (sum, weight)
    conceded_sums: dict[str, tuple[float, float]] = {}  # conceded → (sum, weight)
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
        # Down-weight friendlies relative to qualifiers and top tournaments.
        w *= _competition_weight(str(row.get("competition") or ""))
        if normalized_strengths:
            opp = match_opponents.get(str(row.get("kickoff_utc") or ""))
            if opp:
                opp_strength = normalized_strengths.get(
                    canonical_team_name(opp), mean_strength
                )
                # Multiplicative reweighting. Capped to avoid letting one
                # very strong/weak opponent dominate.
                ratio = max(0.4, min(2.5, opp_strength / mean_strength))
                w *= ratio
        s, ws = weighted_sums.get(metric, (0.0, 0.0))
        weighted_sums[metric] = (s + float(value) * w, ws + w)
        total_weight = max(total_weight, ws + w)

        # Conceded: same weight, but the value is from the OTHER team in
        # this match (what they produced *against us*).
        pair = by_match_metric.get((str(row.get("kickoff_utc") or ""), metric), [])
        for other_team, other_value in pair:
            if _matches_team({"team_name": other_team}, team_name):
                continue
            cs, cws = conceded_sums.get(metric, (0.0, 0.0))
            conceded_sums[metric] = (cs + other_value * w, cws + w)
            break  # only one opponent per match

    # Apply Bayesian shrinkage toward tournament mean.
    metrics: dict[str, MetricEstimate] = {}
    conceded: dict[str, MetricEstimate] = {}
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
            metric=metric, dimension=catalog_dim, value=shrunk,
            sample_size=w, tournament_mean=tmean,
        )
        # Conceded estimate (asymmetric profile: what rivals produce against us).
        cs, cw = conceded_sums.get(metric, (0.0, 0.0))
        if cw > 0 or tmean > 0:
            conceded_mean = cs / cw if cw > 0 else tmean
            conceded_shrunk = (cw * conceded_mean + prior * tmean) / (cw + prior)
            conceded[metric] = MetricEstimate(
                metric=metric, dimension=catalog_dim, value=conceded_shrunk,
                sample_size=cw, tournament_mean=tmean,
            )

    return TeamProfile(
        team_name=team_name,
        metrics=metrics,
        sample_weight=total_weight,
        conceded_metrics=conceded,
    )


def _build_team_profile_from_context(
    team_name: str,
    deep_rows: list[dict],
    as_of_utc: datetime,
    context: _TeamProfileBuildContext,
    *,
    half_life_days: float,
    shrinkage_prior_matches: float,
    opponent_strengths: dict[str, float] | None,
) -> TeamProfile:
    if as_of_utc.tzinfo is None:
        as_of_utc = as_of_utc.replace(tzinfo=timezone.utc)

    normalized_strengths: dict[str, float] = {}
    if opponent_strengths:
        for raw_name, value in opponent_strengths.items():
            normalized_strengths[canonical_team_name(str(raw_name))] = float(value)
    mean_strength = (
        sum(normalized_strengths.values()) / len(normalized_strengths)
        if normalized_strengths else 1.0
    ) or 1.0

    own_rows = context.rows_by_team.get(canonical_team_name(team_name), ())
    weighted_sums: dict[str, tuple[float, float]] = {}
    conceded_sums: dict[str, tuple[float, float]] = {}
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
        w *= _competition_weight(str(row.get("competition") or ""))
        if normalized_strengths:
            opp = _opponent_for_match(context, str(row.get("kickoff_utc") or ""), team_name)
            if opp:
                opp_strength = normalized_strengths.get(
                    canonical_team_name(opp), mean_strength
                )
                ratio = max(0.4, min(2.5, opp_strength / mean_strength))
                w *= ratio
        s, ws = weighted_sums.get(metric, (0.0, 0.0))
        weighted_sums[metric] = (s + float(value) * w, ws + w)
        total_weight = max(total_weight, ws + w)

        pair = context.by_match_metric.get((str(row.get("kickoff_utc") or ""), metric), [])
        for other_team, other_value in pair:
            if _matches_team({"team_name": other_team}, team_name):
                continue
            cs, cws = conceded_sums.get(metric, (0.0, 0.0))
            conceded_sums[metric] = (cs + other_value * w, cws + w)
            break

    metrics: dict[str, MetricEstimate] = {}
    conceded: dict[str, MetricEstimate] = {}
    for metric, (catalog_dim, _) in METRIC_CATALOG.items():
        tmean = context.tournament_means.get(metric, 0.0)
        s, w = weighted_sums.get(metric, (0.0, 0.0))
        if w <= 0 and tmean <= 0:
            continue
        own_mean = s / w if w > 0 else tmean
        prior = shrinkage_prior_matches
        shrunk = (w * own_mean + prior * tmean) / (w + prior)
        metrics[metric] = MetricEstimate(
            metric=metric, dimension=catalog_dim, value=shrunk,
            sample_size=w, tournament_mean=tmean,
        )
        cs, cw = conceded_sums.get(metric, (0.0, 0.0))
        if cw > 0 or tmean > 0:
            conceded_mean = cs / cw if cw > 0 else tmean
            conceded_shrunk = (cw * conceded_mean + prior * tmean) / (cw + prior)
            conceded[metric] = MetricEstimate(
                metric=metric, dimension=catalog_dim, value=conceded_shrunk,
                sample_size=cw, tournament_mean=tmean,
            )

    return TeamProfile(
        team_name=team_name,
        metrics=metrics,
        sample_weight=total_weight,
        conceded_metrics=conceded,
    )


def _opponent_for_match(context: _TeamProfileBuildContext, kickoff_key: str, team_name: str) -> str | None:
    from wcpredict.names import same_team
    for other_team in context.teams_by_match.get(kickoff_key, ()):
        if not same_team(other_team, team_name):
            return other_team
    return None


def _matches_team(row: dict, team_name: str) -> bool:
    from wcpredict.names import same_team
    return same_team(str(row.get("team_name") or ""), team_name)


from wcpredict.names import canonical_team_name  # re-export for typing  # noqa: E402


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
