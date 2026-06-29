from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wcpredict.database import initialize_database
from wcpredict.knockout_bracket import resolve_knockout_bracket, seed_knockout_bracket
from wcpredict.repository import Repository
from wcpredict.transfermarkt_penalties import (
    eligible_penalty_teams,
    fetch_html,
    load_penalty_team_snapshot,
    parse_penalty_attempts,
    penalty_urls,
    player_targets_for_teams,
    reconcile_penalty_teams,
    search_transfermarkt_player,
    write_identity_review,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch Transfermarkt penalty history for WC knockout-qualified teams."
    )
    parser.add_argument("--db", default=str(ROOT / "data" / "worldcup.sqlite"))
    parser.add_argument("--knockout-csv", default=str(ROOT / "data" / "fixtures" / "world_cup_2026_knockouts.csv"))
    parser.add_argument(
        "--team-snapshot",
        default=str(ROOT / "data" / "fixtures" / "world_cup_2026_penalty_teams.csv"),
        help="Canonical qualified-team snapshot used unless --teams is supplied.",
    )
    parser.add_argument("--cache-dir", default=str(ROOT / "data" / "cache" / "transfermarkt_penalties"))
    parser.add_argument("--review-csv", default=str(ROOT / "output" / "penalty_identity_review.csv"))
    parser.add_argument("--teams", nargs="*", help="Optional explicit team list; otherwise uses bracket/group status.")
    parser.add_argument("--resolve-ids", action="store_true", help="Search Transfermarkt for missing player ids.")
    parser.add_argument("--auto-confidence", type=float, default=0.95)
    parser.add_argument("--refresh", action="store_true", help="Ignore cached Transfermarkt HTML.")
    parser.add_argument("--dry-run", action="store_true", help="Do not write penalty_attempts.")
    args = parser.parse_args()

    db_path = Path(args.db)
    initialize_database(db_path)
    repo = Repository(db_path)
    knockout_csv = Path(args.knockout_csv)
    if knockout_csv.exists():
        seed_knockout_bracket(repo, knockout_csv)
        resolve_knockout_bracket(repo)

    dynamic_teams = eligible_penalty_teams(repo)
    snapshot_path = Path(args.team_snapshot)
    snapshot_teams = load_penalty_team_snapshot(snapshot_path) if snapshot_path.exists() else []
    teams = args.teams or snapshot_teams or dynamic_teams
    if not teams:
        print("No hay selecciones elegibles todavía.")
        return 0

    if snapshot_teams:
        reconciliation = reconcile_penalty_teams(snapshot_teams, dynamic_teams)
        if reconciliation["missing_from_bracket"]:
            print(
                "Pendientes de aparecer en el cuadro dinámico: "
                + ", ".join(reconciliation["missing_from_bracket"])
            )
        if reconciliation["unexpected_in_bracket"]:
            print(
                "Equipos dinámicos fuera de la instantánea: "
                + ", ".join(reconciliation["unexpected_in_bracket"])
            )

    targets = player_targets_for_teams(repo, teams)
    print(f"Selecciones: {', '.join(teams)}")
    print(f"Jugadores de convocatoria detectados: {len(targets)}")

    cache_dir = Path(args.cache_dir)
    review_candidates = []
    missing = []
    fetched_players = 0
    saved_attempts = 0
    fetched_at = datetime.now(timezone.utc)

    for target in targets:
        transfermarkt_id = target.transfermarkt_player_id
        if transfermarkt_id is None and args.resolve_ids:
            candidate = search_transfermarkt_player(
                target.player_name, target.team_name, cache_dir, refresh=args.refresh,
            )
            if candidate is not None:
                review_candidates.append(candidate)
                if candidate.confidence >= args.auto_confidence:
                    transfermarkt_id = candidate.transfermarkt_player_id
                    if not args.dry_run:
                        repo.save_transfermarkt_player_identity(
                            target.player_name,
                            target.team_name,
                            transfermarkt_id,
                            {
                                "candidate_name": candidate.candidate_name,
                                "confidence": candidate.confidence,
                                "reason": candidate.reason,
                                "url": candidate.url,
                            },
                        )
            else:
                missing.append(target)
        elif transfermarkt_id is None:
            missing.append(target)

        if transfermarkt_id is None:
            continue
        attempts = []
        failed_pages = 0
        for default_outcome, url in penalty_urls(target.player_name, transfermarkt_id):
            try:
                html = fetch_html(url, cache_dir, refresh=args.refresh)
            except Exception as exc:
                failed_pages += 1
                print(
                    f"ERROR {target.player_name} ({target.team_name}, {default_outcome}): "
                    f"{type(exc).__name__}: {exc}"
                )
                continue
            attempts.extend(parse_penalty_attempts(
                html,
                player_name=target.player_name,
                team_name=target.team_name,
                transfermarkt_player_id=transfermarkt_id,
                source_url=url,
                fetched_at_utc=fetched_at,
                default_outcome=default_outcome,
            ))
        if failed_pages == 2:
            continue
        fetched_players += 1
        if not args.dry_run:
            saved_attempts += repo.save_penalty_attempts(attempts)
        print(f"{target.team_name} | {target.player_name}: {len(attempts)} penaltis")

    write_identity_review(Path(args.review_csv), review_candidates, missing)
    print(f"Jugadores consultados: {fetched_players}")
    print(f"Intentos guardados/actualizados: {saved_attempts}")
    print(f"Revisión de identidades: {args.review_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
