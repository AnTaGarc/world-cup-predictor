# Diseño de optimización integral de la app

## Objetivo

Reducir la latencia de arranque, navegación y cambio de partido sin alterar datos, probabilidades ni flujos de importación. La optimización se guiará por mediciones reproducibles sobre una copia de la base local real.

## Hallazgos iniciales

- `Repository.list_matches()` materializa 4.362 partidos y ejecuta cinco subconsultas correlacionadas por partido, aunque la interfaz solo necesita el Mundial 2026. Medición en frío: aproximadamente 0,21 s.
- `Repository.list_deep_team_metric_observations_before()` materializa 116.532 observaciones para construir el perfil de dos selecciones. Medición en frío: aproximadamente 1,89 s.
- `get_all_match_evidence_statuses()` calcula cobertura para los 4.362 partidos aunque la pantalla de calibración solo presenta el Mundial 2026. Medición: aproximadamente 0,18 s.
- La actualización diaria crea otro repositorio y vuelve a inicializar el esquema, aunque `_repo()` ya garantiza la inicialización una vez por proceso.
- La app ya dispone de fragmentos y cachés para los paneles secundarios. Añadir más caché global no solucionaría la sobrelectura y aumentaría el riesgo de datos obsoletos.

## Diseño aprobado

1. Empujar los filtros hacia SQLite: competición para calendario/cobertura y equipos para perfiles profundos.
2. Mantener las APIs compatibles mediante parámetros opcionales; los scripts de entrenamiento seguirán pudiendo pedir el histórico completo.
3. Añadir un índice específico para la selección de observaciones profundas por equipo, estado, fecha y métrica.
4. Reutilizar el repositorio Streamlit ya inicializado en la actualización diaria.
5. Cubrir cada cambio con pruebas de comportamiento y contratos de interfaz antes de implementarlo.
6. Comparar las mismas operaciones antes y después y ejecutar la suite completa con un timeout suficiente.

## Límites

- No se modifican fórmulas, pesos, calibración, probabilidades ni contenido de la base real.
- No se introduce ejecución en segundo plano ni nuevas dependencias.
- No se cachean resultados sin una clave de invalidación existente.
- La copia del SQLite del worktree es solo para pruebas; no se incluirá en el commit salvo que existan cambios de datos solicitados expresamente.

## Resultado medido

Mediciones sobre la misma copia local de 66,6 MB, antes y después del cambio:

| Operación | Antes | Después (mejor de 3) | Filas antes → después | Mejora |
|---|---:|---:|---:|---:|
| Calendario usado por la UI | 0,209 s | 0,015 s | 4.362 → 97 | 13,6× |
| Perfil profundo de dos equipos | 1,891 s | 0,071 s | 116.532 → 3.918 | 26,7× |
| Cobertura de calibración | 0,180 s | 0,038 s | 4.362 → 97 | 4,8× |

Las APIs sin filtro conservan su comportamiento completo para importaciones, entrenamiento y backtesting.

La comparación sobre la base real copiada confirmó equivalencia exacta entre la consulta optimizada y el subconjunto de la consulta anterior: 97 partidos, 3.918 observaciones profundas y 97 estados de cobertura coinciden fila por fila.
