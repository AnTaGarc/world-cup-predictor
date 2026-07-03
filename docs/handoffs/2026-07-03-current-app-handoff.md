---
context_transfer_version: 1
package_id: world-cup-predictor-2026-07-03-current-state
created_at: 2026-07-03T00:00:00+02:00
source_chat: mixed
fidelity_mode: complete-visible-literal-plus-structured-summary
language_policy: preserve-original-chat-language
redaction_policy: none-literal-visible-content
search_scope: codex-local
previous_transfer: none
---

# Context Transfer Package

## 1. Executive Summary

La aplicación `world-cup-predictor` está operativa y el usuario confirma expresamente
que Streamlit funciona bien y que la supuesta lentitud de arranque fue una falsa alarma.
El repositorio principal está limpio y sincronizado con GitHub:

- raíz: `C:\Users\anton\Onedrive\Documentos\Random\world-cup-predictor`;
- rama: `main`;
- HEAD local/remoto: `927929e` (`'update:eliminatorias 01/07'`);
- remoto: `https://github.com/AnTaGarc/world-cup-predictor.git`;
- `git status`: `main...origin/main`, sin cambios pendientes.

El trabajo acumulado de esta conversación quedó integrado: carga postpartido por
periodos, validación opcional del acumulado de 120 minutos, liquidación versionada de
eliminatorias, tandas del Mundial actual, modelo de penaltis con portero titular fijo,
evidencia histórica de porteros/tandas, actualización dinámica de equipos vivos,
precálculos de penaltis, sincronización segura push/pull y diagnóstico claro de las
fuentes diarias.

Estado de datos observado el 3 de julio de 2026:

- 4.358 partidos totales;
- 82 resultados;
- 164 filas de estadísticas de equipo;
- 38 filas de estadísticas por periodo;
- 159 filas de estadísticas de jugador;
- 118.498 observaciones;
- 203 importaciones;
- 54 JSON profundos revisados versionados;
- 2.895 penaltis de lanzadores;
- 1.475 penaltis afrontados por porteros;
- 84 coberturas de competiciones históricas;
- 28 tandas históricas y 282 lanzamientos;
- 22 lanzamientos de tandas del Mundial 2026;
- 72 snapshots de predicción;
- 3.662 filas de backtest;
- 17 contextos de penaltis precalculados.

## 2. Ready-To-Continue State

Objetivo operativo actual: continuar incorporando resultados, estadísticas profundas y
liquidaciones de los dieciseisavos restantes, recalibrar y precalcular los cruces de
octavos a medida que se resuelven.

Último estado confirmado:

- la app arranca y funciona correctamente;
- todos los proveedores diarios tienen su último check en estado `ready`;
- el error transitorio 403 de Kaggle para el banco de equipos fue resuelto forzando una
  nueva descarga de la versión 28;
- la UI ahora distingue `partial` de `stale` y muestra el proveedor fallido si reaparece;
- el usuario puede sincronizar con los launchers seguros descritos más abajo;
- no hay cambios Git pendientes.

La frase anterior describe el estado observado antes de crear este paquete. Al entregarlo,
este propio archivo Markdown queda como único artefacto nuevo sin commit, salvo que el
usuario decida versionarlo posteriormente.

Siguiente paso seguro recomendado:

1. Revisar los partidos con kickoff pasado que aún figuran `scheduled`.
2. Importar solamente los cierres/resultados/deep stats que falten.
3. Resolver el cuadro.
4. Precalcular los nuevos octavos con los datos más recientes.
5. Usar `push_project.ps1`, que incluye datos duraderos y excluye temporales.

No hay una tarea de corrección activa en este momento.

## 3. Source Coverage

Fuentes consultadas para este paquete:

- conversación visible de esta sesión y resumen operativo acumulado;
- repositorio local, historial Git y ramas/worktrees;
- SQLite `data/worldcup.sqlite` mediante consultas de solo lectura;
- archivos de especificación y planes bajo `docs/superpowers/`;
- fixtures, precálculos y modelos locales;
- metadata y endpoints públicos de Kaggle inspeccionados durante el diagnóstico;
- capturas proporcionadas por el usuario en la conversación.

