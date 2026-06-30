# Simplificación de estadísticas por periodo en eliminatorias

## Objetivo

Mostrar y exigir únicamente los JSON por periodo que el usuario puede obtener realmente, evitando acumulados pendientes u opcionales que no forman parte del flujo operativo.

## Diseño aprobado

El selector de cómo terminó la eliminatoria determina exactamente los cargadores visibles:

- `regulation`: solo `regulation_total` (acumulado de 90 minutos), obligatorio.
- `extra_time`: `first_half`, `second_half`, `extra_time_first` y `extra_time_second`, todos obligatorios.
- `shootout`: los mismos cuatro periodos que `extra_time`, todos obligatorios.

En los caminos de prórroga y penaltis no se mostrarán `regulation_total`, `extra_time_total` ni `full_match_total`. Si existen importaciones antiguas para esos periodos, permanecerán guardadas y consultables, pero no condicionarán el cierre nuevo.

## Validación

- Un partido decidido en 90 minutos solo puede cerrarse cuando se haya importado `regulation_total`.
- Un partido decidido en prórroga o penaltis solo puede cerrarse cuando estén importados los cuatro periodos atómicos visibles.
- Los periodos ocultos nunca aparecerán como pendientes, opcionales ni bloqueantes.
- Las validaciones de marcador, porteros y secuencia de tanda permanecen intactas.

## Interfaz

El texto introductorio explicará que:

- para un partido decidido en 90 minutos se importa su acumulado;
- si hubo prórroga, se importan por separado las dos partes de los 90 minutos y las dos partes de la prórroga.

## Enfoques descartados

1. Mantener los acumulados como opcionales: conserva ruido visual y estados que el usuario no puede completar.
2. Mantener alternativas entre partes y acumulados: no refleja los archivos disponibles y hace ambiguo qué se exige.
3. Ocultarlos solo con CSS: dejaría la validación y el estado interno contradiciendo la interfaz.

## Pruebas

- Las secciones visibles deben coincidir exactamente con cada vía de decisión.
- La ruta de 90 minutos debe rechazar un borrador sin `regulation_total`.
- Las rutas de prórroga y penaltis deben rechazar cualquier borrador al que le falte uno de los cuatro periodos atómicos.
- Los acumulados ocultos no deben satisfacer por sí solos esas rutas.
