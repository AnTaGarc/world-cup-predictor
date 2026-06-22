def default_market_rows(team_a: str, team_b: str) -> list[dict]:
    base = [
        ("match_result", "1X2", team_a, None),
        ("match_result", "1X2", "Draw", None),
        ("match_result", "1X2", team_b, None),
        ("match_result", "Double Chance", f"{team_a} or Draw", None),
        ("match_result", "Double Chance", f"{team_b} or Draw", None),
        ("goals", "Over/Under 1.5", "Over 1.5", 1.5),
        ("goals", "Over/Under 1.5", "Under 1.5", 1.5),
        ("goals", "Over/Under 2.5", "Over 2.5", 2.5),
        ("goals", "Over/Under 2.5", "Under 2.5", 2.5),
        ("goals", "Both Teams To Score", "Yes", None),
        ("goals", "Both Teams To Score", "No", None),
        ("corners", "Total Corners 8.5", "Over 8.5", 8.5),
        ("corners", "Total Corners 8.5", "Under 8.5", 8.5),
        ("corners", f"{team_a} Corners 4.5", "Over 4.5", 4.5),
        ("corners", f"{team_b} Corners 4.5", "Over 4.5", 4.5),
        ("cards", "Total Cards 3.5", "Over 3.5", 3.5),
        ("cards", "Total Cards 3.5", "Under 3.5", 3.5),
        ("shots", f"{team_a} Shots 10.5", "Over 10.5", 10.5),
        ("shots", f"{team_b} Shots 10.5", "Over 10.5", 10.5),
        ("shots_on_target", f"{team_a} Shots On Target 4.5", "Over 4.5", 4.5),
        ("shots_on_target", f"{team_b} Shots On Target 4.5", "Over 4.5", 4.5),
        ("player_goal", "Player Anytime Goal", "", None),
        ("player_shots", "Player Shots 1.5", "Over 1.5", 1.5),
        ("player_shots_on_target", "Player Shots On Target 0.5", "Over 0.5", 0.5),
        ("player_cards", "Player Card", "Yes", None),
        ("player_passes", "Player Passes 35.5", "Over 35.5", 35.5),
    ]
    return [
        {
            "market_family": family,
            "market_name": name,
            "selection_name": selection,
            "line": line,
            "decimal_odds": None,
            "bookmaker": "Winamax",
        }
        for family, name, selection, line in base
    ]


def _optional_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return float(value)


def normalize_market_rows(rows: list[dict]) -> list[dict]:
    normalized = []
    for row in rows:
        odds = _optional_float(row.get("decimal_odds"))
        if odds is None:
            continue
        normalized.append(
            {
                "market_family": str(row.get("market_family", "")),
                "market_name": str(row.get("market_name", "")),
                "selection_name": str(row.get("selection_name", "")),
                "line": _optional_float(row.get("line")),
                "decimal_odds": odds,
                "bookmaker": str(row.get("bookmaker") or "Manual"),
            }
        )
    return normalized
