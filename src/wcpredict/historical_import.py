from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
import csv

from wcpredict.names import canonical_team_name


@dataclass(frozen=True)
class HistoricalMatchInput:
    played_on: date
    team_a: str
    team_b: str
    goals_a: int
    goals_b: int
    tournament: str
    city: str
    country: str
    neutral_site: bool
    source_ids: tuple[str, ...]

    @property
    def identity(self) -> tuple:
        return (
            self.played_on,
            self.team_a,
            self.team_b,
            self.goals_a,
            self.goals_b,
        )


def read_results_csv(path: Path, source_id: str) -> list[HistoricalMatchInput]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = csv.DictReader(handle)
        parsed = []
        for row in rows:
            try:
                goals_a = int(row["home_score"])
                goals_b = int(row["away_score"])
            except (KeyError, TypeError, ValueError):
                continue
            parsed.append(HistoricalMatchInput(
                played_on=date.fromisoformat(row["date"]),
                team_a=canonical_team_name(row["home_team"]),
                team_b=canonical_team_name(row["away_team"]),
                goals_a=goals_a,
                goals_b=goals_b,
                tournament=row["tournament"],
                city=row.get("city", ""),
                country=row.get("country", ""),
                neutral_site=row.get("neutral", "FALSE").upper() == "TRUE",
                source_ids=(source_id,),
            ))
        return parsed


def merge_international_sources(
    *groups: list[HistoricalMatchInput],
) -> list[HistoricalMatchInput]:
    merged: dict[tuple, HistoricalMatchInput] = {}
    for row in (item for group in groups for item in group):
        previous = merged.get(row.identity)
        if previous is None:
            merged[row.identity] = row
        else:
            merged[row.identity] = replace(
                previous,
                source_ids=tuple(
                    sorted(set(previous.source_ids + row.source_ids))
                ),
            )
    return sorted(merged.values(), key=lambda item: item.identity)
