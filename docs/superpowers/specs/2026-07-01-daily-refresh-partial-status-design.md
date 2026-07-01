# Diseño: estado parcial de actualización diaria

## Problema

El panel muestra `Calendario diario: Obsoleto` cuando una fuente falla y las demás
están vigentes, sin cambios o actualizadas. El estado `stale` se calcula únicamente a
partir de que no haya descargas nuevas y exista al menos un fallo con caché, por lo que
confunde un fallo parcial del banco de equipos con un calendario completamente obsoleto.

## Comportamiento esperado

- `updated`: al menos una fuente cambió y ninguna falló.
- `partial`: al menos una fuente falló y al menos otra terminó correctamente, quedó sin
  cambios o se omitió por seguir vigente.
- `stale`: todas las fuentes consultadas fallaron, pero existe caché aprovechable.
- `failed`: todas las fuentes consultadas fallaron y no existe caché.
- `current`: no hubo fallos ni cambios porque las fuentes siguen vigentes o no cambiaron.

La etiqueta del resumen será `Datos diarios`, no `Calendario diario`, porque agrega
partidos, equipos, jugadores y calendario externo.

## Detalle del error

Cuando haya fallos, el panel mostrará debajo de las píldoras una explicación desplegable
con el nombre de cada proveedor fallido y el último mensaje guardado en
`dataset_refresh_checks`. El texto se limita y escapa mediante componentes Streamlit; no
se renderiza HTML procedente del proveedor.

## Persistencia y tolerancia

No se eliminan snapshots válidos ni se bloquean predicciones. Se mantiene el backoff de
una hora tras un fallo reciente. La corrección solo cambia la clasificación agregada y
la claridad del diagnóstico visual.

## Pruebas

- Una fuente fallida con caché y otra vigente produce `partial`.
- Todas las fuentes fallidas con caché producen `stale`.
- Todas fallidas sin caché producen `failed`.
- La interfaz usa `Datos diarios` y muestra el detalle de errores.
- La suite completa debe continuar pasando.
