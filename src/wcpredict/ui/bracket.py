"""Knockout bracket visualization for Streamlit.

Generates a horizontal bracket with CSS-only connectors. All output is
a single HTML string suitable for ``st.markdown(html, unsafe_allow_html=True)``.

The CSS classes (``bracket-*``) must be injected once via the theme CSS block
in ``theme.py``.

Slot data structure
-------------------
Each match is a dict::

    {
        "match_id": "M73",
        "round": "round_of_32",       # round_of_32 | round_of_16 | quarter | semi | final | third_place
        "date": "28 jun",
        "stadium": "Philadelphia Stadium",
        "home": {
            "name": "South Africa",
            "crest_html": '<img src="data:image/png;base64,…" …>',
            "is_placeholder": False,
        },
        "away": {
            "name": "Canada",
            "crest_html": '<img src="data:image/png;base64,…" …>',
            "is_placeholder": False,
        },
        "status": "pending",          # pending | live | closed
        "score": None,                # "2-1" str or [2, 1] list when closed/live
        "advances_to": "M89",         # slot the winner feeds into; None for final
        "href": "?page=lab&match_id=42",  # optional: makes the card clickable
    }
"""

from __future__ import annotations

from html import escape
from typing import Any


# Round key normalisation
_ROUND_KEYS = {
    "round_of_32": "r32",
    "round_of_16": "r16",
    "quarter": "qf",
    "semi": "sf",
    "final": "final",
    "third_place": "third",
    # accept short forms too
    "r32": "r32",
    "r16": "r16",
    "qf": "qf",
    "sf": "sf",
    "third": "third",
}

_ROUND_ORDER = ["r32", "r16", "qf", "sf", "final"]

_ROUND_LABELS = {
    "r32": "Dieciseisavos",
    "r16": "Octavos",
    "qf": "Cuartos",
    "sf": "Semifinales",
    "final": "Final",
}

# How many connector pairs between consecutive rounds
_CONN_COUNTS = [8, 4, 2, 1]


def _normalise_round(raw: str) -> str:
    key = _ROUND_KEYS.get(raw)
    if key is None:
        raise ValueError(f"Unknown round key: {raw!r}")
    return key


def _parse_score(score: Any) -> tuple[int | None, int | None]:
    """Accept '2-1', [2,1], (2,1) or None."""
    if score is None:
        return None, None
    if isinstance(score, str):
        parts = score.split("-")
        if len(parts) == 2:
            try:
                return int(parts[0].strip()), int(parts[1].strip())
            except ValueError:
                return None, None
        return None, None
    if isinstance(score, (list, tuple)) and len(score) == 2:
        try:
            return int(score[0]), int(score[1])
        except (TypeError, ValueError):
            return None, None
    return None, None


def _team_row(team: dict, score_val: int | None, is_winner: bool) -> str:
    cls = "bracket-slot-team"
    if is_winner:
        cls += " bracket-slot-winner"
    h = f'<div class="{cls}">'
    crest = team.get("crest_html", "")
    if crest:
        h += crest + " "
    name = escape(team.get("name", ""))
    is_ph = team.get("is_placeholder", False)
    name_cls = "bracket-team-name bracket-placeholder" if is_ph else "bracket-team-name"
    h += f'<span class="{name_cls}">{name}</span>'
    if score_val is not None:
        h += f'<span class="bracket-team-score">{score_val}</span>'
    h += "</div>"
    return h


def _card_html(slot: dict) -> str:
    status = slot.get("status", "pending")
    live = status == "live"
    closed = status == "closed"
    show_score = live or closed
    sh, sa = _parse_score(slot.get("score"))

    cls = "bracket-slot"
    if live:
        cls += " bracket-live"
    if closed:
        cls += " bracket-closed"

    href = slot.get("href")
    open_tag = f'<a class="bracket-slot-link" href="{escape(href)}">' if href else ""
    close_tag = "</a>" if href else ""

    h = f'{open_tag}<div class="{cls}">'
    # Gradient header
    h += '<div class="bracket-slot-head">'
    h += f'<span class="bracket-slot-mid">{escape(slot.get("match_id", ""))}</span>'
    h += f'<span class="bracket-slot-date">{escape(slot.get("date", ""))}</span>'
    h += "</div>"
    # Stadium
    stadium = slot.get("stadium", "")
    if stadium:
        h += f'<div class="bracket-slot-venue"><span class="bracket-venue-pin">\U0001F4CD</span>{escape(stadium)}</div>'

    winner = slot.get("winner")  # "home" | "away" | None — wins out over score
    if winner == "home":
        home_win, away_win = True, False
    elif winner == "away":
        home_win, away_win = False, True
    else:
        home_win = closed and sh is not None and sa is not None and sh > sa
        away_win = closed and sh is not None and sa is not None and sa > sh

    h += _team_row(slot.get("home", {}), sh if show_score else None, home_win)
    h += '<div class="bracket-vs">vs</div>'
    h += _team_row(slot.get("away", {}), sa if show_score else None, away_win)
    h += f"</div>{close_tag}"
    return h


def render_bracket(slots: list[dict]) -> str:
    """Return the complete bracket HTML for all knockout slots."""
    by_round: dict[str, list[dict]] = {r: [] for r in _ROUND_ORDER}
    by_round["third"] = []
    for s in slots:
        rk = _normalise_round(s["round"])
        by_round[rk].append(s)

    h = '<div class="bracket-container"><div class="bracket-inner">'

    # ── Round headers ──
    h += '<div class="bracket-headers">'
    for i, rk in enumerate(_ROUND_ORDER):
        if i > 0:
            h += '<div class="bracket-rh-spacer"></div>'
        h += f'<div class="bracket-rh bracket-rh-{rk}">{_ROUND_LABELS[rk]}</div>'
    h += "</div>"

    # ── Bracket body ──
    h += '<div class="bracket-body">'
    for i, rk in enumerate(_ROUND_ORDER):
        h += f'<div class="bracket-round bracket-{rk}">'
        for slot in by_round[rk]:
            h += _card_html(slot)
        h += "</div>"

        # Connector column (except after final)
        if i < len(_ROUND_ORDER) - 1:
            src = by_round[rk]
            count = _CONN_COUNTS[i]
            h += '<div class="bracket-conn-col">'
            for j in range(count):
                a = src[j * 2] if j * 2 < len(src) else None
                b = src[j * 2 + 1] if j * 2 + 1 < len(src) else None
                resolved = (
                    a is not None
                    and b is not None
                    and a.get("status") == "closed"
                    and b.get("status") == "closed"
                )
                cls = "bracket-conn-pair"
                if resolved:
                    cls += " bracket-conn-resolved"
                h += f'<div class="{cls}"></div>'
            h += "</div>"

    h += "</div>"  # bracket-body

    # Third-place match (below the main bracket)
    third_slots = by_round.get("third", [])
    if third_slots:
        h += '<div class="bracket-third">'
        h += '<div class="bracket-third-label">Tercer y cuarto puesto</div>'
        for slot in third_slots:
            h += _card_html(slot)
        h += "</div>"

    h += "</div></div>"  # bracket-inner, bracket-container
    return h
