"""Referee-aware card multiplier (Phase 3 of the github_wc2026 rollout).

The external dataset assigns a referee to every fixture and publishes a
career cards-per-game average. That average has no declared sample, so it
enters as a weak prior (worth ~5 matches); the referee's observed cards in
this World Cup (from the event timeline) progressively take over. The
blended rate is compared with the tournament mean and shrunk toward 1.0,
clamped to [0.80, 1.25] so a single referee can never dominate the card
markets. Teams' own discipline profiles remain the main signal.
"""
from __future__ import annotations


UPSTREAM_PRIOR_MATCHES = 5.0
SHRINK_MATCHES = 10.0
MULTIPLIER_LOW = 0.80
MULTIPLIER_HIGH = 1.25


def blend_referee_rate(
    internal_cards: int,
    internal_matches: int,
    upstream_avg: float | None,
    tournament_mean: float,
) -> float:
    if tournament_mean <= 0:
        return 1.0
    numerator = float(internal_cards)
    denominator = float(internal_matches)
    if upstream_avg is not None:
        numerator += float(upstream_avg) * UPSTREAM_PRIOR_MATCHES
        denominator += UPSTREAM_PRIOR_MATCHES
    if denominator <= 0:
        return 1.0
    blended_rate = numerator / denominator
    raw = blended_rate / tournament_mean
    shrink = denominator / (denominator + SHRINK_MATCHES)
    multiplier = 1.0 + (raw - 1.0) * shrink
    return max(MULTIPLIER_LOW, min(MULTIPLIER_HIGH, multiplier))


def referee_card_multiplier_for_match(repo, match_id: int) -> tuple[float, str | None]:
    """Multiplier for the referee assigned to ``match_id`` (neutral 1.0 when
    no referee is assigned or no evidence exists)."""
    with repo.session() as con:
        assigned = con.execute(
            "SELECT gh.external_referee_id, gh.referee_name FROM gh_matches gh "
            "WHERE gh.match_id = ? AND gh.referee_name IS NOT NULL",
            (match_id,),
        ).fetchone()
        if assigned is None:
            return 1.0, None
        # matches_detailed.csv publishes only the referee's name (no id),
        # so both lookups resolve by name.
        referee_name = str(assigned["referee_name"])
        upstream = con.execute(
            "SELECT avg_cards_per_game FROM gh_referees "
            "WHERE lower(referee_name) = lower(?)",
            (referee_name,),
        ).fetchone()
        upstream_avg = (
            float(upstream["avg_cards_per_game"])
            if upstream is not None and upstream["avg_cards_per_game"] is not None
            else None
        )
        internal = con.execute(
            "SELECT COUNT(DISTINCT gh.external_match_id) AS n, "
            "COALESCE(SUM(CASE WHEN e.event_type IN ('Yellow Card', 'Red Card') "
            "THEN 1 ELSE 0 END), 0) AS cards "
            "FROM gh_matches gh "
            "LEFT JOIN gh_match_events e ON e.external_match_id = gh.external_match_id "
            "WHERE lower(gh.referee_name) = lower(?) AND gh.home_score IS NOT NULL "
            "AND (gh.match_id IS NULL OR gh.match_id != ?)",
            (referee_name, match_id),
        ).fetchone()
        tournament = con.execute(
            "SELECT COUNT(DISTINCT gh.external_match_id) AS n, "
            "COALESCE(SUM(CASE WHEN e.event_type IN ('Yellow Card', 'Red Card') "
            "THEN 1 ELSE 0 END), 0) AS cards "
            "FROM gh_matches gh "
            "LEFT JOIN gh_match_events e ON e.external_match_id = gh.external_match_id "
            "WHERE gh.home_score IS NOT NULL",
        ).fetchone()
    tournament_mean = (
        float(tournament["cards"]) / float(tournament["n"])
        if tournament is not None and tournament["n"] else 4.0
    )
    internal_matches = int(internal["n"]) if internal is not None else 0
    internal_cards = int(internal["cards"]) if internal is not None else 0
    multiplier = blend_referee_rate(
        internal_cards, internal_matches, upstream_avg, tournament_mean
    )
    return multiplier, referee_name
