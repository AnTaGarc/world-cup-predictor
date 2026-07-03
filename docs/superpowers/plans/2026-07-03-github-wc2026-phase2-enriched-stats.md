# Fase 2 — Stats enriquecidas del torneo actual · Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** llevar los datos ya importados en las tablas `gh_*` (Fase 0) a los modelos: refuerzo bayesiano de perfiles de lanzador/portero con evidencia del propio Mundial, y puente de stats de equipo (faltas, offsides, paradas, posesión, tiros, córners) hacia la tabla `observations` que alimenta `team_profile` y `outcome_ml_deep`.

**Architecture:** dos workstreams independientes. (2A) `penalty_context_cache` inyecta attempts sintéticos derivados de `gh_player_stats.penalty_goals` con corte temporal, y usa saves/goals_conceded del torneo como fallback de `deep_save_rate`. (2B) un sincronizador idempotente `gh_match_team_stats → observations` con `evidence_status='verified_external'` (ya aceptado por el filtro de `list_deep_team_observations`) y `source_id='github_wc2026'`; `team_profile` y todo lo aguas abajo lo consume sin cambios.

**Tech Stack:** Python 3.12, sqlite3, unittest. Convención de ejecución de tests: `PYTHONPATH=src python -m unittest tests.<módulo> -v`.

## Global Constraints

- Nunca modificar la lógica de resolución de tandas ni `shootout_kicks`.
- Corte temporal estricto: ninguna evidencia con fecha posterior al kickoff del partido a predecir puede entrar en su contexto (`last_verified <= kickoff`).
- El refuerzo del torneo actual entra con peso acotado: los attempts sintéticos usan `phase="regular"` (peso 1.0, no 1.5) y solo aportan éxitos conocidos; el prior de 12 pseudo-intentos ya amortigua el sesgo de "solo vemos goles".
- Cambios en attempts/contexto alteran el fingerprint → invalidan precálculos antiguos automáticamente (comportamiento existente deseado; no suprimirlo).
- El puente a `observations` respeta el UNIQUE existente `(match_id, subject_type, subject_name, metric, context_json, source_id)` con upsert; jamás pisa filas de otros `source_id` (las capturas revisadas del usuario prevalecen por sí mismas porque conviven como filas separadas y el filtro `MAX(o2.id)` favorece la más reciente — ver riesgo en Task 5).
- No tocar `METRIC_CATALOG`, `team_profile.py`, `outcome_ml_deep.py` ni `advanced_form.py`.
- Estilo commit: `feat(ghwc2):`, `test(ghwc2):`, `docs(ghwc2):` + trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Base para tests: `Repository(Path(tempdir) / "app.sqlite")` + `repo.initialize()`.

## File Structure

**Se crean:**
- `src/wcpredict/github_wc2026_enrichment.py` — mapeo de métricas y helpers puros de los dos workstreams.
- `tests/test_github_wc2026_enrichment.py` — workstream 2B (puente observations).
- `tests/test_github_wc2026_penalty_boost.py` — workstream 2A (refuerzo penaltis).

**Se modifican:**
- `src/wcpredict/repository.py` — `list_gh_tournament_penalty_evidence`, `list_gh_goalkeeper_tournament_rates`, `sync_gh_team_stats_to_observations`.
- `src/wcpredict/penalty_context_cache.py` — inyección de evidencia del torneo en `attempts` y `deep_rates`.
- `scripts/backfill_github_wc2026.py` — llamar al sincronizador de observations al final del backfill.
- `src/wcpredict/ui/pages.py` — llamar al sincronizador tras el refresh diario cuando `github_wc2026_team_stats` esté en `updated`.
- `tests/test_app_contract.py` — contrato de la llamada post-refresh.

---

### Task 1: Mapeo de métricas y evidencia sintética (helpers puros)

**Files:**
- Create: `src/wcpredict/github_wc2026_enrichment.py`
- Create: `tests/test_github_wc2026_enrichment.py`

