from datetime import datetime, timezone
import unittest

from wcpredict.models import MarketFamily, Match, Team
from wcpredict.quality import Confidence, assess_market_confidence, calibrate_confidence


class QualityTests(unittest.TestCase):
    def test_team_and_match_models(self):
        spain = Team(id=1, name="Spain", fifa_code="ESP")
        japan = Team(id=2, name="Japan", fifa_code="JPN")
        match = Match(
            id=10,
            competition="FIFA World Cup 2026",
            stage="Group",
            kickoff_utc=datetime(2026, 6, 18, 19, 0, tzinfo=timezone.utc),
            team_a=spain,
            team_b=japan,
            status="scheduled",
        )
        self.assertEqual("Spain vs Japan", match.label)

    def test_high_confidence_for_complete_team_market(self):
        confidence = assess_market_confidence(
            market_family=MarketFamily.GOALS,
            sample_size=14,
            missing_fields=[],
            lineup_dependent=False,
            manually_estimated=False,
        )
        self.assertEqual(Confidence.HIGH, confidence)

    def test_low_confidence_for_player_lineup_uncertainty(self):
        confidence = assess_market_confidence(
            market_family=MarketFamily.PLAYER_SHOTS,
            sample_size=5,
            missing_fields=["expected_minutes"],
            lineup_dependent=True,
            manually_estimated=True,
        )
        self.assertEqual(Confidence.LOW, confidence)

    def test_not_estimable_for_required_missing_probability_inputs(self):
        confidence = assess_market_confidence(
            market_family=MarketFamily.PLAYER_GOAL,
            sample_size=0,
            missing_fields=["player", "team", "expected_minutes"],
            lineup_dependent=True,
            manually_estimated=False,
        )
        self.assertEqual(Confidence.NOT_ESTIMABLE, confidence)

    def test_calibration_only_increases_confidence_with_enough_evidence(self):
        self.assertEqual(Confidence.MEDIUM, calibrate_confidence(Confidence.HIGH, 5, .10))
        self.assertEqual(Confidence.HIGH, calibrate_confidence(Confidence.MEDIUM, 25, .12))
        self.assertEqual(Confidence.LOW, calibrate_confidence(Confidence.MEDIUM, 25, .35))


if __name__ == "__main__":
    unittest.main()
