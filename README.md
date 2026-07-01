# World Cup Predictor

Aplicación local Streamlit para analizar el Mundial 2026 con probabilidades explicables, datos con procedencia, cuotas manuales, EV, mercados de jugador y backtesting.

## Qué hace

- Sincroniza el calendario y permite elegir partidos reales.
- Reutiliza el collector local `analisis-de-datos` sin copiar ni mostrar claves.
- Conserva estados `completo`, `parcial`, `cacheado` o `no disponible` por partido.
- Modela 1X2, doble oportunidad, draw no bet, goles y BTTS con Poisson y rango de incertidumbre.
- Solo estima córners, tarjetas y tiros cuando existen tasas observadas para ambos equipos.
- Admite cuotas manuales por tabla, calcula cuota justa, probabilidad implícita y EV.
- Incluye mercados de jugador asistidos; si faltan minutos/rol, baja la confianza o no estima.
- Guarda snapshots y evaluaciones Brier en SQLite.
- Ofrece un importador experimental de URL pública de SofaScore, sin cookies ni sesión del navegador.

La app es una herramienta de apoyo analítico, no coloca apuestas ni demuestra rentabilidad con una muestra corta.

## Development

Run tests:

```powershell
cd world-cup-predictor
$env:PYTHONPATH="src"
python -m unittest discover -s tests -v
```

Run app:

```powershell
cd world-cup-predictor
streamlit run app.py
```

Sincronizar el calendario local:

```powershell
python scripts/sync_schedule.py
```

Importar las cuatro capturas de calibración existentes en `sports-data/sports.db`:

```powershell
python scripts/import_calibration.py
```

Importar o actualizar el histórico internacional abierto (martj42 upstream):

```powershell
python scripts/import_open_history.py
```

El importador conserva URL, fecha, SHA-256 y número de filas. Los partidos programados sin marcador (`NA`) se excluyen; nunca se convierten en empates 0-0.

## Bancos de información y credenciales

La vista **Calidad de datos** clasifica cada fuente por fiabilidad, coste y consumo. La prioridad depende del dominio:

1. autoridad: captura postpartido revisada, fuente oficial y cuota exacta del bookmaker;
2. abiertos estructurados: martj42, StatsBomb Open Data, Open-Meteo y datasets locales con licencia revisada;
3. APIs operativas: API-Football, football-data.org y proveedores de cuotas;
4. fallback experimental: SofaScore híbrido, soccerdata, espejos de Kaggle y sentimiento social.

Fuentes abiertas y captura manual funcionan sin clave. Las integraciones opcionales reconocen estas variables, mostrando únicamente si existen, nunca su contenido:

```text
API_SPORTS_KEY
APIFOOTBALL_API_KEY
FOOTBALL_DATA_API_KEY
ODDSPAPI_API_KEY
THE_ODDS_API_KEY
THESPORTSDB_API_KEY
SPORTMONKS_API_TOKEN
X_API_BEARER_TOKEN
X_API_BUDGET_USD
OPENAI_API_KEY
OPENAI_MODEL
```

Una API de pago no se consulta si falta su clave o si su presupuesto es cero.

## Bancos diarios del Mundial 2026

Antes de mostrar una previsión, la aplicación comprueba los datasets operativos de
Kaggle `swaptr/fifa-wc-2026-matches`, `teams` y `players`. Si la última revisión
correcta tiene menos de 24 horas no descarga nada. Cuando cambia la fuente, conserva
versión, SHA-256, fecha y número de filas. Un fallo mantiene la caché como obsoleta y
aplica una hora de backoff para no repetir llamadas en cada rerun.

Son fuentes primarias operativas para el Mundial actual, pero de procedencia
comunitaria: una fuente oficial o una captura revisada prevalece ante un conflicto.

## Modelos por mercado

- 1X2, goles y BTTS conservan la matriz de marcadores activa. Dixon–Coles, Poisson
  bivariante y el ensemble multiclase son challengers hasta superar validación temporal.
- Córners, amarillas y tiros usan binomial negativa cuando existe dispersión estimada;
  de lo contrario muestran el fallback Poisson.
- Rojas se tratan como evento raro y tiros a puerta como candidato condicionado a tiros.
- Las estadísticas individuales producen un ajuste limitado a ±15 %, contraído por
  muestra y acompañado de la lista de jugadores que lo originó.

## OpenAI opcional

La Responses API solo se invoca al pulsar el botón de explicación y si existe
`OPENAI_API_KEY`. Recibe las probabilidades ya calculadas, pero su salida solo admite
narrativa y alertas: no puede sustituir el resultado determinista. La app funciona
íntegramente sin esta clave.

## Capturas de SofaScore

El OCR también reconoce de forma conservadora tablas de jugadores con minutos,
valoración, goles, asistencias, tiros, precisión de pase, entradas e intercepciones.
Cada celda permanece pendiente hasta que el usuario la confirma, corrige o descarta;
se conservan imagen, hash, texto bruto, confianza, advertencias, equipo y decisión.

