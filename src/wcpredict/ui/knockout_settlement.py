from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from wcpredict.match_phases import (
    MatchPhaseResultInput,
    PhaseValidationIssue,
    ShootoutKickInput,
    validate_phase_result,
    validate_shootout_sequence,
)


PERIOD_LABELS = {
    "first_half": "Primera parte (0'–45')",
    "second_half": "Segunda parte (45'–90')",
    "regulation_total": "Acumulado de 90'",
    "extra_time_first": "Primera parte de la prórroga (90'–105')",
    "extra_time_second": "Segunda parte de la prórroga (105'–120')",
    "extra_time_total": "Acumulado de prórroga",
    "full_match_total": "Acumulado de 120' (opcional)",
}
REGULATION_PARTS = {"first_half", "second_half"}
EXTRA_TIME_PARTS = {"extra_time_first", "extra_time_second"}
EXTENDED_REQUIRED_PERIODS = REGULATION_PARTS | EXTRA_TIME_PARTS


@dataclass(frozen=True)
class SettlementSections:
    visible_periods: tuple[str, ...]
    show_extra_time_score: bool
    show_shootout: bool


@dataclass(frozen=True)
class KnockoutSettlementDraft:
    phase_result: MatchPhaseResultInput
    kicks: tuple[ShootoutKickInput, ...]
    imported_periods: frozenset[str]
    goalkeeper_a_id: int | None
    goalkeeper_b_id: int | None


def build_settlement_sections(decided_in: str) -> SettlementSections:
    if decided_in == "regulation":
        return SettlementSections(("regulation_total",), False, False)
    extended = (
        "first_half",
        "second_half",
        "extra_time_first",
        "extra_time_second",
        "full_match_total",
    )
    return SettlementSections(extended, True, decided_in == "shootout")


def period_statuses(
    decided_in: str,
    imported_periods: set[str],
    issues: list[PhaseValidationIssue],
) -> dict[str, str]:
    visible = set(build_settlement_sections(decided_in).visible_periods)
    has_mismatch = any(issue.severity == "blocking" for issue in issues)
    output = {}
    for period in PERIOD_LABELS:
        if period not in visible:
            output[period] = "not_played"
        elif has_mismatch and period == "full_match_total" and period in imported_periods:
            output[period] = "mismatch"
        elif period in imported_periods:
            output[period] = "imported"
        elif period == "full_match_total":
            output[period] = "optional"
        else:
            output[period] = "pending"
    return output


def validate_settlement_draft(draft: KnockoutSettlementDraft) -> tuple[str, ...]:
    errors = list(validate_phase_result(draft.phase_result))
    imported = set(draft.imported_periods)
    if draft.phase_result.decided_in == "regulation" and "regulation_total" not in imported:
        errors.append(
            "Faltan las estadísticas de los 90 minutos: importa el acumulado de 90'."
        )
    if draft.phase_result.decided_in in {"extra_time", "shootout"}:
        if EXTENDED_REQUIRED_PERIODS - imported:
            errors.append(
                "Faltan estadísticas por partes: importa la primera y segunda parte "
                "de los 90 minutos y la primera y segunda parte de la prórroga."
            )
    if draft.phase_result.decided_in == "shootout":
        if draft.goalkeeper_a_id is None or draft.goalkeeper_b_id is None:
            errors.append("Debes seleccionar el portero de ambas selecciones.")
        errors.extend(validate_shootout_sequence(draft.kicks).errors)
    elif draft.kicks:
        errors.append("Solo puede haber lanzamientos cuando el partido termina en penaltis.")
    return tuple(dict.fromkeys(errors))


