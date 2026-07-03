# Fase 0 — Cimentación del proveedor github_wc2026 · Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** integrar el dataset externo `github.com/mominullptr/FIFA-World-Cup-2026-Dataset` como proveedor diario más del pipeline, con reconciliación de entidades (equipos/jugadores/árbitros) y verificación cruzada de marcador, sin tocar los modelos predictivos existentes ni la infraestructura de tandas.

**Architecture:** módulo nuevo `github_wc2026_dataset.py` paralelo a `world_cup_data.py`, 8 tablas nuevas `gh_*` + `entity_alias_map` + `gh_score_verifications`, ampliación de `daily_refresh.DEFAULT_PROVIDERS`, y UI mínima bajo el badge de "Datos diarios" para revisar discrepancias y alias pendientes. Reconciliación en tres pasadas (fifa_code → normalización → alias pendiente). Verificación cruzada de marcador que **nunca** autocorrige.

**Tech Stack:** Python 3.12, `sqlite3`, `requests`, `unittest`, Streamlit (ya en uso). Test runner: `python -m unittest`.

## Global Constraints

- Todo el trabajo persiste en `data/worldcup.sqlite`; se aplican `PRAGMA foreign_keys = ON` y `PRAGMA busy_timeout = 30000`, ya establecidos por `Repository.connect()`.
- Nunca sobreescribir `match_results` con `source_type='verified_user_capture'`; solo detectar discrepancia y encolarla.
- No modificar los flujos de penaltis (`shootout_kicks`, `penalty_history_model`, `transfermarkt_penalties`, `espn_shootouts`).
- No modificar el vector de features de `outcome_ml.py` / `outcome_ml_deep.py` en esta fase.
- `WCP_OFFLINE=1` desactiva todas las peticiones HTTP (compatibilidad con lo existente).
- Un único fetch a la API `GET commits/main` de GitHub por ciclo de `ensure_current_world_cup_data`, compartido por todos los `github_*`.
- Provider IDs literales, no cambiar: `github_wc2026_events`, `github_wc2026_team_stats`, `github_wc2026_lineups`, `github_wc2026_matches`, `github_wc2026_referees`, `github_wc2026_player_stats`, `github_wc2026_teams`.
- Base para tests: `Repository(Path(tempdir) / "app.sqlite")` + `repo.initialize()`, patrón ya usado en `tests/test_daily_refresh.py`.
- Estilo commit: sin emojis, en presente imperativo, prefijo tipo `feat(ghwc):`, `fix(ghwc):`, `test(ghwc):`, `docs(ghwc):`.
- Idioma: código y tests en inglés; comentarios mínimos.
- Trailer obligatorio en cada commit: `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.

## File Structure

**Se crean:**
- `src/wcpredict/github_wc2026_dataset.py` — cliente HTTP + parsers + importador central.
- `scripts/backfill_github_wc2026.py` — comando one-shot idempotente de backfill.
- `tests/test_github_wc2026_dataset.py` — parsers puros contra CSV fixtures.
- `tests/test_github_wc2026_import.py` — import completo contra BD in-memory con reconciliación.
- `tests/test_github_wc2026_score_verification.py` — casos match/mismatch/no_local_result.
- `tests/test_github_wc2026_period_derivation.py` — mapeo `minute` → `period`.
- `tests/fixtures/github_wc2026/` — mini CSVs (uno por provider) usados por los tests.

**Se modifican:**
- `src/wcpredict/database.py` — 8 `CREATE TABLE IF NOT EXISTS` nuevos + índices.
- `src/wcpredict/daily_refresh.py` — ampliar `DEFAULT_PROVIDERS`.
- `src/wcpredict/services.py` — fetcher factoría que despacha por prefijo, resolviendo el SHA una vez por ciclo.
- `src/wcpredict/repository.py` — métodos `replace_gh_*`, `resolve_pending_aliases`, `list_pending_aliases`, `confirm_alias`, `record_score_verification`, `list_score_mismatches`.
- `src/wcpredict/ui/pages.py` — expander "Dataset externo (mominullptr)" bajo el badge de datos diarios.
- `tests/test_daily_refresh.py` — extender escenario mixto para incluir `github_*`.
- `tests/test_app_contract.py` — extender contrato UI para el nuevo expander.
- `README.md` — sección "Fuente externa: mominullptr/FIFA-World-Cup-2026-Dataset".

---

### Task 1: Esquema de tablas nuevas

**Files:**
- Modify: `src/wcpredict/database.py` (añadir después del último `CREATE TABLE` existente y antes de los `CREATE INDEX`)
- Test: `tests/test_github_wc2026_schema.py` (nuevo)

**Interfaces:**
- Produces: 8 tablas nuevas creadas idempotentemente por `Repository.initialize()`:
  - `gh_match_events(provider_id, external_event_id, external_match_id, match_id, minute, period, event_type, external_team_id, team_id, external_player_id, player_id, provider_version, imported_at_utc)` — PK `(provider_id, external_event_id)`.
  - `gh_match_team_stats(provider_id, external_match_id, match_id, external_team_id, team_id, possession_pct, total_shots, shots_on_target, corners, fouls, offsides, saves, player_of_the_match, data_source, last_updated, imported_at_utc)` — PK `(provider_id, external_match_id, external_team_id)`.
  - `gh_match_lineups(provider_id, external_lineup_id, external_match_id, match_id, external_player_id, player_id, external_team_id, team_id, is_starting_xi, tactical_position, minutes_played, imported_at_utc)` — PK `(provider_id, external_lineup_id)`.
  - `gh_matches(provider_id, external_match_id, match_id, date, kickoff_time_utc, stage_name, stadium_name, city, country, external_home_team_id, home_team_id, home_team_name, home_fifa_code, external_away_team_id, away_team_id, away_team_name, away_fifa_code, home_score, away_score, status, home_xg, away_xg, home_goalkeeper, away_goalkeeper, player_of_the_match_name, external_referee_id, referee_id, referee_name, imported_at_utc)` — PK `external_match_id`.
  - `gh_referees(provider_id, external_referee_id, referee_name, country, avg_cards_per_game, imported_at_utc)` — PK `(provider_id, external_referee_id)`.
  - `gh_player_stats(provider_id, external_player_id, player_id, external_team_id, team_id, player_name, position, matches_played, matches_started, minutes_played, goals, assists, shots, shots_on_target, yellow_cards, red_cards, penalty_goals, own_goals, clean_sheets, saves, goals_conceded, average_rating, data_source, last_verified, imported_at_utc)` — PK `(provider_id, external_player_id)`.
  - `gh_teams(provider_id, external_team_id, team_id, team_name, fifa_code, group_letter, confederation, fifa_ranking_pre_tournament, elo_rating, manager_name, imported_at_utc)` — PK `(provider_id, external_team_id)`.
  - `entity_alias_map(entity_type, source_key, external_id, internal_id, confirmed_by, confirmed_at_utc)` — PK `(entity_type, source_key, external_id)`.
  - `gh_score_verifications(match_id, provider_version, dataset_home_score, dataset_away_score, local_home_score, local_away_score, status, detected_at_utc, reviewed_by, reviewed_at_utc, resolution)` — PK `(match_id, provider_version)`.
- Índices: `idx_gh_match_events_match ON gh_match_events(match_id)`, `idx_gh_match_events_ext_match ON gh_match_events(external_match_id)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_github_wc2026_schema.py`:

```python
import tempfile
import unittest
from pathlib import Path

from wcpredict.repository import Repository


EXPECTED_TABLES = (
    "gh_match_events",
    "gh_match_team_stats",
    "gh_match_lineups",
    "gh_matches",
    "gh_referees",
    "gh_player_stats",
    "gh_teams",
    "entity_alias_map",
    "gh_score_verifications",
)


class GithubWc2026SchemaTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "app.sqlite")
        self.repo.initialize()

    def tearDown(self):
        self.directory.cleanup()

    def test_all_expected_tables_are_present(self):
        with self.repo.session() as con:
            names = {
                row["name"] for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        for table in EXPECTED_TABLES:
            self.assertIn(table, names)

    def test_initialize_is_idempotent(self):
        self.repo.initialize()
        self.repo.initialize()
        with self.repo.session() as con:
            count = con.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='gh_matches'"
            ).fetchone()[0]
        self.assertEqual(1, count)

    def test_gh_match_events_has_expected_columns(self):
        with self.repo.session() as con:
            columns = {
                row["name"] for row in con.execute("PRAGMA table_info(gh_match_events)")
            }
        for column in (
            "provider_id", "external_event_id", "external_match_id", "match_id",
            "minute", "period", "event_type", "external_team_id", "team_id",
            "external_player_id", "player_id", "provider_version", "imported_at_utc",
        ):
            self.assertIn(column, columns)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_github_wc2026_schema -v`
Expected: FAIL — las tablas `gh_*` no existen.

- [ ] **Step 3: Add tables to schema**

En `src/wcpredict/database.py`, localizar el bloque `SCHEMA = """..."""` (empieza en la línea 6). Añadir los 9 `CREATE TABLE IF NOT EXISTS` antes del cierre `"""`, en el orden listado en Interfaces. Añadir además los dos `CREATE INDEX IF NOT EXISTS` al final del bloque índices (~línea 620 en adelante). Sigue el estilo indentado del schema actual (4 espacios, comas al final, PK en línea aparte cuando es compuesta).

Contenido literal a añadir (colócalo justo antes del cierre `"""` del `SCHEMA`, y para los índices dentro de la sección de índices al final del `SCHEMA`):

```sql
CREATE TABLE IF NOT EXISTS gh_match_events (
    provider_id TEXT NOT NULL,
    external_event_id INTEGER NOT NULL,
    external_match_id INTEGER NOT NULL,
    match_id INTEGER,
    minute INTEGER NOT NULL,
    period TEXT NOT NULL,
    event_type TEXT NOT NULL,
    external_team_id INTEGER NOT NULL,
    team_id INTEGER,
    external_player_id INTEGER NOT NULL,
    player_id INTEGER,
    provider_version TEXT NOT NULL,
    imported_at_utc TEXT NOT NULL,
    PRIMARY KEY(provider_id, external_event_id)
);

CREATE TABLE IF NOT EXISTS gh_match_team_stats (
    provider_id TEXT NOT NULL,
    external_match_id INTEGER NOT NULL,
    match_id INTEGER,
    external_team_id INTEGER NOT NULL,
    team_id INTEGER,
    possession_pct REAL,
    total_shots INTEGER,
    shots_on_target INTEGER,
    corners INTEGER,
    fouls INTEGER,
    offsides INTEGER,
    saves INTEGER,
    player_of_the_match TEXT,
    data_source TEXT,
    last_updated TEXT,
    imported_at_utc TEXT NOT NULL,
    PRIMARY KEY(provider_id, external_match_id, external_team_id)
);

CREATE TABLE IF NOT EXISTS gh_match_lineups (
    provider_id TEXT NOT NULL,
    external_lineup_id INTEGER NOT NULL,
    external_match_id INTEGER NOT NULL,
    match_id INTEGER,
    external_player_id INTEGER NOT NULL,
    player_id INTEGER,
    external_team_id INTEGER NOT NULL,
    team_id INTEGER,
    is_starting_xi INTEGER NOT NULL,
    tactical_position TEXT,
    minutes_played INTEGER,
    imported_at_utc TEXT NOT NULL,
    PRIMARY KEY(provider_id, external_lineup_id)
);

