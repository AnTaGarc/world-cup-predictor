# Knockout Period Upload Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mostrar y exigir solo los periodos realmente disponibles, conservando el acumulado de 120 minutos como comprobación opcional.

**Architecture:** Los helpers puros de `ui/knockout_settlement.py` seguirán siendo la única fuente para visibilidad, estado y obligatoriedad. `match_phases.validate_period_totals` añadirá una comparación independiente entre las cuatro partes atómicas y `full_match_total`; el almacenamiento existente no cambia.

**Tech Stack:** Python 3.12, `unittest`, Streamlit, SQLite.

## Global Constraints

- `regulation` muestra y exige solo `regulation_total`.
- `extra_time` y `shootout` exigen `first_half`, `second_half`, `extra_time_first` y `extra_time_second`.
- `full_match_total` permanece visible y opcional en `extra_time` y `shootout`.
- `regulation_total` y `extra_time_total` no aparecen ni satisfacen el cierre cuando hubo prórroga.
- No se modifican las validaciones de marcador, porteros o tanda.
- No se eliminan importaciones históricas ni se altera el esquema SQLite.

---

### Task 1: Ajustar secciones y obligatoriedad del cierre

**Files:**
- Modify: `tests/test_knockout_settlement_ui.py`
- Modify: `src/wcpredict/ui/knockout_settlement.py`

**Interfaces:**
- Consumes: `build_settlement_sections(decided_in: str) -> SettlementSections`, `period_statuses(decided_in, imported_periods, issues) -> dict[str, str]`, `validate_settlement_draft(draft) -> tuple[str, ...]`.
- Produces: las mismas firmas, con periodos visibles y requisitos acordes al diseño.

- [ ] **Step 1: Sustituir las expectativas antiguas por pruebas RED**

```python
def test_regulation_path_only_exposes_required_90_minute_total(self):
    state = build_settlement_sections("regulation")
    self.assertEqual(("regulation_total",), state.visible_periods)
    self.assertEqual("pending", period_statuses("regulation", set(), [])["regulation_total"])
    self.assertEqual("not_played", period_statuses("regulation", set(), [])["first_half"])

def test_shootout_path_exposes_atomic_periods_and_optional_120_total(self):
    state = build_settlement_sections("shootout")
    self.assertEqual(
        ("first_half", "second_half", "extra_time_first", "extra_time_second", "full_match_total"),
        state.visible_periods,
    )
    statuses = period_statuses("shootout", set(), [])
    self.assertEqual("optional", statuses["full_match_total"])
    self.assertEqual("not_played", statuses["regulation_total"])
    self.assertEqual("not_played", statuses["extra_time_total"])

def test_extra_time_requires_all_four_atomic_periods(self):
    draft = KnockoutSettlementDraft(
        phase_result=MatchPhaseResultInput(1, 1, 1, 0, None, None, "extra_time"),
        kicks=(),
        imported_periods=frozenset({"regulation_total", "extra_time_total"}),
        goalkeeper_a_id=None,
        goalkeeper_b_id=None,
    )
    errors = validate_settlement_draft(draft)
    self.assertTrue(any("primera y segunda parte" in error.casefold() for error in errors))

def test_extra_time_closes_with_four_atomic_periods_without_totals(self):
    draft = KnockoutSettlementDraft(
        phase_result=MatchPhaseResultInput(1, 1, 1, 0, None, None, "extra_time"),
        kicks=(),
        imported_periods=frozenset({"first_half", "second_half", "extra_time_first", "extra_time_second"}),
        goalkeeper_a_id=None,
        goalkeeper_b_id=None,
    )
    self.assertEqual((), validate_settlement_draft(draft))
```

- [ ] **Step 2: Ejecutar las pruebas y confirmar RED**

Run:

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_knockout_settlement_ui -v
```

Expected: fallos en periodos visibles y en la aceptación antigua de acumulados.

- [ ] **Step 3: Implementar la regla mínima**

En `build_settlement_sections`:

```python
def build_settlement_sections(decided_in: str) -> SettlementSections:
    if decided_in == "regulation":
        return SettlementSections(("regulation_total",), False, False)
    visible = (
        "first_half",
        "second_half",
        "extra_time_first",
        "extra_time_second",
        "full_match_total",
    )
    return SettlementSections(visible, True, decided_in == "shootout")