No existe acceso a un API de exportación completo del historial oculto de chats ni a
razonamiento interno. La sección de transcript conserva las solicitudes visibles y la
secuencia operativa recuperable, pero no pretende ser un volcado byte a byte de una
plataforma externa. El archivo inicial
`C:\Users\anton\Downloads\# Continuar conversación en Claude.txt` se mencionó al inicio,
pero no se releyó durante la generación de este paquete.

No se detectaron credenciales, tokens o secretos visibles. Se conservan rutas locales y
nombres de archivos conforme a la política literal solicitada por la skill.

## 4. Complete Visible Transcript

Transcripción visible/reconstruida de las solicitudes del usuario relevantes para el
estado actual, en orden cronológico. Se omiten mensajes puramente instrumentales del
sistema y salidas extensas de tests, que quedan resumidas en las secciones 8 y 9.

1. `Continua por donde se quedó` y confirmación `Sí`.
2. Pregunta por el contexto previo de subir deep stats por partes.
3. Reporte de discrepancia: el texto de contexto de penaltis mostraba 36,8% para Alemania,
   mientras la barra seguía 50/50.
4. Reporte de que la calibración no parecía haber cambiado; después confirmó:
   `He rebooteado el local y está todo bien`.
5. Requisito para cierres por periodos: si termina en penaltis, no debe exigirse el
   acumulado de 90 ni el acumulado de prórroga; este último debía eliminarse.
6. Aclaración: `El acumulado opcional de los 120 se puede usar para asegurar que se hayan
   recogido bien las estadísticas por partes`.
7. Solicitud de comprobar calibración, importación de estadísticas y penaltis de
   Alemania-Paraguay.
8. Solicitud de revisar Países Bajos-Marruecos, las fuentes, Bounou y el historial reciente
   de tandas.
9. Decisión: usar al portero titular para la probabilidad de tanda porque los cambios de
   portero son excepcionales.
10. Requisito general: incluir tandas recientes de las últimas competiciones y estadísticas
    de todos los porteros titulares parando penaltis, dentro y fuera de tandas.
11. Solicitud de commit y continuación de todas las tareas.
12. Restricción de alcance inicial: trabajar solo con equipos vivos; se excluyeron
    Alemania, Sudáfrica, Países Bajos y Japón tras sus eliminaciones.
13. Aprobación del diseño e implementación directa.
14. Solicitud reiterada `Continua`.
15. Discusión sobre qué datos locales debían subirse: el usuario aclaró que se refería a
    deep stats, resultados y datos persistentes, y solo los que faltasen.
16. Pregunta por cómo cambiaron los porcentajes de penaltis tras la actualización.
17. Comprobación de que las tareas anteriores estaban completas y de que las tandas del
    Mundial actual quedaban guardadas y afectaban a predicciones posteriores.
18. Solicitud de crear programas seguros para push y pull que nunca subieran cachés,
    logs ni temporales, pero sí estadísticas, marcadores, deep stats, modelos, fixtures,
    evidencias y precálculos.
19. Decisión para pull: si hay cambios locales duraderos, detenerse y exigir push primero;
    no usar stash/rebase automático.
20. Aprobaciones sucesivas de diseño, especificación e implementación en la misma sesión.
21. Reporte visual: `Calendario diario: Obsoleto`, `Actualizadas 0`, `Con error 1`.
22. Aclaración posterior: no quería solo cambiar la etiqueta, sino conseguir que el
    proveedor dejase de fallar y el estado quedase current.
23. Se forzó la actualización cuando Kaggle volvió a responder; resultado sin fallos.
24. Reporte posterior: `Le está costando mucho a streamlit cargar la app, q ha pasado?`.
25. Corrección final del propio usuario para este handoff:
    `Por cierto, no estaba costandole arrancar a la app, todo va bien`.