CREATE TABLE IF NOT EXISTS gh_matches (
    provider_id TEXT NOT NULL,
    external_match_id INTEGER PRIMARY KEY,
    match_id INTEGER,
    date TEXT,
    kickoff_time_utc TEXT,
    stage_name TEXT,
    stadium_name TEXT,
    city TEXT,
    country TEXT,
    external_home_team_id INTEGER,
    home_team_id INTEGER,
    home_team_name TEXT,
    home_fifa_code TEXT,
    external_away_team_id INTEGER,
    away_team_id INTEGER,
    away_team_name TEXT,
    away_fifa_code TEXT,
    home_score INTEGER,
    away_score INTEGER,
    status TEXT,
    home_xg REAL,
    away_xg REAL,
    home_goalkeeper TEXT,
    away_goalkeeper TEXT,
    player_of_the_match_name TEXT,
    external_referee_id INTEGER,
    referee_id INTEGER,
    referee_name TEXT,
    imported_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gh_referees (
    provider_id TEXT NOT NULL,
    external_referee_id INTEGER NOT NULL,
    referee_name TEXT NOT NULL,
    country TEXT,
    avg_cards_per_game REAL,
    imported_at_utc TEXT NOT NULL,
    PRIMARY KEY(provider_id, external_referee_id)
);

CREATE TABLE IF NOT EXISTS gh_player_stats (
    provider_id TEXT NOT NULL,
    external_player_id INTEGER NOT NULL,
    player_id INTEGER,
    external_team_id INTEGER,
    team_id INTEGER,
    player_name TEXT NOT NULL,
    position TEXT,
    matches_played INTEGER,
    matches_started INTEGER,
    minutes_played INTEGER,
    goals INTEGER,
    assists INTEGER,
    shots INTEGER,
    shots_on_target INTEGER,
    yellow_cards INTEGER,
    red_cards INTEGER,
    penalty_goals INTEGER,
    own_goals INTEGER,
    clean_sheets INTEGER,
    saves INTEGER,
    goals_conceded INTEGER,
    average_rating REAL,
    data_source TEXT,
    last_verified TEXT,
    imported_at_utc TEXT NOT NULL,
    PRIMARY KEY(provider_id, external_player_id)
);

CREATE TABLE IF NOT EXISTS gh_teams (
    provider_id TEXT NOT NULL,
    external_team_id INTEGER NOT NULL,
    team_id INTEGER,
    team_name TEXT NOT NULL,
    fifa_code TEXT,
    group_letter TEXT,
    confederation TEXT,
    fifa_ranking_pre_tournament INTEGER,
    elo_rating INTEGER,
    manager_name TEXT,
    imported_at_utc TEXT NOT NULL,
    PRIMARY KEY(provider_id, external_team_id)
);

CREATE TABLE IF NOT EXISTS entity_alias_map (
    entity_type TEXT NOT NULL,
    source_key TEXT NOT NULL,
    external_id TEXT NOT NULL,
    internal_id INTEGER NOT NULL,
    confirmed_by TEXT NOT NULL,
    confirmed_at_utc TEXT NOT NULL,
    PRIMARY KEY(entity_type, source_key, external_id)
);

CREATE TABLE IF NOT EXISTS gh_score_verifications (
    match_id INTEGER NOT NULL,
    provider_version TEXT NOT NULL,
    dataset_home_score INTEGER,
    dataset_away_score INTEGER,
    local_home_score INTEGER,
    local_away_score INTEGER,
    status TEXT NOT NULL,
    detected_at_utc TEXT NOT NULL,
    reviewed_by TEXT,
    reviewed_at_utc TEXT,
    resolution TEXT,
    PRIMARY KEY(match_id, provider_version)
);

CREATE INDEX IF NOT EXISTS idx_gh_match_events_match
ON gh_match_events(match_id);
CREATE INDEX IF NOT EXISTS idx_gh_match_events_ext_match
ON gh_match_events(external_match_id);
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_github_wc2026_schema -v`
Expected: 3 tests PASS.

Run: `python -m unittest tests.test_daily_refresh -v` (regresión)
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wcpredict/database.py tests/test_github_wc2026_schema.py
git commit -m "$(cat <<'EOF'
feat(ghwc): add schema for github_wc2026 dataset tables

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Parsers puros de CSV

**Files:**
- Create: `src/wcpredict/github_wc2026_dataset.py`
- Create: `tests/fixtures/github_wc2026/match_events.csv`
- Create: `tests/fixtures/github_wc2026/match_team_stats.csv`
- Create: `tests/fixtures/github_wc2026/match_lineups.csv`
- Create: `tests/fixtures/github_wc2026/matches_detailed.csv`
- Create: `tests/fixtures/github_wc2026/referees.csv`
- Create: `tests/fixtures/github_wc2026/player_stats.csv`
- Create: `tests/fixtures/github_wc2026/teams.csv`
- Create: `tests/test_github_wc2026_dataset.py`

**Interfaces:**
- Produces:
  - `parse_events_rows(csv_text: str) -> list[dict]` — cada dict con `external_event_id`, `external_match_id`, `minute`, `event_type`, `external_team_id`, `external_player_id`.
  - `parse_team_stats_rows(csv_text: str) -> list[dict]` — dicts con `external_match_id`, `external_team_id`, `possession_pct`, `total_shots`, `shots_on_target`, `corners`, `fouls`, `offsides`, `saves`, `player_of_the_match`, `data_source`, `last_updated`.
  - `parse_lineups_rows(csv_text: str) -> list[dict]` — dicts con `external_lineup_id`, `external_match_id`, `external_player_id`, `external_team_id`, `is_starting_xi`, `tactical_position`, `minutes_played`.
  - `parse_matches_rows(csv_text: str) -> list[dict]` — dicts con todos los campos de `matches_detailed.csv` incluyendo `date`, `kickoff_time_utc`, `stage_name`, `stadium_name`, `city`, `country`, `home_team_name`, `home_fifa_code`, `away_team_name`, `away_fifa_code`, `home_score`, `away_score`, `status`, `home_xg`, `away_xg`, `home_goalkeeper`, `away_goalkeeper`, `player_of_the_match_name`, `referee_name`.
  - `parse_referees_rows(csv_text: str) -> list[dict]` — dicts con `external_referee_id`, `referee_name`, `country`, `avg_cards_per_game`.
  - `parse_player_stats_rows(csv_text: str) -> list[dict]` — dicts con todos los campos de `player_stats.csv`.
  - `parse_teams_rows(csv_text: str) -> list[dict]` — dicts con `external_team_id`, `team_name`, `fifa_code`, `group_letter`, `confederation`, `fifa_ranking_pre_tournament`, `elo_rating`, `manager_name`.
  - `derive_period(minute: int) -> str` — mapeo determinista descrito en Task 3.

- [ ] **Step 1: Write fixture CSVs (mini)**

Cada fixture debe cubrir un caso positivo y uno con valor faltante donde el parser deba tolerar.

`tests/fixtures/github_wc2026/match_events.csv`:

```csv
event_id,match_id,minute,event_type,team_id,player_id
1,1,9,Goal,1,16
2,1,9,Assist,1,6
3,1,17,Yellow Card,2,30
4,1,110,Yellow Card,2,45
```

`tests/fixtures/github_wc2026/match_team_stats.csv`:

```csv
match_id,team_id,possession_pct,total_shots,shots_on_target,corners,fouls,offsides,saves,player_of_the_match,data_source,last_updated
1,1,57,16,4,6,11,2,1,Julián Andrés Quinones,fifa.com,2026-06-24
1,2,43,3,2,3,15,1,4,,fifa.com,2026-06-24
```

`tests/fixtures/github_wc2026/match_lineups.csv`:

```csv
lineup_id,match_id,player_id,team_id,is_starting_xi,tactical_position,minutes_played
1,1,1,1,1,GK,90
4,1,4,1,0,DEF,14
```

`tests/fixtures/github_wc2026/matches_detailed.csv`:

```csv
match_id,date,kickoff_time_utc,stage_name,stadium_name,city,country,home_team_name,home_fifa_code,away_team_name,away_fifa_code,home_score,away_score,status,home_xg,away_xg,home_goalkeeper,away_goalkeeper,player_of_the_match_name,referee_name
1,2026-06-11,2026-06-11T20:00:00Z,Group Stage,Mexico City Stadium (Estadio Azteca),Mexico City,MEX,Mexico,MEX,South Africa,RSA,2,0,Completed,1.8,0.6,José Raúl Rangel,Ronwen Williams,Julián Andrés Quinones,Szymon Marciniak
83,2026-07-03,2026-07-03T18:00:00Z,Round of 32,Los Angeles Stadium,Los Angeles,USA,Spain,ESP,Austria,AUT,,,Scheduled,,,,,,Michael Oliver
```

`tests/fixtures/github_wc2026/referees.csv`:

```csv
referee_id,name,country,avg_cards_per_game
1,Szymon Marciniak,Poland,4.2
3,Michael Oliver,England,3.9
```

`tests/fixtures/github_wc2026/player_stats.csv`:

```csv
player_id,player_name,team_id,position,matches_played,matches_started,minutes_played,goals,assists,shots,shots_on_target,yellow_cards,red_cards,penalty_goals,own_goals,clean_sheets,saves,goals_conceded,average_rating,data_source,last_verified
1,José Raúl Rangel,1,GK,4,4,348,0,0,,,0,0,0,0,4,5,0,,sofascore.com,2026-07-01
16,Julián Andrés Quinones,1,FWD,3,3,220,3,1,10,4,0,0,1,0,,,,7.9,sofascore.com,2026-07-01
```

`tests/fixtures/github_wc2026/teams.csv`:

```csv
team_id,team_name,fifa_code,group_letter,confederation,fifa_ranking_pre_tournament,elo_rating,manager_name
1,Mexico,MEX,A,CONCACAF,14,1810,Javier Aguirre
55,Cabo Verde,CPV,,CAF,80,1550,Pedro Leitão Brito
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_github_wc2026_dataset.py`:

```python
import unittest
from pathlib import Path

from wcpredict.github_wc2026_dataset import (
    parse_events_rows,
    parse_team_stats_rows,
    parse_lineups_rows,
    parse_matches_rows,
    parse_referees_rows,
    parse_player_stats_rows,
    parse_teams_rows,
)


FIXTURES = Path(__file__).parent / "fixtures" / "github_wc2026"


def _read(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class ParserTests(unittest.TestCase):
    def test_events(self):
        rows = parse_events_rows(_read("match_events.csv"))
        self.assertEqual(4, len(rows))
        self.assertEqual(
            {
                "external_event_id": 1,
                "external_match_id": 1,
                "minute": 9,
                "event_type": "Goal",
                "external_team_id": 1,
                "external_player_id": 16,
            },
            rows[0],
        )

    def test_team_stats_null_potm_becomes_none(self):
        rows = parse_team_stats_rows(_read("match_team_stats.csv"))
        self.assertEqual(2, len(rows))
        self.assertIsNone(rows[1]["player_of_the_match"])
        self.assertEqual(57.0, rows[0]["possession_pct"])
        self.assertEqual(16, rows[0]["total_shots"])

    def test_lineups(self):
        rows = parse_lineups_rows(_read("match_lineups.csv"))
        self.assertEqual(1, rows[0]["is_starting_xi"])
        self.assertEqual(0, rows[1]["is_starting_xi"])
        self.assertEqual("GK", rows[0]["tactical_position"])
        self.assertEqual(14, rows[1]["minutes_played"])

    def test_matches_optional_score_is_none(self):
        rows = parse_matches_rows(_read("matches_detailed.csv"))
        self.assertEqual(2, len(rows))
        finished, scheduled = rows
        self.assertEqual(2, finished["home_score"])
        self.assertEqual("MEX", finished["home_fifa_code"])
        self.assertEqual("Szymon Marciniak", finished["referee_name"])
        self.assertIsNone(scheduled["home_score"])
        self.assertIsNone(scheduled["away_score"])
        self.assertEqual("Scheduled", scheduled["status"])

    def test_referees(self):
        rows = parse_referees_rows(_read("referees.csv"))
        self.assertEqual(2, len(rows))
        self.assertEqual("Szymon Marciniak", rows[0]["referee_name"])
        self.assertEqual(4.2, rows[0]["avg_cards_per_game"])

    def test_player_stats_optional_numbers(self):
        rows = parse_player_stats_rows(_read("player_stats.csv"))
        self.assertEqual(2, len(rows))
        keeper = rows[0]
        self.assertEqual("GK", keeper["position"])
        self.assertIsNone(keeper["shots"])
        self.assertEqual(4, keeper["clean_sheets"])
        forward = rows[1]
        self.assertEqual(3, forward["goals"])
        self.assertEqual(7.9, forward["average_rating"])

    def test_teams_missing_group_letter(self):
        rows = parse_teams_rows(_read("teams.csv"))
        self.assertEqual("Cabo Verde", rows[1]["team_name"])
        self.assertIsNone(rows[1]["group_letter"])
        self.assertEqual(80, rows[1]["fifa_ranking_pre_tournament"])
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m unittest tests.test_github_wc2026_dataset -v`
Expected: FAIL — módulo `github_wc2026_dataset` no existe.

- [ ] **Step 4: Create the module with parsers**

Create `src/wcpredict/github_wc2026_dataset.py`:

```python
from __future__ import annotations

import csv
import re
from io import StringIO
from typing import Any


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _rows(csv_text: str) -> list[dict[str, str | None]]:
    reader = csv.DictReader(StringIO(csv_text.lstrip("﻿")))
    return [
        {
            _key(str(name)): (value.strip() if value is not None and value.strip() else None)
            for name, value in row.items()
        }
        for row in reader
    ]


def _int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(str(value).replace(",", ".")))
    except ValueError:
        return None


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace("%", "").replace(",", "."))
    except ValueError:
        return None