**Interfaces:**
- Produces:
  - `GH_TEAM_METRIC_MAP: dict[str, str]` — columna gh → clave METRIC_CATALOG:
    - `possession_pct` → `resumen_del_partido.posesion_de_balon_pct`
    - `total_shots` → `tiros.tiros_totales`
    - `shots_on_target` → `tiros.tiros_a_puerta`
    - `corners` → `resumen_del_partido.saques_de_esquina`
    - `fouls` → `resumen_del_partido.faltas`
    - `offsides` → `ataque.fueras_de_juego`
    - `saves` → `porteria.paradas`
  - `build_synthetic_penalty_attempts(player_rows: list[dict], before_kickoff_iso: str) -> list[dict]` — para cada fila de `gh_player_stats` con `penalty_goals >= 1` y `last_verified` no nulo y `last_verified <= before_kickoff_iso[:10]`, emite `penalty_goals` dicts:
    `{"player_name": <player_name>, "team_name": <team_name>, "phase": "regular", "outcome": "scored", "attempted_on": <last_verified>, "goalkeeper_name": None, "source_provider": "github_wc2026", "source_url": None, "source_row_key": f"ghps:{external_player_id}:{i}"}`.
  - `goalkeeper_tournament_save_rate(row: dict) -> float | None` — con `saves` y `goals_conceded` no nulos y `saves + goals_conceded >= 3`, devuelve `saves / (saves + goals_conceded)`; si no, `None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_github_wc2026_enrichment.py`:

```python
import unittest

from wcpredict.github_wc2026_enrichment import (
    GH_TEAM_METRIC_MAP,
    build_synthetic_penalty_attempts,
    goalkeeper_tournament_save_rate,
)


class MetricMapTests(unittest.TestCase):
    def test_map_covers_expected_columns(self):
        self.assertEqual(
            {"possession_pct", "total_shots", "shots_on_target", "corners",
             "fouls", "offsides", "saves"},
            set(GH_TEAM_METRIC_MAP),
        )
        self.assertEqual(
            "resumen_del_partido.faltas", GH_TEAM_METRIC_MAP["fouls"]
        )
        self.assertEqual("ataque.fueras_de_juego", GH_TEAM_METRIC_MAP["offsides"])
        self.assertEqual("porteria.paradas", GH_TEAM_METRIC_MAP["saves"])


class SyntheticAttemptTests(unittest.TestCase):
    def test_emits_one_attempt_per_penalty_goal_before_cutoff(self):
        rows = [{
            "external_player_id": 16, "player_name": "Julián Andrés Quinones",
            "team_name": "Mexico", "penalty_goals": 2,
            "last_verified": "2026-07-01",
        }]
        attempts = build_synthetic_penalty_attempts(rows, "2026-07-04T17:00:00+00:00")
        self.assertEqual(2, len(attempts))
        self.assertEqual("scored", attempts[0]["outcome"])
        self.assertEqual("regular", attempts[0]["phase"])
        self.assertEqual("github_wc2026", attempts[0]["source_provider"])
        self.assertEqual("ghps:16:0", attempts[0]["source_row_key"])
        self.assertEqual("ghps:16:1", attempts[1]["source_row_key"])

    def test_excludes_rows_verified_after_kickoff(self):
        rows = [{
            "external_player_id": 16, "player_name": "A", "team_name": "T",
            "penalty_goals": 1, "last_verified": "2026-07-05",
        }]
        self.assertEqual(
            [], build_synthetic_penalty_attempts(rows, "2026-07-04T17:00:00+00:00")
        )

    def test_excludes_zero_or_null_penalty_goals(self):
        rows = [
            {"external_player_id": 1, "player_name": "A", "team_name": "T",
             "penalty_goals": 0, "last_verified": "2026-07-01"},
            {"external_player_id": 2, "player_name": "B", "team_name": "T",
             "penalty_goals": None, "last_verified": "2026-07-01"},
        ]
        self.assertEqual(
            [], build_synthetic_penalty_attempts(rows, "2026-07-04T17:00:00+00:00")
        )


class GoalkeeperRateTests(unittest.TestCase):
    def test_rate_with_enough_sample(self):
        self.assertAlmostEqual(
            5 / 6,
            goalkeeper_tournament_save_rate({"saves": 5, "goals_conceded": 1}),
        )

    def test_small_sample_returns_none(self):
        self.assertIsNone(
            goalkeeper_tournament_save_rate({"saves": 1, "goals_conceded": 1})
        )

    def test_null_fields_return_none(self):
        self.assertIsNone(goalkeeper_tournament_save_rate({"saves": None, "goals_conceded": 2}))
        self.assertIsNone(goalkeeper_tournament_save_rate({"saves": 3, "goals_conceded": None}))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m unittest tests.test_github_wc2026_enrichment -v`
Expected: FAIL — módulo no existe.

- [ ] **Step 3: Implement the module**

Create `src/wcpredict/github_wc2026_enrichment.py`:

