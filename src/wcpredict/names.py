import re
import unicodedata


_ALIASES = {
    "alemania": "Germany", "arabia saudi": "Saudi Arabia", "argelia": "Algeria",
    "belgica": "Belgium", "bosnia": "Bosnia and Herzegovina", "brasil": "Brazil",
    "bosnia herzegovina": "Bosnia and Herzegovina",
    "bosnia and herzegovina": "Bosnia and Herzegovina",
    "bosnia herz": "Bosnia and Herzegovina",
    "bosnia and herz": "Bosnia and Herzegovina",
    "bih": "Bosnia and Herzegovina",
    "cabo verde": "Cape Verde",
    "cape verde": "Cape Verde",
    "cv": "Cape Verde",
    "cote d ivoire": "Cote d'Ivoire",
    "cote divoire": "Cote d'Ivoire",
    "ivory coast": "Cote d'Ivoire",
    "czech republic": "Czechia",
    "czechia": "Czechia",
    "chequia": "Czechia", "corea del sur": "South Korea",
    "costa de marfil": "Cote d'Ivoire", "curazao": "Curacao",
    "egipto": "Egypt", "escocia": "Scotland", "espana": "Spain",
    "estados unidos": "USA", "francia": "France", "haiti": "Haiti",
    "inglaterra": "England", "iran": "IR Iran", "irak": "Iraq", "japon": "Japan",
    "jordania": "Jordan", "croacia": "Croatia",
    "korea republic": "South Korea",
    "republic of korea": "South Korea",
    "south korea": "South Korea",
    "marruecos": "Morocco", "mexico": "Mexico", "noruega": "Norway",
    "nueva zelanda": "New Zealand", "paises bajos": "Netherlands",
    "panama": "Panama", "rd congo": "Congo DR", "sudafrica": "South Africa",
    "suecia": "Sweden", "suiza": "Switzerland", "tunez": "Tunisia",
    "turquia": "Turkiye", "uzbekistan": "Uzbekistan",
    "turkey": "Turkiye",
    "turkiye": "Turkiye",
    "usa": "USA",
    "united states": "USA",
    "united states of america": "USA",
    # Self-aliases for canonicals whose casing is NOT preserved by title-casing.
    # Without these, calling canonical_team_name("IR Iran") falls through to
    # title-case and returns "Ir Iran", producing duplicate rows.
    "ir iran": "IR Iran",
    "congo dr": "Congo DR",
    "dr congo": "Congo DR",
    "ir of iran": "IR Iran",
    "islamic republic of iran": "IR Iran",
    "congo democratic republic": "Congo DR",
    "democratic republic of congo": "Congo DR",
}


def _key(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    words = re.sub(r"[^a-z0-9]+", " ", ascii_value.casefold()).strip()
    return words


def canonical_team_name(value: str) -> str:
    key = _key(value)
    if key in _ALIASES:
        return _ALIASES[key]
    return " ".join(word.capitalize() for word in key.split())


def same_team(left: str, right: str) -> bool:
    return canonical_team_name(left) == canonical_team_name(right)
