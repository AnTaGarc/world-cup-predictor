from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import streamlit as st

from wcpredict.ui.i18n import Language, normalise_language, translate


STORAGE_KEY = "wcpredict.language.v1"
SESSION_KEY = "ui_language"
WRITE_KEY = "_ui_language_write"

COMPONENT_JS = f"""
export default function(component) {{
    const {{ data, setStateValue }} = component;
    const valid = value => ['es', 'en'].includes(value) ? value : null;
    let language = null;
    try {{
        const requested = valid(data?.write);
        if (requested) {{
            localStorage.setItem('{STORAGE_KEY}', requested);
        }}
        language = valid(localStorage.getItem('{STORAGE_KEY}'));
    }} catch (_) {{
        language = valid(data?.write);
    }}
    setStateValue('ready', true);
    setStateValue('language', language);
}}
"""

def _preference_component():
    """Register against the active runtime (AppTest and Cloud may reset it)."""
    return st.components.v2.component(
        "wcpredict_language_preference",
        html="<span aria-hidden='true'></span>",
        css=":host { display: none; }",
        js=COMPONENT_JS,
        isolate_styles=False,
    )


@dataclass(frozen=True)
class PreferenceState:
    status: Literal["resolving", "unselected", "resolved"]
    language: Language | None


def resolve_preference(component_value: object, session_value: object) -> PreferenceState:
    session_language = normalise_language(session_value)
    if session_language:
        return PreferenceState("resolved", session_language)
    if component_value is None:
        return PreferenceState("resolving", None)
    ready = _component_field(component_value, "ready") is True
    if not ready:
        return PreferenceState("resolving", None)
    stored_language = normalise_language(_component_field(component_value, "language"))
    if stored_language:
        return PreferenceState("resolved", stored_language)
    return PreferenceState("unselected", None)


def _component_field(value: object, field: str) -> object:
    if isinstance(value, dict):
        return value.get(field)
    return getattr(value, field, None)


def _store_choice(language: Language) -> None:
    st.session_state[SESSION_KEY] = language
    st.session_state[WRITE_KEY] = language


def render_language_preference() -> Language | None:
    pending_write = normalise_language(st.session_state.get(WRITE_KEY))
    component_value = _preference_component()(
        key="language-browser-preference",
        data={"write": pending_write},
        default={"ready": True, "language": None},
        height=0,
        on_ready_change=lambda: None,
        on_language_change=lambda: None,
    )
    if pending_write:
        st.session_state.pop(WRITE_KEY, None)

    state = resolve_preference(component_value, st.session_state.get(SESSION_KEY))
    if state.status == "resolving":
        st.empty()
        return None
    if state.status == "resolved":
        st.session_state[SESSION_KEY] = state.language
        return state.language

    st.markdown('<div class="language-gate">', unsafe_allow_html=True)
    st.title(translate("language.choose.title", language="es") + " · " + translate("language.choose.title", language="en"))
    st.caption(translate("language.choose.body", language="es") + " / " + translate("language.choose.body", language="en"))
    spanish, english = st.columns(2)
    if spanish.button("🇪🇸 Español", width="stretch", key="choose-language-es"):
        _store_choice("es")
        st.rerun()
    if english.button("🇬🇧 English", width="stretch", key="choose-language-en"):
        _store_choice("en")
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)
    return None


def render_language_selector(language: Language) -> Language:
    selector = st.sidebar.container(key="language-selector-shell")
    selector.caption(translate("language.selector", language=language))
    selected = selector.radio(
        translate("language.selector", language=language),
        options=("es", "en"),
        index=0 if language == "es" else 1,
        format_func=lambda value: "ES" if value == "es" else "EN",
        key="language-selector",
        horizontal=False,
        label_visibility="collapsed",
    )
    selected_language = normalise_language(selected) or language
    if selected_language != language:
        _store_choice(selected_language)
        st.rerun()
    return language


def current_language() -> Language:
    return normalise_language(st.session_state.get(SESSION_KEY)) or "es"
