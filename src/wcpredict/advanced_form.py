from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from wcpredict.names import canonical_team_name, same_team


@dataclass(frozen=True)
class XgFormAdjustment:
    factor_a: float
    factor_b: float
    sample_a: int
    sample_b: int
    explanation: str


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _team_strength(
    team_strengths: dict[str, dict[str, float]] | None,
    team_name: str,
) -> dict[str, float]:
    if not team_strengths:
        return {"attack": 1.0, "defense": 1.0}
    return team_strengths.get(canonical_team_name(team_name), {"attack": 1.0, "defense": 1.0})


def build_xg_form_adjustment(
    team_a: str,
    team_b: str,
    rows: list[dict],
    as_of_utc: datetime,
    *,
    half_life_days: float = 60.0,
    max_blend: float = 0.65,
    team_strengths: dict[str, dict[str, float]] | None = None,
) -> XgFormAdjustment:
    eligible = [
        row for row in rows
        if _parse(row["kickoff_utc"]) < as_of_utc
        and row.get("xg_a") is not None and row.get("xg_b") is not None
    ]
    if not eligible:
        return XgFormAdjustment(1.0, 1.0, 0, 0, "Sin xG profundo anterior utilizable.")

    def values_for(prefix: str) -> list[float]:
        values: list[float] = []
        for row in eligible:
            for key in (f"{prefix}_a", f"{prefix}_b"):
                value = row.get(key)
                if value is not None:
                    values.append(float(value))
        return values

    def average(values: list[float], fallback: float) -> float:
        return sum(values) / len(values) if values else fallback

    xg_baseline = average(values_for("xg"), 1.25)
    shots_baseline = average(values_for("shots"), 12.0)
    sot_baseline = average(values_for("shots_on_target"), 4.0)
    possession_baseline = average(values_for("possession"), 50.0)
    # Extended signals — available only when the deep JSON contains them.
    clear_chances_baseline = average(values_for("clear_chances"), 3.5)
    box_touches_baseline = average(values_for("box_touches"), 25.0)
    goals_prevented_baseline = average(values_for("goals_prevented"), 0.0)
    errors_to_shot_baseline = average(values_for("errors_to_shot"), 1.0)

    def ratio(value: float | None, baseline: float) -> float | None:
        if value is None or baseline <= 0:
            return None
        return _clamp(float(value) / baseline, 0.2, 5.0)

    def mixed_signal(parts: list[tuple[float | None, float]]) -> float:
        available = [(value, weight) for value, weight in parts if value is not None]
        if not available:
            return 1.0
        total_weight = sum(weight for _, weight in available)
        return sum(value * weight for value, weight in available) / total_weight

    def profile(team: str) -> tuple[float, float, int]:
        totals = {
            "xg_for": 0.0, "xg_against": 0.0,
            "shots_for": 0.0, "shots_against": 0.0,
            "sot_for": 0.0, "sot_against": 0.0,
            "possession_for": 0.0, "possession_against": 0.0,
            "clear_chances_for": 0.0, "clear_chances_against": 0.0,
            "box_touches_for": 0.0, "box_touches_against": 0.0,
            "goals_prevented_for": 0.0,
            "errors_to_shot_against": 0.0,  # own errors that gave the rival a shot
        }
        present_extras = {key: False for key in (
            "clear_chances", "box_touches", "goals_prevented", "errors_to_shot",
        )}
        total_weight = 0.0
        count = 0
        for row in eligible:
            if same_team(str(row["team_a"]), team):
                own_suffix, opponent_suffix = "a", "b"
                opponent_name = str(row["team_b"])
            elif same_team(str(row["team_b"]), team):
                own_suffix, opponent_suffix = "b", "a"
                opponent_name = str(row["team_a"])
            else:
                continue

            strength = _team_strength(team_strengths, opponent_name)
            attack_context = _clamp(1.0 / max(0.35, float(strength.get("defense", 1.0))), 0.75, 1.25)
            concession_context = _clamp(1.0 / max(0.35, float(strength.get("attack", 1.0))), 0.75, 1.25)

            age = max(0.0, (as_of_utc - _parse(row["kickoff_utc"])).total_seconds() / 86400)
            weight = 0.5 ** (age / half_life_days)
            for label, prefix in (
                ("xg", "xg"),
                ("shots", "shots"),
                ("sot", "shots_on_target"),
                ("possession", "possession"),
                ("clear_chances", "clear_chances"),
                ("box_touches", "box_touches"),
            ):
                own = row.get(f"{prefix}_{own_suffix}")
                opponent = row.get(f"{prefix}_{opponent_suffix}")
                if own is not None:
                    totals[f"{label}_for"] += float(own) * attack_context * weight
                    if label in present_extras:
                        present_extras[label] = True
                if opponent is not None:
                    totals[f"{label}_against"] += float(opponent) * concession_context * weight
            # Asymmetric extras: defensive errors and GK overperformance only
            # contribute to one side of the signal.
            errors_to_shot = row.get(f"errors_to_shot_{own_suffix}")
            if errors_to_shot is not None:
                totals["errors_to_shot_against"] += float(errors_to_shot) * concession_context * weight
                present_extras["errors_to_shot"] = True
            goals_prevented = row.get(f"goals_prevented_{own_suffix}")
            if goals_prevented is not None:
                totals["goals_prevented_for"] += float(goals_prevented) * weight
                present_extras["goals_prevented"] = True
            total_weight += weight
            count += 1
        if not count or not total_weight:
            return 1.0, 1.0, 0

        averages = {key: value / total_weight for key, value in totals.items()}
        # Attack signal: xG still dominates; clear chances and box touches add
        # small refinements when present.
        attack_parts = [
            (ratio(averages["xg_for"], xg_baseline), 0.55),
            (ratio(averages["sot_for"], sot_baseline), 0.12),
            (ratio(averages["shots_for"], shots_baseline), 0.08),
            (ratio(averages["possession_for"], possession_baseline), 0.08),
        ]
        if present_extras["clear_chances"]:
            attack_parts.append((ratio(averages["clear_chances_for"], clear_chances_baseline), 0.12))
        if present_extras["box_touches"]:
            attack_parts.append((ratio(averages["box_touches_for"], box_touches_baseline), 0.05))
        attack_ratio = mixed_signal(attack_parts)

        # Concession signal mirrors the attack one. Defensive errors hurt
        # (treated as concession-against equivalent), goals_prevented is a
        # multiplicative bonus capped to a tight band.
        concession_parts = [
            (ratio(averages["xg_against"], xg_baseline), 0.55),
            (ratio(averages["sot_against"], sot_baseline), 0.12),
            (ratio(averages["shots_against"], shots_baseline), 0.08),
            (ratio(averages["possession_against"], possession_baseline), 0.08),
        ]
        if present_extras["clear_chances"]:
            concession_parts.append((ratio(averages["clear_chances_against"], clear_chances_baseline), 0.12))
        if present_extras["errors_to_shot"]:
            concession_parts.append((ratio(averages["errors_to_shot_against"], errors_to_shot_baseline), 0.05))
        concession_ratio = mixed_signal(concession_parts)
        if present_extras["goals_prevented"] and goals_prevented_baseline != 0:
            # Negative goals_prevented => GK underperformed => concede more.
            gp_avg = averages["goals_prevented_for"]
            gp_adj = _clamp(1.0 - (gp_avg - goals_prevented_baseline) * 0.10, 0.85, 1.15)
            concession_ratio *= gp_adj

        blend = max_blend * count / (count + 3.0)
        return _clamp(attack_ratio, 0.2, 5.0) ** blend, _clamp(concession_ratio, 0.2, 5.0) ** blend, count

    attack_a, concession_a, sample_a = profile(team_a)
    attack_b, concession_b, sample_b = profile(team_b)
    if not sample_a and not sample_b:
        return XgFormAdjustment(1.0, 1.0, 0, 0, "Sin xG profundo anterior utilizable.")

    def dynamic_cap(sample: int) -> float:
        # Opens from 1.35 up to 1.60 as evidence accumulates; <3 samples stays tight.
        return 1.35 + 0.25 * min(1.0, max(0, sample - 2) / 10.0)

    cap_a = dynamic_cap(sample_a)
    cap_b = dynamic_cap(sample_b)
    factor_a = _clamp(attack_a * concession_b, 0.65, cap_a)
    factor_b = _clamp(attack_b * concession_a, 0.65, cap_b)
    rival_note = " con fuerza rival" if team_strengths else ""
    explanation = (
        f"Forma xG anterior con tiros/posesión{rival_note} y contracción: "
        f"{team_a} factor {factor_a:.3f} ({sample_a} partidos, techo {cap_a:.2f}); "
        f"{team_b} factor {factor_b:.3f} ({sample_b} partidos, techo {cap_b:.2f}). "
        f"Peso máximo {max_blend:.0%}."
    )
    return XgFormAdjustment(factor_a, factor_b, sample_a, sample_b, explanation)


