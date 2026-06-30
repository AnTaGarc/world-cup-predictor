# Simplificación de estadísticas por periodo en eliminatorias

## Objetivo

Mostrar y exigir únicamente los JSON por periodo que el usuario puede obtener realmente, evitando acumulados pendientes u opcionales que no forman parte del flujo operativo.

## Diseño aprobado

El selector de cómo terminó la eliminatoria determina exactamente los cargadores visibles:

- `regulation`: solo `regulation_total` (acumulado de 90 minutos), obligatorio.
- `extra_time`: `first_half`, `second_half`, `extra_time_first` y `extra_time_second`, todos obligatorios; `full_match_total` es un control opcional.
- `shootout`: los mismos cuatro periodos obligatorios que `extra_time`, más `full_match_total` como control opcional.

En los caminos de prórroga y penaltis no se mostrarán `regulation_total` ni `extra_time_total`. Si existen importaciones antiguas para esos periodos, permanecerán guardadas y consultables, pero no condicionarán el cierre nuevo.

## Validación

- Un partido decidido en 90 minutos solo puede cerrarse cuando se haya importado `regulation_total`.
- Un partido decidido en prórroga o penaltis solo puede cerrarse cuando estén importados los cuatro periodos atómicos visibles.
- Los periodos ocultos nunca aparecerán como pendientes, opcionales ni bloqueantes.
- `full_match_total` nunca será obligatorio ni sustituirá a las cuatro partes. Si se importa, sus métricas aditivas se compararán con la suma de `first_half`, `second_half`, `extra_time_first` y `extra_time_second`; una discrepancia bloqueante identificará equipo, métrica, suma calculada y total importado.
- Las validaciones de marcador, porteros y secuencia de tanda permanecen intactas.

## Interfaz

El texto introductorio explicará que:

- para un partido decidido en 90 minutos se importa su acumulado;
- si hubo prórroga, se importan por separado las dos partes de los 90 minutos y las dos partes de la prórroga;
- el acumulado de 120 minutos puede añadirse opcionalmente para comprobar que las cuatro partes se recogieron correctamente.

## Enfoques descartados

1. Mantener todos los acumulados como opcionales: conserva ruido visual y estados que el usuario no puede completar. Se conserva únicamente el total de 120 minutos porque sí aporta una comprobación independiente de las cuatro partes.
2. Mantener alternativas entre partes y acumulados: no refleja los archivos disponibles y hace ambiguo qué se exige.
3. Ocultarlos solo con CSS: dejaría la validación y el estado interno contradiciendo la interfaz.

## Pruebas

- Las secciones visibles deben coincidir exactamente con cada vía de decisión.
- La ruta de 90 minutos debe rechazar un borrador sin `regulation_total`.
- Las rutas de prórroga y penaltis deben rechazar cualquier borrador al que le falte uno de los cuatro periodos atómicos.
- Los acumulados ocultos no deben satisfacer por sí solos esas rutas.
- El acumulado opcional de 120 minutos debe detectar una discrepancia con la suma de los cuatro periodos atómicos.
