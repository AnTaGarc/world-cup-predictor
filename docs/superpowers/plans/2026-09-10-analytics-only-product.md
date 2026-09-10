# Analytics-Only Product Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convertir la aplicación en un producto de IA y analítica deportiva sin apuestas, cuotas ni EV, conservando deep stats, proveedores deportivos, proyecciones de jugadores, snapshots analíticos y auditorías.

**Architecture:** Separar las salidas estadísticas neutrales de las antiguas abstracciones de mercado, adaptar la interfaz sobre esos contratos y mantener lectores compatibles con snapshots heredados. Las eliminaciones de datos de cuotas se ejecutan al final mediante una migración idempotente que compara invariantes deportivos antes y después.

**Tech Stack:** Python 3.12, Streamlit, SQLite, pandas, unittest, modelos Poisson/binomial negativa/Dixon-Coles y artefactos joblib existentes.

**Spec:** `docs/superpowers/specs/2026-09-10-analytics-only-product-design.md`

## Global Constraints

- No borrar ni modificar semánticamente `observations`, deep stats, estadísticas por equipo/jugador/periodo, resultados, evidencias, alineaciones, disponibilidad, modelos o auditorías.
- Conservar API-Sports, APIFootball, Football-Data.org, TheSportsDB, SportMonks, StatsBomb, Kaggle/swaptr, GitHub WC2026, martj42, OpenFootball, Transfermarkt, xgabora deportivo, SofaScore revisado y evidencia web.
- Eliminar únicamente datos y dependencias inequívocamente exclusivos de cuotas: Winamax/OddsPapi, The Odds API, `manual_odds`, cálculos de cuota/EV y sus fixtures.
- No modificar variables de entorno del usuario ni la skill externa `analisis-de-datos`.
- Escribir una prueba que falle por el comportamiento anterior antes de modificar código de producción.
- Preservar los cambios preexistentes del usuario en `data/worldcup.sqlite` y `data/evidence/reviewed-json/` durante todos los commits intermedios.
- No limpiar la base externa `sports-data/sports.db` sin copia recuperable, comprobación de claves foráneas y autorización ya acotada a filas de odds.

---

### Task 1: Contratos neutrales para resultados y marcadores

**Files:**
- Modify: `src/wcpredict/models.py`
- Modify: `src/wcpredict/services.py`
- Modify: `src/wcpredict/ui/view_models.py`
- Test: `tests/test_services.py`
- Test: `tests/test_score_mode_consistency.py`
- Test: `tests/test_view_models.py`

**Interfaces:**
- Produces: `PredictionTarget`, `MatchProjection`, `predict_match(team_a, team_b, ...) -> list[MatchProjection]` o nombres equivalentes coherentes.
- Preserves: compatibilidad temporal de `predict_match_markets` como adaptador privado solo mientras se migran consumidores.
- Produces: filas visibles `Resultado`, `Selección`, `Probabilidad`, `Mín.`, `Máx.`, `Confianza`, `Muestra`, `Origen`, `Explicación`.

- [ ] **Step 1: Escribir pruebas fallidas para el contrato neutral**

Añadir pruebas que exijan que las salidas públicas incluyan únicamente:

```python
visible_targets = {row.target for row in predict_match("Spain", "Portugal", results, as_of)}
self.assertEqual(
    {"match_outcome", "score_mode", "score_alternative", "expected_goals", "score_grid"},
    visible_targets,
)
```

Comprobar también que las selecciones del resultado sean los dos equipos y `Empate`, sin etiquetas visibles `1X2`, `Draw No Bet`, `Over/Under` o `Both Teams To Score`.

- [ ] **Step 2: Ejecutar las pruebas y confirmar RED**

Run:

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_services tests.test_score_mode_consistency tests.test_view_models -v
```

Expected: FAIL porque todavía solo existen `MarketFamily`, `MarketPrediction` y etiquetas apostables.

- [ ] **Step 3: Implementar el contrato neutral mínimo**

Separar la construcción de la matriz de goles de la lista de mercados. Mantener sin cambios:

- cálculo de xG;
- binomial negativa y Dixon-Coles;
- ensamble ML;
- ajustes de forma, sede, equipo y calibración;
- top de marcadores y matriz 0–5.

La nueva lista pública no debe generar doble oportunidad, empate no válido, over/under ni BTTS. Si se conserva un adaptador legado durante esta tarea, no debe llegar a la UI.

- [ ] **Step 4: Ejecutar las pruebas y confirmar GREEN**

Run: el mismo comando del Step 2.  
Expected: PASS.

- [ ] **Step 5: Comprobar que el motor numérico no cambió**

Run:

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_poisson tests.test_score_mode_consistency tests.test_services_team_corrections -v
```