def render_knockout_settlement(
    repo,
    match,
    evidence_dir: Path,
    batch_id: int | None = None,
) -> int | None:
    import pandas as pd
    import streamlit as st

    from wcpredict.deep_match_import import load_deep_match_file

    active = repo.get_active_match_phase_result(match.id) or {}
    decision_labels = {
        "En 90 minutos": "regulation",
        "En prórroga": "extra_time",
        "En penaltis": "shootout",
    }
    default_decision = str(active.get("decided_in") or "regulation")
    default_label = next(
        label for label, value in decision_labels.items() if value == default_decision
    )
    chosen_label = st.radio(
        "¿Cómo terminó la eliminatoria?",
        tuple(decision_labels),
        index=list(decision_labels).index(default_label),
        horizontal=True,
        key=f"ko_decision_{match.id}",
    )
    decided_in = decision_labels[chosen_label]
    sections = build_settlement_sections(decided_in)

    period_rows = repo.list_team_match_period_stats(match.id)
    imported_periods = {str(row["period"]) for row in period_rows}
    issues = repo.validate_match_period_stats(match.id)
    statuses = period_statuses(decided_in, imported_periods, issues)
    status_labels = {
        "imported": "✅ Importado",
        "pending": "🟡 Pendiente",
        "optional": "Opcional",
        "not_played": "No disputado",
        "mismatch": "🔴 No cuadra",
    }
    st.markdown("#### Estadísticas por periodo")
    if decided_in == "regulation":
        st.caption("Si el partido terminó en 90', importa el JSON acumulado de los 90 minutos.")
    else:
        st.caption(
            "Importa por separado las dos partes de los 90 minutos y las dos partes "
            "de la prórroga. El acumulado de 120' es opcional y sirve para comprobar las sumas."
        )
    for period in sections.visible_periods:
        status = statuses[period]
        with st.expander(f"{PERIOD_LABELS[period]} · {status_labels[status]}"):
            upload = st.file_uploader(
                f"JSON · {PERIOD_LABELS[period]}",
                type=["json"],
                key=f"ko_period_upload_{match.id}_{period}",
            )
            reviewed = st.checkbox(
                "He comprobado que corresponde a este periodo",
                key=f"ko_period_reviewed_{match.id}_{period}",
            )
            if st.button(
                "Validar e importar",
                disabled=upload is None or not reviewed,
                key=f"ko_period_import_{match.id}_{period}",
            ):
                content = upload.getvalue()
                evidence_dir.mkdir(parents=True, exist_ok=True)
                stored = evidence_dir / f"{sha256(content).hexdigest()}.json"
                stored.write_bytes(content)
                try:
                    result = repo.import_deep_match_period(
                        load_deep_match_file(stored),
                        imported_at_utc=datetime.now(timezone.utc),
                        intended_match_id=match.id,
                        period=period,
                    )
                    st.success(
                        f"Periodo importado: {result.imported_matches}; "
                        f"sin cambios: {result.unchanged_matches}."
                    )
                except (ValueError, OSError) as exc:
                    st.error(str(exc))
    if issues:
        for issue in issues:
            st.error(issue.message)

    score_cols = st.columns(2)
    regulation_a = int(score_cols[0].number_input(
        f"Goles al 90' · {match.team_a.name}",
        0, 20, int(active.get("regulation_goals_a") or 0),
        key=f"ko_reg_a_{match.id}",
    ))
    regulation_b = int(score_cols[1].number_input(
        f"Goles al 90' · {match.team_b.name}",
        0, 20, int(active.get("regulation_goals_b") or 0),
        key=f"ko_reg_b_{match.id}",
    ))
    extra_a = extra_b = None
    if sections.show_extra_time_score:
        et_cols = st.columns(2)
        extra_a = int(et_cols[0].number_input(
            f"Goles en prórroga · {match.team_a.name}",
            0, 10, int(active.get("extra_time_goals_a") or 0),
            key=f"ko_et_a_{match.id}",
        ))
        extra_b = int(et_cols[1].number_input(
            f"Goles en prórroga · {match.team_b.name}",
            0, 10, int(active.get("extra_time_goals_b") or 0),
            key=f"ko_et_b_{match.id}",
        ))

    kicks: tuple[ShootoutKickInput, ...] = ()
    keeper_a_id = keeper_b_id = None
    shootout_a = shootout_b = None
    if sections.show_shootout:
        st.markdown("#### Tanda de penaltis")
        squad_a = repo.list_selectable_squad_players(match.team_a.id, match.team_a.name)
        squad_b = repo.list_selectable_squad_players(match.team_b.id, match.team_b.name)
        keepers_a = [row for row in squad_a if "GK" in str(row.get("position") or "").upper()]
        keepers_b = [row for row in squad_b if "GK" in str(row.get("position") or "").upper()]
        keeper_cols = st.columns(2)
        keeper_a_name = keeper_cols[0].selectbox(
            f"Portero · {match.team_a.name}",
            [row["player_name"] for row in keepers_a],
            key=f"ko_keeper_a_{match.id}",
        ) if keepers_a else None
        keeper_b_name = keeper_cols[1].selectbox(
            f"Portero · {match.team_b.name}",
            [row["player_name"] for row in keepers_b],
            key=f"ko_keeper_b_{match.id}",
        ) if keepers_b else None
        keeper_a_id = next((row["player_id"] for row in keepers_a if row["player_name"] == keeper_a_name), None)
        keeper_b_id = next((row["player_id"] for row in keepers_b if row["player_name"] == keeper_b_name), None)
        existing_kicks = repo.list_active_shootout_kicks(match.id)
        kick_count = int(st.number_input(
            "Lanzamientos registrados",
            2, 30, max(10, len(existing_kicks)), 2,
            key=f"ko_kick_count_{match.id}",
        ))
        kick_values = []
        outcome_labels = {
            "Gol": "scored",
            "Parada": "saved",
            "Fuera/poste": "off_target_or_woodwork",
        }
        for index in range(kick_count):
            team_is_a = index % 2 == 0
            team = match.team_a if team_is_a else match.team_b
            squad = squad_a if team_is_a else squad_b
            opposing_keeper = keeper_b_id if team_is_a else keeper_a_id
            cols = st.columns([0.7, 2.2, 1.4])
            cols[0].markdown(f"**{index + 1}. {team.name}**")
            names = [row["player_name"] for row in squad]
            previous = existing_kicks[index] if index < len(existing_kicks) else None
            default_name = previous.get("player_name") if previous else None
            player_name = cols[1].selectbox(
                "Tirador",
                names,
                index=names.index(default_name) if default_name in names else 0,
                key=f"ko_taker_{match.id}_{index}",
                label_visibility="collapsed",
            ) if names else None
            default_outcome = previous.get("outcome") if previous else "scored"
            default_label_outcome = next(
                (label for label, value in outcome_labels.items() if value == default_outcome),
                "Gol",
            )
            outcome_label = cols[2].selectbox(
                "Resultado",
                tuple(outcome_labels),
                index=list(outcome_labels).index(default_label_outcome),
                key=f"ko_outcome_{match.id}_{index}",
                label_visibility="collapsed",
            )
            player_id = next((row["player_id"] for row in squad if row["player_name"] == player_name), None)
            if player_id is not None and opposing_keeper is not None:
                kick_values.append(ShootoutKickInput(
                    index + 1, team.id, int(player_id), int(opposing_keeper), outcome_labels[outcome_label]
                ))
        kicks = tuple(kick_values)
        shootout_a = sum(kick.team_id == match.team_a.id and kick.outcome == "scored" for kick in kicks)
        shootout_b = sum(kick.team_id == match.team_b.id and kick.outcome == "scored" for kick in kicks)
        st.caption(f"Marcador calculado de la tanda: {shootout_a}-{shootout_b}")

    phase_result = MatchPhaseResultInput(
        regulation_a,
        regulation_b,
        extra_a,
        extra_b,
        shootout_a,
        shootout_b,
        decided_in,
    )
    draft = KnockoutSettlementDraft(
        phase_result,
        kicks,
        frozenset(imported_periods),
        keeper_a_id,
        keeper_b_id,
    )
    errors = list(validate_settlement_draft(draft))
    errors.extend(issue.message for issue in issues if issue.severity == "blocking")
    if st.button("Guardar borrador", key=f"ko_save_draft_{match.id}"):
        st.info("Borrador conservado en esta sesión. Los periodos importados ya están guardados.")
    if errors:
        st.warning("No se puede cerrar todavía: " + " ".join(dict.fromkeys(errors)))
    if st.button(
        "Cerrar eliminatoria y recalibrar",
        type="primary",
        disabled=bool(errors),
        key=f"ko_finalize_{match.id}",
        width="stretch",
    ):
        return repo.settle_knockout_match_versioned(
            match.id,
            phase_result,
            kicks,
            batch_id,
            datetime.now(timezone.utc),
        )
    return None