26. Solicitud actual: `Genera un handoff completo de la situación actual de la app, todos
    los cambios y donde estamos ahora mismo.`

## 5. Decisions Made

### Estadísticas postpartido y periodos

- Si el partido termina en 90 minutos, el acumulado de 90 es suficiente y obligatorio.
- Si hay prórroga o tanda, se requieren los cuatro periodos atómicos:
  primera parte, segunda parte, primera parte de prórroga y segunda parte de prórroga.
- Los acumulados ocultos de 90 y prórroga no bloquean el cierre de un partido con prórroga.
- El acumulado completo de 120 minutos es opcional y se usa como control cruzado.
- La comparación del total de 120 suma las cuatro partes atómicas para métricas aditivas.
- No se corrigen automáticamente discrepancias: deben quedar visibles para revisión.

### Liquidación de eliminatorias

- Los cierres son versionados; una corrección desactiva la versión anterior.
- Marcador oficial y resultado de entrenamiento se separan: la forma del modelo usa los
  90 minutos cuando corresponde, aunque el marcador oficial incluya prórroga.
- La tanda persiste secuencia, equipo, lanzador, portero y resultado de cada lanzamiento.
- Solo la versión activa de la tanda alimenta auditoría y evidencia futura.

### Modelo de penaltis

- Modelo operativo: `path-monte-carlo-v4`.
- El portero titular confirmado queda fijo durante la tanda, salvo que exista un cambio
  de portero confirmado explícitamente.
- Los jugadores de campo sí se simulan según rol, titularidad, sustituciones y marcador.
- La evidencia del portero se almacena separada de los intentos del lanzador.
- Solo `saved` acredita parada; fuera/poste cuenta como penalti afrontado, no como parada.
- Los fallos genéricos de ESPN se almacenan como `missed`, sin inventar portero ni causa.
- Se usan las últimas tres competiciones sénior oficiales seleccionadas por equipo.
- Los equipos objetivo se obtienen dinámicamente del cuadro no finalizado.
- Las tandas del Mundial actual solo afectan a partidos posteriores mediante corte temporal.
- Cambiar la evidencia modifica el fingerprint e invalida precálculos antiguos.

### Fuentes de tandas y porteros

- Transfermarkt se usa para historial individual y penaltis afrontados por porteros.
- ESPN se usa como fuente reproducible de secuencia de tandas históricas cuando ofrece
  lanzador y gol/fallo.
- FIFA, UEFA, CAF, CONMEBOL, CONCACAF y AFC se usan para delimitar competiciones oficiales.
- No se atribuye una parada cuando la fuente solo dice `missed`.

### Sincronización Git

- Push solo desde `main` a `origin/main`, sin force-push.
- Pull se detiene si existe cualquier cambio local versionable.
- Pull usa exclusivamente fast-forward; no hace merge/rebase/stash automático.
- Push hace checkpoint WAL e `integrity_check` de SQLite.
- Push ejecuta la suite completa salvo uso explícito de `-SkipTests`.
- Push usa `git add -A`, verifica que no queden archivos versionables fuera y bloquea
  rutas prohibidas incluso si alguien las fuerza al índice.
- Datos obligatorios: SQLite, modelos, fixtures, evidencias JSON y precálculos.
- Datos excluidos: cachés, logs, output, adjuntos de Codex, WAL/SHM/journals y backups.

### Actualización diaria

- Un fallo mezclado con fuentes vigentes se clasifica `partial`, no `stale`.
- `stale` queda reservado para fallo total con caché disponible.
- La UI usa `Datos diarios`, no `Calendario diario`, porque agrega cuatro bancos.
- Los errores se muestran en un expander seguro con proveedor y mensaje.
- Se mantiene un backoff de una hora tras errores para evitar golpear proveedores.
- El 403 de equipos del 1 de julio fue transitorio durante la publicación de Kaggle v28;
  al reintentar, Kaggle devolvió 200 y el banco quedó ready.

