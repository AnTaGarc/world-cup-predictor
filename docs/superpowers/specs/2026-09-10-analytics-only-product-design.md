# Reconversión a producto de analítica deportiva

**Fecha:** 2026-09-10  
**Estado:** aprobado por el usuario  
**Objetivo:** retirar de la aplicación y del repositorio público la funcionalidad de apuestas, cuotas y EV sin perder datos deportivos, capacidad predictiva, actualización automática, calibración ni auditorías.

## 1. Resultado de producto

La aplicación se presentará como un proyecto de IA y análisis de datos del Mundial 2026. Sus capacidades principales serán:

- ingestión automática y manual de datos con procedencia;
- modelado probabilístico de resultados y marcadores;
- estimaciones estadísticas por equipo;
- proyecciones estadísticas de jugadores;
- análisis específico de eliminatorias, prórrogas y penaltis;
- evaluación temporal, Brier, log-loss y calibración;
- auditorías predicho frente a real;
- control de cobertura, frescura y calidad de datos.

No mostrará ni documentará casas de apuestas, cuotas, cuota justa, probabilidad implícita derivada de cuotas, EV, edge, picks, líneas de bookmaker ni mercados apostables.

## 2. Invariantes de conservación

La limpieza no puede borrar, invalidar ni dejar inaccesible ninguno de estos activos:

- `observations` y todas las deep stats importadas o revisadas;
- `team_match_stats`, `player_match_stats` y estadísticas por periodos;
- resultados, calendario, equipos, jugadores, alineaciones y disponibilidad;
- evidencias, capturas, JSON revisados, hashes, procedencia y decisiones de revisión;
- históricos internacionales, StatsBomb, Kaggle, GitHub WC2026, martj42 y otros datasets deportivos;
- modelos entrenados, ratings, forma, correcciones, contexto de plantilla y perfiles de equipo;
- snapshots predictivos necesarios para auditorías;
- resultados de evaluación útiles para Brier, log-loss y calibración;
- modelos y evidencias de prórroga, tandas y penaltis;
- la presentación y semántica de las auditorías por partido, incluida la tabla predicho/real por equipo mostrada en la referencia visual del usuario.

Antes y después de cualquier migración se comprobarán recuentos y huellas de las tablas deportivas críticas. La migración fallará sin hacer cambios si no puede demostrar que solo está transformando o eliminando información de cuotas.

## 3. Arquitectura de interfaz

### 3.1 Navegación principal

La página `Predicción y valor` pasará a llamarse `Análisis predictivo`. La navegación interna será:

1. `Modelo`
2. `Marcadores`
3. `Estadísticas por equipo`
4. `Jugadores`
5. `Datos y fuentes`
6. `Historial`

Se eliminará la vista `Mercados y EV`.

### 3.2 Resumen

El hero describirá forma, cobertura, predicción y calibración. El contador `Cuotas` desaparecerá; podrá sustituirse por un indicador analítico ya disponible, como número de evaluaciones, partidos con deep stats o fuentes activas.

### 3.3 Modelo predictivo

El resultado del partido se presentará como tres clases explícitas:

- victoria de la primera selección;
- empate;
- victoria de la segunda selección.

No se utilizará `1X2` como etiqueta visible. La tabla `Mercados modelados` será reemplazada por un resumen de objetivos predictivos relevantes. No se mostrarán doble oportunidad, empate no válido, over/under ni ambos marcan como productos apostables.

La explicación del ensamble, xG, forma, localía, perfil profundo, calibración y señales ML se conservará.

### 3.4 Marcadores

Se conservará la matriz conjunta de goles existente, basada en goles esperados, binomial negativa, corrección Dixon-Coles y armonización con el clasificador de resultado.

La vista mostrará:

- marcador con mayor probabilidad individual;
- otros marcadores plausibles ordenados por probabilidad;
- goles esperados de cada selección;
- mapa de calor de la distribución de marcadores;
- masa probabilística cubierta por los marcadores destacados, si aporta claridad.

Las estructuras visibles `Exact Score`, `Exact Score (alt)` y `Exact Score Grid` se traducirán a conceptos analíticos. Se eliminará cualquier mención a usar o no usar el resultado como apuesta.

### 3.5 Estadísticas estimadas por equipo

Se eliminará el panel agregado `Mercados de volumen`, incluidas línea, probabilidad de más, rangos over/under y lean.

Se conservará el cálculo central por equipo para:

- xG y goles esperados;
- tiros;
- tiros a puerta;
- córners;
- tarjetas;
- posesión;
- faltas;
- fueras de juego;
- cualquier otra métrica deportiva con datos suficientes.

El cálculo seguirá combinando producción propia, concesión rival y media del torneo, junto con ajustes deportivos como la tendencia arbitral. Sus tipos y nombres dejarán de usar `market`: por ejemplo, `TeamMarketLine` se convertirá en una proyección estadística y `MARKET_CATALOG` en un catálogo de métricas.

