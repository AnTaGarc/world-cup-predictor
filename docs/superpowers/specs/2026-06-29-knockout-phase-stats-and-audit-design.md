# Estadísticas por fase y auditoría de eliminatorias

## Objetivo

Registrar por separado lo ocurrido en los 90 minutos, la prórroga y la tanda de penaltis de cada eliminatoria para:

- conservar el comportamiento actual de las predicciones de 90 minutos;
- mejorar exclusivamente los goles esperados y el resultado condicional de la prórroga;
- evaluar por separado lo que el modelo predijo para 90 minutos, prórroga y penaltis;
- incorporar las tandas observadas durante el Mundial a los perfiles de lanzadores y porteros de partidos posteriores;
- mostrar correctamente el marcador final y la tanda en el bracket.

La importación por fases contiene estadísticas de equipo. Las estadísticas de jugador siguen llegando a partido completo desde el repositorio actual y no se reparten artificialmente entre periodos.

## Restricción principal del modelo

La separación por partes no puede alterar ninguna predicción ajena a la prórroga.

- El acumulado de 90 minutos en `team_match_stats` continúa alimentando forma, xG, tiros, volúmenes y el resto de predicciones actuales.
- Primera y segunda parte permiten reconstruir y validar ese acumulado, pero no se vuelven a introducir en el modelo porque duplicarían la misma evidencia.
- Las estadísticas de prórroga solo pueden modificar:
  - goles esperados de cada equipo durante la prórroga;
  - victoria del equipo A durante la prórroga;
  - empate tras la prórroga;
  - victoria del equipo B durante la prórroga.
- El acumulado de 120 minutos se conserva como evidencia y validación, pero no alimenta el modelo normal de 90 minutos.
- La tanda mantiene su modelo independiente.

## Enfoques considerados

### 1. Añadir un periodo a `team_match_stats`

Rechazado. Cambiaría la cardinalidad y la clave primaria de una tabla consumida por numerosas consultas actuales, con alto riesgo de mezclar 90 y 120 minutos.

### 2. Tablas específicas por fase con compatibilidad hacia atrás — seleccionado

Las nuevas tablas guardan periodos, resultados parciales y lanzamientos. `team_match_stats`, `match_results` y las interfaces existentes conservan su función actual. Los consumidores que necesitan detalle de eliminatorias consultan las nuevas tablas explícitamente.

### 3. Guardar todo únicamente en `observations.context_json`

Rechazado como almacenamiento principal. Es flexible, pero dificulta restricciones, consultas, correcciones versionadas, entrenamiento y validación aritmética. Las observaciones seguirán conservando todas las métricas profundas y su procedencia, mientras las métricas estructuradas tendrán filas por periodo.

## Semántica de resultados

`match_results` conserva el marcador oficial anterior a la tanda:

- si el partido termina en 90 minutos, contiene el resultado al 90';
- si se juega prórroga, contiene el resultado acumulado al 120';
- los goles de la tanda nunca se suman a `match_results`.

`match_phase_results` conserva las partes necesarias para evaluación y aprendizaje:

- resultado al 90';
- goles marcados exclusivamente durante la prórroga;
- resultado de la tanda;
- fase de decisión: `regulation`, `extra_time` o `shootout`.

Para el entrenamiento y evaluación del modelo normal se utiliza el resultado al 90'. Para resolver el bracket se utiliza el resultado acumulado al 120' o el ganador de la tanda.

Ejemplo: 1-1 al 90', 2-2 al 120' y 5-4 en penaltis.

- El modelo normal aprende de 1-1.
- El modelo de prórroga aprende de los goles 1-1 de esos 30 minutos.
- El modelo de penaltis aprende de cada lanzamiento.
- `match_results` muestra 2-2.
- El bracket clasifica al ganador de la tanda.

## Modelo de datos

### `match_phase_results`

Una fila activa por partido de eliminatoria:

- `match_id`, clave primaria y referencia a `matches`;
- `regulation_goals_a`, `regulation_goals_b`;
- `extra_time_goals_a`, `extra_time_goals_b`, nulos cuando no hubo prórroga;
- `shootout_goals_a`, `shootout_goals_b`, nulos cuando no hubo tanda;
- `decided_in`, restringido a `regulation`, `extra_time` o `shootout`;
- `source_id`, `recorded_at_utc` y `manual_edit`.

Reglas de coherencia:

