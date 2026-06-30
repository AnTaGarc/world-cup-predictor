from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wcpredict.historical_shootouts import import_historical_shootout_csv
from wcpredict.repository import Repository
from wcpredict.transfermarkt_penalties import active_knockout_teams


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Importa tandas internacionales revisadas de selecciones aún vivas."
    )
    parser.add_argument("--db", default=str(ROOT / "data" / "worldcup.sqlite"))
    parser.add_argument(
        "--coverage",
        default=str(ROOT / "data" / "fixtures" / "active_team_shootout_coverage.csv"),
    )
    parser.add_argument(
        "--kicks",
        default=str(ROOT / "data" / "fixtures" / "active_team_shootout_kicks.csv"),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    repo = Repository(Path(args.db))
    repo.initialize()
    active = set(active_knockout_teams(repo))
    summary = import_historical_shootout_csv(
        repo,
        Path(args.coverage),
        Path(args.kicks),
        active_teams=active,
        dry_run=args.dry_run,
    )
    print(f"Equipos vivos: {len(active)}")
    print(f"Coberturas seleccionadas: {summary.coverage_rows}")
    print(f"Tandas revisadas: {summary.shootout_rows}")
    print(f"Lanzamientos revisados: {summary.kick_rows}")
    print("Modo: solo validación" if args.dry_run else "Modo: importación")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
