"""Team crests for the World Cup 2026 redesign.

48 official PNG crests live in ``data/crests/``. This module exposes a
canonical mapping from team name (with the common aliases the predictor
uses) to filename, plus tiny helpers that emit either a Streamlit
``st.image`` call or an inline HTML ``<img>`` element (so the same icon can
be rendered inside a markdown table, a caption, a hero or wherever).
"""

from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

from wcpredict.names import canonical_team_name


_CRESTS_DIR = Path(__file__).resolve().parents[3] / "data" / "crests"


# Names taken from crests.js. Both the canonical predictor name and common
# aliases map to the same file so any unnormalised input still resolves.
TEAM_TO_FILE: dict[str, str] = {
    "Algeria": "algeria.png",
    "Argentina": "argentina.png",
    "Australia": "australia.png",
    "Austria": "austria.png",
    "Belgium": "belgium.png",
    "Bosnia and Herzegovina": "bosnia-and-herzegovina.png",
    "Brazil": "brazil.png",
    "Cabo Verde": "cabo-verde.png",
    "Cape Verde": "cabo-verde.png",
    "Canada": "canada.png",
    "Colombia": "colombia.png",
    "Cote d'Ivoire": "cote-d-ivoire.png",
    "Côte d'Ivoire": "cote-d-ivoire.png",
    "Ivory Coast": "cote-d-ivoire.png",
    "Croatia": "croatia.png",
    "Curacao": "curacao.png",
    "Curaçao": "curacao.png",
    "Czechia": "czechia.png",
    "Czech Republic": "czechia.png",
    "Congo DR": "dr-congo.png",
    "DR Congo": "dr-congo.png",
    "Ecuador": "ecuador.png",
    "Egypt": "egypt.png",
    "England": "england.png",
    "France": "france.png",
    "Germany": "germany.png",
    "Ghana": "ghana.png",
    "Haiti": "haiti.png",
    "IR Iran": "iran.png",
    "Iran": "iran.png",
    "Iraq": "iraq.png",
    "Japan": "japan.png",
    "Jordan": "jordan.png",
    "Mexico": "mexico.png",
    "Morocco": "morocco.png",
    "Netherlands": "netherlands.png",
    "New Zealand": "new-zealand.png",
    "Norway": "norway.png",
    "Panama": "panama.png",
    "Paraguay": "paraguay.png",
    "Portugal": "portugal.png",
    "Qatar": "qatar.png",
    "Saudi Arabia": "saudi-arabia.png",
    "Scotland": "scotland.png",
    "Senegal": "senegal.png",
    "South Africa": "south-africa.png",
    "South Korea": "south-korea.png",
    "Korea Republic": "south-korea.png",
    "Spain": "spain.png",
    "Sweden": "sweden.png",
    "Switzerland": "switzerland.png",
    "Tunisia": "tunisia.png",
    "Turkey": "turkey.png",
    "Türkiye": "turkey.png",
    "Turkiye": "turkey.png",
    "Uruguay": "uruguay.png",
    "USA": "usa.png",
    "United States": "usa.png",
    "Uzbekistan": "uzbekistan.png",
}


def crest_path(team_name: str | None) -> Path | None:
    if not team_name:
        return None
    candidate = TEAM_TO_FILE.get(team_name)
    if candidate is None:
        canonical = canonical_team_name(team_name)
        candidate = TEAM_TO_FILE.get(canonical)
    if candidate is None:
        return None
    path = _CRESTS_DIR / candidate
    return path if path.exists() else None


@lru_cache(maxsize=128)
def crest_data_uri(team_name: str | None) -> str | None:
    """Return a ``data:image/png;base64,…`` URI so the crest can be embedded
    inside markdown / inline HTML without needing a static file server."""
    path = crest_path(team_name)
    if path is None:
        return None
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def crest_html(team_name: str | None, *, size: int = 20, alt: str | None = None) -> str:
    """Render the crest as an inline ``<img>`` tag (safe to drop into markdown
    blocks that use ``unsafe_allow_html=True``). Returns an empty string when
    the team isn't mapped."""
    uri = crest_data_uri(team_name)
    if uri is None:
        return ""
    alt_text = alt or (team_name or "")
    return (
        f'<img src="{uri}" alt="{alt_text}" '
        f'width="{size}" height="{size}" '
        f'style="vertical-align:middle;border-radius:3px;object-fit:contain;display:inline-block;" />'
    )


@lru_cache(maxsize=256)
def team_with_crest_html(team_name: str | None, *, size: int = 20) -> str:
    """Crest + team name on a single line, vertically centered.

    Cached because the player ranking tables render 50 rows × 4 tabs and used
    to rebuild the base64 data URI each time the search box was typed in.
    Caching by (team_name, size) collapses that to one lookup per team."""
    if not team_name:
        return ""
    icon = crest_html(team_name, size=size)
    if icon:
        return (
            f'<span style="display:inline-flex;align-items:center;gap:8px;'
            f'white-space:nowrap;">{icon}'
            f'<span>{team_name}</span></span>'
        )
    return f"<span>{team_name}</span>"
