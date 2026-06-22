import math


def brier_score(probability: float, occurred: bool) -> float:
    if not 0.0 <= probability <= 1.0:
        raise ValueError("probability must be between 0 and 1")
    actual = 1.0 if occurred else 0.0
    return (probability - actual) ** 2


def market_hit(selection_type: str, observed_value: float, line: float | None = None) -> bool:
    if selection_type == "over":
        if line is None:
            raise ValueError("line is required for over markets")
        return observed_value > line
    if selection_type == "under":
        if line is None:
            raise ValueError("line is required for under markets")
        return observed_value < line
    if selection_type == "yes":
        return observed_value > 0
    if selection_type == "no":
        return observed_value == 0
    raise ValueError(f"unsupported selection_type {selection_type}")


def calibration_bands(rows: list[tuple[float, bool]], band_size: float = 0.1) -> dict[str, dict[str, float]]:
    if not 0.0 < band_size <= 1.0:
        raise ValueError("band_size must be between 0 and 1")
    bands: dict[str, dict[str, float]] = {}
    for probability, occurred in rows:
        lower = math.floor(probability / band_size) * band_size
        upper = min(1.0, lower + band_size)
        label = f"{lower:.2f}-{upper:.2f}"
        band = bands.setdefault(label, {"count": 0, "predicted_sum": 0.0, "actual_sum": 0.0})
        band["count"] += 1
        band["predicted_sum"] += probability
        band["actual_sum"] += 1.0 if occurred else 0.0
    for band in bands.values():
        band["avg_probability"] = band["predicted_sum"] / band["count"]
        band["hit_rate"] = band["actual_sum"] / band["count"]
    return bands


def summarize_by_market_family(rows: list[dict]) -> dict[str, dict[str, float]]:
    summaries: dict[str, dict[str, float]] = {}
    for row in rows:
        family = str(row.get("market_family") or "unknown")
        summary = summaries.setdefault(family, {"count": 0, "brier_sum": 0.0, "hits": 0})
        summary["count"] += 1
        summary["brier_sum"] += float(row.get("brier_score") or 0.0)
        summary["hits"] += int(bool(row.get("hit")))
    for summary in summaries.values():
        summary["avg_brier"] = summary["brier_sum"] / summary["count"]
        summary["hit_rate"] = summary["hits"] / summary["count"]
        summary["reliability"] = "provisional" if summary["count"] < 20 else "calibrated"
    return summaries


def calibration_drift(rows: list[dict]) -> list[dict]:
    ordered = sorted(rows, key=lambda row: str(row.get("evaluated_at_utc") or ""))
    drift = []
    running = 0.0
    for index, row in enumerate(ordered, start=1):
        running += float(row.get("brier_score") or 0.0)
        drift.append(
            {
                "evaluated_at_utc": row.get("evaluated_at_utc"),
                "cumulative_brier": running / index,
                "count": index,
            }
        )
    return drift