## 6. Requirements And Constraints

- Idioma principal de UI y comunicación: español.
- Prioridad del usuario: datos reales y persistentes por encima de cachés/artefactos.
- Nunca omitir del push resultados, marcadores, deep stats, modelos, evidencias o
  precálculos que sean nuevos.
- No duplicar datos ya versionados; los JSON se identifican por hash/nombre.
- No subir `data/cache/`, `output/`, logs ni temporales.
- Preservar los cambios del usuario y evitar comandos destructivos.
- La app debe seguir funcionando si una fuente remota falla; conservar último snapshot.
- Las fuentes parciales no bloquean el resto del pipeline.
- Las probabilidades de tanda deben ser auditables: porteros, muestra, fuentes, corte y
  versión de modelo visibles.
- Los cálculos prepartido nunca pueden usar evidencia posterior al kickoff.
- El usuario prefiere implementación directa una vez aprobado el diseño.
- No se usaron subagentes; el usuario eligió ejecución inline en esta sesión.

## 7. Files, Artifacts, And Changes

### Núcleo de datos y modelo

- `data/worldcup.sqlite`: base viva y versionada. Tamaño observado en el último commit de
  datos: aproximadamente 63 MB. GitHub avisa por superar 50 MB, aunque sigue por debajo
  del límite duro habitual de 100 MB.
- `data/models/outcome_ml.joblib`: modelo recalibrado tras cierres revisados.
- `data/models/outcome_ml_deep.joblib`: modelo deep existente, ~3,25 MB.
- `data/evidence/reviewed-json/`: 54 archivos revisados por hash.
- `data/precomputed/penalties/`: 17 contextos JSON; el loader valida modelo y fingerprint.

### Periodos y liquidación

- `src/wcpredict/ui/knockout_settlement.py`: requisitos de periodos según vía de cierre.
- `src/wcpredict/repository.py`: persistencia versionada, fases, tandas y proyección de stats.
- `src/wcpredict/match_phases.py`: validación de marcador y secuencia de tanda.
- `tests/test_knockout_settlement_ui.py`
- `tests/test_phase_stats_persistence.py`
- `tests/test_knockout_phase_integration.py`
- `tests/test_match_phases.py`

### Penaltis

- `src/wcpredict/transfermarkt_penalties.py`: selector dinámico de equipos, URLs y parsers
  de lanzadores/porteros.
- `src/wcpredict/penalty_profiles.py`: perfiles bayesianos, pesos por recencia/fase.
- `src/wcpredict/penalty_history_model.py`: Monte Carlo v4, portero titular fijo.
- `src/wcpredict/penalty_context_cache.py`: fingerprints y construcción de contextos.
- `src/wcpredict/espn_shootouts.py`: parser cronológico de tandas ESPN.
- `src/wcpredict/historical_shootouts.py`: importador de coberturas/tandas.
- `scripts/fetch_transfermarkt_penalties.py`: recopila lanzadores y porteros activos.
- `scripts/build_espn_shootout_fixture.py`: genera fixture auditable desde ESPN.
- `scripts/import_historical_shootouts.py`: importación real o `--dry-run`.
- `scripts/precompute_penalty_contexts.py`: genera JSON para cruces activos.
- `data/fixtures/active_team_shootout_coverage.csv`: coberturas por competición.
- `data/fixtures/active_team_shootout_events.csv`: eventos ESPN seleccionados.
- `data/fixtures/active_team_shootout_kicks.csv`: lanzamientos normalizados.
- tests principales: `test_penalty_profiles.py`, `test_penalty_history_model.py`,
  `test_penalty_context_cache.py`, `test_transfermarkt_penalties.py`,
  `test_historical_shootouts.py`, `test_espn_shootouts.py`,
  `test_penalty_tournament_evidence.py`.

### Push/pull seguros

