from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


ATOMIC_PERIODS = (
    "first_half",
    "second_half",
    "extra_time_first",
    "extra_time_second",
)
CUMULATIVE_PERIODS = (
    "regulation_total",
    "extra_time_total",
    "full_match_total",
)
ALL_PERIODS = ATOMIC_PERIODS + CUMULATIVE_PERIODS
ADDITIVE_METRICS = frozenset({
    "goals",
    "xg",
    "shots",
    "shots_on_target",
    "corners",
    "yellow_cards",
    "red_cards",
    "saves",
    "goals_conceded",
})
SHOOTOUT_OUTCOMES = frozenset({"scored", "saved", "off_target_or_woodwork"})


@dataclass(frozen=True)
class MatchPhaseResultInput:
    regulation_goals_a: int
    regulation_goals_b: int
    extra_time_goals_a: int | None
    extra_time_goals_b: int | None
    shootout_goals_a: int | None
    shootout_goals_b: int | None
    decided_in: str


@dataclass(frozen=True)
class ShootoutKickInput:
    sequence_number: int
    team_id: int
    taker_player_id: int
    goalkeeper_player_id: int
    outcome: str


@dataclass(frozen=True)
class PeriodStatInput:
    team_id: int
    period: str
    metrics: dict[str, float | int | None]
    source_id: str
    content_sha256: str


@dataclass(frozen=True)
class PhaseValidationIssue:
    severity: str
    team_name: str
    metric: str
    calculated: float | None
    imported: float | None
    message: str

    @property
    def comparison(self) -> tuple[str, str, float | None, float | None]:
        return self.team_name, self.metric, self.calculated, self.imported


@dataclass(frozen=True)
class ShootoutSummary:
    goals_by_team: dict[int, int]
    goalkeeper_saves: dict[int, int]
    goalkeeper_faced: dict[int, int]
    winner_team_id: int | None
    errors: tuple[str, ...]


def validate_phase_result(value: MatchPhaseResultInput) -> tuple[str, ...]:
    errors: list[str] = []
    if value.decided_in not in {"regulation", "extra_time", "shootout"}:
        errors.append("La fase de decisión no es válida.")
    numeric = (
        value.regulation_goals_a,
        value.regulation_goals_b,
        value.extra_time_goals_a,
        value.extra_time_goals_b,
        value.shootout_goals_a,
        value.shootout_goals_b,
    )
    if any(item is not None and int(item) < 0 for item in numeric):
        errors.append("Los marcadores no pueden ser negativos.")
    et_values = (value.extra_time_goals_a, value.extra_time_goals_b)
    penalty_values = (value.shootout_goals_a, value.shootout_goals_b)
    if value.decided_in == "regulation":
        if any(item is not None for item in et_values):
            errors.append("Un partido decidido en 90 minutos no admite datos de prórroga.")
        if any(item is not None for item in penalty_values):
            errors.append("Un partido decidido en 90 minutos no admite tanda de penaltis.")
        if value.regulation_goals_a == value.regulation_goals_b:
            errors.append("Una eliminatoria decidida en 90 minutos no puede terminar empatada.")
    elif value.decided_in in {"extra_time", "shootout"}:
        if value.regulation_goals_a != value.regulation_goals_b:
            errors.append("La prórroga exige empate al 90'.")
        if any(item is None for item in et_values):
            errors.append("Faltan los goles marcados durante la prórroga.")
        else:
            total_a = value.regulation_goals_a + int(value.extra_time_goals_a or 0)
            total_b = value.regulation_goals_b + int(value.extra_time_goals_b or 0)
            if value.decided_in == "extra_time" and total_a == total_b:
                errors.append("Una eliminatoria decidida en prórroga necesita un ganador al 120'.")
            if value.decided_in == "shootout" and total_a != total_b:
                errors.append("La tanda exige empate al 120'.")
        if value.decided_in == "extra_time" and any(item is not None for item in penalty_values):
            errors.append("Un partido decidido en prórroga no admite marcador de tanda.")
        if value.decided_in == "shootout":
            if any(item is None for item in penalty_values):
                errors.append("Falta el marcador de la tanda.")
            elif value.shootout_goals_a == value.shootout_goals_b:
                errors.append("La tanda debe tener un ganador.")
    return tuple(errors)


def summarize_shootout(kicks: Iterable[ShootoutKickInput]) -> ShootoutSummary:
    goals: dict[int, int] = {}
    saves: dict[int, int] = {}
    faced: dict[int, int] = {}
    errors: list[str] = []
    for kick in kicks:
        goals.setdefault(kick.team_id, 0)
        saves.setdefault(kick.goalkeeper_player_id, 0)
        faced[kick.goalkeeper_player_id] = faced.get(kick.goalkeeper_player_id, 0) + 1
        if kick.outcome not in SHOOTOUT_OUTCOMES:
            errors.append(f"Resultado inválido en el lanzamiento {kick.sequence_number}.")
            continue
        if kick.outcome == "scored":
            goals[kick.team_id] += 1
        elif kick.outcome == "saved":
            saves[kick.goalkeeper_player_id] += 1
    return ShootoutSummary(goals, saves, faced, None, tuple(errors))


