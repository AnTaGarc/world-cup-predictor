from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
from typing import Callable, Any
import json
import os
import subprocess
import sys

from wcpredict.collector_store import CollectorStore
from wcpredict.names import canonical_team_name


@dataclass(frozen=True)
class RefreshResult:
    status: str
    message: str
    calls_made: int
    bundle: Any | None
    odds_status: str = "skipped_zero_budget"
    providers: tuple[str, ...] = ()
    odds_providers: tuple[str, ...] = ()
    missing_critical: tuple[str, ...] = ()
    stderr_tail: str = ""


def default_collector_script() -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    return codex_home / "skills" / "analisis-de-datos" / "scripts" / "collect.py"


def build_collect_command(
    python: Path,
    script: Path,
    team_a: str,
    team_b: str,
    kickoff_utc: datetime,
    sports_data_dir: Path,
) -> list[str]:
    return [
        str(python),
        str(script),
        "--sport",
        "football",
        "--event",
        f"{canonical_team_name(team_a)} vs {canonical_team_name(team_b)}",
        "--date",
        kickoff_utc.date().isoformat(),
        "--competition",
        "FIFA World Cup",
        "--data-dir",
        str(sports_data_dir),
        "--max-api-calls",
        "14",
        "--max-odds-credits",
        "0",
    ]


def _summary(stdout: str) -> dict:
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def refresh_match(
    team_a: str,
    team_b: str,
    kickoff_utc: datetime,
    sports_data_dir: Path,
    collector_script: Path | None = None,
    *,
    runner: Callable[..., CompletedProcess] = subprocess.run,
    store: Any | None = None,
    require_script: bool = True,
) -> RefreshResult:
    script = collector_script or default_collector_script()
    evidence_store = store or CollectorStore(sports_data_dir / "sports.db")
    if require_script and not script.exists():
        cached = evidence_store.find_event(team_a, team_b, kickoff_utc.date())
        return RefreshResult(
            "cached" if cached else "unavailable",
            "El recolector no esta instalado; se mantienen los datos cacheados." if cached else "El recolector local no esta instalado.",
            0,
            cached,
        )

    command = build_collect_command(
        Path(sys.executable), script, team_a, team_b, kickoff_utc, sports_data_dir
    )
    try:
        completed = runner(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except TimeoutExpired:
        cached = evidence_store.find_event(team_a, team_b, kickoff_utc.date())
        return RefreshResult(
            "cached" if cached else "failed",
            "La actualizacion supero el tiempo limite; se mantienen los datos cacheados.",
            0,
            cached,
        )

    summary = _summary(completed.stdout or "")
    cached = evidence_store.find_event(team_a, team_b, kickoff_utc.date())
    calls_made = int(summary.get("calls_made") or 0)
    providers = tuple(str(value) for value in (summary.get("providers") or ()))
    odds_providers = tuple(str(value) for value in (summary.get("odds_providers") or ()))
    missing_critical = tuple(str(value) for value in (summary.get("missing_critical") or ()))
    stderr_tail = "\n".join((completed.stderr or "").splitlines()[-5:])
    if completed.returncode != 0:
        return RefreshResult(
            "cached" if cached else "failed",
            "El proveedor no pudo completar la actualizacion; se conserva el ultimo estado disponible.",
            calls_made, cached,
            providers=providers, odds_providers=odds_providers,
            missing_critical=missing_critical, stderr_tail=stderr_tail,
        )
    complete = bool(summary.get("coverage_complete"))
    return RefreshResult(
        "complete" if complete else "partial",
        "Actualizacion completa." if complete else "Actualizacion parcial: faltan algunos campos y se muestran como tales.",
        calls_made, cached,
        providers=providers, odds_providers=odds_providers,
        missing_critical=missing_critical, stderr_tail=stderr_tail,
    )
