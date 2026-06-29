from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import math
from typing import Any, Iterable

from wcpredict.match_phases import MatchPhaseResultInput


GLOBAL_PENALTY_CONVERSION = 0.76


@dataclass(frozen=True)
class PhaseAuditSection:
    status: str
    actual_score: str | None
    actual_outcome: str | None
    predicted_outcome: str | None
    observed_probability: float | None
    brier: float | None
    rows: tuple[dict, ...] = ()


@dataclass(frozen=True)
class KnockoutPhaseAudit:
    regulation: PhaseAuditSection
    extra_time: PhaseAuditSection
    shootout: PhaseAuditSection


def _object_dict(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    return dict(vars(value))


def build_knockout_snapshot_section(
    prediction,
    extra_time_xg: tuple[float, float],
    penalty_context,
) -> dict:
    et_a, et_b = float(extra_time_xg[0]), float(extra_time_xg[1])
    players = [_object_dict(row) for row in getattr(penalty_context, "player_rows", ())]
    coverage = _object_dict(getattr(penalty_context, "coverage", None))
    return {
        "extra_time": {
            "expected_xg": [et_a, et_b],
            "mode_score": f"{math.floor(et_a)}-{math.floor(et_b)}",
            "conditional": {
                "home": float(prediction.cond_home_wins_et_given_draw_90),
                "draw": float(prediction.cond_draw_after_et_given_draw_90),
                "away": float(prediction.cond_away_wins_et_given_draw_90),
            },
            "reach_shootout": float(prediction.p_draw_after_et),
        },
        "shootout": {
            "conditional": {
                "home": float(getattr(
                    penalty_context,
                    "team_a_shootout_win_probability",
                    prediction.cond_home_wins_penalties_given_draw_after_et,
                )),
                "away": float(getattr(
                    penalty_context,
                    "team_b_shootout_win_probability",
                    prediction.cond_away_wins_penalties_given_draw_after_et,
                )),
            },
            "players": players,
            "coverage": coverage,
        },
    }


def _outcome(goals_a: int, goals_b: int) -> str:
    return "home" if goals_a > goals_b else "away" if goals_b > goals_a else "draw"


def _predicted_outcome(probabilities: dict[str, float]) -> str | None:
    return max(probabilities, key=probabilities.get) if probabilities else None


def _binary_brier(probability: float | None) -> float | None:
    return None if probability is None else (float(probability) - 1.0) ** 2


def _not_played() -> PhaseAuditSection:
    return PhaseAuditSection("not_played", None, None, None, None, None)


def evaluate_knockout_snapshot(
    snapshot: dict,
    phase_result: MatchPhaseResultInput | dict,
    kicks: Iterable[dict],
) -> KnockoutPhaseAudit:
    phase = _object_dict(phase_result)
    team_a = str(snapshot.get("team_a") or "")
    team_b = str(snapshot.get("team_b") or "")

    primary_by_key: dict[str, float] = {}
    for row in snapshot.get("primary", []):
        selection = str(row.get("selection_name") or "")
        key = "home" if selection == team_a else "away" if selection == team_b else "draw" if selection == "Draw" else None
        if key:
            primary_by_key[key] = float(row.get("probability") or 0.0)
    regulation_a = int(phase["regulation_goals_a"])
    regulation_b = int(phase["regulation_goals_b"])
    regulation_outcome = _outcome(regulation_a, regulation_b)
    regulation_probability = primary_by_key.get(regulation_outcome)
    regulation = PhaseAuditSection(
        "played",
        f"{regulation_a}-{regulation_b}",
        regulation_outcome,
        _predicted_outcome(primary_by_key),
        regulation_probability,
        _binary_brier(regulation_probability),
    )

    decided_in = str(phase["decided_in"])
    knockout = snapshot.get("knockout", {})
    if decided_in == "regulation":
        extra_time = _not_played()
    else:
        et_a = int(phase.get("extra_time_goals_a") or 0)
        et_b = int(phase.get("extra_time_goals_b") or 0)
        et_outcome = _outcome(et_a, et_b)
        conditional = {
            key: float(value)
            for key, value in knockout.get("extra_time", {}).get("conditional", {}).items()
        }
        observed = conditional.get(et_outcome)
        extra_time = PhaseAuditSection(
            "played",
            f"{et_a}-{et_b}",
            et_outcome,
            _predicted_outcome(conditional),
            observed,
            _binary_brier(observed),
            ({
                "expected_xg": knockout.get("extra_time", {}).get("expected_xg", []),
                "mode_score": knockout.get("extra_time", {}).get("mode_score"),
            },),
        )

    if decided_in != "shootout":
        shootout = _not_played()
    else:
        score_a = int(phase.get("shootout_goals_a") or 0)
        score_b = int(phase.get("shootout_goals_b") or 0)
        actual = "home" if score_a > score_b else "away"
        conditional = {
            key: float(value)
            for key, value in knockout.get("shootout", {}).get("conditional", {}).items()
        }
        observed = conditional.get(actual)
        frozen_players = {
            (str(row.get("team_name") or ""), str(row.get("player_name") or "")): row
            for row in knockout.get("shootout", {}).get("players", [])
        }
        kick_rows = []
        for kick in kicks:
            team_name = str(kick.get("team_name") or "")
            player_name = str(kick.get("player_name") or "")
            frozen = frozen_players.get((team_name, player_name), {})
            probability = float(frozen.get("conversion", GLOBAL_PENALTY_CONVERSION))
            scored = str(kick.get("outcome") or "") == "scored"
            kick_rows.append({
                "team_name": team_name,
                "player_name": player_name,
                "outcome": kick.get("outcome"),
                "predicted_conversion": probability,
                "on_field_probability": frozen.get("on_field_probability"),
                "first_five_probability": frozen.get("first_five_probability"),
                "brier": (probability - float(scored)) ** 2,
            })
        shootout = PhaseAuditSection(
            "played",
            f"{score_a}-{score_b}",
            actual,
            _predicted_outcome(conditional),
            observed,
            _binary_brier(observed),
            tuple(kick_rows),
        )
    return KnockoutPhaseAudit(regulation, extra_time, shootout)