Expected: PASS con las probabilidades y marcadores esperados existentes.

- [ ] **Step 6: Commit**

```powershell
git add src/wcpredict/models.py src/wcpredict/services.py src/wcpredict/ui/view_models.py tests/test_services.py tests/test_score_mode_consistency.py tests/test_view_models.py
git commit -m "refactor: expose neutral match projections"
```

---

### Task 2: Proyecciones estadísticas por equipo sin mercados de volumen

**Files:**
- Modify: `src/wcpredict/team_volume_markets.py` (rename to `src/wcpredict/team_projections.py` after tests are green)
- Modify: `src/wcpredict/volume_markets.py`
- Modify: `src/wcpredict/ui/pages.py`
- Modify: `src/wcpredict/audit.py`
- Test: `tests/test_team_profile.py`
- Test: `tests/test_volume_markets.py` (rename to `tests/test_team_projections.py`)
- Test: `tests/test_audit.py`
- Test: `tests/test_app_contract.py`

**Interfaces:**
- Produces: `TeamStatProjection(metric, label, team_name, expected, confidence, sample_size)`.
- Produces: `predict_team_statistics(profile_a, profile_b, card_multiplier=1.0) -> list[TeamStatProjection]`.
- Preserves: `team_volume_predictions` compatibility inside the audit until renamed in all consumers.

- [ ] **Step 1: Escribir pruebas fallidas de proyección y auditoría**

Exigir que cada equipo reciba una expectativa por métrica sin línea ni probabilidad over:

```python
rows = predict_team_statistics(profile_a, profile_b)
self.assertTrue(rows)
self.assertTrue(all(hasattr(row, "expected") for row in rows))
self.assertTrue(all(not hasattr(row, "line") for row in rows))
self.assertTrue(all(not hasattr(row, "over_probability") for row in rows))
```

Añadir una prueba de `build_per_team_audit` que confirme que xG, goles, tiros, tiros a puerta, córners, tarjetas y posesión mantienen columnas predicho/real/delta.

- [ ] **Step 2: Ejecutar las pruebas y confirmar RED**

Run:

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_team_profile tests.test_volume_markets tests.test_audit tests.test_app_contract -v
```

Expected: FAIL porque las proyecciones actuales exponen `line`, `over_probability` y `Mercados de volumen`.

- [ ] **Step 3: Extraer la expectativa central por equipo**

Crear el contrato neutral usando la misma mezcla existente 45% producción propia, 30% concesión rival y 25% media del torneo. Conservar el multiplicador arbitral de tarjetas. Retirar del contrato público las líneas comunes y las CDF over/under.

- [ ] **Step 4: Sustituir la UI de volumen**

Eliminar `_render_volume_markets` y el bloque agregado de totales. Crear `_render_team_statistics` que muestre solo estadística, expectativa de ambos equipos, confianza y muestra. Mantener esos valores en el bundle auxiliar usado por auditoría.

- [ ] **Step 5: Ejecutar las pruebas y confirmar GREEN**

Run: el comando del Step 2.  
Expected: PASS.

- [ ] **Step 6: Renombrar archivos y consumidores**

Mover el módulo y el test a sus nombres neutrales, actualizar imports en scripts de calibración y conservar `derive_xg_factors_from_profile` sin cambios numéricos.

- [ ] **Step 7: Ejecutar pruebas ampliadas**

Run:

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_team_projections tests.test_audit tests.test_knockout_audit tests.test_services_team_corrections -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add src/wcpredict/team_projections.py src/wcpredict/ui/pages.py src/wcpredict/audit.py scripts tests/test_team_projections.py tests/test_audit.py tests/test_app_contract.py
git commit -m "refactor: present team statistics as projections"
```

---

### Task 3: Jugadores sin cuotas, conservando todas sus predicciones

**Files:**
- Modify: `src/wcpredict/player_markets.py` (rename to `src/wcpredict/player_projections.py`)
- Modify: `src/wcpredict/ui/interaction_models.py`
- Modify: `src/wcpredict/ui/pages.py`
- Test: `tests/test_player_markets.py` (rename to `tests/test_player_projections.py`)
- Test: `tests/test_ui_interaction_models.py`
- Test: `tests/test_streamlit_smoke.py`
- Test: `tests/test_app_contract.py`