- `scripts/project_sync.py`: política, SQLite, Git, push/pull y CLI.
- `scripts/push_project.ps1`: launcher Windows.
- `scripts/pull_project.ps1`: launcher Windows.
- `tests/test_project_sync.py`: 19 pruebas con repositorios y remotos bare temporales.
- `README.md`: sección de uso.
- `.gitignore`: además ignora accesos personales de doble clic:
  `Subir cambios.cmd` y `Actualizar desde nube.cmd`.

Uso normal:

```powershell
.\scripts\push_project.ps1 -Message "data: actualizar resultados y deep stats"
.\scripts\pull_project.ps1
```

Comprobación sin mutar:

```powershell
.\scripts\push_project.ps1 -Message "comprobación" -WhatIf
.\scripts\pull_project.ps1 -WhatIf
```

### Actualización diaria

- `src/wcpredict/daily_refresh.py`: clasificación current/updated/partial/stale/failed.
- `src/wcpredict/world_cup_data.py`: fetch/import de Kaggle y martj42.
- `src/wcpredict/ui/pages.py`: badge `Datos diarios` y detalle de errores.
- `tests/test_daily_refresh.py`: incluido escenario mixto vigente+fallido.
- `tests/test_app_contract.py`: contrato de etiqueta y expander.

### Documentación de diseño/planes

- `docs/superpowers/specs/2026-06-30-active-teams-penalty-evidence-design.md`
- `docs/superpowers/plans/2026-06-30-active-teams-penalty-evidence.md`
- `docs/superpowers/specs/2026-07-01-safe-push-pull-design.md`
- `docs/superpowers/plans/2026-07-01-safe-push-pull.md`
- `docs/superpowers/specs/2026-07-01-daily-refresh-partial-status-design.md`
- `docs/superpowers/plans/2026-07-01-daily-refresh-partial-status.md`

### Worktrees existentes

- `.worktrees/knockout-phase-stats-audit` -> `codex/knockout-phase-stats-audit` @ `aa1463e`
- `.worktrees/penalty-simulation` -> `codex/penalty-simulation` @ `acfa5f2`
- `.worktrees/period-upload-simplification` -> `codex/period-upload-simplification` @ `6949fbf`
- `.worktrees/safe-project-sync` -> `codex/safe-project-sync` @ `46ca63d`

Son worktrees de desarrollo históricos. No eliminarlos sin revisar si contienen cambios
locales no versionados; el worktree de periodos tuvo una SQLite modificada por tests.

## 8. Commands And Results

Verificaciones relevantes ejecutadas:

- Suite antes de la generalización de penaltis: 451 tests, OK.
- Batería focal de penaltis/periodos: 72 tests, OK.
- Suite tras modelo, fixtures y UI: 467 tests, OK.
- Suite tras scripts seguros push/pull: 486 tests, OK.
- Suite tras estado parcial diario: 488 tests, OK.
- `PRAGMA integrity_check`: `ok` antes de commits de datos.
- QA de tandas inicial: 26 equipos/78 coberturas/244 lanzamientos en la copia de trabajo;
  se detectó que esa copia no reflejaba Marruecos/Paraguay.
- QA corregida sobre la base real: 28 equipos/84 coberturas/28 tandas/282 lanzamientos.
- Fetch de porteros: 727 jugadores detectados, 631 consultados, 2.533 intentos de
  lanzadores guardados/actualizados y 1.475 intentos afrontados por porteros.
- Precompute: 20.000 simulaciones por cruce.
- Push seguro probado contra remoto bare local y luego usado realmente varias veces.
- Último diagnóstico Kaggle:
  metadata de equipos pública, versión 28, `isPrivate=false`; descarga inicialmente 403,
  después 200 con ZIP de 7.263 bytes.
- Refresh forzado tras retirar solo el check fallido:
  `status=updated`, `updated=('swaptr_wc2026_teams',)`, tres proveedores skipped y
  `failed=()`.

Commits clave, de más reciente a antiguo:

- `927929e` `'update:eliminatorias 01/07'`: 6 JSON revisados, modelo y SQLite.
- `fcf1b52` `update: cambios manuales`: shortcuts personales añadidos a `.gitignore`.
- `2cecb22` `data: refresh current World Cup team bank`.
- `a7083c8` `fix(ui): report partial daily refresh accurately`.
- `2033016` `feat: add safe project synchronization` más datos locales pendientes.
- `46ca63d`, `6012b17`, `9d40e5d`, `9385a63`, `0b37ad6`: implementación sync.
- `a8f7305` `data: import active-team penalty evidence`.
- `8da2be3` `feat(ui): explain penalty evidence coverage`.
- `bf74b54` `feat(penalties): model keeper and shootout evidence`.
- `ac47746` `feat(data): add historical shootout import pipeline`.
- `205990c` `feat(penalties): collect goalkeeper penalty records`.
- `e6287f3` `feat(data): store goalkeeper penalty evidence separately`.
- `6d3db58` `feat(penalties): target active knockout teams`.
- `0fcd08c` `fix(penalties): pin starting goalkeepers`.
- `9e7e5ff` `feat(phases): validate optional 120 minute total`.
- `23206a0` `fix(knockout): require available period files`.

## 9. Errors, Failed Attempts, And Fixes

### Barra 50/50 pese al texto de porteros

El contexto textual y la barra usaban rutas distintas/incompletas. Se unificó el modelo
de tanda, se añadieron perfiles de portero y se invalidaron artefactos antiguos por
versión/fingerprint.

### Calibración aparentemente sin cambios

La aplicación local necesitaba reinicio; el usuario confirmó que tras reiniciar estaba
correcta.

### Acumulados de 90/prórroga mal exigidos

Se separó el flujo de 90 minutos del flujo atómico de prórroga/tanda y se dejó 120 como
validación opcional.

### Discrepancia de total 120 en Alemania-Paraguay

Auditoría observada: suma de partes de `shots_on_target` de Alemania = 7 frente a total
120 = 6; suma de `saves` de Paraguay = 7 frente a total = 6. No se corrigió
automáticamente; el sistema debe presentarlo para revisión.

### Importador histórico no aceptaba `missed`

ESPN solo distingue gol/no gol. Se añadió `missed` con TDD y se evita acreditar una
parada sin fuente suficiente.

### Fixture inicial excluyó Marruecos y Paraguay

Una SQLite de worktree modificada por tests devolvió 26 vivos. La base real devolvió 28.
Se restauraron 6 coberturas y 4 tandas; el import final quedó 84/28/282.

### Nuevas tablas de penaltis inicialmente vacías

Se había subido código/fixtures sin ejecutar importación real. Se detectaron ceros en
`goalkeeper_penalty_attempts` y tablas históricas; después se importaron y precalcularon.

### Datos locales omitidos en un primer push

Se interpretó erróneamente “preservar” como “no versionar”. El usuario aclaró que deep
stats/resultados debían subirse. Se corrigió y posteriormente se construyó el programa
seguro para impedir nuevas omisiones.

### Timeout de suites conjuntas

Una ejecución fue cortada a 120 s y otra a 424 s; no se contó como éxito. Con margen
suficiente las suites completas concluyeron correctamente.

### Kaggle teams 403 y `Calendario diario: Obsoleto`

Solo `swaptr_wc2026_teams` falló con 403; partidos, jugadores y calendario seguían
vigentes. Se corrigió la semántica agregada (`partial`) y la etiqueta (`Datos diarios`).
Kaggle volvió a servir v28; se forzó el refresh y todos los checks quedaron ready.

### Supuesta lentitud de Streamlit

Se inició una inspección de procesos/logs, pero el turno fue interrumpido. El usuario
aclaró después que la app no estaba tardando y que todo funciona bien. No hay bug de
rendimiento activo ni cambios derivados de ese diagnóstico.

## 10. Pending Work

### Datos/partidos pendientes de revisión inmediata

Con fecha local 3 de julio de 2026, la base todavía muestra como `scheduled` estos
dieceiseisavos con kickoff ya pasado:

- Portugal vs Croatia, 1 julio 20:00 UTC;
- Spain vs Austria, 1 julio 23:00 UTC;
- Switzerland vs Algeria, 2 julio 17:00 UTC;
- Argentina vs Cape Verde, 2 julio 20:00 UTC;
- Colombia vs Ghana, 2 julio 23:00 UTC.

Australia vs Egypt está programado para 3 julio 20:00 UTC. Antes de asumir que existe un
bug, comprobar si simplemente faltan cierres manuales/importaciones del usuario.

### Octavos ya resueltos en el cuadro

- Paraguay vs France, 4 julio 17:00 UTC.
- Canada vs Morocco, 4 julio 20:00 UTC.
- Brazil vs Norway, 4 julio 23:00 UTC.
- Mexico vs England, 5 julio 17:00 UTC.
- USA vs Belgium, 5 julio 23:00 UTC.

Solo Canada-Morocco tiene actualmente un contexto explícito de octavos precalculado en
la lista observada. Deben regenerarse los contextos de los nuevos cruces cuando la base,
convocatorias y cierres estén listos:

```powershell
python scripts/precompute_penalty_contexts.py --force
```

### Precálculos antiguos

Existen JSON de cruces ya finalizados. No son peligrosos porque el loader valida match,
equipos, versión y fingerprint, pero pueden mantenerse como auditoría o limpiarse en una
tarea explícita posterior. No borrarlos automáticamente.

### Tamaño de SQLite

La base supera 50 MB y GitHub emite warning. Aún se puede subir, pero el crecimiento
continuo puede alcanzar el límite duro. Evaluar Git LFS, releases de datos o una estrategia
de snapshots antes de acercarse a 100 MB. No migrar sin diseño y aprobación.

### Worktrees históricos

Revisar y limpiar solo cuando el usuario lo solicite. Algunas ramas están obsoletas pero
pueden contener artefactos locales de tests.

## 11. Environment

- OS/shell: Windows, PowerShell.
- Workspace: `C:\Users\anton\Onedrive\Documentos\Random\world-cup-predictor`.
- Python: `C:\Users\anton\AppData\Local\Programs\Python\Python312\python.exe`.
- Fecha del handoff: 3 de julio de 2026.
- Zona horaria: Europe/Madrid.
- Rama principal: `main`.
- Remoto: `origin` en GitHub.
- Base: SQLite versionada en `data/worldcup.sqlite`.
- UI: Streamlit.
- Tests: `unittest`.
- Sandbox habitual: escritura limitada al workspace; red y ejecución de Python local
  pueden requerir aprobación escalada.
- Advertencia Git recurrente no funcional: acceso denegado a
  `C:\Users\anton\.config\git\ignore`; no impidió commits ni pushes.

## 12. User Preferences

- Comunicación en español, directa y con resultados verificables.
- No afirmar que algo está arreglado sin comprobarlo.
- Mantener continuidad entre conversaciones; no ignorar acuerdos anteriores.
- Ejecutar la implementación directamente una vez aprobado el diseño.
- Incluir en Git todos los datos duraderos nuevos: estadísticas, marcadores, deep stats,
  evidencias, modelos, fixtures y precálculos.
- Excluir siempre cachés, logs y temporales.
- Trabajar solo con equipos vivos para recopilación/precompute, pero conservar evidencia
  histórica relevante contra rivales eliminados cuando alimenta a un equipo vivo.
- Usar portero titular para la probabilidad de tanda.
- Revisar fuentes y no atribuir paradas no demostradas.
- El acumulado 120 es opcional y de control, no requisito de cierre.
- Prefiere pull conservador: detenerse si hay cambios locales y exigir push primero.
- Aclaración final: la app funciona bien; no hay lentitud de arranque que investigar.

## 13. Links, Citations, Images, And External References

Capturas aportadas durante la conversación:

- `C:\Users\anton\AppData\Local\Temp\codex-clipboard-0b7ecfb0-2616-419f-b7a0-225482728655.png`
  — Alemania-Paraguay, barra 50/50 vs contexto de portero.
