from datetime import datetime, timezone
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
import tempfile
import unittest

from wcpredict.refresh import build_collect_command, refresh_match


class FakeStore:
    def __init__(self, bundle=None):
        self.bundle = bundle

    def find_event(self, team_a, team_b, event_date):
        return self.bundle


class RefreshTests(unittest.TestCase):
    def test_zero_odds_budget_reports_intentional_skip(self):
        command = build_collect_command(
            Path("python.exe"),
            Path("collect.py"),
            "Canada",
            "Qatar",
            datetime(2026, 6, 18, 22, tzinfo=timezone.utc),
            Path("sports-data"),
        )
        self.assertEqual("0", command[command.index("--max-odds-credits") + 1])

    def test_command_is_bounded_and_disables_odds_credits(self):
        command = build_collect_command(
            Path("python.exe"), Path("collect.py"), "Czechia", "South Africa",
            datetime(2026, 6, 18, 16, tzinfo=timezone.utc), Path("sports-data")
        )
        self.assertIn("Czechia vs South Africa", command)
        self.assertEqual("0", command[command.index("--max-odds-credits") + 1])
        self.assertEqual("14", command[command.index("--max-api-calls") + 1])

    def test_success_parses_collector_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "collect.py"
            script.write_text("# fixture", encoding="utf-8")
            seen = []

            def runner(command, **kwargs):
                seen.append((command, kwargs))
                return CompletedProcess(command, 0, '{"calls_made": 9, "coverage_complete": false}\n', "traceback from optional odds")

            result = refresh_match(
                "Czechia", "South Africa", datetime(2026, 6, 18, 16, tzinfo=timezone.utc),
                Path(tmp), script, runner=runner, store=FakeStore(bundle={"event": 8})
            )
        self.assertEqual("partial", result.status)
        self.assertEqual(9, result.calls_made)
        self.assertEqual({"event": 8}, result.bundle)
        self.assertNotIn("traceback", result.message.casefold())
        self.assertEqual(120, seen[0][1]["timeout"])

    def test_refresh_result_surfaces_providers_missing_and_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "collect.py"
            script.write_text("# fixture", encoding="utf-8")
            summary = (
                '{"calls_made": 11, "coverage_complete": false, '
                '"providers": ["api_sports_football", "football_data"], '
                '"odds_providers": [], '
                '"missing_critical": ["lineups", "team_statistics"]}\n'
            )

            def runner(command, **kwargs):
                return CompletedProcess(command, 0, summary, "warn: missing optional field\nother trace line")

            result = refresh_match(
                "Czechia", "South Africa", datetime(2026, 6, 18, 16, tzinfo=timezone.utc),
                Path(tmp), script, runner=runner, store=FakeStore(bundle={"event": 8})
            )
        self.assertEqual(("api_sports_football", "football_data"), result.providers)
        self.assertEqual((), result.odds_providers)
        self.assertEqual(("lineups", "team_statistics"), result.missing_critical)
        self.assertIn("missing optional field", result.stderr_tail)

    def test_timeout_returns_cached_state_without_traceback(self):
        def runner(command, **kwargs):
            raise TimeoutExpired(command, kwargs["timeout"])

        result = refresh_match(
            "Canada", "Qatar", datetime(2026, 6, 18, 22, tzinfo=timezone.utc),
            Path("sports-data"), Path("collect.py"), runner=runner, store=FakeStore(bundle={"cached": True}),
            require_script=False,
        )
        self.assertEqual("cached", result.status)
        self.assertIn("tiempo", result.message.casefold())


if __name__ == "__main__":
    unittest.main()
