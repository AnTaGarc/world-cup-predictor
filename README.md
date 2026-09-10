# World Cup Predictor — IA y analítica deportiva

Aplicación Streamlit para explorar el Mundial 2026 mediante modelos probabilísticos, ingeniería de datos reproducible y auditorías posteriores al partido. El proyecto muestra cómo combinar fuentes heterogéneas, estadísticas profundas y aprendizaje automático sin ocultar la incertidumbre ni la procedencia de los datos.

## Qué incluye

- Probabilidades de resultado, xG esperado y distribución de marcadores 0–5.
- Marcador principal y alternativas con un modelo Dixon-Coles/binomial negativa.
- Proyecciones por selección de tiros, tiros a puerta, córners, tarjetas, faltas y fueras de juego.
- Proyecciones de jugadores: gol, asistencia, tiros, tiros a puerta, tarjetas, pases, paradas, goles concedidos y portería a cero.
- Ensamble de matriz de goles, Elo/forma cronológica y modelo ML con deep stats.
- Historial de predicciones congeladas antes del inicio para evitar fuga temporal.
- Auditoría posterior al partido con Brier, marcador predicho frente al real y comparación predicho/real por equipo.
- Revisión humana, hashes y procedencia para capturas y datos importados.

## Fuentes de datos

La actualización conserva los proveedores deportivos que alimentan calendario, resultados, plantillas, jugadores, alineaciones, disponibilidad y estadísticas:

- API-Sports Football, APIFootball, Football-Data.org, TheSportsDB y SportMonks.
- StatsBomb Open Data, Transfermarkt y conjuntos históricos de xgabora.
- Bancos WC2026 de swaptr y GitHub, martj42 y OpenFootball.
- Kaggle y SofaScore revisado como evidencia complementaria.
- Capturas y JSON revisados por el usuario, siempre con procedencia y huella.

Los proveedores se resuelven con una política explícita de autoridad, frescura y conflicto. Una fuente parcial no bloquea el análisis: los campos ausentes se muestran como tales y reducen la confianza.

## Arquitectura

```text
fuentes deportivas
       ↓
normalización + SQLite + procedencia
       ↓
perfiles de equipo/jugador + forma temporal
       ↓
Poisson/NB + Dixon-Coles + Elo/ML deep
       ↓
proyecciones explicables + snapshots
       ↓
resultado real + auditoría + calibración
```

El corte temporal se aplica antes de cada partido. Los deep stats recopilados se reutilizan en partidos posteriores, nunca se eliminan al actualizar una predicción.

## Instalación

Requiere Python 3.12.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:PYTHONPATH='src'
streamlit run app.py
```

Las credenciales deportivas son opcionales y se configuran únicamente para los proveedores que se quieran activar:

```text
API_SPORTS_KEY
APIFOOTBALL_API_KEY
FOOTBALL_DATA_API_KEY
THESPORTSDB_API_KEY
SPORTMONKS_API_TOKEN
```

Sin credenciales, la aplicación sigue usando los bancos abiertos, la caché local y la evidencia revisada disponible.

## Verificación

```powershell
$env:PYTHONPATH='src'
python -m unittest discover -s tests -v
python -m compileall -q app.py src scripts
```

La migración incluida elimina exclusivamente datos heredados ajenos al producto analítico, crea una copia recuperable y verifica hashes de todas las tablas deportivas:

```powershell
$env:PYTHONPATH='src'
python scripts/migrate_analytics_only.py --database data/worldcup.sqlite --backup data/worldcup.before-analytics.sqlite
```

## Límites del modelo

Las proyecciones expresan incertidumbre, no certeza. La calidad depende de la actualidad de las plantillas, los minutos disponibles, la muestra histórica y la cobertura profunda de ambos equipos. El rendimiento se evalúa cronológicamente y los ajustes solo se promueven cuando mejoran una validación temporal.