def validate_shootout_sequence(kicks: tuple[ShootoutKickInput, ...]) -> ShootoutSummary:
    base = summarize_shootout(kicks)
    errors = list(base.errors)
    if not kicks:
        return ShootoutSummary(base.goals_by_team, base.goalkeeper_saves, base.goalkeeper_faced, None, ("La tanda no contiene lanzamientos.",))
    ordered = tuple(sorted(kicks, key=lambda item: item.sequence_number))
    if tuple(item.sequence_number for item in ordered) != tuple(range(1, len(ordered) + 1)):
        errors.append("El orden de lanzamientos debe ser consecutivo desde 1.")
    teams: list[int] = []
    for kick in ordered:
        if kick.team_id not in teams:
            teams.append(kick.team_id)
    if len(teams) != 2:
        errors.append("La tanda debe contener exactamente dos selecciones.")
        return ShootoutSummary(base.goals_by_team, base.goalkeeper_saves, base.goalkeeper_faced, None, tuple(errors))
    for index, kick in enumerate(ordered):
        if kick.team_id != teams[index % 2]:
            errors.append("Los lanzamientos deben alternar entre las dos selecciones.")
            break

    goals = {team: 0 for team in teams}
    attempts = {team: 0 for team in teams}
    winner: int | None = None
    for index, kick in enumerate(ordered):
        attempts[kick.team_id] += 1
        if kick.outcome == "scored":
            goals[kick.team_id] += 1
        a, b = teams
        if attempts[a] <= 5 and attempts[b] <= 5:
            remaining_a = 5 - attempts[a]
            remaining_b = 5 - attempts[b]
            if goals[a] > goals[b] + remaining_b:
                winner = a
            elif goals[b] > goals[a] + remaining_a:
                winner = b
        elif attempts[a] == attempts[b] and goals[a] != goals[b]:
            winner = a if goals[a] > goals[b] else b
        if winner is not None and index != len(ordered) - 1:
            errors.append("Hay lanzamientos posteriores a la finalización de la tanda.")
            break
    if winner is None:
        errors.append("La secuencia todavía no produce un ganador válido.")
    return ShootoutSummary(goals, base.goalkeeper_saves, base.goalkeeper_faced, winner, tuple(errors))


def aggregate_additive_periods(
    rows: list[dict], periods: tuple[str, ...]
) -> dict[tuple[int, str], float]:
    totals: dict[tuple[int, str], float] = {}
    for row in rows:
        if row.get("period") not in periods:
            continue
        team_id = int(row["team_id"])
        for metric in ADDITIVE_METRICS:
            value = row.get(metric)
            if value is not None:
                key = team_id, metric
                totals[key] = totals.get(key, 0.0) + float(value)
    return totals


def validate_period_totals(rows: list[dict]) -> list[PhaseValidationIssue]:
    issues: list[PhaseValidationIssue] = []
    by_team_period = {
        (int(row["team_id"]), str(row["period"])): row for row in rows
    }
    team_names = {
        int(row["team_id"]): str(row.get("team_name") or row["team_id"]) for row in rows
    }
    comparisons = (
        ((
            "first_half", "second_half",
            "extra_time_first", "extra_time_second",
        ), "full_match_total"),
    )
    for team_id, team_name in team_names.items():
        for atomic, total_period in comparisons:
            total_row = by_team_period.get((team_id, total_period))
            atomic_rows = [by_team_period.get((team_id, period)) for period in atomic]
            if total_row is None or any(row is None for row in atomic_rows):
                continue
            for metric in ADDITIVE_METRICS:
                imported = total_row.get(metric)
                values = [row.get(metric) for row in atomic_rows if row is not None]
                if imported is None or any(value is None for value in values):
                    continue
                calculated = sum(float(value) for value in values)
                tolerance = 0.02 if metric == "xg" else 0.0
                if abs(calculated - float(imported)) > tolerance:
                    issues.append(PhaseValidationIssue(
                        "blocking",
                        team_name,
                        metric,
                        calculated,
                        float(imported),
                        f"{team_name}: {metric} suma {calculated:g}, pero el acumulado indica {float(imported):g}.",
                    ))
    return issues


def regulation_projection(rows: list[dict]) -> dict[int, dict[str, float | int | None]]:
    by_team_period = {
        (int(row["team_id"]), str(row["period"])): row for row in rows
    }
    team_ids = {int(row["team_id"]) for row in rows}
    output: dict[int, dict[str, float | int | None]] = {}
    for team_id in team_ids:
        total = by_team_period.get((team_id, "regulation_total"))
        if total is not None:
            output[team_id] = {metric: total.get(metric) for metric in ADDITIVE_METRICS}
            output[team_id]["possession"] = total.get("possession")
            continue
        halves = [
            by_team_period.get((team_id, "first_half")),
            by_team_period.get((team_id, "second_half")),
        ]
        metrics: dict[str, float | int | None] = {}
        for metric in ADDITIVE_METRICS:
            values = [row.get(metric) for row in halves if row is not None and row.get(metric) is not None]
            metrics[metric] = sum(float(value) for value in values) if values else None
        metrics["possession"] = None
        output[team_id] = metrics
    return output
