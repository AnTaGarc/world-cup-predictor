def prediction_occurred(
    prediction: dict,
    team_a: str,
    team_b: str,
    goals_a: int,
    goals_b: int,
) -> bool | None:
    market = str(prediction.get("market_name") or "")
    selection = str(prediction.get("selection_name") or "")
    if goals_a > goals_b:
        result = team_a
    elif goals_b > goals_a:
        result = team_b
    else:
        result = "Draw"
    if market == "1X2":
        return selection == result
    if market == "Double Chance":
        return result in [part.strip() for part in selection.split(" or ")]
    if market == "Draw No Bet":
        return None if result == "Draw" else selection == result
    if market.startswith("Over/Under"):
        line = float(prediction["line"])
        total = goals_a + goals_b
        if selection.startswith("Over"):
            return total > line
        if selection.startswith("Under"):
            return total < line
    if market == "Both Teams To Score":
        occurred = goals_a > 0 and goals_b > 0
        return occurred if selection == "Yes" else not occurred if selection == "No" else None
    return None
