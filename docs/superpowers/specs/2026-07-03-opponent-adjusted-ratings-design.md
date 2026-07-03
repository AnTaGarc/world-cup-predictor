# Ratings ajustados por fuerza del rival

Fecha: 2026-07-03
Estado: aprobado verbalmente (investigación previa en la conversación; contraste con mercado bet365 en España-Portugal M93)

## 1. Problema

`ratings.build_team_ratings` calcula ataque/defensa con goles brutos sin ajuste por
rival: marcar 4 a Arabia Saudí puntúa igual que marcárselos a Francia, y encajar 0
contra Cabo Verde vale lo mismo que contra Brasil. El `opponent_factor` (±10%) solo
existe en `explain_team_form` — texto de la UI — y no toca la predicción.

Evidencia del sesgo (España-Portugal, octavos):

- Modelo: xG 1.71/0.72; 1X2 58.2/27.4/14.4; avance España 76.8%.
- Mercado (bet365, sin margen): 51.2/26.3/22.5; avance España 65.6%.
- Elo pre-torneo (2120 vs 2010): implica ~60% de avance.
- El empate está clavado (estructura Poisson sana); el sesgo está en la entrada xG.

Patrón sistémico: sobrevalora a equipos con calendario blando en racha; infravalora
a equipos con calendario duro. Se amplifica porque perfil deep, ajuste xG de forma
y shifts 5b comparten la misma fuente de evidencia.

## 2. Diseño

### 2.1 Normalización iterativa dentro de `build_team_ratings`

Se añade un parámetro `iterations: int = 3`. La pasada 1 es el cálculo actual
(sin ajuste). Las pasadas siguientes recalculan usando los ratings de la pasada
anterior para normalizar cada partido:

- **Goles a favor**: `eff_gf = goles * (1 / clamp(opp_defense, 0.60, 1.60))`.
  Marcar contra defensa débil (defense > 1, encaja mucho) se descuenta; contra
  defensa fuerte (defense < 1) se premia.
- **Goles en contra**: `eff_ga = goles / clamp(opp_attack, 0.60, 1.60)`.
  Encajar contra ataque potente es excusable; contra ataque débil, agravante.
- **Peso defensivo de la muestra**: las porterías a cero contra ataques débiles
  no deben acreditar defensa de élite (la división no afecta al 0). El peso del
  partido para el término defensivo se multiplica por `clamp(opp_attack, 0.60, 1.60)`:
  un clean sheet ante Cabo Verde aporta menos certeza defensiva que ante Brasil.
- **Peso ofensivo de la muestra**: simétrico, se multiplica por
  `clamp(opp_defense_strength, 0.60, 1.60)` donde `opp_defense_strength = 1/opp_defense`
  reescalado: no marcar contra una defensa débil pesa más en contra.

Todo lo demás (recencia 450d, pesos por tipo, shrink `min(1, sample/8)`, suelos
0.35) se conserva. La firma pública no cambia (parámetro nuevo con default), por
lo que todo el downstream (`expected_goals_for_match`, Poisson, mercados) queda
intacto.

### 2.2 Coherencia de la UI

`explain_team_form` pasa a mostrar el factor de normalización realmente aplicado
(el de la última iteración) en lugar del `opponent_factor` cosmético actual, para
que el desglose de la UI describa el cálculo real.

### 2.3 Lo que NO se toca

- `derive_xg_factors_from_profile` (capado ±20%, secundario).
- Ajuste xG de forma avanzada, shifts 5b, capa de flujo.
- La estructura Poisson/Dixon-Coles.

Un solo cambio quirúrgico en la base; si tras esto el sesgo residual sigue siendo
relevante, las capas superiores se revisan en proyectos separados.

## 3. Puerta de validación (obligatoria antes de activar)

Script de comparación A/B `scripts/backtest_opponent_ratings.py`, que reconstruye
las probabilidades 1X2 pre-kickoff de los ~79 partidos cerrados del Mundial por la
misma vía que `backfill_live_residuals.py`, con ratings antiguos (`iterations=1`)
y nuevos (`iterations=3`):

- **Gate 1**: el log-loss agregado con ratings nuevos no empeora (mejora o queda
  dentro de +0.5% del antiguo).
- **Gate 2**: el xG de España-Portugal se estrecha hacia el rango mercado/Elo
  (avance de España < 72%).
- Si falla cualquiera de los dos: no se activa; se documenta el resultado y se
  replantea.

## 4. Tests unitarios

1. Con rivales de fuerza idéntica, `iterations=3` reproduce el resultado de
   `iterations=1` (compatibilidad hacia atrás).
2. Marcar 3 goles a una defensa débil produce menos ataque que marcárselos a una
   defensa fuerte.
3. Clean sheet contra ataque débil produce peor (mayor) rating defensivo que el
   mismo clean sheet contra ataque fuerte.
4. La normalización es estable: `iterations=3` vs `iterations=6` difieren < 1e-6.
5. Suite completa sin regresiones (los tests existentes de ratings/poisson/backtest
   pueden requerir actualización de valores pineados; cambio esperado y documentado).

## 6. Resultado de la puerta de validación (2026-07-03)

- Gate 1 (log-loss, 85 partidos cerrados): APROBADA. 0.8996 -> 0.8539
  (+5.08% relativo), aciertos 48/85 -> 52/85.
- Gate 2 (España-Portugal hacia mercado): FALLIDA en dirección contraria —
  el ajuste sube a España (61.2% vs 56.7% legacy; mercado 51.2%). Causa
  trazable: los ratings usan ~3 años de historia y el ajuste premia ahora
  los títulos de España contra élite (Euro 2024, Nations League), más de lo
  que descuenta el grupo blando del Mundial. No es el bug original.
- Decisión del usuario: ACTIVAR pese a Gate 2 (la evidencia agregada de 85
  partidos prevalece sobre la heurística de un partido contra un mercado
  con sus propios sesgos). `iterations=3` queda como default.