**Interfaces:**
- Produces: `PlayerProjection` with metric, expected count when applicable, threshold probability, confidence, sample size and explanation.
- Produces: `estimate_player_projection(assumption, metric, threshold, sample_size)`.
- Preserves: selección de equipo/posición/jugador, plantilla, minutos, titularidad, métricas de campo y portero y ajustes deep.

- [ ] **Step 1: Escribir pruebas fallidas de la experiencia de jugadores**

Las pruebas contractuales deben confirmar:

```python
self.assertNotIn('number_input("Cuota"', source)
self.assertNotIn("compare_odds_to_probability", player_section)
self.assertIn("Umbral estadístico", player_section)
self.assertIn("Probabilidad estimada", player_section)
```

Añadir pruebas numéricas para todas las familias existentes: gol, asistencia, tiros, tiros a puerta, tarjetas, pases, paradas, goles concedidos y portería a cero.

- [ ] **Step 2: Ejecutar las pruebas y confirmar RED**

Run:

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_player_markets tests.test_ui_interaction_models tests.test_streamlit_smoke tests.test_app_contract -v
```

Expected: FAIL por la entrada de cuota y el resultado EV existentes.

- [ ] **Step 3: Implementar proyecciones neutrales**

Renombrar `line` a `threshold` en el contrato público y calcular/exponer el conteo esperado a partir de tasa por 90 y minutos. Mantener los modelos de portero y la probabilidad de superar el umbral.

- [ ] **Step 4: Adaptar la UI de jugadores**

Eliminar solamente la entrada de cuota y la tabla EV. Conservar plantilla, filtros, selector de métrica, umbral, tasa, minutos, titularidad, explicación y confianza. Añadir métricas claras `Valor esperado` y `Probabilidad de alcanzar el umbral`.

- [ ] **Step 5: Ejecutar las pruebas y confirmar GREEN**

Run: el comando del Step 2.  
Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/wcpredict/player_projections.py src/wcpredict/ui/interaction_models.py src/wcpredict/ui/pages.py tests/test_player_projections.py tests/test_ui_interaction_models.py tests/test_streamlit_smoke.py tests/test_app_contract.py
git commit -m "feat: keep player projections without odds"
```

---

### Task 4: Navegación, marcadores, historial y auditorías neutrales

**Files:**
- Modify: `app.py`
- Modify: `src/wcpredict/ui/pages.py`
- Modify: `src/wcpredict/ui/theme.py`
- Modify: `src/wcpredict/ui/translations.py`
- Modify: `src/wcpredict/ui/view_models.py`
- Modify: `src/wcpredict/prediction_report.py`
- Test: `tests/test_app_contract.py`
- Test: `tests/test_streamlit_smoke.py`
- Test: `tests/test_view_models.py`
- Test: `tests/test_knockout_audit.py`
- Test: `tests/test_knockout_phase_integration.py`

**Interfaces:**
- Produces navigation: `Modelo`, `Marcadores`, `Estadísticas por equipo`, `Jugadores`, `Datos y fuentes`, `Historial`.
- Preserves audit UI: marcador final, Brier, observadas, score/xG, tabla por equipo y fases KO.

- [ ] **Step 1: Escribir pruebas fallidas de copy y navegación**

Comprobar que el código visible contiene las seis secciones nuevas y no contiene `Predicción y valor`, `Mercados y EV`, `Mercados modelados`, `Ranking EV`, `Cuotas tocadas` ni `apuestas evaluadas`.

Añadir una prueba que construya el HTML/filas del marcador y confirme top de marcadores, xG y mapa 0–5.

- [ ] **Step 2: Ejecutar las pruebas y confirmar RED**