- `C:\Users\anton\AppData\Local\Temp\codex-clipboard-973851b1-08e6-4fdf-9328-a971bad6d9ef.png`
  — cierre postpartido inicialmente vacío.
- `C:\Users\anton\AppData\Local\Temp\codex-clipboard-1d69405e-81e0-441b-886c-672f5a08843d.png`
  — periodos requeridos al seleccionar penaltis.
- `C:\Users\anton\AppData\Local\Temp\codex-clipboard-2fbf069e-26d2-46cf-8ee1-96c25418b6d1.png`
  — Países Bajos-Marruecos y probabilidades de tanda.
- `C:\Users\anton\AppData\Local\Temp\codex-clipboard-0ef22b18-8921-4e66-9f85-804c034abad1.png`
  — badge de datos diarios obsoleto/error.

Fuentes externas consultadas/relevantes:

- FIFA Marruecos-España 2022:
  `https://www.fifa.com/en/articles/world-cup-qatar-2022-morocco-spain-match-review`
- UEFA Sevilla-Roma 2023 y Bounou:
  `https://www.uefa.com/uefaeuropaleague/news/0281-1825b16a79b7-12fe014d1f96-1000--sevilla-1-1-roma-aet-sevilla-win-4-1-on-penalties-bounou-th/`
- UEFA records de tandas:
  `https://www.uefa.com/uefaeuro/history/news/0259-0e74d6e5f08a-91b1b93ffe14-1000--euro-penalty-shoot-out-records/`
- Transfermarkt Bounou:
  `https://www.transfermarkt.co.uk/bono/elfmeterstatistik/spieler/207834`
- Transfermarkt Verbruggen:
  `https://www.transfermarkt.nl/bart-verbruggen/elfmeterstatistik/spieler/565093`
- CAF Marruecos-Nigeria AFCON 2025:
  `https://www.cafonline.com/afcon2025/news/morocco-seal-win-in-dramatic-penalty-shootout-for-first-final-in-22-years-mane-the-hero-as-senegal-edge-egypt/`
- CAF Nigeria-Egipto AFCON 2025:
  `https://www.cafonline.com/afcon2025/news/afcon-2025-nwabali-comes-big-as-nigeria-clinch-bronze-in-casablanca/`
- ESPN API usada para tandas:
  `https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/summary?event={event_id}`
- Kaggle datasets:
  `swaptr/fifa-wc-2026-players`, `swaptr/fifa-wc-2026-teams`,
  `swaptr/fifa-wc-2026-matches`.

## 14. Changes Since Previous Transfer

`none`: este es un paquete standalone y no continúa otro handoff formal.

## 15. Gaps, Assumptions, And Verification Needs

- El timestamp exacto de creación se fija al día del handoff; no se consultó un reloj de
  alta precisión para completar hora/minuto.
- Los partidos con kickoff pasado y estado `scheduled` se señalan como discrepancia
  operativa, no como bug confirmado. Puede que el usuario todavía no haya importado sus
  cierres.
- `predictions` tiene 0 filas mientras `prediction_snapshots` tiene 72. La UI calcula
  predicciones en memoria/cache y persiste snapshots; no se diagnosticó esto como error.
- Los 17 precálculos incluyen cruces finalizados; la validez efectiva depende del
  fingerprint al cargar, no solo de la fecha del archivo.
- No se reabrió visualmente Streamlit al crear el handoff porque el usuario confirmó que
  funciona correctamente.
- No se ejecutó una nueva suite completa durante la creación del handoff. La última suite
  registrada tras cambios de código fue 488/488 y los dos commits posteriores
  (`fcf1b52`, `927929e`) contienen `.gitignore` y datos/modelo, no cambios de lógica.
- La limpieza de worktrees y posible migración Git LFS requieren autorización explícita.
- El archivo de handoff se entrega guardado localmente pero no se ha añadido a Git ni
  subido al remoto.
