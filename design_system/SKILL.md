# Contexto técnico — Analista del Mundial 2026

Lee esto antes de proponer cualquier diseño. La app es **Streamlit** (Python),
no una web hecha a mano: hay un contrato estricto entre lo que se puede
diseñar y lo que se puede implementar.

## Qué es la app y cómo funciona

Aplicación de análisis y predicción del Mundial 2026, en **castellano**,
**modo claro**, usada a diario y **mucho desde iPhone** (viewport estrecho).
Cinco páginas navegadas por radio en el sidebar oscuro:

1. **Resumen** — hero, KPIs, partidos de hoy, estado de datos diarios,
   bracket eliminatorio.
2. **Predicción y valor** — la vista principal por partido: 1X2 con barras,
   marcadores exactos, proyecciones estadísticas, embudo de
   avance en eliminatorias (90'/prórroga/penaltis), auditoría postpartido
   por fases.
3. **Jugadores** — rankings de impacto/goles/asistencias/tiros.
4. **Calibración** — cierres de partido, importación de estadísticas por
   periodos, tandas de penaltis, recalibraciones.
5. **Calidad de datos** — cobertura, fuentes, salud de proveedores.

Los datos vienen de un motor predictivo en Python + SQLite. El diseño no
puede cambiar el flujo de datos, solo su presentación.

## La base técnica y sus consecuencias

- **Streamlit renderiza los widgets nativos** (selectbox, number_input,
  dataframe, expander, tabs, radio, button, metric). Su DOM interno NO es
  nuestro: solo se estiliza vía CSS inyectado con selectores
  `data-testid`, que son frágiles. Rediseñar la *estructura* interna de un
  widget nativo no es posible.
- **Los bloques HTML propios sí son 100% nuestros**: hero, pills, callouts,
  barras de probabilidad, tarjetas de marcador, embudo KO, auditoría por
  fases, tablas HTML custom, bracket. Se emiten con
  `st.markdown(html, unsafe_allow_html=True)`. Aquí el diseño tiene
  libertad total de markup y CSS.
- **CSS global único**: todo vive en `theme.py` como una hoja inyectada.
  Tokens en `:root` (colores, radios, sombras, espaciado 4·8·12·16·24·32·48).
- **Sin JavaScript propio**: nada de interacciones custom, drag&drop,
  tooltips JS, charts interactivos propios ni animaciones disparadas por
  estado. Solo CSS (transiciones/keyframes) y los widgets de Streamlit.
  Los gráficos complejos usan Altair (limitado a su gramática).
- **Modelo de ejecución por rerun**: cada interacción re-ejecuta el script.
  Los patrones tipo modal, wizard multipaso fluido o estados locales ricos
  no encajan; expanders, tabs y segmented controls sí.
- **Layout por columnas** (`st.columns`): rejillas simples con proporciones
  fijas. En iPhone las columnas colapsan verticalmente — diseñar siempre
  la versión estrecha primero.
- **Tipografía**: Inter (ya cargada). Se pueden proponer pesos/tamaños/
  features (tnum activo en números), no fuentes nuevas sin justificación.

## Qué SÍ se puede cambiar (pide lo que quieras aquí)

- Todos los **tokens**: paleta, radios, sombras, espaciado, jerarquía
  tipográfica.
- Cualquier **bloque HTML custom** al completo: hero, pills, callouts,
  barras 1X2, score-cards, embudo KO, auditoría por fases, expander del
  dataset externo, bracket, tablas HTML propias.
- **Estilo superficial de widgets nativos**: colores, bordes, radios,
  hover, tipografía de botones/tabs/metrics/dataframes/expanders.
- **Composición de página**: orden de secciones, agrupación, densidad,
  aire, encabezados de sección.
- Iconografía inline SVG dentro de bloques custom.

## Qué NO es posible (no lo propongas)

- Reestructurar el DOM interno de widgets nativos (p. ej. un selectbox
  con imágenes dentro, un dataframe con celdas custom renderizadas).
- Interactividad JavaScript: tooltips ricos, sliders custom, charts
  interactivos propios, sticky headers complejos, gestos.
- Modales reales, toasts posicionados, wizard con transiciones.
- Navegación distinta al sidebar de Streamlit (se puede re-estilizar,
  no reemplazar por una top-bar propia).
- Modo oscuro por ahora (decisión de producto: modo claro).
- Cambiar el idioma (castellano) o eliminar información de auditoría:
  regla de honestidad del producto — probabilidades, fuentes y muestras
  siempre visibles; el diseño puede jerarquizarlas, nunca ocultarlas.

## Reglas del producto que el diseño debe respetar

- El estado nunca se comunica solo con color: siempre etiqueta + valor
  (los pills llevan texto, las barras llevan porcentaje).
- Números con figuras tabulares alineados.
- Densidad informativa alta pero escaneable: el usuario analiza partidos
  a diario, prima la velocidad de lectura sobre la espectacularidad.
- Los escudos de selecciones son PNG cuadrados 18–44 px sobre fondos
  claros u oscuros (en el hero llevan chip translúcido).

## Cómo se implementa lo que diseñes

Cada card de este proyecto refleja CSS/markup real de `theme.py`. Cuando
una propuesta se apruebe, el desarrollador (Claude Code) la integra
componente a componente en `theme.py`/`pages.py` con tests de contrato.
Propón cambios **por componente**, no rediseños monolíticos de toda la
app: se integran y validan uno a uno.