- `regulation`: no admite goles de prórroga ni tanda;
- `extra_time`: exige goles de prórroga y un resultado acumulado al 120' no empatado;
- `shootout`: exige prórroga, empate acumulado al 120', una tanda válida y un ganador;
- el resultado oficial debe coincidir con 90' más los goles de prórroga.

### `team_match_period_stats`

Una fila por partido, equipo, periodo y fuente para las métricas estructuradas:

- periodos atómicos: `first_half`, `second_half`, `extra_time_first`, `extra_time_second`;
- periodos acumulados de validación: `regulation_total`, `extra_time_total`, `full_match_total`;
- métricas compatibles con `team_match_stats`: goles, xG, tiros, tiros a puerta, posesión, córners, amarillas, rojas, paradas y goles encajados;
- `source_id`, `manual_edit`, `observed_at_utc` y huella del contenido importado.

La unicidad por partido, equipo, periodo y fuente hace que repetir el mismo archivo sea idempotente.

Las métricas profundas no estructuradas permanecen en `observations`, pero el periodo deja de depender de texto libre dentro de `context_json`: cada observación importada debe llevar uno de los periodos canónicos anteriores.

### `shootout_kicks`

Una fila por lanzamiento:

- `id`, `match_id` y `sequence_number`;
- `team_id` del lanzador;
- `taker_player_id`;
- `goalkeeper_player_id` del rival;
- `outcome`: `scored`, `saved` o `off_target_or_woodwork`;
- `source_provider = world_cup_2026_manual`;
- `recorded_at_utc`, `manual_edit` y referencia a la versión de cierre.

Solo `saved` acredita una parada al portero. `off_target_or_woodwork` cuenta como fallo del lanzador y penalti afrontado por el portero, pero nunca como parada.

`shootout_kicks` es el registro operativo canónico del torneo. Los perfiles de penaltis combinan estas filas con `penalty_attempts` en lectura; no se copian filas ni se reinterpreta la historia importada de Transfermarkt.

## Flujo guiado de cierre

El cierre de una eliminatoria empieza seleccionando cómo terminó:

1. En 90 minutos.
2. En prórroga.
3. En penaltis.

Después se registra el marcador al 90' y se aporta una de estas dos rutas equivalentes:

- un único JSON con el acumulado completo de los 90'; o
- los JSON de primera y segunda parte.

Por tanto, si el encuentro termina en 90 minutos, el JSON acumulado de esa fase es suficiente para cerrar. Las dos partes permiten más detalle y validación, pero no son obligatorias cuando existe el acumulado.

Si hubo prórroga, se habilitan:

- goles marcados exclusivamente durante la prórroga;
- un único JSON con el acumulado de la prórroga, o los JSON de sus dos partes;
- acumulado de 120' opcional como control adicional.

Si hubo tanda, se habilita el editor de lanzamientos. El usuario selecciona un portero defensor para cada selección; ese portero se asocia automáticamente a todos los lanzamientos rivales. Cada fila permite elegir un tirador de la plantilla correspondiente y uno de los tres resultados canónicos.

Las fases no disputadas aparecen como `No disputado`, no como datos pendientes. Se puede guardar un borrador incompleto. El cierre definitivo exige coherencia de marcadores y, si hubo tanda, una secuencia válida con ganador.

## Validación de importaciones

Las métricas aditivas deben cuadrar entre periodos atómicos y acumulados:

- goles;
- xG;
- tiros y tiros a puerta;
- córners;
- tarjetas;
- paradas;
- goles encajados;
- cualquier otra métrica clasificada expresamente como contador.

La comparación numérica usa tolerancia para valores decimales como xG y coincidencia exacta para enteros.

Los porcentajes y tasas no se suman. La posesión, precisión de pase y métricas equivalentes siguen estas reglas:

- si existen numerador y denominador, la aplicación recalcula el acumulado;
- si no existen, el acumulado importado es la referencia y las partes se conservan sin agregación artificial;
- una diferencia visible no bloquea el cierre si matemáticamente no puede reconstruirse, pero queda marcada para revisión.

Una discrepancia bloqueante identifica métrica, equipo, suma calculada, acumulado importado y periodos implicados. Corregir o volver a importar una fase no obliga a repetir las demás.

Al cerrar el partido, el acumulado validado de 90 minutos se proyecta sobre `team_match_stats`. Si se importó `regulation_total`, ese registro es la fuente preferida y funciona por sí solo; si también existen las dos partes, se comprueba contra ellas. Si no se importó, se deriva de `first_half + second_half`. Para entrenar el submodelo de prórroga, `extra_time_total` también funciona por sí solo y es la fuente preferida; en su ausencia se suman sus dos partes. Nunca se proyecta `full_match_total` sobre `team_match_stats`.

