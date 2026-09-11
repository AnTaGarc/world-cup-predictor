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
from wcpredict.ui.language_preference import (  # noqa: E402
    render_language_preference,
    render_language_selector,
)
from wcpredict.ui.i18n import translate  # noqa: E402


_initial_language = st.session_state.get("ui_language", "es")
st.set_page_config(
    page_title=translate("app.browser_title", language=_initial_language if _initial_language in {"es", "en"} else "es"),
    page_icon="⚽",
    layout="wide",
)
apply_theme()

language = render_language_preference()
if language is None:
    st.stop()

render_language_selector(language)
st.sidebar.markdown(
    '<div style="padding:6px 0 2px;">'
    f'<div style="font-size:18px;font-weight:760;letter-spacing:-0.01em;">⚽ {translate("app.name", language=language)}</div>'
    f'<div style="font-size:13px;opacity:0.7;margin-top:2px;">{translate("app.tagline", language=language)}</div>'
    '</div>',
    unsafe_allow_html=True,
)

PAGE_KEYS = ("dashboard", "analysis", "players", "calibration", "quality")

# Allow other views (dashboard match links) to deep-link into the prediction
# lab via ?page=lab&match_id=N. Set the default index of the radio so the
# sidebar reflects the deep-link target.
_default_index = 0
_qp_page = st.query_params.get("page")
if _qp_page == "lab":
    _default_index = 1  # Análisis predictivo

page = st.sidebar.radio(
    translate("nav.label", language=language),
    PAGE_KEYS,
    index=_default_index,
    format_func=lambda key: translate(f"nav.{key}", language=language),
    label_visibility="collapsed",
)

if page == "dashboard":
    render_dashboard()
elif page == "analysis":
    render_prediction_lab()
elif page == "players":
    render_player_intelligence()
elif page == "calibration":
    render_backtesting()
else:
    render_data_quality()