### JSON profundo revisado

La pestaña **Datos / SofaScore** también admite un JSON estructurado obtenido manualmente
con ChatGPT a partir de las capturas. Antes de importarlo exige confirmación del usuario.
Cada archivo conserva SHA-256, nombres de las capturas y ruta de cada métrica.

```powershell
python scripts/import_deep_match_json.py C:\ruta\estadisticas.json --dry-run
python scripts/import_deep_match_json.py C:\ruta\estadisticas.json
python scripts/retrain_outcome_model.py
```

El ajuste inmediato usa únicamente xG de partidos anteriores al encuentro objetivo,
ponderado por recencia y contraído por muestra. Las demás métricas se almacenan para
perfil de estilo y validación futura. Una roja agregada no genera una suspensión sin
identificar al jugador; sanciones, lesiones y cambios de entrenador se registran en
**Calidad de datos** con fuente y partido afectado.

## Modelo ML, jugadores y sentimiento

- El contraste ML 1X2 usa orden cronológico, forma de los últimos cinco partidos, diferencial de goles, rating previo y sede neutral. Reserva un tramo posterior para calibrar la temperatura de las probabilidades.
- Cada cierre postpartido revisado actualiza el histórico y registra una nueva ejecución del modelo; una corrección sustituye el marcador anterior sin duplicarlo.
- El panel de jugadores calcula métricas por 90, minutos, muestra e impacto interpretable. K-Means agrupa estilos solo con una muestra mínima y no se usa como causalidad.
- El sentimiento se guarda en snapshots prepartido acotados. Es experimental y queda fuera del entrenamiento, del EV y de la calibración hasta superar una validación temporal.
- X API funciona con pago por uso; la aplicación no abre un stream ni consume recursos sin clave y presupuesto positivo.

## Actualización de datos

El botón **Actualizar datos** ejecuta una consulta acotada para un solo partido:

- máximo 14 llamadas de API;
- 0 créditos de cuotas automáticas;
- timeout de 120 segundos;
- caché preservada si un proveedor falla;
- ningún traceback o secreto se muestra en la interfaz.

Las cuotas se introducen manualmente. SofaScore es un fallback experimental y puede dejar de funcionar si cambia sus endpoints internos.

Formato CSV estricto:

```text
market_family,market_name,selection_name,line,decimal_odds,bookmaker
match_result,1X2,Canada,,2.25,Winamax
```

## Forma y calibración postpartido

- El ledger de forma usa todos los partidos finalizados disponibles antes del kickoff; excluye cualquier resultado futuro.
- Cada resultado muestra rival, marcador, antigüedad, peso de competición, ajuste acotado por fuerza rival y contribución ponderada.
- En **Backtesting**, los partidos ya iniciados aparecen pendientes de cierre. Introduce el marcador y completa tiros, tiros a puerta, córners, tarjetas o posesión cuando estén disponibles.
- Guardar el cierre cambia el partido a finalizado, conserva la fuente manual, evalúa automáticamente predicciones compatibles y evita duplicados.
- La calibración muestra Brier, acierto, bandas probabilísticas, fiabilidad por familia y deriva acumulada.
- Una familia con menos de 20 evaluaciones se considera provisional y no debe elevar la confianza del modelo.

## Demo

## SincronizaciÃ³n segura

Cierra Streamlit y cualquier proceso que pueda estar escribiendo en la base antes de
sincronizar. Para guardar y publicar todos los cambios del proyecto:

```powershell
.\scripts\push_project.ps1 -Message "data: update deep stats and results"
```

El programa hace checkpoint y valida `data/worldcup.sqlite`, ejecuta la suite completa,
incluye cÃ³digo y todos los datos persistentes (base, modelos, fixtures, JSON revisados y
precÃ¡lculos), crea el commit y publica `main`. Nunca prepara `data/cache/`, `output/`,
logs, adjuntos de Codex ni archivos auxiliares de SQLite.

Para descargar una actualizaciÃ³n:

```powershell
.\scripts\pull_project.ps1
```

El pull solo permite fast-forward. Si existen estadÃ­sticas, cÃ³digo u otros cambios
versionables locales, se detiene y pide ejecutar primero el push. Las cachÃ©s y los
temporales ignorados no bloquean la descarga.

Puede comprobarse cualquiera de los flujos sin modificar Git:

```powershell
.\scripts\push_project.ps1 -Message "comprobaciÃ³n" -WhatIf
.\scripts\pull_project.ps1 -WhatIf
```

Seed local demo data:

```powershell
cd world-cup-predictor
python scripts/seed_demo.py
```

Run the Streamlit workbench:

```powershell
cd world-cup-predictor
python -m streamlit run app.py
```