def _str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_events_rows(csv_text: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in _rows(csv_text):
        event_id = _int(row.get("event_id"))
        match_id = _int(row.get("match_id"))
        minute = _int(row.get("minute"))
        team_id = _int(row.get("team_id"))
        player_id = _int(row.get("player_id"))
        event_type = _str(row.get("event_type"))
        if event_id is None or match_id is None or minute is None or team_id is None or player_id is None or event_type is None:
            continue
        output.append(
            {
                "external_event_id": event_id,
                "external_match_id": match_id,
                "minute": minute,
                "event_type": event_type,
                "external_team_id": team_id,
                "external_player_id": player_id,
            }
        )
    return output


def parse_team_stats_rows(csv_text: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in _rows(csv_text):
        match_id = _int(row.get("match_id"))
        team_id = _int(row.get("team_id"))
        if match_id is None or team_id is None:
            continue
        output.append(
            {
                "external_match_id": match_id,
                "external_team_id": team_id,
                "possession_pct": _float(row.get("possession_pct")),
                "total_shots": _int(row.get("total_shots")),
                "shots_on_target": _int(row.get("shots_on_target")),
                "corners": _int(row.get("corners")),
                "fouls": _int(row.get("fouls")),
                "offsides": _int(row.get("offsides")),
                "saves": _int(row.get("saves")),
                "player_of_the_match": _str(row.get("player_of_the_match")),
                "data_source": _str(row.get("data_source")),
                "last_updated": _str(row.get("last_updated")),
            }
        )
    return output


def parse_lineups_rows(csv_text: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in _rows(csv_text):
        lineup_id = _int(row.get("lineup_id"))
        match_id = _int(row.get("match_id"))
        player_id = _int(row.get("player_id"))
        team_id = _int(row.get("team_id"))
        if lineup_id is None or match_id is None or player_id is None or team_id is None:
            continue
        output.append(
            {
                "external_lineup_id": lineup_id,
                "external_match_id": match_id,
                "external_player_id": player_id,
                "external_team_id": team_id,
                "is_starting_xi": _int(row.get("is_starting_xi")) or 0,
                "tactical_position": _str(row.get("tactical_position")),
                "minutes_played": _int(row.get("minutes_played")),
            }
        )
    return output


def parse_matches_rows(csv_text: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in _rows(csv_text):
        match_id = _int(row.get("match_id"))
        if match_id is None:
            continue
        output.append(
            {
                "external_match_id": match_id,
                "date": _str(row.get("date")),
                "kickoff_time_utc": _str(row.get("kickoff_time_utc")),
                "stage_name": _str(row.get("stage_name")),
                "stadium_name": _str(row.get("stadium_name")),
                "city": _str(row.get("city")),
                "country": _str(row.get("country")),
                "home_team_name": _str(row.get("home_team_name")),
                "home_fifa_code": _str(row.get("home_fifa_code")),
                "away_team_name": _str(row.get("away_team_name")),
                "away_fifa_code": _str(row.get("away_fifa_code")),
                "home_score": _int(row.get("home_score")),
                "away_score": _int(row.get("away_score")),
                "status": _str(row.get("status")),
                "home_xg": _float(row.get("home_xg")),
                "away_xg": _float(row.get("away_xg")),
                "home_goalkeeper": _str(row.get("home_goalkeeper")),
                "away_goalkeeper": _str(row.get("away_goalkeeper")),
                "player_of_the_match_name": _str(row.get("player_of_the_match_name")),
                "referee_name": _str(row.get("referee_name")),
            }
        )
    return output


def parse_referees_rows(csv_text: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in _rows(csv_text):
        referee_id = _int(row.get("referee_id"))
        name = _str(row.get("name"))
        if referee_id is None or name is None:
            continue
        output.append(
            {
                "external_referee_id": referee_id,
                "referee_name": name,
                "country": _str(row.get("country")),
                "avg_cards_per_game": _float(row.get("avg_cards_per_game")),
            }
        )
    return output


def parse_player_stats_rows(csv_text: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in _rows(csv_text):
        player_id = _int(row.get("player_id"))
        name = _str(row.get("player_name"))
        if player_id is None or name is None:
            continue
        output.append(
            {
                "external_player_id": player_id,
                "player_name": name,
                "external_team_id": _int(row.get("team_id")),
                "position": _str(row.get("position")),
                "matches_played": _int(row.get("matches_played")),
                "matches_started": _int(row.get("matches_started")),
                "minutes_played": _int(row.get("minutes_played")),
                "goals": _int(row.get("goals")),
                "assists": _int(row.get("assists")),
                "shots": _int(row.get("shots")),
                "shots_on_target": _int(row.get("shots_on_target")),
                "yellow_cards": _int(row.get("yellow_cards")),
                "red_cards": _int(row.get("red_cards")),
                "penalty_goals": _int(row.get("penalty_goals")),
                "own_goals": _int(row.get("own_goals")),
                "clean_sheets": _int(row.get("clean_sheets")),
                "saves": _int(row.get("saves")),
                "goals_conceded": _int(row.get("goals_conceded")),
                "average_rating": _float(row.get("average_rating")),
                "data_source": _str(row.get("data_source")),
                "last_verified": _str(row.get("last_verified")),
            }
        )
    return output


def parse_teams_rows(csv_text: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in _rows(csv_text):
        team_id = _int(row.get("team_id"))
        name = _str(row.get("team_name"))
        if team_id is None or name is None:
            continue
        output.append(
            {
                "external_team_id": team_id,
                "team_name": name,
                "fifa_code": _str(row.get("fifa_code")),
                "group_letter": _str(row.get("group_letter")),
                "confederation": _str(row.get("confederation")),
                "fifa_ranking_pre_tournament": _int(row.get("fifa_ranking_pre_tournament")),
                "elo_rating": _int(row.get("elo_rating")),
                "manager_name": _str(row.get("manager_name")),
            }
        )
    return output
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m unittest tests.test_github_wc2026_dataset -v`
Expected: 7 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wcpredict/github_wc2026_dataset.py tests/fixtures/github_wc2026/ tests/test_github_wc2026_dataset.py
git commit -m "$(cat <<'EOF'
feat(ghwc): add pure CSV parsers for github_wc2026 dataset

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Derivación de `period` desde `minute`

**Files:**
- Modify: `src/wcpredict/github_wc2026_dataset.py` (añadir función pura `derive_period`)
- Create: `tests/test_github_wc2026_period_derivation.py`

**Interfaces:**
- Produces: `derive_period(minute: int) -> str` con reglas:
  - `1 <= minute <= 45` → `"first_half"`
  - `46 <= minute <= 90` → `"second_half"`
  - `91 <= minute <= 105` → `"et_first"`
  - `106 <= minute <= 120` → `"et_second"`
  - fuera de rango → `ValueError`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_github_wc2026_period_derivation.py`:

```python
import unittest

from wcpredict.github_wc2026_dataset import derive_period


class PeriodDerivationTests(unittest.TestCase):
    def test_boundaries(self):
        cases = [
            (1, "first_half"),
            (45, "first_half"),
            (46, "second_half"),
            (90, "second_half"),
            (91, "et_first"),
            (105, "et_first"),
            (106, "et_second"),
            (120, "et_second"),
        ]
        for minute, expected in cases:
            with self.subTest(minute=minute):
                self.assertEqual(expected, derive_period(minute))

    def test_out_of_range_raises(self):
        for bad in (0, -1, 121, 200):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    derive_period(bad)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_github_wc2026_period_derivation -v`
Expected: FAIL — `derive_period` no existe.

- [ ] **Step 3: Add the function**

Añadir al final de `src/wcpredict/github_wc2026_dataset.py`:

```python
def derive_period(minute: int) -> str:
    if 1 <= minute <= 45:
        return "first_half"
    if 46 <= minute <= 90:
        return "second_half"
    if 91 <= minute <= 105:
        return "et_first"
    if 106 <= minute <= 120:
        return "et_second"
    raise ValueError(f"minute out of expected range: {minute}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_github_wc2026_period_derivation -v`
Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wcpredict/github_wc2026_dataset.py tests/test_github_wc2026_period_derivation.py
git commit -m "$(cat <<'EOF'
feat(ghwc): derive period label from event minute

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Reconciliación de equipos y alias

**Files:**
- Modify: `src/wcpredict/repository.py` (añadir métodos al final de la clase `Repository`)
- Create: `tests/test_github_wc2026_alias.py`

**Interfaces:**
- Produces (métodos en `Repository`):
  - `reconcile_gh_team(external_id: int, name: str, fifa_code: str | None, now_utc_iso: str) -> int | None` — devuelve `teams.id` si encaja por `fifa_code` (exacto, casefold) o por nombre normalizado (`unicodedata.normalize("NFKD", name).casefold()` + strip apóstrofes/guiones); si encaja, upserta en `entity_alias_map(entity_type='team', source_key='github_wc2026', external_id=str(external_id), internal_id=<id>, confirmed_by='auto_exact_match'|'auto_normalized', confirmed_at_utc=now)` y retorna el id. Si no, retorna `None` y NO inserta en alias.
  - `list_pending_aliases(entity_type: str) -> list[dict]` — retorna filas con `external_id` que aparecieron en tablas `gh_*` pero no tienen mapeo en `entity_alias_map`.
  - `confirm_alias(entity_type: str, source_key: str, external_id: str, internal_id: int, actor: str, now_utc_iso: str) -> None` — upsert con `confirmed_by=f"user:{actor}"`.
- La consulta de `list_pending_aliases` de tipo `team` opera contra `gh_teams`; de tipo `player`, contra `gh_player_stats`; de tipo `referee`, contra `gh_referees` (aunque para referees siempre están "mapeados" a sí mismos, se incluye por simetría — retornar lista vacía si no aplica).

- [ ] **Step 1: Write the failing test**

Create `tests/test_github_wc2026_alias.py`:

```python
import tempfile
import unittest
import unicodedata
from pathlib import Path

from wcpredict.repository import Repository


class GhwcAliasTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "app.sqlite")
        self.repo.initialize()
        with self.repo.session() as con:
            con.execute(
                "INSERT INTO teams(id, name, fifa_code) VALUES(?, ?, ?)",
                (1, "Mexico", "MEX"),
            )
            con.execute(
                "INSERT INTO teams(id, name, fifa_code) VALUES(?, ?, ?)",
                (2, "Cape Verde", "CPV"),
            )
        self.now = "2026-07-03T12:00:00+00:00"

    def tearDown(self):
        self.directory.cleanup()

    def test_reconcile_by_fifa_code_exact_case_insensitive(self):
        internal_id = self.repo.reconcile_gh_team(1, "México", "mex", self.now)
        self.assertEqual(1, internal_id)
        with self.repo.session() as con:
            row = con.execute(
                "SELECT confirmed_by, internal_id FROM entity_alias_map "
                "WHERE entity_type='team' AND source_key='github_wc2026' AND external_id='1'"
            ).fetchone()
        self.assertEqual("auto_exact_match", row["confirmed_by"])
        self.assertEqual(1, row["internal_id"])

    def test_reconcile_by_normalized_name(self):
        internal_id = self.repo.reconcile_gh_team(55, "Cabo Verde", None, self.now)
        self.assertEqual(2, internal_id)
        with self.repo.session() as con:
            row = con.execute(
                "SELECT confirmed_by FROM entity_alias_map "
                "WHERE entity_type='team' AND external_id='55'"
            ).fetchone()
        self.assertEqual("auto_normalized", row["confirmed_by"])

    def test_unresolved_returns_none_without_insert(self):
        internal_id = self.repo.reconcile_gh_team(999, "Atlantis", "ATL", self.now)
        self.assertIsNone(internal_id)
        with self.repo.session() as con:
            row = con.execute(
                "SELECT * FROM entity_alias_map WHERE external_id='999'"
            ).fetchone()
        self.assertIsNone(row)

    def test_list_pending_aliases_for_teams(self):
        with self.repo.session() as con:
            con.execute(
                "INSERT INTO gh_teams(provider_id, external_team_id, team_name, imported_at_utc) "
                "VALUES('github_wc2026_teams', 77, 'Freedonia', ?)",
                (self.now,),
            )
        pending = self.repo.list_pending_aliases("team")
        self.assertEqual(1, len(pending))
        self.assertEqual("77", pending[0]["external_id"])
        self.assertEqual("Freedonia", pending[0]["display_name"])

    def test_confirm_alias(self):
        self.repo.confirm_alias("team", "github_wc2026", "77", 2, "anton", self.now)
        with self.repo.session() as con:
            row = con.execute(
                "SELECT confirmed_by, internal_id FROM entity_alias_map WHERE external_id='77'"
            ).fetchone()
        self.assertEqual("user:anton", row["confirmed_by"])
        self.assertEqual(2, row["internal_id"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_github_wc2026_alias -v`
Expected: FAIL — métodos no existen.

- [ ] **Step 3: Implement reconcile + list_pending + confirm**

Añadir al final de la clase `Repository` en `src/wcpredict/repository.py`:

```python
    def _normalize_team_name(self, name: str) -> str:
        import unicodedata
        stripped = unicodedata.normalize("NFKD", name)
        stripped = "".join(ch for ch in stripped if not unicodedata.combining(ch))
        for junk in ("'", "'", "`", "-", "_"):
            stripped = stripped.replace(junk, "")
        return stripped.casefold().strip()

    def reconcile_gh_team(
        self, external_id: int, name: str, fifa_code: str | None, now_utc_iso: str
    ) -> int | None:
        with self.session() as con:
            if fifa_code:
                row = con.execute(
                    "SELECT id FROM teams WHERE lower(fifa_code)=lower(?)",
                    (fifa_code,),
                ).fetchone()
                if row is not None:
                    internal_id = int(row["id"])
                    self._upsert_alias(
                        con, "team", "github_wc2026", str(external_id),
                        internal_id, "auto_exact_match", now_utc_iso,
                    )
                    return internal_id
            target = self._normalize_team_name(name)
            for candidate in con.execute("SELECT id, name FROM teams").fetchall():
                if self._normalize_team_name(str(candidate["name"])) == target:
                    internal_id = int(candidate["id"])
                    self._upsert_alias(
                        con, "team", "github_wc2026", str(external_id),
                        internal_id, "auto_normalized", now_utc_iso,
                    )
                    return internal_id
        return None

    def _upsert_alias(self, con, entity_type, source_key, external_id,
                      internal_id, confirmed_by, confirmed_at_utc):
        con.execute(
            "INSERT INTO entity_alias_map(entity_type, source_key, external_id, "
            "internal_id, confirmed_by, confirmed_at_utc) VALUES(?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(entity_type, source_key, external_id) DO UPDATE SET "
            "internal_id=excluded.internal_id, "
            "confirmed_by=excluded.confirmed_by, "
            "confirmed_at_utc=excluded.confirmed_at_utc",
            (entity_type, source_key, external_id, internal_id,
             confirmed_by, confirmed_at_utc),
        )

    def list_pending_aliases(self, entity_type: str) -> list[dict]:
        query_map = {
            "team": (
                "SELECT CAST(gh.external_team_id AS TEXT) AS external_id, "
                "gh.team_name AS display_name "
                "FROM gh_teams gh LEFT JOIN entity_alias_map m "
                "ON m.entity_type='team' AND m.source_key='github_wc2026' "
                "AND m.external_id=CAST(gh.external_team_id AS TEXT) "
                "WHERE m.internal_id IS NULL "
                "GROUP BY gh.external_team_id, gh.team_name"
            ),
            "player": (
                "SELECT CAST(gh.external_player_id AS TEXT) AS external_id, "
                "gh.player_name AS display_name "
                "FROM gh_player_stats gh LEFT JOIN entity_alias_map m "
                "ON m.entity_type='player' AND m.source_key='github_wc2026' "
                "AND m.external_id=CAST(gh.external_player_id AS TEXT) "
                "WHERE m.internal_id IS NULL "
                "GROUP BY gh.external_player_id, gh.player_name"
            ),
        }
        query = query_map.get(entity_type)
        if query is None:
            return []
        with self.session() as con:
            return [dict(row) for row in con.execute(query).fetchall()]

    def confirm_alias(self, entity_type: str, source_key: str, external_id: str,
                      internal_id: int, actor: str, now_utc_iso: str) -> None:
        with self.session() as con:
            self._upsert_alias(
                con, entity_type, source_key, external_id, internal_id,
                f"user:{actor}", now_utc_iso,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_github_wc2026_alias -v`
Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wcpredict/repository.py tests/test_github_wc2026_alias.py
git commit -m "$(cat <<'EOF'
feat(ghwc): reconcile teams and manage entity aliases

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Importador central por provider_id

**Files:**
- Modify: `src/wcpredict/github_wc2026_dataset.py` (añadir importador central y constantes)
- Modify: `src/wcpredict/repository.py` (añadir `replace_gh_*` para 7 tablas)
- Create: `tests/test_github_wc2026_import.py`

**Interfaces:**
- Produces (en `github_wc2026_dataset.py`):
  - `GITHUB_DATASETS = {"github_wc2026_events": "match_events.csv", ...}` con las 7 rutas.
  - `PARSER_VERSION = "1"`.
  - `import_github_wc2026_download(repository, download, imported_at_utc)` — despacha por `download.provider_id` al parser correspondiente y llama al `replace_gh_*` del repositorio con `(provider_id, rows, version, imported_at_utc)`. Tras cada `replace_gh_*` de una tabla que contenga IDs externos de equipos/jugadores/árbitros/matches, invoca `repository.resolve_gh_foreign_keys(provider_id)` (definido en Task 6).
- Produces (en `Repository`):
  - Para cada tabla: `replace_gh_events`, `replace_gh_team_stats`, `replace_gh_lineups`, `replace_gh_matches`, `replace_gh_referees`, `replace_gh_player_stats`, `replace_gh_teams`.
  - Firma común: `replace_gh_X(self, provider_id: str, rows: list[dict], provider_version: str, imported_at_utc: datetime) -> None`. Comportamiento: dentro de una transacción, `DELETE FROM tabla WHERE provider_id=?` seguido de `INSERT` fila a fila. Todas las FK reconciliadas (`team_id`, `player_id`, etc.) se dejan a `NULL`; la resolución vive en Task 6.
  - Para `gh_match_events` la fila incluye además `period = derive_period(minute)` y `provider_version`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_github_wc2026_import.py`:

```python
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from wcpredict.daily_refresh import DatasetDownload
from wcpredict.github_wc2026_dataset import import_github_wc2026_download
from wcpredict.repository import Repository


def _fixture(name: str) -> bytes:
    return (Path(__file__).parent / "fixtures" / "github_wc2026" / name).read_bytes()


class GhwcImportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "app.sqlite")
        self.repo.initialize()
        with self.repo.session() as con:
            con.execute("INSERT INTO teams(id, name, fifa_code) VALUES(1, 'Mexico', 'MEX')")
            con.execute("INSERT INTO teams(id, name, fifa_code) VALUES(2, 'South Africa', 'RSA')")
        self.now = datetime(2026, 7, 3, 12, tzinfo=timezone.utc)

    def tearDown(self):
        self.directory.cleanup()

    def _download(self, provider_id: str, filename: str) -> DatasetDownload:
        return DatasetDownload(
            provider_id, "sha:abc1234/parser-1", _fixture(filename), self.now, 1
        )

    def test_import_events_populates_rows_with_period(self):
        import_github_wc2026_download(
            self.repo, self._download("github_wc2026_events", "match_events.csv"), self.now
        )
        with self.repo.session() as con:
            rows = list(con.execute(
                "SELECT external_event_id, minute, period, event_type FROM gh_match_events "
                "ORDER BY external_event_id"
            ))
        self.assertEqual(4, len(rows))
        self.assertEqual("first_half", rows[0]["period"])
        self.assertEqual("et_second", rows[3]["period"])

    def test_import_is_idempotent(self):
        dl = self._download("github_wc2026_events", "match_events.csv")
        import_github_wc2026_download(self.repo, dl, self.now)
        import_github_wc2026_download(self.repo, dl, self.now)
        with self.repo.session() as con:
            count = con.execute("SELECT COUNT(*) FROM gh_match_events").fetchone()[0]
        self.assertEqual(4, count)

    def test_import_matches(self):
        import_github_wc2026_download(
            self.repo, self._download("github_wc2026_matches", "matches_detailed.csv"), self.now
        )
        with self.repo.session() as con:
            rows = list(con.execute(
                "SELECT external_match_id, home_score, referee_name FROM gh_matches ORDER BY external_match_id"
            ))
        self.assertEqual(2, len(rows))
        self.assertEqual(2, rows[0]["home_score"])
        self.assertEqual("Szymon Marciniak", rows[0]["referee_name"])
        self.assertIsNone(rows[1]["home_score"])

    def test_import_all_seven_providers_end_to_end(self):
        provider_files = [
            ("github_wc2026_events", "match_events.csv"),
            ("github_wc2026_team_stats", "match_team_stats.csv"),
            ("github_wc2026_lineups", "match_lineups.csv"),
            ("github_wc2026_matches", "matches_detailed.csv"),
            ("github_wc2026_referees", "referees.csv"),
            ("github_wc2026_player_stats", "player_stats.csv"),
            ("github_wc2026_teams", "teams.csv"),
        ]
        for provider_id, filename in provider_files:
            import_github_wc2026_download(self.repo, self._download(provider_id, filename), self.now)
        with self.repo.session() as con:
            for table, expected in [
                ("gh_match_events", 4),
                ("gh_match_team_stats", 2),
                ("gh_match_lineups", 2),
                ("gh_matches", 2),
                ("gh_referees", 2),
                ("gh_player_stats", 2),
                ("gh_teams", 2),
            ]:
                self.assertEqual(
                    expected, con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
                    f"unexpected count in {table}",
                )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_github_wc2026_import -v`
Expected: FAIL — `import_github_wc2026_download` no existe.

- [ ] **Step 3: Implement `replace_gh_*` methods**

Añadir al final de la clase `Repository` en `src/wcpredict/repository.py`:

```python
    def _replace_gh_table(
        self, table: str, provider_id: str,
        columns: tuple[str, ...], rows: list[dict],
        provider_version: str | None, imported_at_utc,
        add_period: bool = False,
        include_provider_version_column: bool = False,
    ) -> None:
        from wcpredict.github_wc2026_dataset import derive_period
        with self.session() as con:
            con.execute(f"DELETE FROM {table} WHERE provider_id=?", (provider_id,))
            for row in rows:
                data = dict(row)
                if add_period and "minute" in data:
                    data["period"] = derive_period(int(data["minute"]))
                if include_provider_version_column:
                    data["provider_version"] = provider_version
                data["provider_id"] = provider_id
                data["imported_at_utc"] = imported_at_utc.isoformat()
                placeholders = ", ".join("?" for _ in columns)
                con.execute(
                    f"INSERT INTO {table}({', '.join(columns)}) VALUES({placeholders})",
                    tuple(data.get(col) for col in columns),
                )

    def replace_gh_events(self, provider_id, rows, provider_version, imported_at_utc):
        self._replace_gh_table(
            "gh_match_events", provider_id,
            ("provider_id", "external_event_id", "external_match_id", "match_id",
             "minute", "period", "event_type", "external_team_id", "team_id",
             "external_player_id", "player_id", "provider_version", "imported_at_utc"),
            rows, provider_version, imported_at_utc,
            add_period=True, include_provider_version_column=True,
        )

    def replace_gh_team_stats(self, provider_id, rows, provider_version, imported_at_utc):
        self._replace_gh_table(
            "gh_match_team_stats", provider_id,
            ("provider_id", "external_match_id", "match_id", "external_team_id", "team_id",
             "possession_pct", "total_shots", "shots_on_target", "corners", "fouls",
             "offsides", "saves", "player_of_the_match", "data_source", "last_updated",
             "imported_at_utc"),
            rows, provider_version, imported_at_utc,
        )

    def replace_gh_lineups(self, provider_id, rows, provider_version, imported_at_utc):
        self._replace_gh_table(
            "gh_match_lineups", provider_id,
            ("provider_id", "external_lineup_id", "external_match_id", "match_id",
             "external_player_id", "player_id", "external_team_id", "team_id",
             "is_starting_xi", "tactical_position", "minutes_played", "imported_at_utc"),
            rows, provider_version, imported_at_utc,
        )

    def replace_gh_matches(self, provider_id, rows, provider_version, imported_at_utc):
        self._replace_gh_table(
            "gh_matches", provider_id,
            ("provider_id", "external_match_id", "match_id", "date", "kickoff_time_utc",
             "stage_name", "stadium_name", "city", "country",
             "external_home_team_id", "home_team_id", "home_team_name", "home_fifa_code",
             "external_away_team_id", "away_team_id", "away_team_name", "away_fifa_code",
             "home_score", "away_score", "status", "home_xg", "away_xg",
             "home_goalkeeper", "away_goalkeeper", "player_of_the_match_name",
             "external_referee_id", "referee_id", "referee_name", "imported_at_utc"),
            rows, provider_version, imported_at_utc,
        )

    def replace_gh_referees(self, provider_id, rows, provider_version, imported_at_utc):
        self._replace_gh_table(
            "gh_referees", provider_id,
            ("provider_id", "external_referee_id", "referee_name", "country",
             "avg_cards_per_game", "imported_at_utc"),
            rows, provider_version, imported_at_utc,
        )

    def replace_gh_player_stats(self, provider_id, rows, provider_version, imported_at_utc):
        self._replace_gh_table(
            "gh_player_stats", provider_id,
            ("provider_id", "external_player_id", "player_id", "external_team_id", "team_id",
             "player_name", "position", "matches_played", "matches_started",
             "minutes_played", "goals", "assists", "shots", "shots_on_target",
             "yellow_cards", "red_cards", "penalty_goals", "own_goals", "clean_sheets",
             "saves", "goals_conceded", "average_rating", "data_source", "last_verified",
             "imported_at_utc"),
            rows, provider_version, imported_at_utc,
        )

    def replace_gh_teams(self, provider_id, rows, provider_version, imported_at_utc):
        self._replace_gh_table(
            "gh_teams", provider_id,
            ("provider_id", "external_team_id", "team_id", "team_name", "fifa_code",
             "group_letter", "confederation", "fifa_ranking_pre_tournament",
             "elo_rating", "manager_name", "imported_at_utc"),
            rows, provider_version, imported_at_utc,
        )
```

- [ ] **Step 4: Implement `import_github_wc2026_download`**

Añadir a `src/wcpredict/github_wc2026_dataset.py`:

```python
GITHUB_DATASETS = {
    "github_wc2026_events": "match_events.csv",
    "github_wc2026_team_stats": "match_team_stats.csv",
    "github_wc2026_lineups": "match_lineups.csv",
    "github_wc2026_matches": "matches_detailed.csv",
    "github_wc2026_referees": "referees.csv",
    "github_wc2026_player_stats": "player_stats.csv",
    "github_wc2026_teams": "teams.csv",
}

PARSER_VERSION = "1"


def import_github_wc2026_download(repository, download, imported_at_utc) -> None:
    text = download.content.decode("utf-8-sig")
    provider_id = download.provider_id
    version = download.version
    if provider_id == "github_wc2026_events":
        repository.replace_gh_events(provider_id, parse_events_rows(text), version, imported_at_utc)
    elif provider_id == "github_wc2026_team_stats":
        repository.replace_gh_team_stats(provider_id, parse_team_stats_rows(text), version, imported_at_utc)
    elif provider_id == "github_wc2026_lineups":
        repository.replace_gh_lineups(provider_id, parse_lineups_rows(text), version, imported_at_utc)
    elif provider_id == "github_wc2026_matches":
        repository.replace_gh_matches(provider_id, parse_matches_rows(text), version, imported_at_utc)
    elif provider_id == "github_wc2026_referees":
        repository.replace_gh_referees(provider_id, parse_referees_rows(text), version, imported_at_utc)
    elif provider_id == "github_wc2026_player_stats":
        repository.replace_gh_player_stats(provider_id, parse_player_stats_rows(text), version, imported_at_utc)
    elif provider_id == "github_wc2026_teams":
        repository.replace_gh_teams(provider_id, parse_teams_rows(text), version, imported_at_utc)
    else:
        raise ValueError(f"unsupported github_wc2026 provider: {provider_id}")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m unittest tests.test_github_wc2026_import -v`
Expected: 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wcpredict/github_wc2026_dataset.py src/wcpredict/repository.py tests/test_github_wc2026_import.py
git commit -m "$(cat <<'EOF'
feat(ghwc): import parsed rows into gh_* tables

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Resolución de FKs tras importar

**Files:**
- Modify: `src/wcpredict/repository.py` (nuevos métodos `resolve_gh_foreign_keys` y helpers)
- Modify: `src/wcpredict/github_wc2026_dataset.py` (llamar a `resolve_gh_foreign_keys` desde el importador central)
- Modify: `tests/test_github_wc2026_import.py` (añadir asserts sobre FKs resueltas)

**Interfaces:**
- Produces (en `Repository`):
  - `resolve_gh_foreign_keys(provider_id: str, now_utc_iso: str) -> None` — pasa por las tablas `gh_*` afectadas y rellena `team_id`/`player_id`/`match_id` usando `entity_alias_map` y las siguientes reglas:
    - Para `gh_teams` y `gh_matches` (home/away): `team_id` desde alias de `team` con `external_team_id`. Si no hay alias, intenta `reconcile_gh_team` con nombre y `fifa_code` del propio dataset (`gh_teams` filas ya importadas).
    - Para `gh_player_stats`, `gh_match_lineups`, `gh_match_events`: `team_id` vía alias de team con `external_team_id`; `player_id` vía join `(team_id resuelto, casefold(player_name))` contra `players`. Si el join produce match, upserta alias de tipo `player`. Si no, deja `NULL`.
    - Para `gh_match_events` y `gh_match_team_stats` y `gh_match_lineups`: `match_id` intentando `SELECT id FROM matches WHERE competition='FIFA World Cup 2026' AND team_a_id=? AND team_b_id=?` (o pareja invertida) con equipos ya resueltos. Si no hay match único, `NULL`.
  - Llamado sin argumentos, resuelve para todos los `provider_id` github (`WHERE provider_id LIKE 'github_wc2026_%'`).

- [ ] **Step 1: Add FK-resolution asserts to import test**

Añadir al final de `tests/test_github_wc2026_import.py`:

```python
    def test_team_id_resolved_via_fifa_code(self):
        import_github_wc2026_download(
            self.repo, self._download("github_wc2026_teams", "teams.csv"), self.now
        )
        self.repo.resolve_gh_foreign_keys("github_wc2026_teams", self.now.isoformat())
        with self.repo.session() as con:
            row = con.execute(
                "SELECT team_id FROM gh_teams WHERE external_team_id=1"
            ).fetchone()
        self.assertEqual(1, row["team_id"])

    def test_events_team_id_resolved_after_teams_imported(self):
        import_github_wc2026_download(
            self.repo, self._download("github_wc2026_teams", "teams.csv"), self.now
        )
        self.repo.resolve_gh_foreign_keys("github_wc2026_teams", self.now.isoformat())
        import_github_wc2026_download(
            self.repo, self._download("github_wc2026_events", "match_events.csv"), self.now
        )
        self.repo.resolve_gh_foreign_keys("github_wc2026_events", self.now.isoformat())
        with self.repo.session() as con:
            rows = list(con.execute(
                "SELECT external_team_id, team_id FROM gh_match_events ORDER BY external_event_id"
            ))
        self.assertEqual(1, rows[0]["team_id"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_github_wc2026_import -v`
Expected: FAIL — `resolve_gh_foreign_keys` no existe.

- [ ] **Step 3: Implement `resolve_gh_foreign_keys`**

Añadir a la clase `Repository` en `src/wcpredict/repository.py`:

```python
    def _apply_alias_teams(self, con, table: str, external_col: str, internal_col: str) -> None:
        con.execute(
            f"UPDATE {table} SET {internal_col} = ("
            "  SELECT internal_id FROM entity_alias_map "
            "  WHERE entity_type='team' AND source_key='github_wc2026' "
            f"  AND external_id=CAST({table}.{external_col} AS TEXT)"
            f") WHERE {internal_col} IS NULL AND {external_col} IS NOT NULL"
        )

    def resolve_gh_foreign_keys(self, provider_id: str | None = None, now_utc_iso: str | None = None) -> None:
        from datetime import datetime, timezone
        now = now_utc_iso or datetime.now(timezone.utc).isoformat()
        with self.session() as con:
            # 1. Auto-reconcile teams present in gh_teams that lack alias yet.
            teams_needing = con.execute(
                "SELECT external_team_id, team_name, fifa_code FROM gh_teams gh "
                "LEFT JOIN entity_alias_map m "
                "ON m.entity_type='team' AND m.source_key='github_wc2026' "
                "AND m.external_id=CAST(gh.external_team_id AS TEXT) "
                "WHERE m.internal_id IS NULL"
            ).fetchall()
        for row in teams_needing:
            self.reconcile_gh_team(
                int(row["external_team_id"]), str(row["team_name"]),
                (str(row["fifa_code"]) if row["fifa_code"] is not None else None),
                now,
            )
        with self.session() as con:
            self._apply_alias_teams(con, "gh_teams", "external_team_id", "team_id")
            self._apply_alias_teams(con, "gh_match_team_stats", "external_team_id", "team_id")
            self._apply_alias_teams(con, "gh_match_lineups", "external_team_id", "team_id")
            self._apply_alias_teams(con, "gh_match_events", "external_team_id", "team_id")
            self._apply_alias_teams(con, "gh_player_stats", "external_team_id", "team_id")
            self._apply_alias_teams(con, "gh_matches", "external_home_team_id", "home_team_id")
            self._apply_alias_teams(con, "gh_matches", "external_away_team_id", "away_team_id")

            # 2. Resolve player_id: match by (team_id, casefold(name)) via players table.
            con.execute(
                "UPDATE gh_player_stats SET player_id = ("
                "  SELECT p.id FROM players p "
                "  WHERE p.team_id = gh_player_stats.team_id "
                "  AND lower(p.name) = lower(gh_player_stats.player_name) "
                "  LIMIT 1"
                ") WHERE player_id IS NULL AND team_id IS NOT NULL"
            )
            # Persist resolved player aliases for lineups/events reuse.
            con.execute(
                "INSERT OR IGNORE INTO entity_alias_map(entity_type, source_key, external_id, "
                "internal_id, confirmed_by, confirmed_at_utc) "
                "SELECT 'player', 'github_wc2026', CAST(external_player_id AS TEXT), "
                "player_id, 'auto_normalized', ? FROM gh_player_stats "
                "WHERE player_id IS NOT NULL",
                (now,),
            )
            # Apply aliases to lineups and events.
            for table in ("gh_match_lineups", "gh_match_events"):
                con.execute(
                    f"UPDATE {table} SET player_id = ("
                    "  SELECT internal_id FROM entity_alias_map "
                    "  WHERE entity_type='player' AND source_key='github_wc2026' "
                    f"  AND external_id=CAST({table}.external_player_id AS TEXT)"
                    f") WHERE player_id IS NULL AND external_player_id IS NOT NULL"
                )

            # 3. Resolve match_id in gh_matches / gh_match_events / gh_match_team_stats /
            # gh_match_lineups by pair of resolved team ids on FIFA World Cup 2026.
            con.execute(
                "UPDATE gh_matches SET match_id = ("
                "  SELECT m.id FROM matches m "
                "  WHERE m.competition = 'FIFA World Cup 2026' "
                "  AND ((m.team_a_id = gh_matches.home_team_id "
                "        AND m.team_b_id = gh_matches.away_team_id) "
                "    OR (m.team_a_id = gh_matches.away_team_id "
                "        AND m.team_b_id = gh_matches.home_team_id)) "
                "  LIMIT 1"
                ") WHERE match_id IS NULL "
                "AND home_team_id IS NOT NULL AND away_team_id IS NOT NULL"
            )
            for table in ("gh_match_events", "gh_match_team_stats", "gh_match_lineups"):
                con.execute(
                    f"UPDATE {table} SET match_id = ("
                    "  SELECT match_id FROM gh_matches "
                    f"  WHERE gh_matches.external_match_id = {table}.external_match_id"
                    f") WHERE match_id IS NULL"
                )
```

- [ ] **Step 4: Call resolver from importer**

En `src/wcpredict/github_wc2026_dataset.py`, al final de `import_github_wc2026_download` (después del `elif`/`else`), añadir:

```python
    repository.resolve_gh_foreign_keys(provider_id)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m unittest tests.test_github_wc2026_import tests.test_github_wc2026_alias -v`
Expected: todos PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wcpredict/repository.py src/wcpredict/github_wc2026_dataset.py tests/test_github_wc2026_import.py
git commit -m "$(cat <<'EOF'
feat(ghwc): resolve foreign keys after each import

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Verificación cruzada de marcador

**Files:**
- Modify: `src/wcpredict/repository.py` (nuevos métodos `record_score_verification`, `list_score_mismatches`)
- Modify: `src/wcpredict/github_wc2026_dataset.py` (invocar `record_score_verification` desde el flujo de `gh_matches`)
- Create: `tests/test_github_wc2026_score_verification.py`

**Interfaces:**
- Produces (en `Repository`):
  - `record_score_verifications_for_provider(provider_id: str, now_utc_iso: str) -> list[dict]` — recorre `gh_matches` con `match_id NOT NULL` y `home_score NOT NULL`; para cada uno consulta `match_results` por `match_id` y compara marcador respetando orden `team_a`/`team_b` de `matches`. Upserta en `gh_score_verifications` con `status` en `{"match", "mismatch", "no_local_result"}`. Devuelve las filas mismatch nuevas para conveniencia UI.
  - `list_score_mismatches(only_unresolved: bool = True) -> list[dict]` — retorna las filas de `gh_score_verifications` con `status='mismatch'` y (si `only_unresolved`) `resolution IS NULL`, joined con `gh_matches` para exponer nombres visibles.

- [ ] **Step 1: Write the failing test**

Create `tests/test_github_wc2026_score_verification.py`:

```python
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from wcpredict.daily_refresh import DatasetDownload
from wcpredict.github_wc2026_dataset import import_github_wc2026_download
from wcpredict.repository import Repository


def _matches_csv_with(rows: list[tuple[int, str, str, str, str, int | None, int | None, str]]) -> bytes:
    header = (
        "match_id,date,kickoff_time_utc,stage_name,stadium_name,city,country,"
        "home_team_name,home_fifa_code,away_team_name,away_fifa_code,"
        "home_score,away_score,status,home_xg,away_xg,home_goalkeeper,away_goalkeeper,"
        "player_of_the_match_name,referee_name\n"
    )
    body = []
    for match_id, home, home_code, away, away_code, hs, aws, status in rows:
        hs_s = "" if hs is None else str(hs)
        aws_s = "" if aws is None else str(aws)
        body.append(
            f"{match_id},2026-06-11,2026-06-11T20:00:00Z,Group Stage,Stadium,City,USA,"
            f"{home},{home_code},{away},{away_code},{hs_s},{aws_s},{status},,,,,,,\n"
        )
    return (header + "".join(body)).encode("utf-8")


class ScoreVerificationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "app.sqlite")
        self.repo.initialize()
        self.now = datetime(2026, 7, 3, 12, tzinfo=timezone.utc)
        with self.repo.session() as con:
            con.execute("INSERT INTO teams(id, name, fifa_code) VALUES(1, 'Mexico', 'MEX')")
            con.execute("INSERT INTO teams(id, name, fifa_code) VALUES(2, 'South Africa', 'RSA')")
            con.execute(
                "INSERT INTO matches(id, competition, stage, kickoff_utc, team_a_id, "
                "team_b_id, status, venue, neutral_site) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (100, "FIFA World Cup 2026", "Group Stage", "2026-06-11T20:00:00+00:00",
                 1, 2, "finished", "Estadio Azteca", 1),
            )
            con.execute(
                "INSERT INTO match_results(match_id, goals_a, goals_b, source_type, "
                "recorded_at_utc) VALUES(?, ?, ?, ?, ?)",
                (100, 2, 0, "verified_user_capture", self.now.isoformat()),
            )

    def tearDown(self):
        self.directory.cleanup()

    def _import(self, content: bytes) -> None:
        import_github_wc2026_download(
            self.repo,
            DatasetDownload("github_wc2026_matches", "sha:abc/parser-1", content, self.now, 1),
            self.now,
        )

    def test_match_when_dataset_agrees(self):
        self._import(_matches_csv_with([(1, "Mexico", "MEX", "South Africa", "RSA", 2, 0, "Completed")]))
        self.repo.record_score_verifications_for_provider(
            "github_wc2026_matches", self.now.isoformat()
        )
        with self.repo.session() as con:
            row = con.execute("SELECT status FROM gh_score_verifications").fetchone()
        self.assertEqual("match", row["status"])
        self.assertEqual([], self.repo.list_score_mismatches())

    def test_mismatch_when_dataset_disagrees(self):
        self._import(_matches_csv_with([(1, "Mexico", "MEX", "South Africa", "RSA", 3, 1, "Completed")]))
        self.repo.record_score_verifications_for_provider(
            "github_wc2026_matches", self.now.isoformat()
        )
        mismatches = self.repo.list_score_mismatches()
        self.assertEqual(1, len(mismatches))
        self.assertEqual(3, mismatches[0]["dataset_home_score"])
        self.assertEqual(2, mismatches[0]["local_home_score"])

    def test_no_local_result_status(self):
        with self.repo.session() as con:
            con.execute("DELETE FROM match_results")
        self._import(_matches_csv_with([(1, "Mexico", "MEX", "South Africa", "RSA", 2, 0, "Completed")]))
        self.repo.record_score_verifications_for_provider(
            "github_wc2026_matches", self.now.isoformat()
        )
        with self.repo.session() as con:
            row = con.execute("SELECT status FROM gh_score_verifications").fetchone()
        self.assertEqual("no_local_result", row["status"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_github_wc2026_score_verification -v`
Expected: FAIL.

- [ ] **Step 3: Implement `record_score_verifications_for_provider` + `list_score_mismatches`**

Añadir a la clase `Repository`:

```python
    def record_score_verifications_for_provider(
        self, provider_id: str, now_utc_iso: str
    ) -> list[dict]:
        new_mismatches: list[dict] = []
        with self.session() as con:
            candidates = con.execute(
                "SELECT gh.external_match_id, gh.match_id, gh.provider_id, "
                "gh.home_team_id, gh.away_team_id, gh.home_score, gh.away_score, "
                "m.team_a_id, m.team_b_id, mr.goals_a, mr.goals_b, "
                "COALESCE(ds.provider_version, ?) AS provider_version "
                "FROM gh_matches gh "
                "LEFT JOIN matches m ON m.id = gh.match_id "
                "LEFT JOIN match_results mr ON mr.match_id = gh.match_id "
                "LEFT JOIN dataset_snapshots ds ON ds.provider_id = gh.provider_id "
                "WHERE gh.provider_id = ? AND gh.match_id IS NOT NULL "
                "AND gh.home_score IS NOT NULL AND gh.away_score IS NOT NULL",
                ("unknown", provider_id),
            ).fetchall()
            for row in candidates:
                # Orient dataset scores to matches.team_a / team_b ordering.
                if row["home_team_id"] == row["team_a_id"]:
                    ds_a, ds_b = row["home_score"], row["away_score"]
                else:
                    ds_a, ds_b = row["away_score"], row["home_score"]
                if row["goals_a"] is None or row["goals_b"] is None:
                    status = "no_local_result"
                    local_a = local_b = None
                elif int(row["goals_a"]) == int(ds_a) and int(row["goals_b"]) == int(ds_b):
                    status = "match"
                    local_a, local_b = int(row["goals_a"]), int(row["goals_b"])
                else:
                    status = "mismatch"
                    local_a, local_b = int(row["goals_a"]), int(row["goals_b"])
                con.execute(
                    "INSERT INTO gh_score_verifications("
                    "match_id, provider_version, dataset_home_score, dataset_away_score, "
                    "local_home_score, local_away_score, status, detected_at_utc) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(match_id, provider_version) DO UPDATE SET "
                    "dataset_home_score=excluded.dataset_home_score, "
                    "dataset_away_score=excluded.dataset_away_score, "
                    "local_home_score=excluded.local_home_score, "
                    "local_away_score=excluded.local_away_score, "
                    "status=excluded.status, detected_at_utc=excluded.detected_at_utc",
                    (row["match_id"], row["provider_version"], ds_a, ds_b,
                     local_a, local_b, status, now_utc_iso),
                )
                if status == "mismatch":
                    new_mismatches.append({
                        "match_id": row["match_id"],
                        "dataset_home_score": ds_a,
                        "dataset_away_score": ds_b,
                        "local_home_score": local_a,
                        "local_away_score": local_b,
                    })
        return new_mismatches

    def list_score_mismatches(self, only_unresolved: bool = True) -> list[dict]:
        query = (
            "SELECT v.match_id, v.provider_version, v.dataset_home_score, "
            "v.dataset_away_score, v.local_home_score, v.local_away_score, "
            "v.detected_at_utc, gh.home_team_name, gh.away_team_name, gh.date "
            "FROM gh_score_verifications v "
            "LEFT JOIN gh_matches gh ON gh.match_id = v.match_id "
            "WHERE v.status = 'mismatch'"
        )
        if only_unresolved:
            query += " AND v.resolution IS NULL"
        query += " ORDER BY v.detected_at_utc DESC"
        with self.session() as con:
            return [dict(row) for row in con.execute(query).fetchall()]
```

- [ ] **Step 4: Invoke verification from importer**

En `src/wcpredict/github_wc2026_dataset.py`, al final de `import_github_wc2026_download`, añadir después de `repository.resolve_gh_foreign_keys(provider_id)`:

```python
    if provider_id == "github_wc2026_matches":
        repository.record_score_verifications_for_provider(provider_id, imported_at_utc.isoformat())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m unittest tests.test_github_wc2026_score_verification -v`
Expected: 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wcpredict/repository.py src/wcpredict/github_wc2026_dataset.py tests/test_github_wc2026_score_verification.py
git commit -m "$(cat <<'EOF'
feat(ghwc): cross-check dataset scores against verified captures

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Fetch HTTP + integración en daily_refresh

**Files:**
- Modify: `src/wcpredict/github_wc2026_dataset.py` (añadir `fetch_github_wc2026_dataset`)
- Modify: `src/wcpredict/daily_refresh.py` (ampliar `DEFAULT_PROVIDERS`)
- Modify: `src/wcpredict/services.py` (fetcher factoría por prefijo con SHA compartido)
- Modify: `tests/test_daily_refresh.py` (escenario mixto con `github_*`)

**Interfaces:**
- Produces (en `github_wc2026_dataset.py`):
  - `RAW_URL_TEMPLATE = "https://raw.githubusercontent.com/mominullptr/FIFA-World-Cup-2026-Dataset/main/{filename}"`.
  - `resolve_latest_commit() -> tuple[str, datetime]` — GET `https://api.github.com/repos/mominullptr/FIFA-World-Cup-2026-Dataset/commits/main`; retorna `(sha_short7, commit_date_utc)`. Timeout 15s. En error HTTP, raise.
  - `fetch_github_wc2026_dataset(provider_id: str, commit_sha: str | None = None, commit_date: "datetime | None" = None) -> DatasetDownload` — si `commit_sha` es None, llama `resolve_latest_commit` internamente. Descarga el CSV (`timeout=30`). `version` = `f"sha:{commit_sha}/parser-{PARSER_VERSION}"`. `updated_at` = `commit_date`. `row_count` = `content.count(b"\n") - 1`.
- Produces (en `services.py`):
  - Función `_build_daily_fetcher()` (o método interno del servicio existente) que en cada llamada resuelve el commit UNA vez (lazy, primer `github_*` que llegue) y despacha:
    - Prefijo `swaptr_*`, `martj42_*` → `fetch_kaggle_world_cup_dataset`.
    - Prefijo `github_wc2026_*` → `fetch_github_wc2026_dataset(provider_id, commit_sha, commit_date)`.

- [ ] **Step 1: Add fetch functions to github_wc2026_dataset.py**

Añadir al final de `src/wcpredict/github_wc2026_dataset.py`:

```python
RAW_URL_TEMPLATE = "https://raw.githubusercontent.com/mominullptr/FIFA-World-Cup-2026-Dataset/main/{filename}"
COMMITS_API_URL = "https://api.github.com/repos/mominullptr/FIFA-World-Cup-2026-Dataset/commits/main"


def resolve_latest_commit():
    import requests
    from datetime import datetime
    response = requests.get(COMMITS_API_URL, timeout=15)
    response.raise_for_status()
    payload = response.json()
    sha = str(payload["sha"])[:7]
    committed = payload["commit"]["committer"]["date"]
    committed_at = datetime.fromisoformat(str(committed).replace("Z", "+00:00"))
    return sha, committed_at


def fetch_github_wc2026_dataset(provider_id, commit_sha=None, commit_date=None):
    import requests
    from wcpredict.daily_refresh import DatasetDownload
    if provider_id not in GITHUB_DATASETS:
        raise ValueError(f"unsupported github_wc2026 provider: {provider_id}")
    if commit_sha is None or commit_date is None:
        commit_sha, commit_date = resolve_latest_commit()
    url = RAW_URL_TEMPLATE.format(filename=GITHUB_DATASETS[provider_id])
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    content = response.content
    return DatasetDownload(
        provider_id, f"sha:{commit_sha}/parser-{PARSER_VERSION}", content,
        commit_date, max(0, content.count(b"\n") - 1),
    )
```

- [ ] **Step 2: Extend DEFAULT_PROVIDERS**

En `src/wcpredict/daily_refresh.py`, reemplazar el bloque:

```python
DEFAULT_PROVIDERS = (
    "swaptr_wc2026_matches",
    "swaptr_wc2026_teams",
    "swaptr_wc2026_players",
)
```

por:

```python
DEFAULT_PROVIDERS = (
    "swaptr_wc2026_matches",
    "swaptr_wc2026_teams",
    "swaptr_wc2026_players",
    "github_wc2026_teams",
    "github_wc2026_referees",
    "github_wc2026_matches",
    "github_wc2026_team_stats",
    "github_wc2026_lineups",
    "github_wc2026_events",
    "github_wc2026_player_stats",
)
```

Orden razonado: `teams` y `referees` primero (habilitan reconciliación de FKs de los que llegan detrás), luego `matches` (crea los external_match_id), luego el resto.

- [ ] **Step 3: Add fetcher factory to services.py**

Localizar en `src/wcpredict/services.py` la función/método que hoy construye el `fetcher` que se pasa a `ensure_current_world_cup_data`. Envolverlo con un cierre que resuelva el commit una sola vez:

```python
def _build_daily_fetcher():
    from wcpredict.world_cup_data import fetch_kaggle_world_cup_dataset
    from wcpredict.github_wc2026_dataset import fetch_github_wc2026_dataset, resolve_latest_commit
    cached = {"sha": None, "date": None}

    def fetcher(provider_id: str):
        if provider_id.startswith("github_wc2026_"):
            if cached["sha"] is None:
                cached["sha"], cached["date"] = resolve_latest_commit()
            return fetch_github_wc2026_dataset(provider_id, cached["sha"], cached["date"])
        return fetch_kaggle_world_cup_dataset(provider_id)

    return fetcher
```

Reemplazar la asignación existente del fetcher por `fetcher = _build_daily_fetcher()` en el punto donde se llama a `ensure_current_world_cup_data`. Análogamente, envolver el `importer` para despachar por prefijo:

```python
def _build_daily_importer(repository, now):
    from wcpredict.world_cup_data import import_world_cup_download
    from wcpredict.github_wc2026_dataset import import_github_wc2026_download

    def importer(download):
        if download.provider_id.startswith("github_wc2026_"):
            import_github_wc2026_download(repository, download, now)
        else:
            import_world_cup_download(repository, download, now)
    return importer
```

Si `services.py` no expone hoy funciones así explícitas, seguir el patrón exacto de la llamada existente y encapsular in-place.

- [ ] **Step 4: Extend daily_refresh test with a github_* scenario**

Añadir al final de `tests/test_daily_refresh.py`:

```python
    def test_mixed_scenario_with_github_provider_partial(self):
        providers = ("swaptr_wc2026_matches", "github_wc2026_events")

        def fetcher(provider):
            if provider == "swaptr_wc2026_matches":
                return DatasetDownload(provider, "v1", b"payload", self.now, 1)
            raise RuntimeError("upstream 500")

        result = ensure_current_world_cup_data(
            self.repo,
            fetcher,
            importer=lambda dl: None,
            now=self.now,
            providers=providers,
        )
        self.assertEqual("partial", result.status)
        self.assertEqual(("swaptr_wc2026_matches",), result.updated)
        self.assertEqual(("github_wc2026_events",), result.failed)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m unittest tests.test_daily_refresh tests.test_github_wc2026_import -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wcpredict/github_wc2026_dataset.py src/wcpredict/daily_refresh.py src/wcpredict/services.py tests/test_daily_refresh.py
git commit -m "$(cat <<'EOF'
feat(ghwc): wire github_wc2026 providers into daily_refresh

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: UI — expander de dataset externo y contrato

**Files:**
- Modify: `src/wcpredict/ui/pages.py` (nuevo expander bajo el badge de "Datos diarios")
- Modify: `tests/test_app_contract.py` (contrato del expander)

**Interfaces:**
- Consumes: `Repository.list_score_mismatches(only_unresolved=True)` y `Repository.list_pending_aliases(entity_type)`.
- Produces: expander con `key="daily_dataset_external"`, título `"Dataset externo (mominullptr)"`, contenido:
  - Contador `"Discrepancias de marcador pendientes: N"`.
  - Contador `"Alias de equipos pendientes: N"`.
  - Contador `"Alias de jugadores pendientes: N"`.
  - Botón `"Revisar…"` que expone la subpágina de resolución (mínima: `st.dataframe` con listado + `st.selectbox` para elegir `internal_id` + `st.button("Confirmar")` que llama `repo.confirm_alias`).

- [ ] **Step 1: Write the failing contract test**

Añadir al final de `tests/test_app_contract.py`:

```python
    def test_external_dataset_expander_present(self):
        contract = self.render_contract()
        self.assertIn("daily_dataset_external", contract["expanders"])
        expander = contract["expanders"]["daily_dataset_external"]
        self.assertIn("Discrepancias de marcador pendientes", expander["text"])
        self.assertIn("Alias de equipos pendientes", expander["text"])
        self.assertIn("Alias de jugadores pendientes", expander["text"])
```

(Ajustar el nombre real de `render_contract` / helpers para casar con lo que ya use el fichero; leer sus tests existentes primero.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_app_contract -v`
Expected: FAIL — el expander no existe.

- [ ] **Step 3: Add expander to pages.py**

Localizar en `src/wcpredict/ui/pages.py` el bloque que renderiza el badge "Datos diarios" y el expander de errores por proveedor. Justo debajo añadir:

```python
    with st.expander("Dataset externo (mominullptr)", expanded=False):
        mismatches = repository.list_score_mismatches(only_unresolved=True)
        pending_teams = repository.list_pending_aliases("team")
        pending_players = repository.list_pending_aliases("player")
        st.write(f"Discrepancias de marcador pendientes: {len(mismatches)}")
        st.write(f"Alias de equipos pendientes: {len(pending_teams)}")
        st.write(f"Alias de jugadores pendientes: {len(pending_players)}")
        if mismatches:
            st.dataframe(mismatches, use_container_width=True)
        if pending_teams:
            st.markdown("**Equipos por asignar**")
            for row in pending_teams:
                col1, col2, col3 = st.columns([2, 2, 1])
                col1.write(f"{row['display_name']} (ext {row['external_id']})")
                internal_id = col2.number_input(
                    "team_id",
                    key=f"gh_team_alias_{row['external_id']}",
                    min_value=1, step=1, value=1,
                )
                if col3.button("Confirmar", key=f"gh_team_confirm_{row['external_id']}"):
                    from datetime import datetime, timezone
                    repository.confirm_alias(
                        "team", "github_wc2026", str(row["external_id"]),
                        int(internal_id), "ui",
                        datetime.now(timezone.utc).isoformat(),
                    )
                    st.rerun()
```

Asegurar que el key literal `"daily_dataset_external"` se registre en el helper de contrato existente (si `test_app_contract.py` inspecciona por key, el expander debe crearse con `key="daily_dataset_external"`; adaptar la línea `with st.expander(...)` a `with st.expander("Dataset externo (mominullptr)", expanded=False, key="daily_dataset_external"):` — Streamlit soporta `key` desde 1.28).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_app_contract -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wcpredict/ui/pages.py tests/test_app_contract.py
git commit -m "$(cat <<'EOF'
feat(ghwc): expose external dataset review expander in daily UI

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Script de backfill idempotente

**Files:**
- Create: `scripts/backfill_github_wc2026.py`
- Create: `tests/test_backfill_github_wc2026.py`

**Interfaces:**
- Consumes: `fetch_github_wc2026_dataset`, `import_github_wc2026_download`, `Repository`.
- Produces: función `run_backfill(repository, fetcher=None, now=None) -> dict` que descarga los 7 providers, los importa en el orden `DEFAULT_PROVIDERS` para github_*, ejecuta `resolve_gh_foreign_keys()` global sin `provider_id` al final, y retorna un dict con `{"imported": [...], "mismatches": N, "pending_team_aliases": N, "pending_player_aliases": N}`.
- CLI: `python scripts/backfill_github_wc2026.py` usa `Repository(Path("data/worldcup.sqlite"))` por defecto.

- [ ] **Step 1: Write the failing test**

Create `tests/test_backfill_github_wc2026.py`:

```python
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from wcpredict.daily_refresh import DatasetDownload
from wcpredict.repository import Repository

from scripts.backfill_github_wc2026 import run_backfill


def _fx(name: str) -> bytes:
    return (Path(__file__).parent / "fixtures" / "github_wc2026" / name).read_bytes()


FIXTURES = {
    "github_wc2026_events": "match_events.csv",
    "github_wc2026_team_stats": "match_team_stats.csv",
    "github_wc2026_lineups": "match_lineups.csv",
    "github_wc2026_matches": "matches_detailed.csv",
    "github_wc2026_referees": "referees.csv",
    "github_wc2026_player_stats": "player_stats.csv",
    "github_wc2026_teams": "teams.csv",
}


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "app.sqlite")
        self.repo.initialize()
        self.now = datetime(2026, 7, 3, 12, tzinfo=timezone.utc)

    def tearDown(self):
        self.directory.cleanup()

    def test_backfill_imports_all_providers_once(self):
        def fetcher(provider_id):
            return DatasetDownload(
                provider_id, "sha:local/parser-1", _fx(FIXTURES[provider_id]), self.now, 1
            )
        report = run_backfill(self.repo, fetcher=fetcher, now=self.now)
        self.assertEqual(7, len(report["imported"]))
        with self.repo.session() as con:
            events = con.execute("SELECT COUNT(*) FROM gh_match_events").fetchone()[0]
        self.assertEqual(4, events)

    def test_backfill_is_idempotent(self):
        def fetcher(provider_id):
            return DatasetDownload(
                provider_id, "sha:local/parser-1", _fx(FIXTURES[provider_id]), self.now, 1
            )
        run_backfill(self.repo, fetcher=fetcher, now=self.now)
        run_backfill(self.repo, fetcher=fetcher, now=self.now)
        with self.repo.session() as con:
            events = con.execute("SELECT COUNT(*) FROM gh_match_events").fetchone()[0]
        self.assertEqual(4, events)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_backfill_github_wc2026 -v`
Expected: FAIL — script no existe.

- [ ] **Step 3: Implement the backfill script**

Create `scripts/backfill_github_wc2026.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

from wcpredict.github_wc2026_dataset import (
    GITHUB_DATASETS,
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
    repo = Repository(Path("data/worldcup.sqlite"))
    repo.initialize()
    report = run_backfill(repo)
    print(report)
    sys.exit(0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_backfill_github_wc2026 -v`
Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/backfill_github_wc2026.py tests/test_backfill_github_wc2026.py
git commit -m "$(cat <<'EOF'
feat(ghwc): add idempotent backfill script for github_wc2026

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Suite completa + documentación

**Files:**
- Modify: `README.md` (nueva sección)
- Run: suite completa

**Interfaces:**
- Consumes: todo lo anterior.
- Produces: sección README explicando qué se automatiza y qué sigue siendo manual, más confirmación de suite verde.

- [ ] **Step 1: Add README section**

Añadir al `README.md`, tras la sección de "Actualización diaria" existente:

```markdown
## Fuente externa: mominullptr/FIFA-World-Cup-2026-Dataset

Además de los feeds de swaptr (Kaggle) y martj42 se ingiere diariamente el
dataset público
[`mominullptr/FIFA-World-Cup-2026-Dataset`](https://github.com/mominullptr/FIFA-World-Cup-2026-Dataset)
(CC0). Aporta:

- Timeline de eventos al minuto (goles, tarjetas, VAR).
- Árbitro asignado por partido y su tendencia histórica de tarjetas.
- Estadísticas de equipo por partido con faltas, offsides, paradas y POTM.
- Stats de jugador acumuladas del torneo (rating medio, penaltis, clean sheets).
- ELO, ranking FIFA pre-torneo y seleccionador.

Estas fuentes **no** sustituyen los cierres oficiales (`verified_user_capture`),
las tandas de penaltis ni la carga por periodos. Su marcador se compara
automáticamente con el nuestro:

- Coincidencia: se registra como verificado.
- Discrepancia: aparece bajo "Datos diarios → Dataset externo (mominullptr)"
  para revisión manual (nunca se autocorrige).
- Sin marcador local: queda disponible como candidato preconfirmado.

Backfill puntual: `python scripts/backfill_github_wc2026.py`.
```

- [ ] **Step 2: Run the full test suite**

Run: `python -m unittest -v`
Expected: TODOS los tests PASS.

- [ ] **Step 3: Verify SQLite integrity**

Run:

```powershell
python -c "import sqlite3; con = sqlite3.connect('data/worldcup.sqlite'); print(con.execute('PRAGMA integrity_check').fetchone())"
```

Expected: `('ok',)`.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs(ghwc): document external dataset ingestion

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Notas finales para el implementador

- Trabaja tarea a tarea. No mezcles cambios de varias tareas en un mismo commit.
- Si un test que ya pasaba antes deja de pasar tras tu cambio, para y diagnostica antes de continuar; no encadenes tareas con regresión.
- `Repository.reconcile_gh_team` abre su propia sesión — no lo llames desde dentro de una sesión abierta o tendrás lock. Si necesitas encadenar reconciliación con más SQL, cierra la sesión anterior primero.
- Los nombres de teams en la BD actual pueden traer apóstrofes o guiones (p.ej. "Cote d'Ivoire"). Verifica que `_normalize_team_name` los limpia como se describe.
- Si `resolve_latest_commit` recibe 403 por rate limit no autenticado, propaga el error: el ciclo lo clasificará como `failed` y reintentará al siguiente ciclo, cumpliendo la política de backoff existente.
- `Repository` puede tener otro método con nombre parecido; comprueba con `grep def replace_ src/wcpredict/repository.py` antes de crear conflictos.
- Los tests deben poder correr en máquina sin red. `test_daily_refresh` ya usa `DatasetDownload` mock; sigue ese patrón.
