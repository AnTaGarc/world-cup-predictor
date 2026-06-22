from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from wcpredict.names import same_team


@dataclass(frozen=True)
class TeamPlayerAdjustment:
    team_name: str
    attack_factor: float
    defense_factor: float
    confidence: str
    sample_minutes: float
    lineup_uncertainty: float
    audit: tuple[str, ...]


def _value(row: dict[str, Any], name: str) -> float:
    try:
        return float(row.get(name) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _participation(row: dict[str, Any]) -> float:
    status = str(row.get("availability") or "available").casefold()
    if status in {"out", "suspended", "injured", "absent"}:
        return 0.0
    probability = max(0.0, min(1.0, _value(row, "starter_probability") if row.get("starter_probability") is not None else 1.0))
    return probability


def build_team_player_adjustment(rows: list[dict[str, Any]], team_name: str) -> TeamPlayerAdjustment:
    selected = [row for row in rows if same_team(str(row.get("team_name") or ""), team_name)]
    if not selected:
        return TeamPlayerAdjustment(team_name, 1.0, 1.0, "no_evidence", 0.0, 1.0, ())

    attack_full = attack_active = defense_full = defense_active = 0.0
    total_minutes = 0.0
    uncertainty_sum = 0.0
    audit: list[str] = []
    for row in selected:
        minutes = max(1.0, _value(row, "minutes"))
        expected_minutes = max(0.0, min(90.0, _value(row, "expected_minutes") or min(90.0, minutes)))
        exposure = expected_minutes / 90.0
        participation = _participation(row)
        goals_p90 = 90.0 * _value(row, "goals") / minutes
        assists_p90 = 90.0 * _value(row, "assists") / minutes
        sot_p90 = 90.0 * _value(row, "shots_on_target") / minutes
        tackles_p90 = 90.0 * _value(row, "tackles_won") / minutes
        interceptions_p90 = 90.0 * _value(row, "interceptions") / minutes
        saves = _value(row, "save_percentage") / 100.0
        attack_score = exposure * (goals_p90 + 0.5 * assists_p90 + 0.08 * sot_p90)
        defense_score = exposure * (0.05 * tackles_p90 + 0.05 * interceptions_p90 + 0.25 * saves)
        attack_full += attack_score
        attack_active += attack_score * participation
        defense_full += defense_score
        defense_active += defense_score * participation
        total_minutes += minutes
        uncertainty_sum += 1.0 - participation
        raw_status = str(row.get("availability") or "available").casefold()
        status = {
            "available": "disponible",
            "out": "baja confirmada",
            "doubtful": "duda",
            "injured": "lesionado",
            "suspended": "sancionado",
        }.get(raw_status, raw_status)
        audit.append(f"{row.get('player_name') or 'Jugador'}: {status}, participación {participation:.0%}, {minutes:.0f} min de muestra")

    coverage = min(1.0, total_minutes / max(360.0, len(selected) * 180.0))

    uncertainty = uncertainty_sum / len(selected)
    confidence = "high" if total_minutes >= 900 and uncertainty <= 0.15 else "medium" if total_minutes >= 360 else "low"
    cap = {"high": 1.25, "medium": 1.18}.get(confidence, 1.15)
    lower = 2.0 - cap
    slope = (cap - lower) / 1.5

    def factor(active: float, full: float) -> float:
        ratio = active / full if full > 0 else 1.0
        raw = max(lower, min(cap, lower + slope * ratio))
        return max(lower, min(cap, 1.0 + (raw - 1.0) * coverage))
    return TeamPlayerAdjustment(
        team_name,
        factor(attack_active, attack_full),
        factor(defense_active, defense_full),
        confidence,
        total_minutes,
        uncertainty,
        tuple(audit),
    )


def adjust_expected_goals(
    xg_a: float,
    xg_b: float,
    team_a: TeamPlayerAdjustment,
    team_b: TeamPlayerAdjustment,
) -> tuple[float, float, str]:
    adjusted_a = max(0.05, xg_a * team_a.attack_factor / team_b.defense_factor)
    adjusted_b = max(0.05, xg_b * team_b.attack_factor / team_a.defense_factor)
    audit = tuple(team_a.audit) + tuple(team_b.audit)
    explanation = "Sin ajuste de jugadores: no hay evidencia utilizable."
    if audit:
        visible_audit = audit[:12]
        remaining = len(audit) - len(visible_audit)
        audit_text = " | ".join(visible_audit)
        if remaining:
            audit_text += f" | {remaining} jugadores más en la tabla de detalle"
        explanation = (
            f"Ajuste de jugadores: ataque/defensa {team_a.team_name} {team_a.attack_factor:.3f}/{team_a.defense_factor:.3f}; "
            f"{team_b.team_name} {team_b.attack_factor:.3f}/{team_b.defense_factor:.3f}. "
            + audit_text
        )
    return adjusted_a, adjusted_b, explanation