```

Eliminar la lógica `alternative_groups` de `period_statuses`; mantener `full_match_total` como `optional` si no está importado. En `validate_settlement_draft`, exigir `regulation_total` para `regulation` y exigir los cuatro periodos atómicos para `extra_time`/`shootout`. Actualizar el texto introductorio de `render_knockout_settlement` con las reglas nuevas.

- [ ] **Step 4: Ejecutar el módulo focalizado y confirmar GREEN**

Run:

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_knockout_settlement_ui -v
```

Expected: todas las pruebas de `test_knockout_settlement_ui` pasan.

- [ ] **Step 5: Commit de la regla de interfaz**

```powershell
git add src/wcpredict/ui/knockout_settlement.py tests/test_knockout_settlement_ui.py
git commit -m "fix(knockout): require available period files"
```

---

### Task 2: Validar el acumulado opcional de 120 minutos

**Files:**
- Modify: `tests/test_phase_stats_persistence.py`
- Modify: `src/wcpredict/match_phases.py`

**Interfaces:**
- Consumes: `validate_period_totals(rows: list[dict]) -> list[PhaseValidationIssue]`.
- Produces: comparación de `full_match_total` contra la suma de los cuatro periodos atómicos usando la tolerancia existente por métrica.

- [ ] **Step 1: Añadir una prueba RED de discrepancia a 120 minutos**

```python
def test_full_match_total_mismatch_compares_all_four_atomic_periods(self):
    self._import("first_half", "first", {"xg": 0.7, "shots": 8}, {"xg": 0.4, "shots": 5})
    self._import("second_half", "second", {"xg": 1.0, "shots": 10}, {"xg": 0.5, "shots": 6})
    self._import("extra_time_first", "et-first", {"xg": 0.2, "shots": 2}, {"xg": 0.1, "shots": 1})
    self._import("extra_time_second", "et-second", {"xg": 0.1, "shots": 1}, {"xg": 0.1, "shots": 1})
    self._import("full_match_total", "full", {"xg": 2.0, "shots": 22}, {"xg": 1.1, "shots": 13})
    issues = self.repo.validate_match_period_stats(self.match_id)
    self.assertIn(("Spain", "shots", 21.0, 22.0), [issue.comparison for issue in issues])
```

- [ ] **Step 2: Ejecutar la prueba y confirmar RED**

Run:

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_phase_stats_persistence.PhaseStatsPersistenceTests.test_full_match_total_mismatch_compares_all_four_atomic_periods -v
```

Expected: FAIL porque `full_match_total` aún no participa en `comparisons`.

- [ ] **Step 3: Añadir la comparación completa**

En `validate_period_totals`, ampliar `comparisons`:

```python
comparisons = (
    (("first_half", "second_half"), "regulation_total"),
    (("extra_time_first", "extra_time_second"), "extra_time_total"),
    (("first_half", "second_half", "extra_time_first", "extra_time_second"), "full_match_total"),
)
```

Reutilizar el bucle, mensajes y tolerancias existentes; no añadir una ruta especial.

- [ ] **Step 4: Ejecutar persistencia y UI focalizadas**

Run:

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_phase_stats_persistence tests.test_knockout_settlement_ui -v
```

Expected: todos los tests pasan y el total opcional solo valida.

- [ ] **Step 5: Commit de validación**

```powershell
git add src/wcpredict/match_phases.py tests/test_phase_stats_persistence.py
git commit -m "feat(phases): validate optional 120 minute total"
```

---

### Task 3: Verificación integrada sin publicar datos locales

**Files:**
- Verify only: `src/wcpredict/ui/knockout_settlement.py`, `src/wcpredict/match_phases.py`, tests relacionados.

**Interfaces:**
- Consumes: los helpers y validadores modificados en Tasks 1 y 2.
- Produces: evidencia de regresión y un diff que excluye `data/worldcup.sqlite` y `data/evidence/reviewed-json/`.

- [ ] **Step 1: Ejecutar las pruebas de integración de fases**

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_knockout_phase_integration tests.test_knockout_settlement tests.test_knockout_settlement_ui tests.test_phase_stats_persistence tests.test_match_phases -v
```

Expected: todas las pruebas pasan.

- [ ] **Step 2: Verificar diff y datos excluidos**

```powershell
git diff --check
git status --short
```

Expected: sin errores de whitespace; SQLite y JSON del usuario permanecen sin stage.

- [ ] **Step 3: Ejecutar la suite completa**

```powershell
$env:PYTHONPATH='src'; python -m unittest discover -s tests -v
```

Expected: suite completa con código de salida 0. Si AppTest modifica SQLite, restaurar únicamente esa mutación de prueba sin borrar los JSON importados por el usuario.