Run:

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_app_contract tests.test_streamlit_smoke tests.test_view_models tests.test_knockout_audit tests.test_knockout_phase_integration -v
```

Expected: FAIL por navegación y copy antiguos.

- [ ] **Step 3: Adaptar navegación y resumen**

Renombrar la página principal a `Análisis predictivo`, sustituir el contador de cuotas por evaluaciones o cobertura profunda y actualizar hero/subtítulos.

- [ ] **Step 4: Adaptar marcadores**

Mantener tarjetas top y mapa de calor. Añadir xG esperado y, si ya está disponible en el bundle sin cálculo adicional, masa cubierta. Retirar `Exact Score` de etiquetas visibles y la frase sobre apuestas.

- [ ] **Step 5: Preservar auditorías y neutralizar copy**

No alterar `build_match_audit`, `build_per_team_audit` ni `evaluate_knockout_snapshot` salvo renombres externos. Cambiar solo copy de apuestas/mercados y confirmar la misma estructura de datos.

- [ ] **Step 6: Ejecutar pruebas y confirmar GREEN**

Run: el comando del Step 2.  
Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add app.py src/wcpredict/ui src/wcpredict/prediction_report.py tests/test_app_contract.py tests/test_streamlit_smoke.py tests/test_view_models.py tests/test_knockout_audit.py tests/test_knockout_phase_integration.py
git commit -m "feat: reshape app as sports analytics workspace"
```

---

### Task 5: Desacoplar exclusivamente los proveedores de odds

**Files:**
- Modify: `src/wcpredict/provider_health.py`
- Modify: `src/wcpredict/source_catalog.py`
- Modify: `src/wcpredict/refresh.py`
- Modify: `src/wcpredict/collector_bridge.py`
- Modify: `src/wcpredict/ui/pages.py`
- Delete: `src/wcpredict/odds_routing.py`
- Test: `tests/test_provider_health.py`
- Test: `tests/test_source_catalog.py`
- Test: `tests/test_refresh.py`
- Test: `tests/test_collector_bridge.py`
- Delete: `tests/test_odds_routing.py`

**Interfaces:**
- Preserves: `RefreshResult` con estado, llamadas, bundle, proveedores deportivos, faltantes y error técnico.
- Removes: `odds_status`, `odds_providers`, `--max-odds-credits` y presentación de cuotas.
- Preserves: todos los IDs de proveedores deportivos definidos en la especificación.

- [ ] **Step 1: Escribir pruebas fallidas de frontera de proveedores**

```python
matrix = credential_matrix({})
self.assertNotIn("oddspapi_winamax", matrix)
self.assertNotIn("the_odds_api", matrix)
for provider in SPORT_DATA_PROVIDERS:
    self.assertIn(provider, matrix)
```

Comprobar además que `default_source_catalog()` conserva fuentes deportivas, elimina `exact_bookmaker` y conserva `xgabora` sin el dominio `historical_odds`.

- [ ] **Step 2: Ejecutar las pruebas y confirmar RED**

Run:

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_provider_health tests.test_source_catalog tests.test_refresh tests.test_collector_bridge -v
```

Expected: FAIL porque todavía existen proveedores y campos de odds.

- [ ] **Step 3: Retirar solo la configuración de odds**

Eliminar OddsPapi/Winamax, The Odds API y `exact_bookmaker`. No retirar ninguna API deportiva. Mantener sus errores, estado y caché.

- [ ] **Step 4: Simplificar el contrato del recolector**

Eliminar argumentos y métricas de cuotas del comando y resultado. Ignorar `market_comparisons` heredado. Confirmar que estadísticas, resultados, alineaciones, disponibilidad, fuentes y cobertura siguen cargándose.

- [ ] **Step 5: Ejecutar pruebas y confirmar GREEN**

Run: el comando del Step 2.  
Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/wcpredict/provider_health.py src/wcpredict/source_catalog.py src/wcpredict/refresh.py src/wcpredict/collector_bridge.py src/wcpredict/ui/pages.py tests/test_provider_health.py tests/test_source_catalog.py tests/test_refresh.py tests/test_collector_bridge.py
git rm src/wcpredict/odds_routing.py tests/test_odds_routing.py
git commit -m "refactor: disconnect odds-only providers"
```

---

### Task 6: Persistencia analítica y migración segura de cuotas

**Files:**
- Modify: `src/wcpredict/database.py`
- Modify: `src/wcpredict/repository.py`
- Create: `src/wcpredict/analytics_migration.py`
- Create: `scripts/migrate_analytics_only.py`
- Delete: `src/wcpredict/market_math.py`
- Delete: `src/wcpredict/market_catalog.py`
- Delete: `src/wcpredict/odds.py`
- Delete: `data/fixtures/sample_odds.csv`
- Test: `tests/test_analytics_migration.py`
- Modify: `tests/test_database_repository.py`
- Modify: `tests/test_prediction_persistence.py`
- Delete: `tests/test_market_math.py`
- Delete: `tests/test_market_catalog.py`
- Delete: `tests/test_odds.py`

