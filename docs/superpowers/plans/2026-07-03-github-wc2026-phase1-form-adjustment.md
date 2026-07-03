# Fase 1 — Capa de ajuste por forma del torneo · Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ajustar las probabilidades 1X2 con la forma del torneo actual (eventos al minuto, xG, fatiga, disciplina de las tablas `gh_*`), mediante un desplazamiento en log-odds con coeficiente α calibrado contra los snapshots de predicción reales ya almacenados.

**Architecture:** módulo nuevo `tournament_form_adjustment.py` con features puras sobre `gh_matches`/`gh_match_events`, un score home-vs-away en [-1,1] con shrinkage por muestra, y `apply_form_adjustment` que desplaza los logits de home/away. α se calibra por búsqueda en rejilla minimizando log-loss sobre los ~63 partidos cerrados con snapshot 1X2 prepartido (base honesta: predicciones reales previas al partido). Si α óptima ≤ 0 o no mejora, se persiste 0 y la capa queda inerte. Integración en `ui/pages.py`: el ajuste se aplica a `ml_probabilities` y `deep_ml_probabilities` antes del ensemble.

**Tech Stack:** Python 3.12, sqlite3, unittest. Tests: `PYTHONPATH=src python -m unittest tests.<módulo> -v`.

## Global Constraints

- Corte temporal estricto: features de un equipo solo usan partidos gh con `kickoff_time_utc` (o `date`) anterior al kickoff del partido a predecir.
- Un único parámetro aprendido (α global); los pesos internos de las 7 features son fijos e iguales.
- Shrinkage: `weight = min(n_a, n_b) / (min(n_a, n_b) + 3)`; con 0 partidos, ajuste = 0.
- Fail-safe: α ≤ 0 en calibración ⇒ se persiste α = 0 y `apply_form_adjustment` es identidad.
- La base de calibración son los snapshots 1X2 más tempranos por partido (`prediction_snapshots`), nunca probabilidades recomputadas a posteriori.
- No modificar `outcome_ml.py`, `outcome_ml_deep.py` ni sus artefactos entrenados.
- Commits `feat(ghwc1):` / `data(ghwc1):` + trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Features (todas delta equipo A − equipo B, normalizadas y recortadas a [-1, 1])

1. `xg_delta`: media de (xG a favor − xG en contra) por partido, desde `gh_matches`; normalizador: /1.5.
2. `late_dominance`: (goles 75-90' a favor − en contra) por partido, desde `gh_match_events`; normalizador: /1.0.
3. `goal_timing`: −(minuto medio de gol propio − 60)/60 (marcar antes ⇒ positivo).
4. `fatigue`: 0.5·(días de descanso, cap 7)/7 − 0.5·(minutos de prórroga en los últimos 14 días)/30.
5. `inferiority`: −(minutos jugados con roja propia por partido)/45.
6. `momentum`: resultado del último partido (+1 victoria, 0 empate, −1 derrota).
7. `discipline`: −(tarjetas por partido − media del torneo)/3.

`score = clip(mean(deltas disponibles), -1, 1)`; features sin datos se omiten del promedio.

## Tasks

### Task 1: Módulo de features y ajuste (`tournament_form_adjustment.py`) + tests
Funciones: `build_form_features(repo, team_name, before_kickoff_iso) -> FormFeatures` (dataclass con las 7 + `matches_played`), `build_match_adjustment(repo, team_a, team_b, kickoff_iso) -> Adjustment(score, weight, detail)`, `apply_form_adjustment(probs: dict[home,draw,away], alpha, score, weight) -> dict` (shift ±α·w·s en logits de home/away, renormaliza), `calibrate_alpha(samples, grid=0..1.5 paso 0.05, ridge λ=0.01) -> Calibration(alpha, sample_size, log_loss_base, log_loss_adjusted)`.
Tests: equipo sin partidos ⇒ weight 0 y ajuste identidad; no-leakage (partido en la fecha de corte no cuenta); shift sube home y baja away con score>0 y suma 1; α=0 cuando las features no correlacionan; α>0 cuando el score predice sistemáticamente al ganador.

### Task 2: Persistencia de calibración
Tabla `outcome_adjustment_calibrations(id, model_version, calibrated_at_utc, alpha, sample_size, log_loss_base, log_loss_adjusted)`. Métodos `save_form_calibration(...)` y `latest_form_calibration()`. `build_calibration_samples(repo)` en el módulo: por cada partido WC2026 cerrado con snapshot, extrae el 1X2 más temprano (mapea selección→home/draw/away por nombres del partido), computa score/weight con corte en el kickoff y el outcome real.

### Task 3: Script `scripts/calibrate_form_adjustment.py`
Construye samples, calibra, persiste y muestra el informe. Ejecutar en real y commitear el resultado.

### Task 4: Integración en `ui/pages.py`
Tras calcular `ml_probabilities` y `deep_ml_probabilities`: cargar α cacheada (por `db_sig`), computar el ajuste del partido y aplicar a ambos dicts. Mostrar en la UI la nota con α, score y desplazamiento cuando α>0. Contract test: `apply_form_adjustment` presente en pages.py.

### Task 5: Suite completa + push
`PYTHONPATH=src python -m unittest discover -s tests`, integridad SQLite, push seguro.
