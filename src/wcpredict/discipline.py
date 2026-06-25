from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CardRecord:
    match_id: int
    kickoff_utc: datetime
    stage: str
    team_name: str
    player_name: str
    yellow_cards: int = 0
    red_cards: int = 0


@dataclass(frozen=True)
class DisciplineSuspension:
    team_name: str
    player_name: str
    event_type: str
    trigger_match_id: int
    affected_match_id: int
    reason: str


@dataclass(frozen=True)
class PlayerDisciplineSnapshot:
    team_name: str
    player_name: str
    yellow_cards: int = 0
    red_cards: int = 0


def yellow_card_period(stage: str) -> str:
    normalized = str(stage or "").casefold()
    if normalized.startswith("group"):
        return "group"
    if "round of 32" in normalized or "round of 16" in normalized or "quarter" in normalized:
        return "knockout_until_quarter"
    return "post_quarter_reset"


def suspension_events_for_records(
    records: list[CardRecord],
    next_match_by_team_and_trigger: dict[str, dict[int, int]],
    yellow_threshold: int = 2,
) -> list[DisciplineSuspension]:
    """Return automatic one-match bans implied by reviewed card records.

    2026 World Cup working rules:
    - red card: next-match suspension;
    - two yellow cards in the same accumulation period: next-match suspension;
    - yellow-card accumulation resets after group stage and after quarter-finals.

    Direct/indirect red details beyond the automatic next match remain a manual
    review problem, so this function only creates the guaranteed minimum ban.
    """
    yellow_counts: dict[tuple[str, str, str], int] = {}
    events: list[DisciplineSuspension] = []
    emitted: set[tuple[str, str, str, int]] = set()

    for record in sorted(records, key=lambda row: (row.kickoff_utc, row.match_id)):
        team_name = str(record.team_name)
        player_name = str(record.player_name)
        yellow_cards = max(0, int(record.yellow_cards or 0))
        red_cards = max(0, int(record.red_cards or 0))
        if not team_name or not player_name or (yellow_cards == 0 and red_cards == 0):
            continue
        affected_match_id = next_match_by_team_and_trigger.get(team_name, {}).get(record.match_id)

        if red_cards > 0:
            if affected_match_id is not None:
                key = (team_name, player_name, "suspension_red", int(affected_match_id))
                if key not in emitted:
                    emitted.add(key)
                    events.append(
                        DisciplineSuspension(
                            team_name=team_name,
                            player_name=player_name,
                            event_type="suspension_red",
                            trigger_match_id=record.match_id,
                            affected_match_id=int(affected_match_id),
                            reason="Roja revisada: sanción automática mínima de un partido.",
                        )
                    )
            # Avoid double-counting a second-yellow red as both red and
            # accumulation. Extra match bans need manual confirmation.
            yellow_counts[(team_name, player_name, yellow_card_period(record.stage))] = 0
            continue

        period = yellow_card_period(record.stage)
        count_key = (team_name, player_name, period)
        yellow_counts[count_key] = yellow_counts.get(count_key, 0) + yellow_cards
        if yellow_counts[count_key] >= yellow_threshold:
            if affected_match_id is not None:
                key = (team_name, player_name, "suspension_yellows", int(affected_match_id))
                if key not in emitted:
                    emitted.add(key)
                    events.append(
                        DisciplineSuspension(
                            team_name=team_name,
                            player_name=player_name,
                            event_type="suspension_yellows",
                            trigger_match_id=record.match_id,
                            affected_match_id=int(affected_match_id),
                            reason=f"{yellow_threshold} amarillas acumuladas en el periodo {period}.",
                        )
                    )
            yellow_counts[count_key] = 0

    return events


def snapshot_suspensions(
    snapshots: list[PlayerDisciplineSnapshot],
    next_match_by_team: dict[str, int],
    yellow_threshold: int = 2,
) -> list[DisciplineSuspension]:
    """Create pending bans from the current player-stat bank.

    This is intentionally conservative: it only says "this player should be
    treated as unavailable for the next known match" when the provider already
    shows a red card or at least the tournament yellow threshold. It does not
    try to infer multi-match extensions.
    """
    events: list[DisciplineSuspension] = []
    emitted: set[tuple[str, str, str, int]] = set()
    for snapshot in snapshots:
        team_name = str(snapshot.team_name)
        player_name = str(snapshot.player_name)
        affected_match_id = next_match_by_team.get(team_name)
        if not team_name or not player_name or affected_match_id is None:
            continue
        red_cards = max(0, int(snapshot.red_cards or 0))
        yellow_cards = max(0, int(snapshot.yellow_cards or 0))
        if red_cards > 0:
            key = (team_name, player_name, "suspension_red", int(affected_match_id))
            if key not in emitted:
                emitted.add(key)
                events.append(
                    DisciplineSuspension(
                        team_name=team_name,
                        player_name=player_name,
                        event_type="suspension_red",
                        trigger_match_id=0,
                        affected_match_id=int(affected_match_id),
                        reason="Roja en el banco de estadísticas de jugadores: sanción automática mínima de un partido.",
                    )
                )
            continue
        if yellow_cards >= yellow_threshold:
            key = (team_name, player_name, "suspension_yellows", int(affected_match_id))
            if key not in emitted:
                emitted.add(key)
                events.append(
                    DisciplineSuspension(
                        team_name=team_name,
                        player_name=player_name,
                        event_type="suspension_yellows",
                        trigger_match_id=0,
                        affected_match_id=int(affected_match_id),
                        reason=f"{yellow_cards} amarillas en el banco de estadísticas de jugadores.",
                    )
                )
    return events