Estas estimaciones seguirán alimentando sin cambios semánticos la auditoría predicho/real.

### 3.6 Jugadores

La mayor parte de la sección se conservará. Seguirá permitiendo:

- seleccionar equipo, posición, jugador y estadística;
- explorar la plantilla y filtrar por minutos;
- ver tasa observada por 90;
- estimar minutos y probabilidad de titularidad;
- proyectar goles, asistencias, tiros, tiros a puerta, tarjetas, pases, paradas, goles concedidos y porterías a cero cuando existan datos;
- mostrar muestra, confianza, procedencia y explicación;
- usar los ajustes profundos de portero y contexto rival existentes.

Se eliminarán la entrada de cuota y la tabla de cuota justa/EV. La actual `línea` se presentará como `umbral estadístico`; la probabilidad indicará la posibilidad de que el jugador alcance o supere dicho umbral, sin referencia a una oferta de apuestas. Cuando la métrica sea más comprensible como conteo esperado, se mostrará también su expectativa.

### 3.7 Datos, historial y auditorías

`Datos / SofaScore` se renombrará `Datos y fuentes`; SofaScore seguirá identificado como una posible fuente de evidencia manual o revisada.

`Guardado` se renombrará `Historial`. Mostrará importaciones, snapshots predictivos y evaluaciones, pero no cuotas.

Las auditorías conservarán:

- marcador final;
- Brier medio y otras métricas de evaluación;
- estadísticas observadas;
- comparación de marcador modal y xG;
- comparación por equipo de predicho, real y desviación;
- auditoría por fases en eliminatorias.

Se corregirán textos como `apuestas evaluadas` por `predicciones evaluadas`.

## 4. Separación de proveedores

### 4.1 Proveedores y fuentes que se conservan

Se mantienen todos los proveedores deportivos y todos sus datos:

- API-Sports / API-Football;
- APIFootball;
- Football-Data.org;
- TheSportsDB;
- SportMonks;
- StatsBomb Open Data;
- datasets `swaptr_wc2026_matches`, `swaptr_wc2026_teams` y `swaptr_wc2026_players`;
- todos los datasets `github_wc2026_*` de equipos, árbitros, partidos, estadísticas, alineaciones, eventos y jugadores;
- martj42 local y remoto;
- OpenFootball, Transfermarkt, xgabora como histórico deportivo, Kaggle y datasets locales revisados;
- capturas, JSON revisados, SofaScore manual/revisado y evidencia web;
- cualquier fuente que aporte estadísticas, alineaciones, disponibilidad, resultados, calendario, entidades o contexto deportivo.

La matriz de credenciales seguirá incluyendo las APIs deportivas aunque alguna no esté configurada actualmente. `xgabora` conservará su función de histórico de clubes, pero su dominio `historical_odds` se retirará del catálogo.

### 4.2 Proveedores que se eliminan

Solo se eliminan los proveedores inequívocamente exclusivos de cuotas:

- `oddspapi_winamax`;
- `the_odds_api`;
- la fuente de catálogo `exact_bookmaker`.

También se retirarán del proyecto las referencias a `ODDSPAPI_API_KEY` y `THE_ODDS_API_KEY`. No se modificarán valores del entorno del usuario fuera del repositorio.

### 4.3 Recolector externo

La aplicación seguirá invocando el recolector local para obtener estadísticas deportivas. El contrato se simplificará para no solicitar ni devolver estado de cuotas. Si una versión heredada del recolector incluye `market_comparisons`, la aplicación ignorará ese bloque y seguirá importando íntegramente:

- estadísticas;
- resultados;
- alineaciones;
- disponibilidad;
- fuentes;
- cobertura, faltantes y frescura.

La skill externa `analisis-de-datos` no forma parte de este repositorio y no se reescribirá como parte de esta reconversión. La aplicación quedará desacoplada de su salida de odds.

## 5. Modelo de dominio y servicios

El modelo interno dejará de tratar toda predicción como un mercado. Se introducirán nombres neutrales para objetivos y proyecciones, manteniendo compatibilidad temporal con snapshots antiguos durante la migración.

La generación del partido se separará conceptualmente en:

1. estimación de xG y matriz de marcadores;
2. clasificación victoria/empate/derrota;
3. proyecciones por equipo;
4. proyecciones por jugador;
5. contexto de eliminatoria;
6. payload de auditoría.

No se conservarán cálculos de cuota justa, probabilidad implícita de bookmaker, EV ni comparación con precios.

Las probabilidades de superar un umbral deportivo pueden mantenerse porque son salidas estadísticas independientes de una cuota. Sus nombres y explicaciones no usarán terminología de apuestas.

## 6. Persistencia y migración

### 6.1 Datos que se eliminan

En `data/worldcup.sqlite` se eliminarán exclusivamente:

- las 37 filas de `manual_odds`, todas procedentes de Winamax;
- la tabla `manual_odds` y sus accesos cuando la migración sea segura;
- entradas `exact_bookmaker` y `odds_api` del catálogo interno;
- cualquier metadato cuya única función sea describir cuotas.

