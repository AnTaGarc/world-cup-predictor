from dataclasses import dataclass
import math

from wcpredict.models import MarketFamily
from wcpredict.quality import Confidence, assess_market_confidence
from wcpredict.count_models import over_probability


@dataclass(frozen=True)
class PlayerAssumption:
    player_name: str
    team_name: str
    expected_minutes: int | None
    starter_probability: float
    per90_rate: float
    opponent_adjustment: float
    manually_estimated: bool
    dispersion: float | None = None


@dataclass(frozen=True)
class PlayerMarketEstimate:
    probability: float | None
    confidence: Confidence
    explanation: str
    model_family: str = "poisson"


@dataclass(frozen=True)
class DerivedPlayerAssumption:
    assumption: PlayerAssumption
    sample_size: int
    metric: str
    explanation: str


PLAYER_MARKET_METRICS = {
    MarketFamily.PLAYER_GOAL: "goals",
    MarketFamily.PLAYER_ASSIST: "assists",
    MarketFamily.PLAYER_SHOTS: "shots",
    MarketFamily.PLAYER_SHOTS_ON_TARGET: "shots_on_target",
    MarketFamily.PLAYER_CARDS: "yellow_cards",
    MarketFamily.PLAYER_PASSES: "passes",
    # Goalkeeper markets share `save_percentage` as the source metric and are
    # transformed by `derive_player_assumption` into rates per 90.
    MarketFamily.PLAYER_SAVES: "save_percentage",
    MarketFamily.PLAYER_GOALS_CONCEDED: "save_percentage",
    MarketFamily.PLAYER_CLEAN_SHEET: "save_percentage",
}


GOALKEEPER_MARKETS = frozenset({
    MarketFamily.PLAYER_SAVES,
    MarketFamily.PLAYER_GOALS_CONCEDED,
    MarketFamily.PLAYER_CLEAN_SHEET,
})


# Average shots-on-target faced by a national side per match. Used as the
# fallback when we don't have the opponent's specific SOT-for rate. Calibrated
# to typical international football (~4 SOT per side).
DEFAULT_OPPONENT_SOT_PER90 = 4.0


def is_goalkeeper(player_row: dict) -> bool:
    """Best-effort detection of goalkeeper rows in the bank."""
    position = str(player_row.get("position") or "").strip().upper()
    if position in {"GK", "G", "GOALKEEPER", "POR", "PORTERO"}:
        return True
    # Some sources put it in lower case or with extra text.
    return "GOAL" in position or "PORTER" in position


def derive_player_assumption(
    player_row: dict,
    market_family: MarketFamily,
    opponent_adjustment: float = 1.0,
    opponent_sot_per90: float | None = None,
    team_save_rate_override: float | None = None,
) -> DerivedPlayerAssumption | None:
    metric = PLAYER_MARKET_METRICS.get(market_family)
    minutes = float(player_row.get("minutes") or 0)
    if metric is None or player_row.get(metric) is None or minutes <= 0:
        return None
    games = max(1, int(player_row.get("games") or player_row.get("matches") or 1))
    starts_raw = player_row.get("starts")
    average_minutes = min(90.0, minutes / games)
    if starts_raw is None:
        starter_probability = min(1.0, average_minutes / 90.0)
    else:
        starter_probability = (max(0, int(starts_raw)) + 1) / (games + 2)

    # Default flow: per-90 from the raw metric divided by total minutes played.
    per90_rate = float(player_row[metric]) * 90.0 / minutes
    explanation_detail = f"{per90_rate:.2f} {metric} por 90"

    # Goalkeeper flow: convert save_percentage into a per-90 rate for the
    # selected market by combining it with the expected SOT-against baseline.
    if market_family in GOALKEEPER_MARKETS:
        if team_save_rate_override is not None:
            save_pct = max(0.0, min(1.0, float(team_save_rate_override)))
            save_source = "deep"
        else:
            save_pct = max(0.0, min(100.0, float(player_row[metric]))) / 100.0
            save_source = "bank"
        baseline = float(opponent_sot_per90 if opponent_sot_per90 is not None else DEFAULT_OPPONENT_SOT_PER90)
        source_label = "histórico deep" if save_source == "deep" else "banco diario"
        if market_family == MarketFamily.PLAYER_SAVES:
            per90_rate = baseline * save_pct
            explanation_detail = (
                f"save% {save_pct:.0%} ({source_label}) × {baseline:.1f} tiros a puerta esperados por 90 "
                f"= {per90_rate:.2f} paradas/90"
            )
        elif market_family == MarketFamily.PLAYER_GOALS_CONCEDED:
            per90_rate = baseline * (1.0 - save_pct)
            explanation_detail = (
                f"(1 - save% {save_pct:.0%}, {source_label}) × {baseline:.1f} SOT esperados "
                f"= {per90_rate:.2f} goles concedidos/90"
            )
        else:  # PLAYER_CLEAN_SHEET
            per90_rate = baseline * (1.0 - save_pct)
            explanation_detail = (
                f"Goles concedidos esperados {per90_rate:.2f}/90 con save% del {source_label} "
                "(Poisson(0) define la portería a cero)"
            )

    assumption = PlayerAssumption(
        player_name=str(player_row.get("player_name") or ""),
        team_name=str(player_row.get("team_name") or ""),
        expected_minutes=int(round(average_minutes)),
        starter_probability=max(0.0, min(1.0, starter_probability)),
        per90_rate=per90_rate,
        opponent_adjustment=opponent_adjustment,
        manually_estimated=False,
    )
    return DerivedPlayerAssumption(
        assumption=assumption,
        sample_size=games,
        metric=metric,
        explanation=(
            f"Calculado con {games} partidos y {int(minutes)} minutos: "
            f"{explanation_detail}; {assumption.expected_minutes} min esperados."
        ),
    )