**Interfaces:**
- Produces: `capture_sports_invariants(connection) -> dict[str, object]`.
- Produces: `migrate_worldcup_database(path: Path, backup_path: Path) -> MigrationReport`.
- Produces: `migrate_snapshot_payload(payload: dict) -> dict` compatible e idempotente.
- Preserves: lector de snapshots heredados durante una versión de transición.

- [ ] **Step 1: Escribir pruebas fallidas de integridad e idempotencia**

Construir una base temporal con deep stats, observaciones, evidencias, resultado, snapshot mixto y cuota Winamax. Exigir:

```python
before = capture_sports_invariants(connection)
report = migrate_worldcup_database(db_path, backup_path)
after = capture_sports_invariants(connection)
self.assertEqual(before, after)
self.assertEqual(0, scalar(connection, "SELECT COUNT(*) FROM manual_odds"))
self.assertEqual(1, report.snapshots_migrated)
self.assertEqual([1.2, 0.8], migrated["expected_xg"])
self.assertEqual(7, migrated["deep_count"])
```

Ejecutar la migración dos veces y exigir el mismo resultado final. Añadir un caso que provoque una diferencia deportiva y confirme rollback.

- [ ] **Step 2: Ejecutar las pruebas y confirmar RED**

Run:

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_analytics_migration tests.test_database_repository tests.test_prediction_persistence -v
```

Expected: FAIL porque no existe la migración ni los invariantes.

- [ ] **Step 3: Implementar captura de invariantes**

Incluir recuento y hash estable de tablas protegidas: equipos, jugadores, partidos, estadísticas, observaciones, importaciones, fuentes deportivas, evidencias, resultados, fases, tandas, datasets actuales y tablas GitHub WC2026. Excluir explícitamente tablas y campos de cuotas.

- [ ] **Step 4: Implementar migración transaccional de snapshots**

Conservar equipos, xG, `deep_count`, `prior_deep_samples`, resultado, marcadores, matriz y knockout. Retirar del payload doble oportunidad, DNB, over/under y BTTS. Usar nombres neutrales para el resultado, conservando un lector de formato antiguo.

- [ ] **Step 5: Implementar eliminación de cuotas en copia controlada**

Crear primero una copia de seguridad con nombre explícito, abrir transacción, migrar snapshots, eliminar filas/tabla de cuotas y fuentes exclusivas, validar invariantes, ejecutar `PRAGMA foreign_key_check` y confirmar solo si todo coincide.

- [ ] **Step 6: Ejecutar pruebas y confirmar GREEN**

Run: el comando del Step 2.  
Expected: PASS.

- [ ] **Step 7: Ejecutar la migración sobre una copia de la base real**

Run:

```powershell
Copy-Item -LiteralPath data/worldcup.sqlite -Destination data/worldcup.analytics-preview.sqlite
$env:PYTHONPATH='src'; python scripts/migrate_analytics_only.py --database data/worldcup.analytics-preview.sqlite --backup data/worldcup.analytics-preview.backup.sqlite
```

Expected: reporte con 37 cuotas eliminadas, 76 snapshots conservados/migrados, `foreign_key_check=ok` e invariantes deportivos iguales.

- [ ] **Step 8: Aplicar a la base real solo tras revisar el reporte de la copia**

Run:

```powershell
$env:PYTHONPATH='src'; python scripts/migrate_analytics_only.py --database data/worldcup.sqlite --backup data/worldcup.before-analytics-migration.sqlite
```

Expected: los mismos invariantes deportivos y una copia recuperable. No añadir la copia de seguridad a Git.

- [ ] **Step 9: Retirar módulos/fixtures exclusivos de cuotas**

Eliminar imports muertos y los módulos/tests indicados. Confirmar con `rg` que ningún consumidor activo los referencia.

- [ ] **Step 10: Commit**

```powershell
git add src/wcpredict/database.py src/wcpredict/repository.py src/wcpredict/analytics_migration.py scripts/migrate_analytics_only.py tests/test_analytics_migration.py tests/test_database_repository.py tests/test_prediction_persistence.py data/worldcup.sqlite
git rm src/wcpredict/market_math.py src/wcpredict/market_catalog.py src/wcpredict/odds.py data/fixtures/sample_odds.csv tests/test_market_math.py tests/test_market_catalog.py tests/test_odds.py
git commit -m "feat: migrate persistence to analytics-only data"
```

---

### Task 7: Documentación pública y limpieza terminológica

**Files:**
- Modify: `README.md`
- Modify or remove: `docs/ralph-audit/*.md`
- Modify or remove: historical `docs/superpowers/` documents containing active betting requirements
- Modify: `design_system/SKILL.md`
- Modify: `design_system/components/metrics-callouts.card.html`
- Modify: `scripts/seed_demo.py`
- Modify: relevant tests and comments identified by the final scan

**Interfaces:**
- Produces: README orientado a IA, ingeniería de datos, fuentes, modelos, auditoría y calibración.

- [ ] **Step 1: Crear una prueba/scan fallido de terminología pública**

Añadir a `tests/test_app_contract.py` un scan limitado a archivos activos que rechace patrones inequívocos:

```python
BANNED = (
    "winamax", "bookmaker", "decimal_odds", "manual_odds",
    "expected_value", "ranking ev", "the_odds_api", "oddspapi",
)
```

Permitir únicamente la especificación y el plan de migración como registro histórico explícito.

- [ ] **Step 2: Ejecutar el scan y confirmar RED**

Run:

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_app_contract -v
```

Expected: FAIL con README, documentos, scripts o comentarios todavía pendientes.

- [ ] **Step 3: Reescribir README y documentación activa**

Documentar arquitectura, actualización automática, fuentes deportivas, precedencia, deep stats, modelos, proyecciones, snapshots, auditorías, calibración, instalación y limitaciones. Eliminar variables, ejemplos y flujos de cuotas.

- [ ] **Step 4: Limpiar diseño, scripts y documentos históricos**

Retirar artefactos exclusivamente apostables. Cuando un documento histórico contenga decisiones deportivas todavía útiles, reescribirlo con términos neutrales en vez de borrarlo.

- [ ] **Step 5: Ejecutar el scan y confirmar GREEN**

Run: el comando del Step 2.  
Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add README.md docs design_system scripts tests/test_app_contract.py
git commit -m "docs: present project as AI sports analytics"
```

---

### Task 8: Verificación integral y limpieza opcional de la base externa

**Files:**
- Verify: all production and test files changed in Tasks 1–7
- Do not commit: `sports-data/sports.db` backup or migrated external DB

**Interfaces:**
- Produces: evidencia final de suite, navegación, integridad SQLite y scan terminológico.

- [ ] **Step 1: Ejecutar toda la suite**

Run:

```powershell
$env:PYTHONPATH='src'; python -m unittest discover -s tests -v
```

Expected: PASS, cero errores y cero fallos.

- [ ] **Step 2: Verificar importación y compilación**

Run:

```powershell
python -m compileall -q app.py src scripts
```

Expected: exit code 0.

- [ ] **Step 3: Ejecutar smoke de Streamlit**

Run:

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_streamlit_smoke tests.test_app_contract -v
```

Expected: todas las secciones nuevas abren sin `app.exception`.

- [ ] **Step 4: Verificar integridad y contenido de la base principal**

Ejecutar el comando de auditoría del script de migración en modo `--check-only`. Confirmar `PRAGMA integrity_check=ok`, `foreign_key_check` vacío, cero cuotas y los invariantes deportivos registrados en el reporte previo.

- [ ] **Step 5: Limpiar la base externa de forma recuperable**

Copiar `sports-data/sports.db` a un backup fechado fuera de Git. Eliminar únicamente las 32 filas `odds_snapshots` de OddsPapi, las dos fuentes stale no compartidas y el proveedor `oddspapi_winamax`, dentro de una transacción. Confirmar que los recuentos y hashes de `statistics`, `lineups`, `availability`, `events`, `participants`, `sources` deportivas y `provider_mappings` no cambian.

- [ ] **Step 6: Ejecutar scan final del repositorio**

Run:

```powershell
rg -n -i "winamax|bookmaker|decimal_odds|manual_odds|expected_value|ranking ev|oddspapi|the_odds_api|mercados y ev|cuota justa" app.py README.md src scripts tests design_system docs
```

Expected: solo la especificación y este plan contienen referencias históricas necesarias; ningún archivo activo de producto o código las contiene.

- [ ] **Step 7: Revisar el diff y cambios del usuario**

Run:

```powershell
git diff --check
git status --short
```

Expected: sin errores de whitespace; confirmar que ningún JSON del usuario fue alterado y que la modificación de `worldcup.sqlite` corresponde únicamente a la migración aprobada además de los cambios preexistentes.