En la base externa `sports-data/sports.db` se podrán eliminar exclusivamente:

- las 32 filas de `odds_snapshots` asociadas a `oddspapi_winamax`;
- sus dos fuentes stale, si no están referenciadas por ninguna estadística, alineación, disponibilidad o evidencia deportiva;
- el registro del proveedor `oddspapi_winamax`, solo después de comprobar integridad referencial.

La base externa está fuera del repositorio. Su limpieza será una operación independiente, con copia de seguridad recuperable y verificación previa/posterior. No será requisito para que la aplicación funcione sin apuestas.

### 6.2 Snapshots predictivos

Los 76 snapshots existentes no se eliminarán. Actualmente contienen `expected_xg`, `deep_count`, `prior_deep_samples`, predicciones, resultado principal y contexto de eliminatoria.

La migración transformará su payload para conservar:

- equipos;
- xG esperado;
- cobertura profunda y muestras previas;
- probabilidades victoria/empate/derrota;
- distribución y principales marcadores;
- prórroga, tanda, cobertura y jugadores de penaltis.

Se descartarán únicamente filas derivadas que solo representen doble oportunidad, empate no válido u over/under. Los nombres de claves podrán modernizarse, pero el lector soportará temporalmente el formato legado hasta verificar la migración completa.

### 6.3 Backtesting y calibración

Los resultados de backtesting no se borrarán de forma indiscriminada. Se conservarán las evaluaciones útiles y se renombrarán sus objetivos:

- `1X2` → resultado del partido;
- `Exact Score Top1/Top3` → cobertura de marcadores;

Las evaluaciones de eliminatoria y penaltis se conservan. Las filas dedicadas exclusivamente a objetivos over/under o BTTS podrán eliminarse o archivarse de la vista activa, después de confirmar que ningún ajuste deportivo vigente depende de ellas.

El esquema y las APIs internas usarán términos como `prediction_target`, `projection` o `evaluation_group` en lugar de `market` cuando sea razonable hacerlo sin una migración de riesgo desproporcionado.

## 7. Documentación y repositorio público

El README se reescribirá para presentar:

- problema analítico;
- arquitectura de datos;
- fuentes y precedencia;
- modelos estadísticos y ML;
- explicabilidad;
- auditoría y calibración;
- ejecución local y pruebas;
- limitaciones metodológicas.

Se eliminarán ejemplos Winamax, CSV de cuotas y variables de APIs de odds. Los documentos históricos que describan el producto de apuestas se actualizarán, retirarán o moverán fuera de la documentación pública activa. Las especificaciones sobre tandas que aclaren que no existe integración con bookmakers pueden conservar la idea con redacción neutral, sin vocabulario promocional de apuestas.

Los fixtures y pruebas exclusivos de cuotas/EV se eliminarán. Los tests mixtos se adaptarán para cubrir proyecciones deportivas y conservación de datos.

## 8. Pruebas y criterios de aceptación

La implementación seguirá TDD. Antes de cambiar cada comportamiento se añadirá una prueba que falle por la presencia del comportamiento antiguo o la ausencia del nuevo.

La reconversión se considerará correcta cuando:

1. La interfaz navegable no contenga apuestas, cuotas, EV, edge, picks, bookmakers ni Winamax.
2. El repositorio activo no contenga módulos o fixtures exclusivos de odds/EV.
3. Las APIs deportivas sigan configurables y el refresco diario conserve todos sus proveedores actuales.
4. `swaptr_wc2026_players` y `github_wc2026_player_stats` sigan alimentando la sección Jugadores.
5. La sección Jugadores produzca probabilidades y expectativas sin pedir cuota.
6. Las estadísticas estimadas por equipo sigan disponibles y alimenten la auditoría.
7. Las auditorías abiertas y cerradas mantengan marcador, xG, Brier, predicho/real y desglose por equipo.
8. Los snapshots existentes puedan leerse o migrarse sin perder campos analíticos.
9. Los recuentos y huellas de las tablas deportivas protegidas coincidan antes y después de la migración.
10. Solo desaparezcan datos identificados de forma demostrable como cuotas.
11. La suite completa pase y la aplicación Streamlit supere pruebas de navegación de todas las secciones.
12. Una búsqueda final de terminología de apuestas no encuentre referencias activas, salvo notas de migración estrictamente necesarias y claramente históricas.

## 9. Estrategia de entrega

La implementación se dividirá en cambios verificables:

1. contratos neutrales de predicción y proyección;
2. interfaz analítica y jugadores sin cuotas;
3. conservación y adaptación de auditorías;
4. desacoplamiento de proveedores de odds;
5. migración segura de SQLite y snapshots;
6. actualización de documentación, fixtures y pruebas;
7. auditoría final funcional, textual y de integridad de datos.

Cada etapa deberá dejar la aplicación ejecutable. Ninguna eliminación de datos se realizará antes de que existan pruebas de conservación y una copia recuperable del archivo afectado.
