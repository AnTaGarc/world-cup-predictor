from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from wcpredict.github_wc2026_dataset import (
    fetch_github_wc2026_dataset,
    import_github_wc2026_download,
    resolve_latest_commit,
)
from wcpredict.repository import Repository


GITHUB_ORDER = (
    "github_wc2026_teams",
    "github_wc2026_referees",
    "github_wc2026_matches",
    "github_wc2026_team_stats",
    "github_wc2026_lineups",
    "github_wc2026_events",
    "github_wc2026_player_stats",
)


def _default_fetcher(commit_sha, commit_date):
    def fetcher(provider_id):
        return fetch_github_wc2026_dataset(provider_id, commit_sha, commit_date)
    return fetcher


def run_backfill(repository: Repository, fetcher=None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if fetcher is None:
        sha, date = resolve_latest_commit()
        fetcher = _default_fetcher(sha, date)
    imported: list[str] = []
    for provider_id in GITHUB_ORDER:
        download = fetcher(provider_id)
        import_github_wc2026_download(repository, download, now)
        imported.append(provider_id)
    repository.resolve_gh_foreign_keys(None, now.isoformat())
    return {
        "imported": imported,
        "mismatches": len(repository.list_score_mismatches()),
        "pending_team_aliases": len(repository.list_pending_aliases("team")),
        "pending_player_aliases": len(repository.list_pending_aliases("player")),
    }


if __name__ == "__main__":
    repo = Repository(Path(__file__).parents[1] / "data" / "worldcup.sqlite")
    repo.initialize()
    report = run_backfill(repo)
    print(report)
    sys.exit(0)
