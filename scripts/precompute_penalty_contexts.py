from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wcpredict.penalty_context_cache import (
    build_repository_penalty_context,
    group_stage_complete,
    load_precomputed_context,
    repository_penalty_input_fingerprint,
    save_precomputed_context,
)
from wcpredict.penalty_history_model import DEFAULT_SIMULATIONS, PENALTY_MODEL_VERSION
from wcpredict.repository import Repository


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Precalcula contextos de penaltis desplegables sin bloquear Streamlit."
    )
    parser.add_argument("--db", default=str(ROOT / "data" / "worldcup.sqlite"))
    parser.add_argument(
        "--output-dir", default=str(ROOT / "data" / "precomputed" / "penalties")
    )
    parser.add_argument("--match-id", type=int, action="append", help="Limita el cálculo a uno o más partidos.")
    parser.add_argument("--simulations", type=int, default=DEFAULT_SIMULATIONS)
    parser.add_argument("--force", action="store_true", help="Regenera artefactos válidos existentes.")
    parser.add_argument(
        "--allow-incomplete", action="store_true",
        help="Solo para pruebas: permite calcular antes de cerrar los tres partidos de grupo.",
    )
    args = parser.parse_args()

    repo = Repository(Path(args.db))
    repo.initialize()
    output_dir = Path(args.output_dir)
    selected_ids = set(args.match_id or [])
    candidates = [
        match for match in repo.list_matches()
        if str(match.stage or "").startswith("Round of 32")
        and (not selected_ids or match.id in selected_ids)
    ]
    if not candidates:
        print("No hay cruces de dieciseisavos resueltos para precalcular.")
        return 0

    generated = skipped = 0
    for match in candidates:
        team_a, team_b = match.team_a.name, match.team_b.name
        if not args.allow_incomplete and not (
            group_stage_complete(repo, team_a) and group_stage_complete(repo, team_b)
        ):
            print(f"PENDIENTE {team_a} vs {team_b}: fase de grupos incompleta.")
            skipped += 1
            continue
        current_fingerprint = repository_penalty_input_fingerprint(
            repo, match, model_version=PENALTY_MODEL_VERSION
        )
        if not args.force and load_precomputed_context(
            output_dir,
            team_a,
            team_b,
            model_version=PENALTY_MODEL_VERSION,
            expected_input_fingerprint=current_fingerprint,
        ) is not None:
            print(f"VIGENTE {team_a} vs {team_b}")
            skipped += 1
            continue
        context, fingerprint = build_repository_penalty_context(
            repo, match, simulations=args.simulations, model_version=PENALTY_MODEL_VERSION
        )
        if not context.player_rows:
            print(f"PENDIENTE {team_a} vs {team_b}: faltan convocatorias completas.")
            skipped += 1
            continue
        target = save_precomputed_context(
            output_dir,
            match.id,
            team_a,
            team_b,
            context,
            input_fingerprint=fingerprint,
            model_version=PENALTY_MODEL_VERSION,
        )
        generated += 1
        print(
            f"GENERADO {team_a} vs {team_b}: {context.simulations:,} escenarios · "
            f"{context.team_a_shootout_win_probability:.1%}/{context.team_b_shootout_win_probability:.1%} · {target}"
        )
    print(f"Resumen: {generated} generado(s), {skipped} pendiente(s)/vigente(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
