from dataclasses import dataclass
from pathlib import Path
import csv


@dataclass(frozen=True)
class TransfermarktPackage:
    national_teams: list[dict]
    players: list[dict]


def _rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_transfermarkt_directory(directory: Path) -> TransfermarktPackage:
    return TransfermarktPackage(
        national_teams=_rows(directory / "national_teams.csv"),
        players=_rows(directory / "players.csv"),
    )
