from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any, Callable


@dataclass(frozen=True)
class CopilotResult:
    status: str
    narrative: str
    flags: tuple[str, ...] = ()
    reason: str | None = None


def _requests_transport(url: str, headers: dict, payload: dict, timeout: int) -> dict:
    import requests

    response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()


def explain_context(
    context: dict[str, Any],
    *,
    api_key: str | None = None,
    model: str | None = None,
    transport: Callable[[str, dict, dict, int], dict] | None = None,
) -> CopilotResult:
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        return CopilotResult("disabled", "", reason="OPENAI_API_KEY no configurada")
    payload = {
        "model": model or os.environ.get("OPENAI_MODEL", "gpt-5.5"),
        "instructions": (
            "Explica únicamente el contexto futbolístico recibido. Señala datos ausentes o contradictorios. "
            "No recalcules, sustituyas ni recomiendes modificar las probabilidades deterministas."
        ),
        "input": json.dumps(context, ensure_ascii=False, sort_keys=True),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "football_context_explanation",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "narrative": {"type": "string"},
                        "flags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["narrative", "flags"],
                    "additionalProperties": False,
                },
            }
        },
    }
    try:
        response = (transport or _requests_transport)(
            "https://api.openai.com/v1/responses",
            {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            payload,
            45,
        )
        parsed = json.loads(str(response.get("output_text") or "{}"))
        narrative = str(parsed.get("narrative") or "").strip()
        flags = tuple(str(flag) for flag in parsed.get("flags") or [])
        if not narrative:
            raise ValueError("empty structured narrative")
        return CopilotResult("ready", narrative, flags)
    except Exception as exc:
        return CopilotResult("failed", "", reason=str(exc)[:240])