## Modelo de prórroga

El submodelo parte de la intensidad de 30 minutos existente y añade un ajuste muy regularizado aprendido exclusivamente de prórrogas cerradas.

Evidencia permitida:

- goles de prórroga;
- xG de prórroga;
- tiros y tiros a puerta de prórroga;
- identidad de los equipos y recencia, siempre con contracción hacia el promedio global.

Las dos partes de la prórroga se suman para formar una muestra de 30 minutos. El acumulado importado sirve para validarla. Las partes de los 90 minutos y el total de 120 no entran en este ajuste.

El submodelo devuelve:

- xG de prórroga de cada equipo;
- marcador de prórroga más probable;
- `P(equipo A gana la prórroga | empate al 90')`;
- `P(empate tras la prórroga | empate al 90')`;
- `P(equipo B gana la prórroga | empate al 90')`.

Las tres probabilidades condicionales suman uno. La probabilidad marginal de alcanzar la tanda continúa siendo la probabilidad de empate al 90' multiplicada por la probabilidad condicional de empate tras la prórroga.

El ajuste no puede modificar predicciones de 90 minutos, jugadores, tarjetas, córners ni otros mercados. Con pocas muestras debe permanecer cerca del modelo base; una muestra aislada nunca produce un cambio grande.

## Aprendizaje de penaltis del torneo

Después de cerrar y validar una tanda, sus lanzamientos pasan a estar disponibles para partidos posteriores:

- tienen mayor peso de recencia que la historia antigua;
- conservan su proveedor y trazabilidad propios;
- actualizan conversión y propensión de los lanzadores;
- actualizan penaltis afrontados y paradas de los porteros;
- `saved` es el único resultado que aumenta las paradas;
- una corrección de cierre reemplaza la versión activa y no duplica intentos.

La predicción y auditoría de un partido siempre usan el snapshot congelado anterior al encuentro. Los datos del propio partido solo pueden influir en encuentros posteriores.

## Snapshot y auditoría predicho-real

El `prediction_snapshots.payload_json` prepartido de una eliminatoria conserva tres secciones versionadas.

### 90 minutos

Mantiene las predicciones y comparaciones actuales: 1X2, marcador, xG, mercados, estadísticas profundas y comparaciones por equipo. La evaluación usa el marcador y el acumulado estadístico al 90', aunque el encuentro continúe.

La severidad visual del resultado no convierte cualquier fallo en rojo. Si el resultado más probable no ocurre, se mide la diferencia entre su probabilidad y la asignada al resultado real: hasta 10 puntos porcentuales se considera un fallo razonable, hasta 25 puntos una advertencia y solo una diferencia mayor se marca como error fuerte. El marcador modal también se suaviza cuando queda a un gol o conserva correctamente el signo 1X2; el rojo se reserva para una desviación grande combinada con un resultado de alta confianza fallado.

### Prórroga

Guarda las predicciones condicionales de xG, marcador y resultado de prórroga, además de la probabilidad marginal de alcanzar la tanda.

Si hubo prórroga, la auditoría compara:

- xG esperado y goles reales de cada equipo durante esos 30 minutos;
- marcador de prórroga más probable y marcador real de la fase;
- resultado condicional más probable y resultado observado;
- probabilidad asignada al resultado observado.

Las restantes deep stats de prórroga aparecen como evidencia usada por el submodelo, pero no como predicho-real si el modelo no produjo una predicción para esa métrica.

### Penaltis

El snapshot guarda la probabilidad de alcanzar la tanda, probabilidad condicional de victoria de cada selección, jugadores probables al minuto 120, probabilidad de estar entre los cinco primeros, conversión posterior de cada jugador y perfil del portero probable.

Si hubo tanda, la auditoría muestra:

- probabilidad asignada al ganador real;
- Brier de la predicción del ganador;
- presencia de los tiradores reales entre los probables al 120' y entre los cinco primeros;
- probabilidad de conversión previa de cada tirador real;
- Brier por lanzamiento;
- paradas esperadas y reales del portero.

Si no se disputó una fase, se muestra `No se disputó` y no se penaliza al modelo por datos ausentes.

## Interfaz

El cierre postpartido muestra:

- selector del camino de resolución;
- marcador por fases;
- tarjeta por periodo con estado `Importado`, `Pendiente`, `No disputado` o `No cuadra`;
- resumen de validación;
- editor de tanda cuando corresponda;
- guardado de borrador y cierre definitivo separados.

