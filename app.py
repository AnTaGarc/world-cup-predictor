import os
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
# Normalise sys.path so the `wcpredict` package can NEVER be imported via two
# different paths (one absolute, one relative through PYTHONPATH). That dual
# import creates two distinct class objects for things like FittedOutcomeModel
# and breaks pickle when we try to save the retrained outcome model after
# settling a match.
canonical_src = os.path.realpath(str(SRC))
sys.path[:] = [
    entry for entry in sys.path
    if not entry or not Path(entry).exists()
    or os.path.realpath(entry) != canonical_src
]
sys.path.insert(0, canonical_src)

from wcpredict.ui.pages import (  # noqa: E402
    render_backtesting,
    render_dashboard,
    render_data_quality,
    render_player_intelligence,
    render_prediction_lab,
)
from wcpredict.ui.theme import apply_theme  # noqa: E402


st.set_page_config(page_title="Analista del Mundial 2026", page_icon="⚽", layout="wide")
apply_theme()
st.sidebar.markdown(
    '<div style="padding:6px 0 2px;">'
    '<div style="font-size:18px;font-weight:760;letter-spacing:-0.01em;">⚽ Analista del Mundial 2026</div>'
    '<div style="font-size:13px;opacity:0.7;margin-top:2px;">Predicción, datos y calibración</div>'
    '</div>',
    unsafe_allow_html=True,
)

page = st.sidebar.radio(
    "Vista",
    ["📊 Resumen", "🎯 Predicción y valor", "👤 Jugadores", "📐 Calibración", "🗄️ Calidad de datos"],
    label_visibility="collapsed",
)
# Strip emoji prefix for page matching
page = page.split(" ", 1)[1] if " " in page else page

if page == "Resumen":
    render_dashboard()
elif page == "Predicción y valor":
    render_prediction_lab()
elif page == "Jugadores":
    render_player_intelligence()
elif page == "Calibración":
    render_backtesting()
else:
    render_data_quality()
