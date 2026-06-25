from pathlib import Path
import unittest

from wcpredict.models import MarketFamily
from wcpredict.quality import Confidence
from wcpredict.services import MarketPrediction
from wcpredict.ui import pages


class AppContractTests(unittest.TestCase):
    def test_all_navigation_pages_are_importable(self):
        for name in (
            "render_dashboard",
            "render_prediction_lab",
            "render_player_intelligence",
            "render_backtesting",
            "render_data_quality",
        ):
            self.assertTrue(callable(getattr(pages, name, None)), name)

    def test_navigation_visible_to_user_is_in_spanish(self):
        source = (Path(__file__).parents[1] / "app.py").read_text(encoding="utf-8")
        self.assertNotIn('"Dashboard"', source)
        self.assertNotIn('"Backtesting"', source)
        self.assertIn('"Resumen"', source)
        self.assertIn('"Calibración"', source)


    def test_player_market_uses_observed_player_selectors_not_manual_rates(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        self.assertIn("derive_player_assumption", source)
        self.assertNotIn('text_input("Jugador", "")', source)
        self.assertNotIn('number_input("Tasa por 90"', source)

    def test_model_and_player_views_have_clear_comparisons(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        self.assertIn("model_comparison_rows", source)
        self.assertIn("Evidencia de modelo disponible", source)
        self.assertIn('["Impacto", "Goles", "Asistencias", "Tiros"]', source)

    def test_market_panel_renders_exact_score_grid(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        self.assertIn("def _score_grid_html", source)
        self.assertIn("score-grid", source)
        self.assertIn("Marcadores posibles", source)

    def test_score_grid_is_compact_and_stays_six_by_six(self):
        predictions = [
            MarketPrediction(
                MarketFamily.GOALS,
                "Exact Score Grid",
                f"{a_goals}-{b_goals}",
                None,
                0.01,
                Confidence.LOW,
                "grid",
            )
            for a_goals in range(7)
            for b_goals in range(7)
        ]
        html = pages._score_grid_html("Spain", "Japan", predictions)
        self.assertEqual(html.count("score-cell"), 36)
        self.assertIn("Spain 5-0 Japan", html)
        self.assertNotIn("Spain 6-0 Japan", html)
        self.assertIn("grid-template-columns:28px", html)

    def test_long_model_audit_is_collapsed_from_the_primary_reading_path(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        self.assertIn('st.expander("Ver cálculo y jugadores usados")', source)

    def test_player_rankings_default_to_a_sample_available_during_group_stage(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        self.assertIn('"Minutos mínimos (solo afecta a Impacto)", 0, 900, 60, 30', source)
        self.assertIn("minimum_minutes > 0", source)
        self.assertIn("cluster_player_styles(profiles[:120]", source)


    def test_odds_and_player_ev_paths_avoid_heavy_rerun_work(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        self.assertIn("@st.cache_resource(show_spinner=False)\ndef _repo", source)
        self.assertIn("@st.cache_resource(ttl=900, show_spinner=False)\ndef _refresh_current_world_cup_banks_cached", source)
        self.assertIn("_load_outcome_model_cached", source)
        self.assertIn("prediction_index = _prediction_index(predictions)", source)
        self.assertIn("player_ev_comparison = compare_odds_to_probability", source)
        self.assertNotIn('st.button("Calcular probabilidad y valor"', source)

    def test_match_analysis_bundle_is_cached_so_tab_switches_skip_recomputation(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        self.assertIn("class MatchAnalysisBundle", source)
        self.assertIn(
            "@st.cache_resource(show_spinner=False)\ndef _match_analysis_bundle_cached",
            source,
        )
        self.assertIn("bundle = _match_analysis_bundle(match)", source)
        # The render path must consume the cached bundle, not recompute predictions twice.
        self.assertEqual(
            source.count("predict_match_markets("),
            2,  # only inside the cached bundle builder (one with ML, one without)
        )

    def test_prediction_bundle_defers_secondary_volume_and_audit_work(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        core_start = source.index("def _match_analysis_bundle_cached")
        secondary_starts = [
            index for marker in (
                "def _match_volume_context_cached",
                "def _match_auxiliary_context_cached",
                "def _render_audit_table",
            )
            if (index := source.find(marker)) != -1
        ]
        core_end = min(secondary_starts)
        core = source[core_start:core_end]
        self.assertIn("class MatchAuxiliaryBundle", source)
        self.assertIn("def _match_auxiliary_context_cached", source)
        self.assertNotIn("list_deep_volume_rows_before", core)
        self.assertNotIn("list_deep_goalkeeper_rows_before", core)
        self.assertNotIn("get_match_result", core)

    def test_prediction_lab_sections_are_lazy_and_cache_versioned(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        self.assertIn("PREDICTION_ENGINE_VERSION", source)
        self.assertIn("engine_version: str", source)
        self.assertIn("PREDICTION_ENGINE_VERSION,", source)
        self.assertIn("st.segmented_control(", source)
        self.assertIn('"Vista de análisis"', source)
        self.assertIn('if section == "Modelo":', source)
        self.assertIn('elif section == "Mercados y EV":', source)
        self.assertNotIn('st.tabs(\n        ["Modelo", "Mercados y EV", "Jugadores", "Datos / SofaScore", "Guardado"]', source)

    def test_player_intelligence_rankings_are_lazy(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        self.assertIn("st.segmented_control(", source)
        self.assertIn('"Ranking"', source)
        self.assertIn('ranking_specs = {', source)
        self.assertNotIn('ranking_tabs = st.tabs(["Impacto", "Goles", "Asistencias", "Tiros"])', source)

    def test_dashboard_and_data_quality_reuse_cached_collector_and_match_lists(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        self.assertIn(
            "@st.cache_resource(show_spinner=False)\ndef _matches_cached",
            source,
        )
        self.assertIn(
            "@st.cache_resource(show_spinner=False)\ndef _collector_bundle_cached",
            source,
        )
        self.assertIn("@st.cache_resource(show_spinner=False)\ndef _store_cached", source)
        # render_dashboard, render_backtesting, render_data_quality should use _list_matches.
        self.assertIn("matches = _list_matches()", source)
        # _cached_bundle should hit the cached path, not a fresh CollectorStore each call.
        self.assertIn("_collector_bundle_cached(", source)

    def test_daily_refresh_reruns_bracket_resolution_only_after_updates(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        self.assertIn("def _resolve_bracket_after_daily_refresh", source)
        self.assertIn('getattr(daily_result, "updated"', source)
        self.assertIn("_resolve_bracket_after_daily_refresh(repo, daily_result)", source)

    def test_bracket_view_resolves_before_rendering(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        section = source[source.index("def _render_bracket_section"):source.index("def render_prediction_lab")]
        self.assertLess(section.index("resolve_knockout_bracket(repo)"), section.index("slots = bracket_view(repo)"))

    def test_volume_markets_render_without_manual_button(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        self.assertNotIn('st.button("Calcular mercados de volumen"', source)
        self.assertIn("class MatchVolumeBundle", source)
        self.assertIn("def _match_volume_context_cached", source)
        self.assertIn("volume_market_rows: list[dict]", source)
        self.assertIn("def _render_volume_markets", source)
        self.assertIn("_render_volume_markets(_match_volume_context(match))", source)

    def test_player_intelligence_caches_profiles_and_clusters(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        self.assertIn(
            "@st.cache_resource(show_spinner=False)\ndef _player_intelligence_rows_cached",
            source,
        )
        self.assertIn("_player_intelligence_rows_cached(_db_signature()", source)

    def test_lectura_inmediata_shows_top_alternative_scores_and_expected(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        self.assertIn('"Marcador más probable (modo)"', source)
        self.assertIn('"Marcador esperado (goles xG)"', source)
        self.assertIn('Exact Score (alt)', source)
        self.assertIn("Alternativos más probables", source)

    def test_backtesting_settlement_uses_form_to_avoid_rerun_on_typing(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        self.assertIn('st.form(key=f"settlement_form_{match.id}"', source)
        self.assertIn("st.form_submit_button(", source)

    def test_predictions_tab_renders_post_match_audit_when_match_is_settled(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        self.assertIn("from wcpredict.audit import", source)
        self.assertIn("def _render_post_match_audit", source)
        self.assertIn("_render_post_match_audit(bundle, _match_auxiliary_context(match), team_a, team_b)", source)
        # The bundle must persist post-match data so the audit is cache-friendly.
        self.assertIn("match_result=dict(match_result)", source)
        self.assertIn("team_match_stats=team_match_stats", source)
        self.assertIn("volume_predictions=volume_predictions", source)
        # The deep-stat per-team comparison must be wired into the panel.
        self.assertIn("build_per_team_audit", source)
        self.assertIn("Comparación por equipo (deep stats vs reales)", source)
        # The audit table must not depend on pandas.Styler (jinja2): we render HTML.
        self.assertNotIn("styler = frame.style", source)

    def test_player_tab_lists_full_squad_table_for_the_selected_team(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        self.assertIn('"**Plantilla disponible de', source)
        self.assertIn('"Tiros/90"', source)
        self.assertIn("Minutos mínimos para mostrar", source)
        self.assertIn('jugadores visibles', source)

    def test_refresh_button_surfaces_providers_and_invalidates_cache(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        self.assertIn("st.cache_resource.clear()", source)
        self.assertIn("Llamadas hechas", source)
        self.assertIn("Proveedores OK", source)
        self.assertIn("Faltantes", source)
        self.assertIn("Salida técnica del recolector", source)

    def test_theme_exposes_full_design_system(self):
        from wcpredict.ui import theme
        # Core helpers required by the redesigned pages.
        for name in ("hero", "status_pill", "callout", "empty_state", "section_note"):
            self.assertTrue(callable(getattr(theme, name, None)), name)
        # Design tokens must include the full handoff palette.
        for token in ("--blue-500", "--sidebar", "--prob-win", "--status-amber-fill", "--r-card"):
            self.assertIn(token, theme.CSS)
        # Inter must be loaded from Google Fonts.
        self.assertIn("fonts.googleapis.com/css2?family=Inter", theme.CSS)
        # Tabular figures applied to numerical surfaces.
        self.assertIn('"tnum"', theme.CSS)

    def test_pwa_head_uses_current_streamlit_html_api(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "theme.py").read_text(encoding="utf-8")
        self.assertNotIn("streamlit.components.v1", source)
        self.assertIn("st.html(_PWA_HEAD, unsafe_allow_javascript=True)", source)

    def test_dashboard_uses_redesigned_visuals(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        # render_dashboard imports and uses crests + callout helpers.
        self.assertIn("from wcpredict.ui.crests import", source)
        self.assertIn("from wcpredict.ui.theme import callout, empty_state, hero, probability_bar, section_note, status_pill", source)
        dashboard_index = source.index("def render_dashboard")
        next_def_after = source.index("\ndef ", dashboard_index + 1)
        dashboard_section = source[dashboard_index:next_def_after]
        self.assertIn("crest_html", dashboard_section)
        self.assertIn('callout(', dashboard_section)
        self.assertNotIn('st.info("Si faltan datos', dashboard_section)

    def test_player_intelligence_has_manual_refresh_button(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        self.assertIn("def _force_refresh_players", source)
        self.assertIn('"Actualizar datos de jugadores"', source)
        # Force-refresh bypasses the 24h freshness gate.
        self.assertIn("max_age=timedelta(seconds=0)", source)
        # The button is rendered inside render_player_intelligence, NOT inside
        # the per-match tab_players block.
        player_intel_index = source.index("def render_player_intelligence")
        intel_section = source[player_intel_index:]
        self.assertIn('"Actualizar datos de jugadores"', intel_section)
        self.assertIn("refresh_players_intelligence", intel_section)

    def test_global_bias_panel_is_opt_in_to_avoid_blocking_the_ui(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        # The 36-match reconstruction sits behind an expander + button so it
        # only runs when the user asks for it, otherwise switching matches in
        # Calibración would lock the UI for ~minute every time.
        self.assertIn('"Recalcular reporte de calibración (pesado)"', source)
        self.assertIn('"Calcular reporte ahora"', source)

    def test_settle_match_keeps_working_when_outcome_model_save_fails(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "repository.py").read_text(encoding="utf-8")
        # The pickle error on Streamlit hot reload must never block the user
        # from closing a match. Surrounding try/except is required.
        self.assertIn("save_outcome_model(fitted", source)
        self.assertIn("save_failed:", source)

    def test_calibration_match_selector_shows_status_markers(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        # Marker labels.
        self.assertIn("Completo", source)
        self.assertIn("Falta marcador", source)
        self.assertIn("Falta estadísticas", source)
        self.assertIn("Faltan stats y marcador", source)
        self.assertIn("Sin jugar", source)
        # The misleading "marcador solo falta" line is gone.
        self.assertNotIn(
            "el marcador solo falta para evaluar las predicciones y calcular Brier.",
            source,
        )
        # The per-status messages exist.
        self.assertIn("Partido completo:", source)
        self.assertIn("Estadísticas importadas, pero falta el marcador final", source)
        self.assertIn("Marcador guardado, pero faltan estadísticas profundas", source)

    def test_deep_stats_import_shows_which_future_matches_will_use_evidence(self):
        source = (Path(__file__).parents[1] / "src" / "wcpredict" / "ui" / "pages.py").read_text(encoding="utf-8")
        self.assertIn("Esta evidencia alimenta el modelo", source)
        self.assertIn("Selecciones afectadas", source)


if __name__ == "__main__":
    unittest.main()