El editor de tanda calcula el marcador y aplica las reglas de finalización de los cinco primeros lanzamientos y la muerte súbita. Permite corregir orden, tirador o resultado antes del cierre.

La auditoría cerrada conserva el diseño de barras de eliminatorias y añade secciones plegables para 90 minutos, prórroga y penaltis sin reintroducir la tabla 1X2 de fase de grupos.

## Bracket

La tarjeta mantiene el diseño actual y representa:

- partido decidido en 90': marcador al 90';
- partido decidido en prórroga: marcador acumulado al 120' y etiqueta discreta `Prórroga`;
- partido decidido en penaltis: marcador acumulado al 120' como valor principal y tantos de la tanda en pequeño junto a cada equipo, por ejemplo `2 (5)` y `2 (4)`.

El ganador conserva el resaltado visual actual. La cifra entre paréntesis no se mezcla con goles de partido, xG, forma ni mercados.

## Versionado y correcciones

El cierre por fases extiende el versionado actual:

- una corrección crea una nueva versión activa;
- la versión anterior y sus evaluaciones quedan inactivas;
- se recalculan auditoría y ganador del bracket;
- las muestras de prórroga y los lanzamientos del perfil se leen solo desde la versión activa;
- repetir un guardado idéntico no crea una versión ni datos duplicados.

La migración debe funcionar sobre la base SQLite actual sin reescribir los partidos ya cerrados. Los partidos antiguos sin detalle por fases conservan el comportamiento previo y muestran cobertura de fase desconocida.

Cuando una eliminatoria nueva se cierre, la fila que se incorpore a `historical_matches` para el modelo normal contendrá el marcador al 90', aunque `match_results` conserve el marcador oficial al 120'. El submodelo de prórroga leerá exclusivamente `match_phase_results` y `team_match_period_stats`, evitando que los goles de prórroga aparezcan dos veces en el entrenamiento.

## Manejo de errores

- Archivo de periodo equivocado: se detecta cuando la etiqueta interna contradice el periodo seleccionado y exige confirmación o corrección.
- Archivo repetido: se reconoce por huella y no duplica filas.
- Métrica aditiva incoherente: bloquea el cierre definitivo y muestra el desglose.
- Porcentaje no reconstruible: muestra advertencia no bloqueante.
- Plantilla sin jugador buscado: no permite inventar una identidad; exige resolver el jugador en el repositorio.
- Portero ausente: bloquea una tanda porque cada lanzamiento debe tener portero rival.
- Secuencia sin ganador o incompatible con las reglas: bloquea el cierre.
- Snapshot antiguo sin secciones de fase: mantiene la auditoría actual y marca la nueva comparación como no disponible.
- Fallo del submodelo de prórroga: utiliza la intensidad base de 30 minutos y deja trazabilidad del fallback.

## Pruebas

Las pruebas deben demostrar:

- migración segura de una base existente;
- persistencia idempotente de cada periodo y acumulado;
- reconstrucción correcta de 90 minutos y prórroga con métricas aditivas;
- tratamiento correcto de tasas y porcentajes;
- fases no disputadas sin falsos pendientes;
- los tres caminos de resolución y sus invariantes;
- tanda normal, finalización anticipada y muerte súbita;
- solo `saved` acredita parada;
- selección de tiradores limitada a la plantilla correcta;
- correcciones versionadas sin duplicar aprendizaje;
- `team_match_stats` sigue representando los 90 minutos;
- la evidencia por partes no cambia ninguna predicción de 90 minutos;
- la evidencia de prórroga solo cambia xG y probabilidades de prórroga;
- probabilidades condicionales de prórroga normalizadas;
- snapshots congelados antes de incorporar datos del partido;
- auditoría de 90 minutos aunque el partido llegue a prórroga;
- auditoría condicional de prórroga y tanda solo cuando se disputan;
- bracket con marcador a 120' y penaltis por equipo entre paréntesis;
- diseño de barras de eliminatorias preservado;
- renderizado de escritorio y móvil sin desbordamientos;
- suite completa existente en verde.

## Fuera de alcance

- Ingesta en directo.
- Repartir por fases las estadísticas individuales de jugador.
- Hacer que la separación de primera y segunda parte modifique el modelo de 90 minutos.
- Crear mercados de córners, tarjetas u otros volúmenes específicos de prórroga.
- Reinterpretar o corregir retrospectivamente la base de Transfermarkt con las nuevas categorías manuales.
- Sumar los tantos de la tanda al marcador oficial.
