from __future__ import annotations

from typing import Any
from wcpredict.ui.translations import localize_model


def _bullets(rows: list[str], empty: str) -> str:
    return "\n".join(f"- {row}" for row in rows) if rows else f"- {empty}"


def build_prediction_report(
    *,
    team_a: str,
    team_b: str,
    probabilities: dict[str, float],
    form_notes: list[str],
    player_notes: list[str],
    context_notes: list[str],
    sources: list[dict[str, Any]],
    model: dict[str, Any],
    missing_data: list[str],
) -> str:
    ordered = sorted(probabilities.items(), key=lambda item: item[1], reverse=True)
    leading_name, leading_probability = ordered[0] if ordered else ("Sin estimación", 0.0)
    probability_rows = "\n".join(
        f"| {name} | {float(probability):.1%} |" for name, probability in probabilities.items()
    )
    source_rows = [
        f"{row.get('label') or row.get('provider_id')}: {row.get('status', 'desconocido')}"
        + (f"; actualizado {row['updated_at']}" if row.get("updated_at") else "")
        for row in sources
    ]
    return f"""## Conclusión principal

La salida más probable es **{leading_name} ({leading_probability:.1%})** para {team_a}–{team_b}. Esta probabilidad no debe interpretarse aislada de su intervalo, la alineación y la frescura de las fuentes.

## Probabilidades

| Resultado | Probabilidad |
|---|---:|
{probability_rows or '| Sin estimación | — |'}

## Estado de forma

{_bullets(form_notes, 'No hay forma reciente verificable.')}

## Jugadores y alineación

{_bullets(player_notes, 'No hay estadísticas o disponibilidad individual verificadas.')}

## Contexto del partido

{_bullets(context_notes, 'No se añadieron factores contextuales.')}

## Incertidumbre

{_bullets(missing_data, 'No se han declarado huecos críticos, aunque sigue existiendo incertidumbre de modelo.')}

## Fuentes

{_bullets(source_rows, 'No hay fuentes registradas.')}

## Modelo y calibración

- Modelo activo: **{localize_model(model.get('active'))}**.
- Modelo candidato: **{localize_model(model.get('challenger'))}**; no se activa sin validación temporal.
- La explicación contextual y cualquier IA opcional no pueden sustituir estas probabilidades deterministas.
"""