```python
from __future__ import annotations

from typing import Any


GH_TEAM_METRIC_MAP: dict[str, str] = {
    "possession_pct": "resumen_del_partido.posesion_de_balon_pct",
    "total_shots": "tiros.tiros_totales",
    "shots_on_target": "tiros.tiros_a_puerta",
    "corners": "resumen_del_partido.saques_de_esquina",
    "fouls": "resumen_del_partido.faltas",
    "offsides": "ataque.fueras_de_juego",
    "saves": "porteria.paradas",
}


def build_synthetic_penalty_attempts(
    player_rows: list[dict[str, Any]], before_kickoff_iso: str
) -> list[dict[str, Any]]:
    cutoff_date = str(before_kickoff_iso)[:10]
    output: list[dict[str, Any]] = []
    for row in player_rows:
        goals = row.get("penalty_goals")
        verified = row.get("last_verified")
        if not goals or not verified:
            continue
        if str(verified)[:10] > cutoff_date:
            continue
        for index in range(int(goals)):
            output.append({
                "player_name": row.get("player_name"),
                "team_name": row.get("team_name"),
                "phase": "regular",
                "outcome": "scored",
                "attempted_on": str(verified)[:10],
                "goalkeeper_name": None,
                "source_provider": "github_wc2026",
                "source_url": None,
                "source_row_key": f"ghps:{row.get('external_player_id')}:{index}",
            })
    return output


def goalkeeper_tournament_save_rate(row: dict[str, Any]) -> float | None:
    saves = row.get("saves")
    conceded = row.get("goals_conceded")
    if saves is None or conceded is None:
        return None
    total = int(saves) + int(conceded)
    if total < 3:
        return None
    return int(saves) / total
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python -m unittest tests.test_github_wc2026_enrichment -v`
Expected: 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wcpredict/github_wc2026_enrichment.py tests/test_github_wc2026_enrichment.py
git commit -m "$(cat <<'EOF'
feat(ghwc2): add enrichment helpers for tournament stats

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Consultas de repositorio para evidencia del torneo

**Files:**
- Modify: `src/wcpredict/repository.py` (añadir al final de la clase)
- Test: ampliar `tests/test_github_wc2026_penalty_boost.py` (nuevo archivo)

**Interfaces:**
- Produces (métodos en `Repository`):
  - `list_gh_tournament_penalty_evidence(team_names: tuple[str, ...], before_kickoff_iso: str) -> list[dict]` — filas de `gh_player_stats` con `team_id` resuelto perteneciente a alguno de `team_names` (por join con `teams.name` usando `same_team`-insensible: comparar por id tras resolver los nombres a ids con una subquery normal `lower(t.name) IN (...)` es suficiente aquí porque los nombres vienen de la propia BD), `penalty_goals >= 1`. Devuelve los dicts crudos (incluye `external_player_id`, `player_name`, `team_name`, `penalty_goals`, `last_verified`).
  - `list_gh_goalkeeper_tournament_rates(team_names: tuple[str, ...]) -> list[dict]` — filas de `gh_player_stats` con `position='GK'` y equipo en `team_names`, devolviendo `player_name`, `team_name`, `saves`, `goals_conceded`, `last_verified`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_github_wc2026_penalty_boost.py`:

```python
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from wcpredict.repository import Repository


class TournamentEvidenceQueryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "app.sqlite")
        self.repo.initialize()
        now = datetime(2026, 7, 3, 12, tzinfo=timezone.utc).isoformat()
        with self.repo.session() as con:
            con.execute("INSERT INTO teams(id, name, fifa_code) VALUES(1, 'Mexico', 'MEX')")
            con.execute(
                "INSERT INTO gh_player_stats(provider_id, external_player_id, team_id, "
                "player_name, position, penalty_goals, saves, goals_conceded, "
                "last_verified, imported_at_utc) VALUES"
                "('github_wc2026_player_stats', 16, 1, 'Quinones', 'FWD', 2, NULL, NULL, '2026-07-01', ?),"
                "('github_wc2026_player_stats', 1, 1, 'Rangel', 'GK', 0, 5, 1, '2026-07-01', ?),"
                "('github_wc2026_player_stats', 99, 1, 'Nadie', 'MID', 0, NULL, NULL, '2026-07-01', ?)",
                (now, now, now),
            )

    def tearDown(self):
        self.directory.cleanup()

    def test_penalty_evidence_only_returns_scorers_of_selected_teams(self):
        rows = self.repo.list_gh_tournament_penalty_evidence(
            ("Mexico",), "2026-07-04T17:00:00+00:00"
        )
        self.assertEqual(1, len(rows))
        self.assertEqual("Quinones", rows[0]["player_name"])
        self.assertEqual(2, rows[0]["penalty_goals"])

    def test_penalty_evidence_other_team_returns_empty(self):
        self.assertEqual(
            [], self.repo.list_gh_tournament_penalty_evidence(("Brazil",), "2026-07-04T17:00:00+00:00")
        )

    def test_goalkeeper_rates_returns_only_gk(self):
        rows = self.repo.list_gh_goalkeeper_tournament_rates(("Mexico",))
        self.assertEqual(1, len(rows))
        self.assertEqual("Rangel", rows[0]["player_name"])
        self.assertEqual(5, rows[0]["saves"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m unittest tests.test_github_wc2026_penalty_boost -v`
Expected: FAIL — métodos no existen.

- [ ] **Step 3: Implement the repository methods**

Añadir al final de la clase `Repository`:

```python
    # ------------------------------------------------------------------
    # github_wc2026 external dataset: tournament evidence for models

    def list_gh_tournament_penalty_evidence(
        self, team_names: tuple[str, ...], before_kickoff_iso: str
    ) -> list[dict]:
        if not team_names:
            return []
        placeholders = ", ".join("?" for _ in team_names)
        query = (
            "SELECT gh.external_player_id, gh.player_name, t.name AS team_name, "
            "gh.penalty_goals, gh.last_verified "
            "FROM gh_player_stats gh JOIN teams t ON t.id = gh.team_id "
            f"WHERE lower(t.name) IN ({placeholders}) "
            "AND gh.penalty_goals >= 1 "
            "AND gh.last_verified IS NOT NULL "
            "AND substr(gh.last_verified, 1, 10) <= substr(?, 1, 10)"
        )
        params = tuple(name.casefold() for name in team_names) + (before_kickoff_iso,)
        with self.session() as con:
            return [dict(row) for row in con.execute(query, params).fetchall()]

    def list_gh_goalkeeper_tournament_rates(
        self, team_names: tuple[str, ...]
    ) -> list[dict]:
        if not team_names:
            return []
        placeholders = ", ".join("?" for _ in team_names)
        query = (
            "SELECT gh.player_name, t.name AS team_name, gh.saves, "
            "gh.goals_conceded, gh.last_verified "
            "FROM gh_player_stats gh JOIN teams t ON t.id = gh.team_id "
            f"WHERE lower(t.name) IN ({placeholders}) "
            "AND upper(COALESCE(gh.position, '')) = 'GK'"
        )
        params = tuple(name.casefold() for name in team_names)
        with self.session() as con:
            return [dict(row) for row in con.execute(query, params).fetchall()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python -m unittest tests.test_github_wc2026_penalty_boost -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wcpredict/repository.py tests/test_github_wc2026_penalty_boost.py
git commit -m "$(cat <<'EOF'
feat(ghwc2): query tournament penalty evidence from gh tables

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Inyección en el contexto de penaltis

**Files:**
- Modify: `src/wcpredict/penalty_context_cache.py` (función que construye `attempts` y `deep_rates`; hoy está en el bloque de las líneas ~187-246)
- Modify: `tests/test_github_wc2026_penalty_boost.py` (añadir tests de integración)

**Interfaces:**
- Consumes: `repo.list_gh_tournament_penalty_evidence`, `repo.list_gh_goalkeeper_tournament_rates`, `build_synthetic_penalty_attempts`, `goalkeeper_tournament_save_rate`.
- Produces: el contexto de penaltis incluye la evidencia del torneo:
  1. Tras construir `attempts` (después de `attempts.extend(historical_attempts)`), añadir:
     `attempts.extend(build_synthetic_penalty_attempts(repo.list_gh_tournament_penalty_evidence(team_names, match.kickoff_utc), match.kickoff_utc))`.
  2. En el bloque de `deep_rates`: para cada portero cuyo nombre no tenga aún rate, si `goalkeeper_tournament_save_rate(fila_gh)` devuelve valor y `last_verified <= kickoff`, usarlo como fallback (`deep_rates.setdefault(nombre, rate)`).
- El fingerprint del contexto ya incluye `attempts` y `deep_rates`, por lo que los precálculos antiguos quedan invalidados sin trabajo extra. Verificarlo con un assert en el test.

- [ ] **Step 1: Write the failing integration test**

Añadir a `tests/test_github_wc2026_penalty_boost.py` (leer primero `penalty_context_cache.py` para ajustar el nombre exacto de la función que construye los inputs — el bloque que hace `attempts = repo.list_penalty_evidence(...)`; si es una función privada tipo `_collect_inputs`, testear a través de ella):

```python
class ContextInjectionTests(unittest.TestCase):
    """Verifica que la evidencia del torneo entra en attempts y deep_rates.

    El implementador debe ajustar la llamada al nombre real de la función
    de construcción de inputs en penalty_context_cache (leerla primero).
    El contrato es: para un match con kickoff posterior a last_verified,
    los attempts del contexto incluyen filas source_provider='github_wc2026'
    y el portero sin baseline recibe el rate del torneo como fallback.
    """
```

El test concreto debe:
1. Sembrar `teams`, `matches` (kickoff 2026-07-04), `gh_player_stats` (un lanzador con `penalty_goals=1`, un GK con saves=5/conceded=1, `last_verified='2026-07-01'`).
2. Invocar la función de construcción de inputs del contexto para ese match.
3. Assert: existe al menos un attempt con `source_provider == "github_wc2026"` y `outcome == "scored"`.
4. Assert: el nombre del GK aparece en `deep_rates` con valor ≈ 5/6 (si no había baseline previo).
5. Assert negativo: con `last_verified='2026-07-05'` (posterior al kickoff) no se inyecta nada.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m unittest tests.test_github_wc2026_penalty_boost -v`
Expected: FAIL en los tests nuevos.

- [ ] **Step 3: Implement the injection**

En `penalty_context_cache.py`, tras `attempts.extend(historical_attempts)`:

```python
    from wcpredict.github_wc2026_enrichment import (
        build_synthetic_penalty_attempts,
        goalkeeper_tournament_save_rate,
    )
    attempts.extend(
        build_synthetic_penalty_attempts(
            repo.list_gh_tournament_penalty_evidence(tuple(team_names), match.kickoff_utc),
            match.kickoff_utc,
        )
    )
```

Y tras el bucle que puebla `deep_rates` desde `build_goalkeeper_baseline`:

```python
    cutoff_date = str(match.kickoff_utc)[:10]
    for gk_row in repo.list_gh_goalkeeper_tournament_rates(tuple(team_names)):
        verified = gk_row.get("last_verified")
        if verified and str(verified)[:10] > cutoff_date:
            continue
        rate = goalkeeper_tournament_save_rate(gk_row)
        if rate is not None:
            deep_rates.setdefault(str(gk_row.get("player_name") or ""), rate)
```

(Import al inicio del archivo, no dentro de la función, si el estilo del archivo lo permite sin ciclos.)

- [ ] **Step 4: Run the focused suite**

Run: `PYTHONPATH=src python -m unittest tests.test_github_wc2026_penalty_boost tests.test_penalty_context_cache tests.test_penalty_profiles tests.test_penalty_history_model -v`
Expected: PASS (los tests existentes de contexto no deben romperse; si un fingerprint pineado en un test existente cambia, actualizar el test con el nuevo fingerprint es correcto y esperado — documentarlo en el commit).

- [ ] **Step 5: Commit**

```bash
git add src/wcpredict/penalty_context_cache.py tests/test_github_wc2026_penalty_boost.py
git commit -m "$(cat <<'EOF'
feat(ghwc2): boost penalty context with current tournament evidence

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Puente gh_match_team_stats → observations

**Files:**
- Modify: `src/wcpredict/repository.py` (método `sync_gh_team_stats_to_observations`)
- Modify: `tests/test_github_wc2026_enrichment.py` (tests de integración del puente)

**Interfaces:**
- Produces: `sync_gh_team_stats_to_observations(now_utc_iso: str | None = None) -> int` — para cada fila de `gh_match_team_stats` con `match_id` y `team_id` resueltos, upserta una observation por cada columna de `GH_TEAM_METRIC_MAP` con valor no nulo:
  - `match_id` = resuelto, `subject_type='team'`, `subject_name` = nombre del equipo (de `teams.name`),
  - `metric` = clave mapeada, `value_number` = valor, `unit=None`, `context_json='{}'`,
  - `source_id='github_wc2026'`, `evidence_status='verified_external'`, `sample_size=None`, `observed_at_utc` = `last_updated` de la fila gh o `now`.
  - Upsert por el UNIQUE existente (`ON CONFLICT ... DO UPDATE SET value_number=excluded.value_number, observed_at_utc=excluded.observed_at_utc`).
  - Devuelve el número de observations insertadas/actualizadas.

- [ ] **Step 1: Write the failing test**

Añadir a `tests/test_github_wc2026_enrichment.py`:

```python
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from wcpredict.repository import Repository


class ObservationBridgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "app.sqlite")
        self.repo.initialize()
        self.now = datetime(2026, 7, 3, 12, tzinfo=timezone.utc).isoformat()
        with self.repo.session() as con:
            con.execute("INSERT INTO teams(id, name, fifa_code) VALUES(1, 'Mexico', 'MEX')")
            con.execute("INSERT INTO teams(id, name, fifa_code) VALUES(2, 'South Africa', 'RSA')")
            con.execute(
                "INSERT INTO matches(id, competition, stage, kickoff_utc, team_a_id, "
                "team_b_id, status) VALUES(100, 'FIFA World Cup 2026', 'Group Stage', "
                "'2026-06-11T20:00:00+00:00', 1, 2, 'finished')"
            )
            con.execute(
                "INSERT INTO gh_match_team_stats(provider_id, external_match_id, match_id, "
                "external_team_id, team_id, possession_pct, total_shots, shots_on_target, "
                "corners, fouls, offsides, saves, last_updated, imported_at_utc) "
                "VALUES('github_wc2026_team_stats', 1, 100, 1, 1, 57, 16, 4, 6, 11, 2, 1, "
                "'2026-06-24', ?)",
                (self.now,),
            )

    def tearDown(self):
        self.directory.cleanup()

    def test_sync_creates_observations_with_mapped_metrics(self):
        count = self.repo.sync_gh_team_stats_to_observations(self.now)
        self.assertEqual(7, count)
        with self.repo.session() as con:
            row = con.execute(
                "SELECT value_number, evidence_status, source_id FROM observations "
                "WHERE match_id=100 AND subject_name='Mexico' "
                "AND metric='resumen_del_partido.faltas'"
            ).fetchone()
        self.assertEqual(11, row["value_number"])
        self.assertEqual("verified_external", row["evidence_status"])
        self.assertEqual("github_wc2026", row["source_id"])

    def test_sync_is_idempotent(self):
        self.repo.sync_gh_team_stats_to_observations(self.now)
        self.repo.sync_gh_team_stats_to_observations(self.now)
        with self.repo.session() as con:
            count = con.execute(
                "SELECT COUNT(*) FROM observations WHERE source_id='github_wc2026'"
            ).fetchone()[0]
        self.assertEqual(7, count)

    def test_unresolved_rows_are_skipped(self):
        with self.repo.session() as con:
            con.execute(
                "INSERT INTO gh_match_team_stats(provider_id, external_match_id, "
                "external_team_id, fouls, imported_at_utc) "
                "VALUES('github_wc2026_team_stats', 2, 9, 20, ?)",
                (self.now,),
            )
        count = self.repo.sync_gh_team_stats_to_observations(self.now)
        with self.repo.session() as con:
            orphan = con.execute(
                "SELECT COUNT(*) FROM observations WHERE source_id='github_wc2026' "
                "AND match_id NOT IN (SELECT id FROM matches)"
            ).fetchone()[0]
        self.assertEqual(0, orphan)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m unittest tests.test_github_wc2026_enrichment -v`
Expected: FAIL — método no existe.

- [ ] **Step 3: Implement the bridge**

Añadir a `Repository`:

```python
    def sync_gh_team_stats_to_observations(self, now_utc_iso: str | None = None) -> int:
        from datetime import datetime, timezone
        from wcpredict.github_wc2026_enrichment import GH_TEAM_METRIC_MAP
        now = now_utc_iso or datetime.now(timezone.utc).isoformat()
        written = 0
        with self.session() as con:
            rows = con.execute(
                "SELECT gh.*, t.name AS team_name FROM gh_match_team_stats gh "
                "JOIN teams t ON t.id = gh.team_id "
                "WHERE gh.match_id IS NOT NULL AND gh.team_id IS NOT NULL"
            ).fetchall()
            for row in rows:
                observed_at = row["last_updated"] or now
                for column, metric in GH_TEAM_METRIC_MAP.items():
                    value = row[column]
                    if value is None:
                        continue
                    con.execute(
                        "INSERT INTO observations(match_id, subject_type, subject_name, "
                        "metric, value_number, value_text, unit, context_json, source_id, "
                        "evidence_status, sample_size, observed_at_utc) "
                        "VALUES(?, 'team', ?, ?, ?, NULL, NULL, '{}', 'github_wc2026', "
                        "'verified_external', NULL, ?) "
                        "ON CONFLICT(match_id, subject_type, subject_name, metric, "
                        "context_json, source_id) DO UPDATE SET "
                        "value_number=excluded.value_number, "
                        "observed_at_utc=excluded.observed_at_utc",
                        (row["match_id"], row["team_name"], metric, float(value), observed_at),
                    )
                    written += 1
        return written
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python -m unittest tests.test_github_wc2026_enrichment -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wcpredict/repository.py tests/test_github_wc2026_enrichment.py
git commit -m "$(cat <<'EOF'
feat(ghwc2): bridge gh team stats into deep observations

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Precedencia de capturas del usuario (guardia anti-conflicto)

**Files:**
- Modify: `src/wcpredict/repository.py` (dentro de `sync_gh_team_stats_to_observations`)
- Modify: `tests/test_github_wc2026_enrichment.py`

**Interfaces:**
- Riesgo detectado en diseño: `list_deep_team_observations` selecciona `MAX(o2.id)` por `(match_id, subject_name, metric)`, es decir, **la fila más reciente gana** independientemente del `source_id`. Si el usuario ya subió deep stats revisadas de un partido y el puente inserta después, la fila `github_wc2026` pisaría a la revisada en la selección.
- Regla a implementar: el puente **omite** cualquier `(match_id, subject_name, metric)` para el que ya exista una observation con `evidence_status IN ('verified', 'verified_user_json', 'verified_user_capture')` — la captura revisada del usuario siempre prevalece.

- [ ] **Step 1: Write the failing test**

Añadir a `ObservationBridgeTests`:

```python
    def test_user_reviewed_observation_is_never_overridden(self):
        with self.repo.session() as con:
            con.execute(
                "INSERT INTO observations(match_id, subject_type, subject_name, metric, "
                "value_number, context_json, source_id, evidence_status, observed_at_utc) "
                "VALUES(100, 'team', 'Mexico', 'resumen_del_partido.faltas', 12, '{}', "
                "'user_capture', 'verified_user_json', ?)",
                (self.now,),
            )
        self.repo.sync_gh_team_stats_to_observations(self.now)
        with self.repo.session() as con:
            gh_row = con.execute(
                "SELECT COUNT(*) FROM observations WHERE source_id='github_wc2026' "
                "AND match_id=100 AND metric='resumen_del_partido.faltas'"
            ).fetchone()[0]
        self.assertEqual(0, gh_row)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m unittest tests.test_github_wc2026_enrichment -v`
Expected: FAIL — el puente inserta la fila gh.

- [ ] **Step 3: Add the guard**

Dentro del bucle de métricas, antes del INSERT:

```python
                    existing = con.execute(
                        "SELECT 1 FROM observations WHERE match_id=? AND subject_type='team' "
                        "AND subject_name=? AND metric=? "
                        "AND evidence_status IN ('verified', 'verified_user_json', "
                        "'verified_user_capture') LIMIT 1",
                        (row["match_id"], row["team_name"], metric),
                    ).fetchone()
                    if existing is not None:
                        continue
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python -m unittest tests.test_github_wc2026_enrichment -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wcpredict/repository.py tests/test_github_wc2026_enrichment.py
git commit -m "$(cat <<'EOF'
feat(ghwc2): user-reviewed observations take precedence over bridge

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Cableado en backfill y refresh diario

**Files:**
- Modify: `scripts/backfill_github_wc2026.py`
- Modify: `src/wcpredict/ui/pages.py` (`_refresh_current_world_cup_banks_cached`)
- Modify: `tests/test_backfill_github_wc2026.py`
- Modify: `tests/test_app_contract.py`

**Interfaces:**
- Backfill: tras `repository.resolve_gh_foreign_keys(...)`, llamar `repository.sync_gh_team_stats_to_observations(now.isoformat())` y añadir la cuenta al report como `"observations_synced"`.
- Refresh diario: en `_refresh_current_world_cup_banks_cached` (pages.py), tras `ensure_current_world_cup_data`, si `"github_wc2026_team_stats"` está en `result.updated`, llamar `repo.sync_gh_team_stats_to_observations()`.

- [ ] **Step 1: Write the failing tests**

En `tests/test_backfill_github_wc2026.py`, añadir a `BackfillTests`:

```python
    def test_backfill_reports_observations_synced(self):
        report = run_backfill(self.repo, fetcher=self._fetcher, now=self.now)
        self.assertIn("observations_synced", report)
```

En `tests/test_app_contract.py`, junto al test del expander externo:

```python
    def test_daily_refresh_syncs_external_observations(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        self.assertIn("sync_gh_team_stats_to_observations", source)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m unittest tests.test_backfill_github_wc2026 tests.test_app_contract -v`
Expected: FAIL en los dos nuevos.

- [ ] **Step 3: Wire both call sites**

En `scripts/backfill_github_wc2026.py`, tras `repository.resolve_gh_foreign_keys(None, now.isoformat())`:

```python
    observations_synced = repository.sync_gh_team_stats_to_observations(now.isoformat())
```

y añadir `"observations_synced": observations_synced,` al dict de retorno.

En `pages.py`, dentro de `_refresh_current_world_cup_banks_cached`, cambiar el `return` final por:

```python
    result = ensure_current_world_cup_data(
        repo,
        build_daily_fetcher(),
        importer=build_daily_importer(repo, now),
        now=now,
        providers=providers,
    )
    if "github_wc2026_team_stats" in result.updated:
        repo.sync_gh_team_stats_to_observations(now.isoformat())
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python -m unittest tests.test_backfill_github_wc2026 tests.test_app_contract -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/backfill_github_wc2026.py src/wcpredict/ui/pages.py tests/test_backfill_github_wc2026.py tests/test_app_contract.py
git commit -m "$(cat <<'EOF'
feat(ghwc2): sync external observations on backfill and daily refresh

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Suite completa, sync real y verificación de impacto

**Files:**
- Run only (sin cambios de código salvo fixes de regresión).

- [ ] **Step 1: Full suite**

Run: `PYTHONPATH=src python -m unittest discover -s tests`
Expected: todos PASS. Si algún test de fingerprint pineado falla por el cambio de contexto de penaltis, actualizar el valor esperado (cambio legítimo) y anotarlo en el commit.

- [ ] **Step 2: Run the real sync**

Run: `PYTHONPATH=src python scripts/backfill_github_wc2026.py`
Expected: report con `observations_synced > 0` (~150 filas × 7 métricas menos nulos y partidos con captura revisada).

- [ ] **Step 3: Integrity + spot-check**

```powershell
python -c "import sqlite3; con=sqlite3.connect('data/worldcup.sqlite'); print(con.execute('PRAGMA integrity_check').fetchone()); print(con.execute(\"SELECT COUNT(*) FROM observations WHERE source_id='github_wc2026'\").fetchone())"
```

Expected: `('ok',)` y un conteo acorde al report.

- [ ] **Step 4: Regenerate penalty precomputes**

Run: `python scripts/precompute_penalty_contexts.py --force`
Expected: contextos regenerados con la nueva evidencia (fingerprints nuevos). Verificar en la salida que los equipos con `penalty_goals` en el torneo muestran más attempts que antes.

- [ ] **Step 5: Commit data + push**

```bash
git add data/
git commit -m "$(cat <<'EOF'
data(ghwc2): sync tournament observations and regenerate penalty contexts

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

Después: `.\scripts\push_project.ps1 -Message "feat: phase 2 tournament-enriched stats"` (el push seguro re-ejecuta la suite).

---

## Notas finales para el implementador

- Antes de tocar `penalty_context_cache.py`, léelo entero: la función que construye los inputs puede tener otro nombre al asumido y el orden attempts/deep_rates importa para el fingerprint.
- El sesgo "solo vemos goles de penalti, no fallos" del torneo es conocido y aceptado: el prior de 12 pseudo-intentos y el peso `regular` (1.0) lo amortiguan. No subir el peso a `shootout` (1.5) bajo ningún concepto.
- Si `penalty_goals` incluyera goles de tanda (no debería: las stats oficiales de torneo no los cuentan como goles), habría doble conteo con `shootout_kicks`. Verificar con Alemania-Paraguay: los lanzadores de esa tanda no deben tener `penalty_goals` incrementado solo por la tanda.
- `observed_at_utc` usa `last_updated` de la fila gh para que el corte temporal `m2.kickoff_utc < ?` de las consultas deep siga siendo el guardián del leakage (el filtro corta por kickoff del partido observado, no por observed_at; el puente solo escribe partidos ya jugados, así que es coherente).
- El guard de precedencia (Task 5) es innegociable: preferencia del usuario documentada — "una fuente oficial o una captura revisada prevalece ante un conflicto".
