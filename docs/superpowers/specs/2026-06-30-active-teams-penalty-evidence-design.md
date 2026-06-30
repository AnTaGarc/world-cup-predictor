# Evidencia histórica de penaltis para selecciones aún vivas

## Objetivo

Completar el modelo prepartido de tandas con dos fuentes de evidencia que hoy faltan: las tandas recientes de cada selección y todos los penaltis afrontados por el portero titular, tanto durante un partido como en una tanda. La recopilación, validación y precalculación se limitarán a las selecciones que continúen vivas en el Mundial 2026.

Este diseño amplía y, en materia de elegibilidad de equipos y porteros, sustituye las reglas correspondientes de `2026-06-28-pre-match-penalty-shootout-design.md`.

## Alcance dinámico de equipos

La base de datos y el cuadro resuelto serán la autoridad para determinar qué selecciones siguen vivas. Un equipo será elegible si ocupa una plaza activa en la siguiente ronda y no existe un cierre revisado que lo haya eliminado.

- La lista no se codificará manualmente.
- Alemania, Sudáfrica, Países Bajos y Japón están excluidas en el estado actual del cuadro.
- Cada nuevo cierre de eliminatoria actualizará automáticamente el conjunto elegible.
- Los datos históricos ya guardados de equipos eliminados se conservarán, pero no se refrescarán ni se precalcularán mientras estén fuera del torneo.
- Los artefactos antiguos de equipos eliminados podrán permanecer en disco, pero no se servirán para partidos activos inexistentes.

## Evidencia de tandas de cada selección

Para cada equipo vivo se recopilarán las tandas disputadas en sus tres grandes competiciones oficiales de selecciones absolutas más recientes. Se incluyen Mundial, campeonato continental, Nations League y equivalentes oficiales. Se excluyen amistosos, clubes y categorías inferiores.

Cada tanda se almacenará a nivel de lanzamiento con:

- competición, edición, fecha y eliminatoria;
- equipos, orden de lanzamiento y resultado final de la tanda;
- lanzador, selección y portero rival cuando la fuente lo identifique;
- resultado normalizado: gol, parada, fuera o poste;
- URL, proveedor, fecha de consulta y clave estable de procedencia.

El resultado agregado de ganar o perder una tanda será descriptivo. El modelo utilizará los lanzamientos individuales para evitar duplicar la misma evidencia con un bonus o castigo adicional por el resultado final.

## Evidencia de los porteros titulares

Antes de una alineación confirmada se elegirá el portero con mayor probabilidad de titularidad. Después, la alineación confirmada tendrá prioridad. Ese portero permanecerá fijo en todos los caminos simulados salvo que exista una sustitución de portero confirmada y disponible como evidencia prepartido.

Para cada posible titular de un equipo vivo se recopilarán todos los penaltis senior verificables que haya afrontado, en clubes y selección:

- penaltis durante el juego;
- lanzamientos de tandas;
- fecha, competición, lanzador y resultado;
- distinción entre parada del portero y fallo no atribuible al portero;
- fuente y momento de recuperación.

Los registros de portero se almacenarán separadamente del historial de lanzadores. No se crearán lanzadores sintéticos ni se asignarán al equipo del guardameta, porque eso contaminaría la conversión ofensiva de la selección.

## Fuentes y calidad

Las fuentes prioritarias serán:

1. FIFA, UEFA y confederaciones para tandas internacionales.
2. Actas o informes oficiales de competiciones cuando estén disponibles.
3. Transfermarkt para el historial amplio de penaltis de porteros y lanzadores, contrastado con fuentes oficiales en tandas relevantes.

Una ausencia en una fuente no se interpretará como cero penaltis. Los registros ambiguos quedarán pendientes de revisión. Las identidades se normalizarán mediante los identificadores existentes y alias revisados.

Toda consulta respetará el corte temporal del inicio del partido. Ningún dato posterior podrá entrar en una predicción prepartido ni en su huella de entrada.

## Persistencia y procedencia

Se añadirán estructuras separadas para:

- cobertura de las tres competiciones revisadas por selección, incluso cuando no hubo tandas;
- tandas históricas internacionales;
- lanzamientos históricos de esas tandas;
- penaltis afrontados por porteros;
- fuentes y estado de revisión.

Las escrituras serán idempotentes mediante claves de proveedor. La procedencia incluirá URL, fecha de recuperación y contenido normalizado suficiente para auditar cada cálculo. Los datos reales ya existentes en `worldcup.sqlite` no se reemplazarán ni se borrarán durante la migración.

