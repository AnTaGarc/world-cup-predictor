from dataclasses import dataclass


@dataclass(frozen=True)
class CandidateDecision:
    decision: str
    corrected_subject_name: str | None = None
    corrected_metric: str | None = None
    corrected_value_number: float | None = None
    corrected_value_text: str | None = None
    corrected_unit: str | None = None
    corrected_period: str | None = None
    rejection_reason: str | None = None


def ensure_batch_finalizable(candidates: list[dict]) -> None:
    pending = [
        row for row in candidates if row["review_status"] == "pending_review"
    ]
    if pending:
        raise ValueError(f"{len(pending)} candidates still require review")


def normalized_review_value(
    candidate: dict, decision: CandidateDecision
) -> dict:
    if decision.decision not in {"confirm", "correct", "discard"}:
        raise ValueError("Unsupported review decision")
    if decision.decision == "discard":
        return {**candidate, "evidence_status": "discarded"}
    return {
        **candidate,
        "subject_name": decision.corrected_subject_name
        or candidate.get("subject_name"),
        "metric": decision.corrected_metric or candidate["metric"],
        "value_number": (
            decision.corrected_value_number
            if decision.corrected_value_number is not None
            else candidate.get("value_number")
        ),
        "value_text": (
            decision.corrected_value_text
            if decision.corrected_value_text is not None
            else candidate.get("value_text")
        ),
        "unit": decision.corrected_unit or candidate.get("unit"),
        "period": decision.corrected_period or candidate.get("period", "ALL"),
        "evidence_status": "verified_user_capture",
    }
