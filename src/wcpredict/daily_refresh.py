from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Callable, Iterable


DEFAULT_PROVIDERS = (
    "swaptr_wc2026_matches",
    "swaptr_wc2026_teams",
    "swaptr_wc2026_players",
)


@dataclass(frozen=True)
class DatasetDownload:
    provider_id: str
    version: str | None
    content: bytes
    updated_at: datetime | None
    row_count: int

    @property
    def sha256(self) -> str:
        return sha256(self.content).hexdigest()


@dataclass(frozen=True)
class DailyRefreshResult:
    status: str
    updated: tuple[str, ...]
    unchanged: tuple[str, ...]
    skipped_recent: tuple[str, ...]
    failed: tuple[str, ...]
    checked_at: datetime


def ensure_current_world_cup_data(
    repository,
    fetcher: Callable[[str], DatasetDownload],
    *,
    importer: Callable[[DatasetDownload], None] | None = None,
    now: datetime | None = None,
    max_age: timedelta = timedelta(hours=24),
    providers: Iterable[str] = DEFAULT_PROVIDERS,
) -> DailyRefreshResult:
    now = now or datetime.now(timezone.utc)
    provider_ids = tuple(providers)
    updated: list[str] = []
    unchanged: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    cached_after_failure = False

    for provider_id in provider_ids:
        checks = repository.list_dataset_refresh_checks(provider_id)
        if checks:
            checked_at = datetime.fromisoformat(str(checks[0]["checked_at_utc"]))
            if checked_at.tzinfo is None:
                checked_at = checked_at.replace(tzinfo=timezone.utc)
            if checks[0]["status"] == "ready" and now - checked_at <= max_age:
                skipped.append(provider_id)
                continue
            if checks[0]["status"] == "failed" and now - checked_at <= timedelta(hours=1):
                failed.append(provider_id)
                cached_after_failure = cached_after_failure or bool(
                    repository.list_dataset_snapshots(provider_id)
                )
                continue
        try:
            download = fetcher(provider_id)
            if not isinstance(download, DatasetDownload) or download.provider_id != provider_id:
                raise ValueError("dataset fetcher returned an invalid provider payload")
            previous = repository.list_dataset_snapshots(provider_id)
            if (
                previous
                and previous[0]["content_sha256"] == download.sha256
                and previous[0].get("provider_version") == download.version
            ):
                unchanged.append(provider_id)
            else:
                if importer is not None:
                    importer(download)
                updated.append(provider_id)
            repository.record_dataset_snapshot(
                provider_id,
                download.version,
                download.sha256,
                now,
                download.updated_at,
                download.row_count,
                "ready",
                None,
            )
            repository.record_dataset_refresh_check(provider_id, now, "ready", None)
        except Exception as exc:  # provider errors must preserve cached state
            failed.append(provider_id)
            cached_after_failure = cached_after_failure or bool(repository.list_dataset_snapshots(provider_id))
            repository.record_dataset_refresh_check(provider_id, now, "failed", str(exc)[:240])

    successful = bool(updated or unchanged or skipped)
    if failed:
        status = "partial" if successful else (
            "stale" if cached_after_failure else "failed"
        )
    elif updated:
        status = "updated"
    else:
        status = "current"
    return DailyRefreshResult(
        status,
        tuple(updated),
        tuple(unchanged),
        tuple(skipped),
        tuple(failed),
        now,
    )