Registrar una competición revisada sin tandas es obligatorio para distinguir evidencia negativa verificada de una ausencia de datos.

## Cálculo

### Lanzadores

El perfil de cada lanzador combinará penaltis durante el juego y tandas. Se aplicará decaimiento temporal y mayor peso a los lanzamientos de tanda. Una prior Beta centrada en la conversión global limitará el efecto de muestras pequeñas.

### Porteros

El perfil del portero utilizará únicamente penaltis realmente afrontados por ese guardameta. Las paradas, goles encajados y fallos fuera/poste se conservarán por separado. La probabilidad de parada se regularizará con una prior global y decaimiento temporal; los lanzamientos de tanda tendrán más peso que los penaltis durante el juego.

La tasa general de paradas solo será un respaldo débil cuando no haya evidencia específica de penaltis. La interfaz deberá indicar expresamente cuándo se usa ese respaldo.

### Experiencia de selección

Los lanzamientos de las tres competiciones recientes alimentarán los perfiles de los jugadores aún convocados y una señal contextual pequeña para la selección. Esta señal reflejará cobertura y experiencia reciente, no una penalización directa por derrotas históricas.

### Simulación

Los jugadores de campo continuarán sujetos al simulador de sustituciones hasta el minuto 120. El portero titular quedará fijado. Cada lanzamiento combinará la posterior del lanzador y la posterior del portero rival. Los componentes de 90 minutos, prórroga y tanda seguirán normalizados y separados.

## Precalculo y actualización

El precalculador seleccionará únicamente partidos futuros entre equipos vivos. La huella incluirá:

- conjunto de equipos activos y versión del cuadro;
- convocatorias y alineaciones;
- tandas internacionales históricas;
- historial de penaltis de lanzadores y porteros;
- corte temporal y versión del modelo.

Un cambio relevante invalidará el JSON anterior. Streamlit nunca ejecutará la simulación completa durante el renderizado; cargará un artefacto vigente o mostrará un fallback explícitamente incompleto.

## Interfaz y auditoría

El contexto de penaltis mostrará:

- porteros titulares utilizados;
- penaltis afrontados, paradas, goles y fallos ajenos al portero;
- separación entre juego y tandas;
- tandas recientes cubiertas para cada selección;
- jugadores con historial y jugadores sostenidos por la prior;
- fuente, fecha de corte y advertencias de cobertura;
- versión del modelo y vigencia del artefacto.

No se mostrará una precisión engañosa cuando falte la evidencia específica del portero o de las tandas.

## Acumulado opcional de 120 minutos

La entrega también integrará el trabajo ya implementado para estadísticas por periodo:

- en prórroga o penaltis son obligatorias las cuatro partes atómicas;
- el acumulado de 120 minutos es opcional;
- si existe, compara sus métricas aditivas con la suma de primera parte, segunda parte y las dos partes de la prórroga;
- una discrepancia identifica equipo, métrica, suma calculada y total importado;
- los acumulados ocultos de 90 minutos y prórroga no bloquean el cierre.

En Alemania-Paraguay la validación experimental detectó `shots_on_target` de Alemania (suma 7, total 6) y `saves` de Paraguay (suma 7, total 6). Estas discrepancias no se corregirán automáticamente: requieren revisar las capturas o aceptar que el proveedor revisó su total final.

## Pruebas y criterio de finalización

La tarea no se considerará terminada hasta demostrar:

- selección dinámica de equipos vivos y exclusión de eliminados;
- conservación de datos históricos de eliminados sin nuevas consultas;
- importación idempotente y con corte prepartido;
- separación estricta de evidencia de lanzador y portero;
- diferenciación entre parada, gol y fuera/poste;
- uso del portero confirmado o del titular más probable;
- portero fijo en todos los caminos simulados;
- mayor peso de tandas y recencia con regularización de muestras pequeñas;
- invalidación de artefactos obsoletos;
- visualización de cobertura y fuentes;
- validación del acumulado opcional de 120 minutos;
- suite focal y suite completa verdes en el resultado integrado en `main`.

## Fuera de alcance

- Equipos ya eliminados, salvo conservar sus datos existentes.
- Amistosos y selecciones juveniles para la señal de experiencia de equipo.
- Sustituciones o eventos en directo.
- Inventar resultados cuando una fuente no distingue parada y fallo.
- Aplicar un bonus psicológico o castigo manual por ganar o perder una tanda.
