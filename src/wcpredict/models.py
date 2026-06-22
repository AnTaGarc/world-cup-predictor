from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class MarketFamily(str, Enum):
    MATCH_RESULT = "match_result"
    DOUBLE_CHANCE = "double_chance"
    DRAW_NO_BET = "draw_no_bet"
    HANDICAP = "handicap"
    GOALS = "goals"
    BTTS = "both_teams_to_score"
    TEAM_TOTALS = "team_totals"
    CORNERS = "corners"
    CARDS = "cards"
    SHOTS = "shots"
    SHOTS_ON_TARGET = "shots_on_target"
    PLAYER_GOAL = "player_goal"
    PLAYER_ASSIST = "player_assist"
    PLAYER_SHOTS = "player_shots"
    PLAYER_SHOTS_ON_TARGET = "player_shots_on_target"
    PLAYER_CARDS = "player_cards"
    PLAYER_PASSES = "player_passes"
    PLAYER_SAVES = "player_saves"
    PLAYER_GOALS_CONCEDED = "player_goals_conceded"
    PLAYER_CLEAN_SHEET = "player_clean_sheet"
    CUSTOM = "custom"


@dataclass(frozen=True)
class Team:
    id: int
    name: str
    fifa_code: str | None = None


@dataclass(frozen=True)
class Player:
    id: int
    name: str
    team_id: int
    position: str | None = None


@dataclass(frozen=True)
class Match:
    id: int
    competition: str
    stage: str
    kickoff_utc: datetime
    team_a: Team
    team_b: Team
    status: str
    venue: str | None = None
    neutral_site: bool = True

    @property
    def label(self) -> str:
        return f"{self.team_a.name} vs {self.team_b.name}"


@dataclass(frozen=True)
class TeamMatchStats:
    match_id: int
    team_id: int
    goals: int | None = None
    xg: float | None = None
    shots: int | None = None
    shots_on_target: int | None = None
    possession: float | None = None
    corners: int | None = None
    yellow_cards: int | None = None
    red_cards: int | None = None


@dataclass(frozen=True)
class PlayerMatchStats:
    match_id: int
    player_id: int
    minutes: int | None = None
    goals: int | None = None
    assists: int | None = None
    shots: int | None = None
    shots_on_target: int | None = None
    yellow_cards: int | None = None
    passes: int | None = None