@dataclass(frozen=True)
class GoalkeeperBaseline:
    """Recency-weighted goalkeeper baseline for a team.

    Derived from past deep-stat matches:
      - save_rate = sum(weighted saves) / sum(weighted SOT against)
      - sample_matches = number of matches contributing
    """
    team_name: str
    save_rate: float | None
    saves_per_match: float | None
    goals_conceded_per_match: float | None
    sample_matches: int
    explanation: str


def build_goalkeeper_baseline(
    team_name: str,
    rows: list[dict],
    as_of_utc: datetime,
    *,
    half_life_days: float = 120.0,
) -> GoalkeeperBaseline:
    """Compute a save_rate for `team_name` using past per-team rows.

    Each row is one match with both teams' saves / SOT / goals (the output of
    list_deep_goalkeeper_rows_before). Older matches decay exponentially.
    """
    weighted_saves = 0.0
    weighted_sot_against = 0.0
    weighted_saves_per_match = 0.0
    weighted_goals_conceded = 0.0
    total_weight = 0.0
    matches = 0
    for row in rows:
        if same_team(str(row.get("team_a") or ""), team_name):
            saves = row.get("saves_a")
            sot_against = row.get("sot_b")
            goals_against = row.get("goals_b")
        elif same_team(str(row.get("team_b") or ""), team_name):
            saves = row.get("saves_b")
            sot_against = row.get("sot_a")
            goals_against = row.get("goals_a")
        else:
            continue
        if saves is None or sot_against is None:
            continue
        age = max(0.0, (as_of_utc - _parse(row["kickoff_utc"])).total_seconds() / 86400)
        weight = 0.5 ** (age / half_life_days)
        weighted_saves += float(saves) * weight
        weighted_sot_against += float(sot_against) * weight
        weighted_saves_per_match += float(saves) * weight
        if goals_against is not None:
            weighted_goals_conceded += float(goals_against) * weight
        total_weight += weight
        matches += 1

    if matches == 0 or total_weight <= 0:
        return GoalkeeperBaseline(
            team_name=team_name, save_rate=None, saves_per_match=None,
            goals_conceded_per_match=None, sample_matches=0,
            explanation="Sin paradas/SOT en muestras profundas anteriores.",
        )
    save_rate = (
        weighted_saves / weighted_sot_against if weighted_sot_against > 0 else None
    )
    saves_per_match = weighted_saves_per_match / total_weight
    goals_per_match = weighted_goals_conceded / total_weight if weighted_goals_conceded else None
    save_rate_label = f"{save_rate:.0%}" if save_rate is not None else "no calculable"
    return GoalkeeperBaseline(
        team_name=team_name,
        save_rate=save_rate,
        saves_per_match=saves_per_match,
        goals_conceded_per_match=goals_per_match,
        sample_matches=matches,
        explanation=(
            f"Baseline portería ({matches} partidos profundos): "
            f"save_rate {save_rate_label}, paradas/partido {saves_per_match:.2f}."
        ),
    )


