SECTIONS = {
    "event": "Resultado",
    "team": "Equipos",
    "lineup": "Alineaciones",
    "player": "Jugadores",
    "official": "Disciplina y contexto",
}


def _optional_number(value):
    import pandas as pd

    return None if pd.isna(value) else float(value)


def review_sections(rows: list[dict]) -> dict[str, list[dict]]:
    grouped = {label: [] for label in SECTIONS.values()}
    for row in rows:
        grouped[SECTIONS.get(row["subject_type"], "Disciplina y contexto")].append(
            row
        )
    return grouped


def can_finalize(rows: list[dict]) -> bool:
    return bool(rows) and all(
        row["review_status"] != "pending_review" for row in rows
    )


def render_capture_review(repo, match, evidence_dir):
    from datetime import datetime, timezone
    from pathlib import Path
    import pandas as pd
    import streamlit as st

    from wcpredict.review import CandidateDecision
    from wcpredict.screenshot_evidence import (
        ScreenshotUpload,
        classify_player_tables_from_ocr_rows,
        classify_sofascore_tokens,
        extract_ocr_tokens_and_rows,
        store_upload,
    )

    st.subheader("Capturas postpartido de SofaScore")
    st.caption(
        "Todas las lecturas requieren confirmación, corrección o descarte. "
        "La confianza OCR nunca sustituye la revisión."
    )
    uploads = st.file_uploader(
        "Añadir capturas",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        key=f"postmatch_images_{match.id}",
    )
    source_url = st.text_input(
        "URL pública de SofaScore (opcional)",
        key=f"postmatch_source_{match.id}",
    )
    batch_key = f"capture_batch_{match.id}"
    finalized_key = f"finalized_capture_batch_{match.id}"
    if st.button(
        "Procesar capturas",
        disabled=not uploads,
        key=f"process_captures_{match.id}",
        width="stretch",
    ):
        now = datetime.now(timezone.utc)
        batch_id = repo.create_screenshot_batch(match.id, source_url or None, now)
        stored_dir = Path(evidence_dir) / str(match.id) / str(batch_id)
        total = 0
        try:
            for upload in uploads or []:
                stored = store_upload(
                    ScreenshotUpload(upload.name, upload.type, upload.getvalue()),
                    stored_dir,
                )
                asset_id = repo.add_screenshot_asset(
                    batch_id,
                    stored.original_name,
                    stored.mime_type,
                    stored.byte_size,
                    stored.sha256,
                    str(stored.stored_path),
                    now,
                )
                tokens, ocr_rows = extract_ocr_tokens_and_rows(stored.stored_path)
                candidates = classify_sofascore_tokens(
                    tokens, match.team_a.name, match.team_b.name, asset_id
                )
                candidates.extend(
                    classify_player_tables_from_ocr_rows(
                        ocr_rows, match.team_a.name, match.team_b.name, asset_id
                    )
                )
                total += len(
                    repo.add_extraction_candidates(
                        batch_id, [candidate.__dict__ for candidate in candidates]
                    )
                )
        except (ImportError, RuntimeError) as exc:
            st.error(f"OCR local no disponible: {exc}")
        st.session_state[batch_key] = batch_id
        if total:
            st.success(f"{total} valores candidatos. Debes revisar todos.")
        else:
            st.warning(
                "No se reconocieron estadísticas automáticamente. "
                "Conservamos las capturas como evidencia."
            )

    batch_id = st.session_state.get(batch_key)
    if not batch_id:
        return None
    candidates = repo.list_extraction_candidates(int(batch_id))
    if not candidates:
        return None
    for stored_path in sorted({row["stored_path"] for row in candidates}):
        if Path(stored_path).exists():
            st.image(stored_path, caption=Path(stored_path).name)
    edited_frames = []
    decision_labels = {
        "pending_review": "Pendiente",
        "confirmed": "Confirmar",
        "corrected": "Corregir",
        "discarded": "Descartar",
    }
    for section, rows in review_sections(candidates).items():
        if not rows:
            continue
        st.markdown(f"#### {section}")
        frame = pd.DataFrame(rows)
        frame["decision"] = frame["review_status"].map(decision_labels)
        edited = st.data_editor(
            frame[
                [
                    "id",
                    "decision",
                    "subject_name",
                    "metric",
                    "period",
                    "value_number",
                    "value_text",
                    "unit",
                    "confidence",
                    "raw_label",
                    "raw_value",
                    "warnings_json",
                ]
            ],
            hide_index=True,
            disabled=["id", "confidence", "raw_label", "raw_value", "warnings_json"],
            column_config={
                "decision": st.column_config.SelectboxColumn(
                    "Revisión",
                    options=["Pendiente", "Confirmar", "Corregir", "Descartar"],
                    required=True,
                )
            },
            key=f"capture_review_{batch_id}_{section}",
            width="stretch",
        )
        edited_frames.append(edited)
    if st.button("Guardar decisiones de revisión", width="stretch"):
        now = datetime.now(timezone.utc)
        for frame in edited_frames:
            for row in frame.to_dict("records"):
                label = row["decision"]
                if label == "Pendiente":
                    continue
                decision_name = {
                    "Confirmar": "confirm",
                    "Corregir": "correct",
                    "Descartar": "discard",
                }[label]
                correction = decision_name == "correct"
                repo.review_candidate(
                    int(row["id"]),
                    CandidateDecision(
                        decision_name,
                        corrected_subject_name=row.get("subject_name") if correction else None,
                        corrected_metric=row.get("metric") if correction else None,
                        corrected_value_number=_optional_number(row.get("value_number")) if correction else None,
                        corrected_value_text=row.get("value_text") if correction else None,
                        corrected_unit=row.get("unit") if correction else None,
                        corrected_period=row.get("period") if correction else None,
                    ),
                    now,
                )
        st.success("Decisiones guardadas.")
        candidates = repo.list_extraction_candidates(int(batch_id))
    ready = can_finalize(candidates)
    if st.button(
        "Guardar capturas verificadas",
        type="primary",
        disabled=not ready,
        width="stretch",
    ):
        repo.finalize_screenshot_batch(int(batch_id), datetime.now(timezone.utc))
        st.session_state[finalized_key] = int(batch_id)
        st.success("Evidencia verificada guardada para análisis y calibración.")
        return int(batch_id)
    return st.session_state.get(finalized_key)
