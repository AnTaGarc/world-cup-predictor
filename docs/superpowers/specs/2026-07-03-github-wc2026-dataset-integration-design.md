# Integración del dataset mominullptr/FIFA-World-Cup-2026-Dataset

Fecha: 2026-07-03
Estado: propuesta de diseño (pendiente de revisión del usuario)

## 1. Contexto y motivación

El pipeline actual del Mundial 2026 obtiene datos de tres orígenes distintos:

- `swaptr_wc2026_matches` / `swaptr_wc2026_teams` / `swaptr_wc2026_players` (Kaggle, dataset del usuario `swaptr`). Aporta calendario, marcadores y estadísticas agregadas de equipo y jugador.
- `martj42_world_schedule` (raw en GitHub). Se usa como espejo del calendario cuando Kaggle falla.
- Carga manual (captura de imagen / entrada por UI) para: cierres oficiales, estadísticas por periodo (1ªP/2ªP/prórroga), tandas de penaltis (con `transfermarkt_penalties.py` y `espn_shootouts.py` como asistencia).

Se ha encontrado un dataset externo, `github.com/mominullptr/FIFA-World-Cup-2026-Dataset`, mantenido durante el propio Mundial y publicado bajo CC0. Aporta información que hoy no existe en el pipeline y que sería costoso reconstruir a mano:

- `match_events.csv`: timeline al minuto de goles, asistencias, tarjetas amarillas, rojas y revisiones VAR.
- `matches_detailed.csv` / `matches.csv`: incluye `home_xg`, `away_xg`, `home_goalkeeper`, `away_goalkeeper`, `player_of_the_match_name` y `referee_name` por partido.
- `match_team_stats.csv`: posesión, tiros, tiros a puerta, córners, faltas, offsides, paradas y jugador del partido por equipo, con `data_source` y `last_updated`.
- `match_lineups.csv`: titular vs banquillo y minutos jugados por jugador y partido, con `tactical_position`.
- `player_stats.csv`: acumulado del torneo con `average_rating`, `penalty_goals`, `clean_sheets`, `goals_conceded`.
- `referees.csv`: árbitros con `avg_cards_per_game` histórico.
- `teams.csv`: `fifa_ranking_pre_tournament`, `elo_rating` y `manager_name` por selección.

Verificación previa realizada (2026-07-03) contra la BD local:

- Cuatro partidos de dieciseisavos ya `verified_user_capture` (Sudáfrica-Canadá 0-1, Brasil-Japón 2-1, Alemania-Paraguay 1-1, USA-Bosnia 2-0) coinciden marcador a marcador con `matches_detailed.csv`.
- `match_events.csv` de Brasil-Japón reconstruye el 2-1 con los goles a los minutos 29', 56' y 90'.
- Alemania-Paraguay muestra eventos hasta el minuto 120 (varias tarjetas de prórroga), lo que confirma que el dataset cubre prórroga a nivel de eventos.
- Ausencia confirmada: el dataset no modela tandas de penaltis, ni identifica explícitamente "decidido en tanda", ni distingue prórroga de tiempo añadido (`minute` es entero sin `period`).
- Tamaño del archivo más grande consultado (`match_events.csv`) ≈ 16 KB. Todo el conjunto se puede descargar por HTTP en segundos y almacenar in-memory sin problema.

Limitaciones/riesgos identificados del dataset:

- Mantenedor único (commits diarios manuales vía `update_dataset.py`). No hay garantía institucional de continuidad.
- `avg_cards_per_game` en `referees.csv` es un único número global por árbitro, sin desglose por competición ni muestra declarada. Debe tratarse como *prior* débil, no como estadística acreditada.
- `matches_detailed.csv` reporta el marcador final tras 90' o tras 120', pero **no** distingue formalmente si hubo prórroga o tanda. La única señal débil es la presencia de eventos con `minute > 90` (y para partidos que van a tanda: coincidencia de marcador con el resultado del cuadro tras 120' completos).
- Los `player_id` y `team_id` del dataset son internos y no coinciden con los IDs de nuestra BD.
- Los nombres pueden variar (verificado: "Cabo Verde" vs "Cape Verde"; "Côte d'Ivoire" vs "Cote d'Ivoire").

Objetivo global de la integración (aprobado por el usuario, Opción C):

Convertir este dataset en la **fuente automática y primaria** para todo el contexto de partido que hoy es manual (eventos al minuto, árbitro asignado, faltas/offsides/paradas/POTM, stats acumuladas del torneo, ELO/ranking/seleccionador), manteniendo swaptr/martj42 como fallback de calendario/resultado, sin tocar el marcador oficial (`verified_user_capture`) ni la infraestructura de penaltis. El marcador nuevo se usa como **verificación cruzada automática** y como candidato preconfirmado en la UI, pero nunca sobreescribe silenciosamente.

## 2. Alcance por fases

El trabajo se divide en cuatro fases independientes. Cada una tiene su propio plan de implementación, tests y verificación. El spec detalla las cuatro para dar visibilidad completa; **solo la Fase 0 pasa a plan de implementación de forma inmediata**. Las fases 1-3 se re-especificarán antes de implementarse (los detalles aquí son de diseño, no cerrados).

Dependencias:

```
Fase 0 (cimentación)  →  Fase 1 (flujo de partido)
                      →  Fase 2 (stats enriquecidas)
                      →  Fase 3 (modelo tarjetas árbitro)
```

Fases 1, 2 y 3 son independientes entre sí una vez completada la 0. Pueden ejecutarse en cualquier orden o en paralelo.

## 3. Diseño transversal (aplica a todas las fases)

### 3.1 Fuente y descarga

- Endpoint base: `https://raw.githubusercontent.com/mominullptr/FIFA-World-Cup-2026-Dataset/main/<archivo>.csv`.
- Fetch mediante `requests.get(..., timeout=30)`, mismo patrón que `martj42_world_schedule` en `world_cup_data.py`.
- Versionado del snapshot: usar el SHA del último commit de `main` (`GET https://api.github.com/repos/mominullptr/FIFA-World-Cup-2026-Dataset/commits/main`) como `provider_version`. Formato: `sha:<7 chars>/parser-<N>`.
- `updated_at` en `DatasetDownload` = `commit.committer.date` del commit `main`, no `datetime.now()`. Así cache invalidation es explícito y auditable.
- Un único fetch al `commits/main` por ciclo, compartido por todos los proveedores nuevos (se pasa a los fetchers vía closure/parámetro para no repetir la llamada).

### 3.2 Módulo nuevo: `github_wc2026_dataset.py`

Paralelo a `world_cup_data.py`. Contiene:

- Constante `GITHUB_DATASETS` con el mapeo `provider_id -> ruta CSV`.
- `PARSER_VERSION = "1"` local al módulo (no compartido con `world_cup_data.py`).
- `fetch_github_wc2026_dataset(provider_id, commit_sha=None, commit_date=None)` — factoría de `DatasetDownload`. Si `commit_sha` no se provee, resuelve él mismo consultando la API de GitHub.
- Parsers puros (`parse_*_rows(csv_text) -> list[dict]`), uno por provider_id, siguiendo el estilo de `parse_player_rows` / `parse_team_rows` / `parse_match_rows` existentes (uso de `_rows`, `_first`, `_number`).
- `import_github_wc2026_download(repository, download, imported_at_utc)` — importador central que despacha por `provider_id` al método `replace_*` correspondiente del repositorio (nuevos, ver §3.4).

Los parsers **no** aplican reconciliación de IDs ni de nombres. Emiten filas planas con los nombres originales del dataset. La reconciliación vive en la capa de repositorio (§3.5).

### 3.3 Registro en `daily_refresh`

Se amplía `DEFAULT_PROVIDERS`:

```python
DEFAULT_PROVIDERS = (
    "swaptr_wc2026_matches",
    "swaptr_wc2026_teams",
    "swaptr_wc2026_players",
    # nuevos
    "github_wc2026_events",
    "github_wc2026_team_stats",
    "github_wc2026_lineups",
    "github_wc2026_referees",
    "github_wc2026_player_stats",
    "github_wc2026_teams",
    "github_wc2026_matches",
)
```

Cada nuevo provider comparte el ciclo actual de `ensure_current_world_cup_data`. El fetcher que se le pasa al ciclo despacha por prefijo (`swaptr_*`, `martj42_*`, `github_*`) al módulo correcto. En la UI el badge "Datos diarios" ya agrega estados; no requiere cambios de contrato.

### 3.4 Tablas nuevas

Se añaden a `database.py` (`CREATE TABLE IF NOT EXISTS`, con índices). Ninguna tabla existente se modifica en Fase 0.

```sql
CREATE TABLE IF NOT EXISTS gh_match_events (
    provider_id TEXT NOT NULL,
    external_event_id INTEGER NOT NULL,
    external_match_id INTEGER NOT NULL,
    match_id INTEGER,            -- FK reconciliada; NULL hasta que se resuelva
    minute INTEGER NOT NULL,
    period TEXT NOT NULL,        -- derivado: 'first_half' / 'second_half' /
                                 -- 'et_first' / 'et_second' según regla §3.6
    event_type TEXT NOT NULL,    -- goal, assist, yellow_card, red_card, var_review
    external_team_id INTEGER NOT NULL,
    team_id INTEGER,             -- FK reconciliada; NULL hasta que se resuelva
    external_player_id INTEGER NOT NULL,
    player_id INTEGER,           -- FK reconciliada; NULL hasta que se resuelva
    provider_version TEXT NOT NULL,
    imported_at_utc TEXT NOT NULL,
    PRIMARY KEY(provider_id, external_event_id)
);
CREATE INDEX IF NOT EXISTS idx_gh_match_events_match ON gh_match_events(match_id);
CREATE INDEX IF NOT EXISTS idx_gh_match_events_ext_match ON gh_match_events(external_match_id);

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
    player_of_the_match TEXT,       -- literal del dataset; opcional resolver a player_id
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

-- Alias entre entidades del dataset y de la BD (multi-purpose; también sirve
-- para futuras integraciones).
CREATE TABLE IF NOT EXISTS entity_alias_map (
    entity_type TEXT NOT NULL,       -- 'team' | 'player' | 'referee'
    source_key TEXT NOT NULL,        -- 'github_wc2026'
    external_id TEXT NOT NULL,       -- external_id como string
    internal_id INTEGER NOT NULL,    -- teams.id / players.id / (para referees, se
                                     -- reutiliza gh_referees.external_referee_id)
    confirmed_by TEXT NOT NULL,      -- 'auto_exact_match' / 'auto_normalized' /
                                     -- 'user_confirmed'
    confirmed_at_utc TEXT NOT NULL,
    PRIMARY KEY(entity_type, source_key, external_id)
);

-- Cola de discrepancias entre marcador propio y marcador del dataset.
CREATE TABLE IF NOT EXISTS gh_score_verifications (
    match_id INTEGER NOT NULL,
    provider_version TEXT NOT NULL,
    dataset_home_score INTEGER,
    dataset_away_score INTEGER,
    local_home_score INTEGER,
    local_away_score INTEGER,
    status TEXT NOT NULL,            -- 'match' | 'mismatch' | 'no_local_result'
    detected_at_utc TEXT NOT NULL,
    reviewed_by TEXT,
    reviewed_at_utc TEXT,
    resolution TEXT,                 -- 'kept_local' | 'accepted_dataset' | 'wontfix'
    PRIMARY KEY(match_id, provider_version)
);
```

Convención: `external_*` = ID/nombre tal como viene del dataset; `*_id` sin prefijo = FK a la BD nuestra tras reconciliación. Filas se pueden importar con `*_id` NULL y resolverse en un paso posterior o durante la primera consulta.

### 3.5 Reconciliación de entidades

Orden de resolución en el importador:

1. **Equipos**: por `fifa_code` (`teams.csv` trae `fifa_code`; nuestra tabla `teams` ya tiene `code` para muchos). Si no hay `code`, por igualdad normalizada de nombre (`unicodedata.normalize("NFKD", name).casefold().strip()`, quita apóstrofes y guiones). Si aún así no coincide, la fila queda con `team_id = NULL` y se registra en la tabla `entity_alias_map` marcada como pendiente.
2. **Árbitros**: se resuelven contra `gh_referees.external_referee_id`. No hay tabla `referees` en nuestra BD; el `gh_referees` actúa como fuente canónica. `entity_alias_map` con `entity_type='referee'` solo se usa si hay que fusionar dos árbitros con nombres distintos (raro).
3. **Jugadores**: por `(team_id_resuelto, casefold(player_name))` contra `players`. Si no encaja, se registra en `entity_alias_map` como pendiente. Los eventos con `player_id = NULL` **no se descartan**: siguen alimentando el timeline de equipo (goles/tarjetas por minuto), solo se pierde la atribución individual hasta que el usuario confirme el alias en la UI.

Los alias resueltos se cachean en `entity_alias_map`. La resolución siguiente hace un solo `SELECT` global por tipo y evita consultas por fila.

UI para resolver pendientes: nueva sub-página o expander bajo "Datos diarios" mostrando entidades sin alias. Cada fila con `[confirmar]` / `[ignorar]`. Esta UI se detalla en la Fase 0 pero puede quedar minimalista.

### 3.6 Derivación de `period` en eventos

El dataset da solo `minute` entero (sin distinguir 45+3' de "minuto 48"). Aproximación segura:

- `1 ≤ minute ≤ 45` → `first_half`
- `46 ≤ minute ≤ 90` → `second_half`
- `91 ≤ minute ≤ 105` → `et_first`
- `106 ≤ minute ≤ 120` → `et_second`

Se documenta como aproximación: un gol al 45+3' cae dentro de `first_half` (correcto), pero un gol al 90+5' cae en `et_first` (incorrecto). En la práctica los tiempos añadidos suelen agregarse al minuto real (`45` para gol al 45+3, `90` para gol al 90+5), no numeradores >90 en tiempos regulares, así que el sesgo esperado es bajo. Se marca como TODO conocido para revisar tras el primer backfill contra 10 partidos.

### 3.7 Verificación cruzada de marcador

Cada vez que `gh_matches` reciba una fila para un `match_id` que **también** tenga fila en `match_results` con `source_type = 'verified_user_capture'`:

- Se compara `(dataset_home_score, dataset_away_score)` con `(match_results.goals_a, match_results.goals_b)` (respetando orden `team_a`/`team_b` de la BD tras resolver home/away).
- Si coinciden: `gh_score_verifications.status = 'match'`.
- Si difieren: `status = 'mismatch'`. La UI de "Datos diarios" muestra el conjunto de mismatches en el expander de errores existente. El usuario resuelve manualmente (aceptar dataset → sobreescribe `match_results`; mantener local → marca como wontfix). Nunca autocorregir.
- Si aún no hay `match_results`: `status = 'no_local_result'`. El dataset queda como *candidato* preconfirmado; la UI de cierre postpartido lo ofrece prerelleno pero exige clic explícito de "confirmar" (nueva pieza pequeña en `ui/knockout_settlement.py` / `ui/postmatch_capture.py`).

Este flujo reutiliza el patrón de "no autocorregir discrepancias" que ya existe para las validaciones opcionales de 120' en `match_phases.py`.

### 3.8 Configuración de red y modo offline

- Backoff: reutilizar el `1h` ya presente en `daily_refresh` para fallos.
- Timeouts: 30s para CSVs (todos son <1MB), 15s para `commits/main`.
- Modo `WCP_OFFLINE=1` (variable de entorno existente): saltar todos los `github_*` como ya se hace con `swaptr_*`.

### 3.9 Política Git de los datos importados

- Todas las tablas `gh_*` viajan dentro de `data/worldcup.sqlite` — ya cubiertas por el push seguro existente.
- El SHA del último commit importado se registra en `dataset_snapshots.provider_version`. Fácil de auditar retroactivamente qué versión del dataset produjo qué fila.
- No se cachea el CSV crudo en disco (solo se guarda `content_sha256` en `dataset_snapshots`, siguiendo el patrón actual).

## 4. Fase 0 — Cimentación (spec detallado)

Objetivo: importar el dataset diariamente y disponibilizarlo en BD, con reconciliación de entidades, verificación cruzada de marcador y UI mínima para revisar discrepancias/alias pendientes. **No** cambia todavía ningún modelo predictivo. Tras esta fase el sistema debería ir tan rápido como antes, con datos nuevos disponibles vía SQL para experimentación de las fases 1-3.

### 4.1 Entregables

1. Migración: siete tablas nuevas (`gh_*`, `entity_alias_map`, `gh_score_verifications`) creadas idempotentemente en el arranque, como el resto.
2. Módulo `src/wcpredict/github_wc2026_dataset.py`:
   - Fetch con resolución de commit SHA compartido.
   - Parsers puros por CSV.
   - Importador central `import_github_wc2026_download`.
3. Métodos en `repository.py`:
   - `replace_gh_events(provider_id, rows, version, imported_at_utc)`
   - `replace_gh_team_stats(...)`, `replace_gh_lineups(...)`, `replace_gh_matches(...)`, `replace_gh_referees(...)`, `replace_gh_player_stats(...)`, `replace_gh_teams(...)`
   - `resolve_pending_aliases()`: pasa por todas las filas con FK NULL y las intenta resolver de nuevo (se llama al final del ciclo de import).
   - `list_pending_aliases(entity_type)` / `confirm_alias(entity_type, external_id, internal_id, actor)`.
   - `record_score_verification(match_id, provider_version, ...)` / `list_score_mismatches()`.
4. Ampliación de `daily_refresh`:
   - `DEFAULT_PROVIDERS` con los 7 nuevos.
   - Fetcher factoría en `services.py` que enrute por prefijo y reutilice el commit SHA compartido dentro del ciclo.
5. UI:
   - Bajo el expander de "Datos diarios" en `ui/pages.py`: contador de mismatches y alias pendientes, con enlace a una sub-sección.
   - Sub-sección minimalista con una `st.dataframe` + botones `confirmar`/`ignorar` por fila. Persistencia inmediata en BD.
6. Backfill:
   - Comando one-shot `python scripts/backfill_github_wc2026.py` que hace un fetch, importa y ejecuta `resolve_pending_aliases`. Debe ser idempotente: correr dos veces no duplica.
7. Tests (`unittest`):
   - `test_github_wc2026_dataset.py`: cada parser sobre fixtures CSV pequeños.
   - `test_github_wc2026_import.py`: import completo contra BD in-memory, verificando:
     - reconciliación exacta por `fifa_code`.
     - reconciliación por normalización (fixture con "Cabo Verde" mapea a `teams` con nombre "Cape Verde").
     - eventos con `player_id` NULL persisten con `team_id` resuelto.
     - `entity_alias_map` se rellena correctamente.
     - re-import con mismo SHA no muta nada (idempotencia).
     - re-import con SHA nuevo actualiza sin duplicar (upsert por PKs).
   - `test_score_verification.py`: casos `match` / `mismatch` / `no_local_result`.
   - `test_period_derivation.py`: minutos frontera (1, 45, 46, 90, 91, 105, 106, 120).
   - `test_daily_refresh.py`: extender el escenario mixto para incluir un `github_*` que falla y otro que va bien (`partial` sigue clasificándose correctamente).
   - `tests/test_app_contract.py`: extender contrato de la UI para incluir el expander de "revisar dataset externo".
8. Documentación:
   - README: sección "Fuente externa: mominullptr/FIFA-World-Cup-2026-Dataset" con explicación de qué automatiza y qué sigue siendo manual.
   - Este spec, más el plan de implementación que se genera después.

### 4.2 Estimación de esfuerzo (Fase 0)

Rough: 6-10 archivos nuevos, ~800-1200 LOC, ~35-45 tests. Ejecución iterativa: primero parsers + import + tests (sin UI), luego reconciliación, luego verificación cruzada, luego UI. Cada capa entregable y committeable por separado.

### 4.3 Criterios de aceptación (Fase 0)

- `ensure_current_world_cup_data` en un entorno limpio importa los 7 CSV con `status="updated"` en el primer ciclo y `status="current"` en el segundo (misma versión).
- Backfill sobre los ~82 partidos ya jugados produce ≥95% de eventos con `player_id` resuelto sin intervención del usuario (medido en tests contra fixture representativo).
- Alemania-Paraguay tiene su `gh_match_events` con 10 filas (verificado durante el diseño), y todas las tarjetas de minuto ≥106 quedan clasificadas en `period='et_second'`.
- Al menos una discrepancia sintética inyectada en el fixture aparece como `mismatch` en `gh_score_verifications` y se muestra en la UI.
- Push seguro sigue superando `PRAGMA integrity_check` con las tablas nuevas presentes.

### 4.4 Riesgos y mitigaciones (Fase 0)

- **Rate limit no autenticado de la API de GitHub** (60 req/h por IP para `/commits/main`). Mitigación: una única llamada por ciclo (todos los `github_*` comparten el mismo SHA), y ante 403 caer al `Last-Modified` HTTP del raw como fallback.
- **Cambios de esquema del dataset upstream** (mantenedor añade/renombra columnas). Mitigación: parsers usan `_first(row, ...)` con múltiples alias, y los tests fallan ruidosamente al primer cambio no manejado.
- **Crecimiento del SQLite**: 82 partidos × ~10-20 eventos + team_stats + lineups ≈ ~4000-6000 filas nuevas. Impacto sobre 63MB actuales: despreciable (<1MB). No compromete el límite de 100MB.
- **Coste de descarga diaria**: ~50KB totales × 1 vez/día → despreciable.

## 5. Fase 1 — Capa de ajuste por forma del torneo (diseño)

### 5.1 Motivación y planteamiento

El dataset externo solo cubre el Mundial 2026, por lo que los eventos al minuto no existen para partidos históricos previos. Metida directamente al vector de entrenamiento de `outcome_ml_deep`, esa asimetría degrada el modelo (features NaN en la mayoría del training set). Pero la forma actual del equipo *pesa mucho* en cómo se desarrolla el resto del torneo, y descartar la señal por dificultad técnica es tirar información valiosa.

Solución: **capa de ajuste posterior** al modelo base, no columnas nuevas en el entrenamiento. La forma se aplica como un desplazamiento en el log-odds de la predicción:

```
logit(p_final_home) = logit(p_base_home) + α · adjustment(team_a, team_b)
```

donde:

- `p_base_home` = probabilidad producida por `outcome_ml_deep` sin tocar.
- `adjustment(·)` = escalar en [-1, +1] a favor del equipo local, suma ponderada de deltas derivados de eventos y stats del propio Mundial.
- `α` = coeficiente global calibrado por leave-one-out CV sobre los partidos ya finalizados del propio Mundial. Un único grado de libertad efectivo → estadísticamente sensato con 82+ observaciones.

El mismo `adjustment` se aplica análogamente a `logit(p_away)`; `p_draw` se recompone renormalizando las tres probabilidades para que sumen 1.

### 5.2 Features usadas dentro de `adjustment`

Cada feature es un delta home-vs-away, normalizado al rango [-1, +1]. Todas se derivan de eventos y stats del propio Mundial 2026 acumulados **estrictamente antes** del kickoff del partido a predecir:

1. **`goal_timing_delta`**: diferencia entre `mean_scoring_minute` de home y away, normalizada. Capta "equipo que golpea temprano" vs. "equipo que decide tarde". Robusto en muestra pequeña porque cada gol aporta un dato entero.
2. **`late_dominance_delta`**: (goles home - encajados home entre 75-90) - (goles away - encajados away entre 75-90), normalizado por número de partidos. Señal muy específica que agregados xg medios no capturan.
3. **`fatigue_delta`**: proxy de fatiga combinando `minutes_played_extra_time_last_14d` + `days_since_last_match_inverted`. Home penalizado si viene de prórroga reciente. Relevante en dieciseisavos consecutivos donde ya ha pasado.
4. **`inferiority_time_delta`**: minutos totales jugados con inferioridad numérica en el torneo (por diferencia de rojas × minutos restantes). Persistente y observable en `gh_match_events`.
5. **`recent_momentum_delta`**: resultado del último partido codificado con peso extra en los últimos 15' (equipo que ganó al final → +1, que empató remontando → +0.5, que perdió al final → -1, etc.). Un solo byte con alta densidad informativa.
6. **`discipline_delta`**: tarjetas por partido en el torneo, dividido por baseline poblacional. Se conecta más tarde con Fase 3 (árbitro asignado modula la sensibilidad).
7. **`xg_delta_recent`**: `(xg_for − xg_against)` acumulado en el torneo dividido por partidos. Usa `gh_matches.home_xg`/`away_xg`. Capta dominio real vs. suerte.

Cada feature contribuye con **igual peso normalizado** al `adjustment`. Los pesos internos NO se aprenden con ML (con 82 partidos y 7 features se sobreajustaría trivialmente); el único parámetro aprendido es la α global.

### 5.3 Shrinkage por muestra por equipo

Un equipo con 2 partidos jugados no tiene la misma fiabilidad de forma que uno con 5. Se aplica peso multiplicativo:

```
weight(team) = n_matches / (n_matches + k)      con k = 3
adjustment_effective = α · min(weight_home, weight_away) · Σ features
```

Efecto práctico:

- 1 partido jugado: `weight ≈ 0.25` → contribución mínima.
- 3 partidos (fase de grupos completa): `weight = 0.5` → contribución media.
- 5 partidos (equipo en cuartos): `weight ≈ 0.63` → contribución máxima esperada en este Mundial.

Así el ajuste crece con el torneo y se degrada suavemente cuando la muestra es muy escasa.

### 5.4 Calibración de α

- Módulo nuevo `tournament_form_adjustment.py`:
  - `build_form_features(team_id, before_kickoff_utc) -> dict[str, float]` — devuelve las 7 features del equipo hasta `before_kickoff_utc` (exclusive).
  - `build_match_adjustment(team_a_id, team_b_id, kickoff_utc) -> tuple[float, float]` — retorna `(adjustment_score, effective_weight)`.
  - `calibrate_alpha(matches_iter) -> float` — leave-one-out CV sobre partidos ya finalizados del torneo. Minimiza log-loss agregado.
- Persistencia: la α calibrada se guarda en tabla nueva `outcome_adjustment_calibrations(model_version, calibrated_at_utc, alpha, sample_size, cv_log_loss)`. Se recalibra automáticamente cada vez que llega un nuevo `verified_user_capture`.
- Salvaguarda: si α ≤ 0 (la capa empeora el modelo en CV), se persiste como 0 y la capa se desactiva sola. Fail-safe honesto.
- El coeficiente α y el `adjustment_score` de cada partido se muestran en la UI de predicción para auditoría: "Ajuste por forma del torneo: +0.08 logit (α=0.42, peso 0.55)".

### 5.5 Integración con el pipeline de predicción

- `services.py` predice como hasta ahora con `outcome_ml_deep`, luego llama a `apply_tournament_form_adjustment(p_base, match)` que devuelve `p_final`.
- Nunca modifica `outcome_ml_deep.joblib` ni su vector de features → cero riesgo de regresión sobre el modelo base.
- Si `α = 0` en la calibración actual, `p_final = p_base` (paso a través, sin coste).
- El desglose por feature (contribución de cada uno al `adjustment_score`) se persiste en `prediction_snapshots.explanation` para auditoría posterior.

### 5.6 Contexto previo a la tanda

Independientemente del modelo de resultado, algunas de las mismas features alimentan el `PenaltyContext` como *priors* débiles:

- `fatigue_delta` y `inferiority_time_delta` se pasan al modelo Monte Carlo v4 como perturbaciones del baseline de conversión/parada.
- Peso deliberadamente pequeño: la evidencia histórica de lanzador/portero sigue dominando (patrón ya establecido en `penalty_history_model.py`).
- Cambio de fingerprint del contexto → invalida precálculos antiguos automáticamente, como ya ocurre.

### 5.7 Verificación (Fase 1)

- **Test estadístico**: leave-one-out CV sobre todos los partidos finalizados del Mundial. Métrica: log-loss `p_base` vs `p_final`. Criterio duro: `α > 0` y mejora ≥3% relativa en log-loss, o α se persiste a 0.
- **Test unitario A**: equipo con 0 partidos jugados → weight 0 → adjustment_effective = 0.
- **Test unitario B**: dos equipos con historia idéntica pero uno viene de prórroga hace 3 días → `fatigue_delta` refleja la diferencia con signo correcto.
- **Test unitario C**: `build_form_features` con `before_kickoff_utc` estrictamente anterior a un evento no incluye ese evento (no leakage temporal).
- **Test de contrato UI**: la vista de predicción muestra el desglose del ajuste cuando `α > 0` y lo oculta cuando `α = 0`.

### 5.8 Riesgos (Fase 1)

- **Sobreajuste a los partidos observados hasta la fecha de calibración**. Mitigación: leave-one-out (no k-fold) + un único parámetro aprendido + shrinkage por muestra. Si el usuario ve que α oscila mucho entre calibraciones, el diseño está fallando y hay que subir `k` (shrinkage) o congelar α tras la fase de grupos.
- **Fallo silencioso de la fuente**: si `gh_match_events` no llega a estar cargado para un partido de un equipo, las features de ese equipo se recalculan sin ese partido → subestima weight, no rompe.
- **Interpretación**: mostrar `+0.08 logit` es opaco para el usuario final. La UI debe traducirlo a "+2.0 puntos porcentuales sobre victoria home base X%" para que sea auditable.

## 6. Fase 2 — Stats enriquecidas (diseño)

Objetivo: llevar las nuevas columnas de `gh_match_team_stats` (fouls, offsides, saves, POTM) y `gh_player_stats` (average_rating, penalty_goals, clean_sheets, goals_conceded) al modelo y a la UI.

### 6.1 Features de equipo

Nuevas columnas agregadas por equipo/torneo:

- `fouls_per_match`, `offsides_per_match`, `saves_per_match_gk`.
- `potm_share`: fracción de partidos en los que un jugador del equipo fue POTM.
- `elo_pre_tournament` (de `gh_teams`): reemplaza fuente ELO actual como *prior* estable.
- `fifa_ranking_pre_tournament`: idem.

Integración: se añaden al `team_profile` (usado por `outcome_ml_deep` y matchup features). El vector `DEEP_FEATURES` gana ~5 columnas de diffs.

### 6.2 Features de jugador

`gh_player_stats` se cruza con nuestra tabla `players` (vía alias resuelto):

- Para lanzadores de penaltis (Fase 3 del modelo de penas): `penalty_goals` acumulado en el torneo → suma bayesiana con evidencia Transfermarkt histórica (`penalty_profiles.py`).
- Para porteros: `clean_sheets`, `goals_conceded`, `saves` → misma lógica bayesiana con la evidencia histórica de `goalkeeper_penalty_attempts`.
- `average_rating` disponible como feature de jugador para modelo de player_impact (opcional; por debajo del corte de utilidad esperada).

Integración: `penalty_profiles.build_profile(player_id, ...)` gana un parámetro opcional `current_tournament_stats: dict | None` y suma sus goles/paradas al muestreo bayesiano con un peso configurable (por defecto: mismo peso por partido que la evidencia histórica reciente).

### 6.3 UI

- Pestaña de análisis del equipo: nuevas gráficas de fouls/offsides/POTM.
- Pestaña de contexto de penaltis: mostrar `average_rating` y `penalty_goals` del torneo actual junto al histórico.

### 6.4 Verificación (Fase 2)

- Test: perfil de lanzador con evidencia histórica + 0 goles actuales debe ser ≈ igual al perfil histórico puro (baseline).
- Test: mismo lanzador con 2 goles de penalti en el torneo actual debe subir su probabilidad de gol en la tanda, con magnitud consistente con el peso configurado.
- Backtest: Brier del modelo de penaltis con y sin refuerzo del torneo, sobre las tandas del propio Mundial ya jugadas (Alemania-Paraguay, Países Bajos-Marruecos, más las que sigan) — se documentan en el spec de implementación de la fase.

## 7. Fase 3 — Modelo de tarjetas por árbitro (diseño)

Objetivo: predicción explícita de mercados de tarjetas (total tarjetas, jugador con tarjeta, roja sí/no) usando el árbitro asignado como feature.

### 7.1 Dato clave y limitaciones

`gh_referees.avg_cards_per_game` es un único número por árbitro. Se usa como *prior* débil (peso equivalente a ~5 partidos). Lo dominante debe seguir siendo:

- Propensión de tarjetas de los equipos (ya presente vía `team_match_stats.yellow_cards`).
- Perfil disciplinario del jugador (ya presente en `player_match_stats.yellow_cards`).

Modelo: Poisson jerárquico por partido con tasa

```
λ_cards = base * team_a_prop * team_b_prop * referee_multiplier
```

donde `referee_multiplier = avg_cards_per_game / mean_avg_cards_per_game`.

Roja: modelo Bernoulli separado con tasa base baja, mismo `referee_multiplier` como *prior* débil.

### 7.2 Integración

- Módulo nuevo `referee_cards_model.py`.
- `market_catalog.py`: nuevos mercados "Total tarjetas O/U", "Roja sí/no", "Jugador con tarjeta" (este último saldría de la propensión individual x minutos esperados x multiplicador de árbitro).
- Se calibra contra los partidos ya jugados en `gh_matches` (donde tenemos árbitro real, tarjetas reales y las stats de equipo previas).

### 7.3 Verificación (Fase 3)

- Cross-validation leave-one-out sobre los 82+ partidos ya jugados. Comparar log-loss contra baseline sin árbitro.
- Requiere ≥15 partidos por árbitro para que la señal sea distinguible del ruido; con muestra menor, `referee_multiplier` colapsa a 1.0 (regularización explícita).

### 7.4 Riesgos (Fase 3)

- `avg_cards_per_game` sin fuente clara puede introducir sesgo sistemático. Mitigación: peso pequeño del multiplicador (`shrinkage` fuerte hacia 1.0 salvo evidencia interna posterior).
- Puede que se necesite re-estimar `avg_cards_per_game` a partir de los propios partidos del Mundial. Si al terminar la fase de grupos hay ≥3 partidos por árbitro, sustituir el valor upstream por el interno.

## 8. Rollout y orden operativo

Recomendación revisada tras la reformulación de la Fase 1 como capa de ajuste:

1. **Semana 1** — Fase 0 (cimentación). Ejecutar backfill. Confirmar que no hay regresiones y que la UI muestra correctamente mismatches y alias pendientes.
2. **Semana 2** — Fase 2 (stats enriquecidas). Impacto directo en el modelo de penaltis, que tiene tandas activas ahora.
3. **Semana 3** — Fase 1 revisada (capa de ajuste por forma del torneo). Sube en prioridad porque, planteada como ajuste posterior con α calibrada y shrinkage, sí aporta señal real sin riesgo sobre el modelo base. Debe implementarse cuando ya haya al menos octavos completos para que la calibración inicial tenga muestra suficiente.
4. **Semana 4 o post-Mundial** — Fase 3 (tarjetas por árbitro). Requiere ≥15 partidos por árbitro para señal distinguible del ruido; puede que solo sea utilizable después del Mundial actual como preparación para el siguiente ciclo, o parcialmente durante semifinales/final.

Cada fase se especifica en un spec propio antes de escribir el plan de implementación. Este documento sirve de mapa maestro.

## 9. Preguntas abiertas / decisiones diferidas

- ¿Guardar copia local en disco del CSV bruto para reproducibilidad, o solo el snapshot en `dataset_snapshots`? Recomendación: solo snapshot (patrón existente).
- ¿Mostrar en la UI el POTM del dataset externo? Puede confundir si aparece antes de que el usuario haya confirmado el partido. Sugerencia: solo dentro del expander de dataset externo, no en la vista de match cerrado.
- ¿Estrategia si el mantenedor abandona el repo? Fallback ya cubierto por swaptr + martj42 para calendario/resultado; las features de eventos degradarían suavemente (todos los NaN → modelos usan valores baseline). No requiere acción reactiva.
- Uso de `market_value_eur` y `date_of_birth` de `squads_and_players.csv`: no incluido en ninguna fase por baja utilidad predictiva demostrada. Se deja como fuente disponible en BD por si aparece un caso de uso.

## 10. Siguiente paso

Aprobar este spec y escribir el **plan de implementación de la Fase 0** en `docs/superpowers/plans/2026-07-03-github-wc2026-dataset-foundations.md` mediante la skill `writing-plans`. Las fases 1-3 se re-especifican como spec propio en su momento.
