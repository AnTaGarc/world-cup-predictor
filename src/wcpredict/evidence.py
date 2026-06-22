from dataclasses import dataclass
from enum import Enum


class EvidenceStatus(str, Enum):
    VERIFIED = "verified"
    CONFLICTING = "conflicting"
    NOT_FOUND = "not_found"
    UNVERIFIABLE = "unverifiable"
    STALE = "stale"
    INCOMPLETE = "incomplete"
    BLOCKED_BY_PROVIDER = "blocked_by_provider"
    PENDING_REVIEW = "pending_review"
    VERIFIED_USER_CAPTURE = "verified_user_capture"
    DISCARDED = "discarded"


@dataclass(frozen=True)
class ObservationInput:
    subject_type: str
    subject_name: str | None
    metric: str
    value_number: float | None
    value_text: str | None
    unit: str | None
    period: str
    source_id: str
    evidence_status: EvidenceStatus
    raw_label: str | None = None
    sample_size: int | None = None