def build_volume_rate_observations(
    team_a: str, team_b: str, rows: list[dict]
) -> list[dict]:
    output: list[dict] = []
    for metric in ("corners", "cards", "shots", "shots_on_target"):
        for team in (team_a, team_b):
            for_values: list[float] = []
            against_values: list[float] = []
            for row in rows:
                if same_team(str(row.get("team_a") or ""), team):
                    own, opponent = row.get(f"{metric}_a"), row.get(f"{metric}_b")
                elif same_team(str(row.get("team_b") or ""), team):
                    own, opponent = row.get(f"{metric}_b"), row.get(f"{metric}_a")
                else:
                    continue
                if own is not None and opponent is not None:
                    for_values.append(float(own))
                    against_values.append(float(opponent))
            if for_values:
                sample = len(for_values)
                output.extend([
                    {"subject_name": team, "metric": f"{metric}_for_avg", "value_number": sum(for_values) / sample, "sample_size": sample},
                    {"subject_name": team, "metric": f"{metric}_against_avg", "value_number": sum(against_values) / sample, "sample_size": sample},
                ])
        totals = [
            float(row[f"{metric}_a"]) + float(row[f"{metric}_b"])
            for row in rows
            if row.get(f"{metric}_a") is not None and row.get(f"{metric}_b") is not None
        ]
        if len(totals) >= 2:
            mean = sum(totals) / len(totals)
            variance = sum((value - mean) ** 2 for value in totals) / (len(totals) - 1)
            dispersion = max(0.0, (variance - mean) / (mean**2)) if mean else 0.0
            output.append({"subject_name": "Partidos", "metric": f"{metric}_dispersion", "value_number": dispersion, "sample_size": len(totals)})
    return output