def _poisson_over_probability(rate: float, line: float) -> float:
    threshold = math.floor(line)
    cumulative = 0.0
    for value in range(threshold + 1):
        cumulative += math.exp(-rate) * rate**value / math.factorial(value)
    return max(0.0, min(1.0, 1.0 - cumulative))


def estimate_player_market_probability(
    assumption: PlayerAssumption,
    market_family: MarketFamily,
    line: float,
    sample_size: int,
) -> PlayerMarketEstimate:
    missing: list[str] = []
    if assumption.expected_minutes is None:
        missing.append("expected_minutes")
    if not assumption.player_name:
        missing.append("player")
    if not assumption.team_name:
        missing.append("team")

    confidence = assess_market_confidence(
        market_family=market_family,
        sample_size=sample_size,
        missing_fields=missing,
        lineup_dependent=True,
        manually_estimated=assumption.manually_estimated,
    )
    if confidence == Confidence.NOT_ESTIMABLE:
        return PlayerMarketEstimate(
            probability=None,
            confidence=confidence,
            explanation="No se estima porque faltan minutos esperados o identidad del jugador/equipo.",
        )

    minutes_factor = (assumption.expected_minutes or 0) / 90.0
    expected_count = (
        assumption.per90_rate
        * minutes_factor
        * assumption.opponent_adjustment
        * max(0.0, min(1.0, assumption.starter_probability))
    )
    distribution = "negative_binomial" if assumption.dispersion is not None and assumption.dispersion > 0 else "poisson"
    # Clean sheet is a binary market: probability the GK's team concedes 0
    # goals. We treat `expected_count` as expected goals conceded and apply
    # Poisson(0) = exp(-lambda). The `line` is ignored for this market.
    if market_family == MarketFamily.PLAYER_CLEAN_SHEET:
        probability = math.exp(-max(0.0, expected_count))
        return PlayerMarketEstimate(
            probability=max(0.0, min(1.0, probability)),
            confidence=confidence,
            explanation=(
                f"P(portería a cero) = exp(-{expected_count:.2f}) = {probability:.2%} "
                f"con minutos, titularidad y rival ya aplicados."
            ),
            model_family="poisson_zero",
        )
    probability = over_probability(
        expected_count, line, distribution=distribution, dispersion=float(assumption.dispersion or 0.0)
    )
    return PlayerMarketEstimate(
        probability=probability,
        confidence=confidence,
        explanation=f"Tasa esperada {expected_count:.2f} ajustada por minutos, titularidad y rival; distribución {distribution}.",
        model_family=distribution,
    )
