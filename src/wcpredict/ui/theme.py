"""World Cup Predictor design system (Claude Design handoff).

Single source of truth for visual identity. Includes:
  * Inter font import (Google Fonts)
  * Full design token set (colors, status sets, geometry, spacing, shadows)
  * Streamlit overrides: metrics, dataframe, tabs, expander, buttons, sidebar
  * Reusable HTML helpers: hero, status_pill, kpi_card, callout, eyebrow

Keep visual decisions HERE. The page renderers in ``pages.py`` should only
import these helpers, never inline CSS.
"""

import streamlit as st


CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;760&display=swap');

/* ---- Hide Streamlit-cloud branding only (without touching tab arrows) ---- */
.viewerBadge_container__1QSob,
[data-testid="stHeader"] [class*="viewerBadge"],
a[href*="streamlit.io/cloud"],
a[href*="share.streamlit.io"] {display: none !important;}

:root {
  /* Core ink/surface */
  --ink: #10233f;
  --muted: #66758b;
  --line: #dfe7f1;
  --panel: #f7f9fc;
  --panel-2: #eef2f8;

  /* Brand */
  --blue-500: #1769e0;
  --blue-link: #0f5fc7;
  --sidebar: #0f2342;

  /* Semantic */
  --success: #17845b;
  --warning: #b66b00;
  --danger: #c63c3c;

  /* Status — soft sets (ink / fill / border) */
  --status-blue-ink: #0f5fc7;   --status-blue-fill: #edf5ff;   --status-blue-border: #c8ddfb;
  --status-green-ink: #0e6d4a;  --status-green-fill: #eaf8f1;  --status-green-border: #bde6d2;
  --status-amber-ink: #8b5200;  --status-amber-fill: #fff7e8;  --status-amber-border: #f2d69c;
  --status-red-ink: #a52929;    --status-red-fill: #fff0f0;    --status-red-border: #efc0c0;

  /* Probability bars */
  --prob-win: #1769e0;
  --prob-draw: #66758b;
  --prob-loss: #9fb0c6;
  --prob-track: #eef2f8;

  /* Geometry */
  --r-card: 14px;
  --r-hero: 20px;
  --r-button: 9px;
  --r-pill: 999px;

  /* Spacing scale (4·8·12·16·24·32·48) — exposed for inline styles when needed */
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 24px;
  --space-6: 32px;
  --space-7: 48px;

  /* Shadows */
  --shadow-card: 0 1px 2px rgba(16, 35, 63, 0.04), 0 0 0 1px rgba(16, 35, 63, 0.02);
  --shadow-card-hover: 0 4px 12px rgba(16, 35, 63, 0.08);
  --shadow-hero: 0 16px 42px rgba(19, 62, 120, 0.18);
  --shadow-popover: 0 8px 24px rgba(16, 35, 63, 0.12);
}

/* ---- Base typography ---- */
html, body, .stApp, [class*="css"] {
  font-family: 'Inter', system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif !important;
  font-feature-settings: "cv02", "cv03", "cv04", "cv11";
}
.stApp { background: #ffffff; color: var(--ink); }

/* Tabular figures wherever numbers live */
[data-testid="stMetric"] [data-testid="stMetricValue"],
[data-testid="stMetric"] [data-testid="stMetricDelta"],
div[data-testid="stDataFrame"] table,
div[data-testid="stDataEditor"] table {
  font-feature-settings: "tnum" 1, "lnum" 1;
}

h1, h2, h3, h4 { color: var(--ink); font-weight: 700; letter-spacing: -0.01em; }
h1 { font-size: 28px; line-height: 1.2; }
h2 { font-size: 22px; line-height: 1.25; }
h3 { font-size: 17px; line-height: 1.3; }
.section-note { color: var(--muted); margin-top: -8px; margin-bottom: 12px; font-size: 14px; }
.eyebrow {
  color: var(--blue-500);
  text-transform: uppercase;
  letter-spacing: 0.12em;
  font-weight: 700;
  font-size: 0.72rem;
}

/* ---- Sidebar ---- */
[data-testid="stSidebar"] { background: var(--sidebar); }
[data-testid="stSidebar"] * { color: #f5f8ff !important; }
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h2,
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h3 {
  color: #ffffff !important;
  letter-spacing: -0.01em;
}
[data-testid="stSidebar"] .stRadio > div { gap: 0 !important; }
[data-testid="stSidebar"] .stRadio > label { display: none !important; }
[data-testid="stSidebar"] .stRadio [role="radiogroup"] {
  gap: 4px !important;
  padding-top: 12px;
}
[data-testid="stSidebar"] .stRadio [role="radiogroup"] label {
  padding: 10px 14px;
  border-radius: 10px;
  transition: background-color 120ms ease;
  font-size: 14.5px;
  font-weight: 600;
  letter-spacing: 0.01em;
}
[data-testid="stSidebar"] .stRadio [role="radiogroup"] label:hover {
  background: rgba(255, 255, 255, 0.10);
}
[data-testid="stSidebar"] .stRadio [role="radiogroup"] label[data-checked="true"],
[data-testid="stSidebar"] .stRadio [role="radiogroup"] [aria-checked="true"] {
  background: rgba(23, 105, 224, 0.25) !important;
  border-left: 3px solid var(--blue-500);
}
[data-testid="stSidebar"] .stRadio [role="radiogroup"] label p {
  font-size: 14.5px !important;
}
[data-testid="stSidebar"] [data-testid="stCaption"] { opacity: 0.6; }

/* ---- Metric cards (KPI tiles) ---- */
[data-testid="stMetric"] {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  padding: 16px 18px;
  box-shadow: var(--shadow-card);
  transition: box-shadow 160ms ease;
}
[data-testid="stMetric"]:hover { box-shadow: var(--shadow-card-hover); }
[data-testid="stMetric"] [data-testid="stMetricLabel"] {
  color: var(--muted);
  font-size: 12.5px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
[data-testid="stMetric"] [data-testid="stMetricValue"] {
  color: var(--ink);
  font-size: 28px;
  font-weight: 700;
  line-height: 1.1;
}

/* ---- Hero ---- */
.hero {
  padding: 28px 30px;
  border-radius: var(--r-hero);
  color: white;
  margin: 4px 0 22px;
  background: linear-gradient(125deg, #0e2b57 0%, #145ebc 72%, #1674d9 100%);
  box-shadow: var(--shadow-hero);
}
.hero-kicker {
  font-size: 0.72rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  opacity: 0.85;
  font-weight: 700;
}
.hero-title {
  font-size: clamp(1.65rem, 4vw, 2.65rem);
  font-weight: 760;
  line-height: 1.08;
  margin: 8px 0 6px;
  letter-spacing: -0.018em;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 14px;
}
.hero-team {
  display: inline-flex;
  align-items: center;
  gap: 12px;
}
.hero-team img {
  width: 44px; height: 44px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.12);
  padding: 4px;
  object-fit: contain;
  flex: 0 0 auto;
}
.hero-vs {
  opacity: 0.7;
  font-weight: 600;
  font-size: 0.7em;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}
.hero-meta { opacity: 0.88; font-size: 0.96rem; }
.hero-crests {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-top: 12px;
}
.hero-crests img {
  width: 44px; height: 44px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.12);
  padding: 4px;
  object-fit: contain;
}

/* ---- Status pills ---- */
.status-row {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
  margin: 6px 0 16px;
}
.pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border-radius: var(--r-pill);
  font-size: 13px;
  font-weight: 700;
  border: 1px solid transparent;
  line-height: 1.2;
}
.pill-blue  { color: var(--status-blue-ink);  background: var(--status-blue-fill);  border-color: var(--status-blue-border); }
.pill-green { color: var(--status-green-ink); background: var(--status-green-fill); border-color: var(--status-green-border); }
.pill-amber { color: var(--status-amber-ink); background: var(--status-amber-fill); border-color: var(--status-amber-border); }
.pill-red   { color: var(--status-red-ink);   background: var(--status-red-fill);   border-color: var(--status-red-border); }
.pill-neutral { color: var(--muted); background: var(--panel-2); border-color: var(--line); }

/* ---- Tables ---- */
div[data-testid="stDataFrame"], div[data-testid="stDataEditor"] {
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  overflow: hidden;
  background: #ffffff;
}
div[data-testid="stDataFrame"] thead tr th,
div[data-testid="stDataEditor"] thead tr th {
  background: var(--panel-2) !important;
  color: var(--muted) !important;
  font-weight: 700 !important;
  font-size: 12px !important;
  text-transform: uppercase !important;
  letter-spacing: 0.04em !important;
}
div[data-testid="stDataFrame"] tbody tr:nth-child(even),
div[data-testid="stDataEditor"] tbody tr:nth-child(even) {
  background: rgba(247, 249, 252, 0.45);
}
div[data-testid="stDataFrame"] tbody tr:hover,
div[data-testid="stDataEditor"] tbody tr:hover {
  background: var(--status-blue-fill);
}

/* ---- Buttons ---- */
.stButton > button {
  border-radius: var(--r-button);
  font-weight: 700;
  min-height: 2.7rem;
  padding: 0 16px;
  transition: transform 80ms ease, box-shadow 120ms ease, background-color 120ms ease;
  border: 1px solid var(--line);
}
.stButton > button:hover { box-shadow: var(--shadow-card-hover); transform: translateY(-1px); }
.stButton > button:active { transform: translateY(0); }
.stButton > button[kind="primary"],
.stButton > button[data-testid="baseButton-primary"] {
  background: var(--blue-500);
  border-color: var(--blue-500);
  color: #ffffff;
}
.stButton > button[kind="primary"]:hover,
.stButton > button[data-testid="baseButton-primary"]:hover {
  background: var(--blue-link);
  border-color: var(--blue-link);
}

/* ---- Tabs ---- */
.stTabs [data-baseweb="tab-list"] {
  gap: 4px;
  border-bottom: 1px solid var(--line);
}
.stTabs [data-baseweb="tab"] {
  padding: 10px 14px;
  font-weight: 600;
  color: var(--muted);
  border-radius: 8px 8px 0 0;
}
.stTabs [aria-selected="true"] {
  color: var(--ink) !important;
  background: transparent !important;
}
.stTabs [data-baseweb="tab-highlight"] {
  background: var(--blue-500);
  height: 2.5px;
  border-radius: 2px;
}

/* ---- Expanders ---- */
.streamlit-expanderHeader, [data-testid="stExpander"] summary {
  background: var(--panel) !important;
  border-radius: var(--r-card) !important;
  border: 1px solid var(--line) !important;
  font-weight: 600;
  color: var(--ink);
}
[data-testid="stExpander"] {
  border: none !important;
  background: transparent !important;
}

/* ---- Soft panels (callouts, info boxes) ---- */
.soft-panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  padding: 16px 18px;
}
.callout {
  padding: 14px 16px;
  border-radius: var(--r-card);
  border-left: 4px solid var(--blue-500);
  background: var(--status-blue-fill);
  color: var(--status-blue-ink);
  margin: 12px 0;
}
.callout.callout-amber { border-left-color: var(--warning); background: var(--status-amber-fill); color: var(--status-amber-ink); }
.callout.callout-green { border-left-color: var(--success); background: var(--status-green-fill); color: var(--status-green-ink); }
.callout.callout-red   { border-left-color: var(--danger);  background: var(--status-red-fill);   color: var(--status-red-ink); }
.callout-title { font-weight: 700; margin-bottom: 4px; font-size: 14px; }

/* ---- Probability bars ---- */
.prob-row { margin: 6px 0; }
.prob-row .label {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  margin-bottom: 4px;
}
.prob-track {
  height: 10px;
  border-radius: 999px;
  background: var(--prob-track);
  overflow: hidden;
}
.prob-fill { height: 100%; border-radius: 999px; transition: width 240ms ease; }
.prob-fill.win  { background: var(--prob-win); }
.prob-fill.draw { background: var(--prob-draw); }
.prob-fill.loss { background: var(--prob-loss); }

/* ---- Exact-score cards (Mercados y EV) ---- */
.score-cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin: 6px 0 14px;
}
.score-card {
  position: relative;
  padding: 14px 12px 12px;
  border-radius: var(--r-md);
  border: 1px solid var(--line);
  background: linear-gradient(155deg, var(--panel) 0%, var(--panel-2) 100%);
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
}
.score-card.rank-1 {
  border-color: var(--accent);
  background: linear-gradient(155deg, var(--status-blue-fill) 0%, var(--panel) 100%);
}
.score-card .rank-tag {
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--muted);
}
.score-card.rank-1 .rank-tag { color: var(--status-blue-ink); }
.score-card .score-value {
  font-size: 28px;
  font-weight: 800;
  letter-spacing: 0.04em;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}
.score-card .score-prob {
  font-size: 16px;
  font-weight: 700;
  color: var(--accent);
  font-variant-numeric: tabular-nums;
}
@media (max-width: 640px) {
  .score-cards { gap: 8px; }
  .score-card { padding: 10px 6px 8px; }
  .score-card .score-value { font-size: 22px; }
  .score-card .score-prob { font-size: 14px; }
}

.score-grid-wrap {
  margin: 6px 0 16px;
  padding: 10px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--panel);
  color: var(--ink);
  box-shadow: var(--shadow-card);
}
.score-grid-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}
.score-grid-head span {
  font-weight: 800;
  font-size: 12px;
  letter-spacing: 0.02em;
  color: var(--ink);
}
.score-grid-head small {
  color: var(--muted);
  font-size: 10.5px;
}
.score-grid {
  display: grid;
  gap: 3px;
}
.score-axis {
  min-height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--muted);
  font-size: 10px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.score-cell {
  aspect-ratio: 1 / 1;
  min-height: 28px;
  border-radius: 5px;
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    linear-gradient(135deg, rgba(23, 105, 224, calc(var(--heat) * 0.86)), rgba(23, 132, 91, calc(var(--heat) * 0.66))),
    #ffffff;
  color: var(--ink);
  font-size: 10px;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
  box-shadow: inset 0 0 0 1px rgba(16, 35, 63, 0.09);
}
@media (max-width: 640px) {
  .score-grid-wrap { padding: 8px; }
  .score-grid { gap: 2px; }
  .score-axis, .score-cell { min-height: 24px; font-size: 9px; }
  .score-grid-head { display: block; }
  .score-grid-head small { display: block; margin-top: 3px; }
}

/* ---- Bracket (dashboard, tournament style) ---- */
.bk-board {
  display: grid;
  grid-template-columns: repeat(5, minmax(190px, 1fr)) minmax(190px, 1fr);
  gap: 16px;
  padding: 8px 0 4px;
  overflow-x: auto;
}
.bk-column {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 190px;
}
.bk-col-title {
  font-weight: 800;
  font-size: 13px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  text-align: center;
  padding: 8px 6px;
  border-radius: var(--r-sm);
  background: var(--panel-2);
  color: var(--ink);
}
.bk-col-blue   .bk-col-title { background: #e6f0ff; color: #1d4ed8; }
.bk-col-teal   .bk-col-title { background: #d8f4f0; color: #0d8a76; }
.bk-col-green  .bk-col-title { background: #dcf5e0; color: #15803d; }
.bk-col-orange .bk-col-title { background: #ffe9d6; color: #c2410c; }
.bk-col-gold   .bk-col-title { background: #fff1c2; color: #a16207; }
.bk-col-grey   .bk-col-title { background: #e8ecf2; color: #475569; }

.bk-card {
  background: var(--panel);
  border: 1.5px solid var(--line);
  border-radius: 10px;
  padding: 0;
  box-shadow: 0 1px 3px rgba(16, 35, 63, 0.06);
  overflow: hidden;
  transition: transform 120ms ease, box-shadow 120ms ease;
}
.bk-card-link {
  color: inherit !important;
  text-decoration: none !important;
  display: block;
}
.bk-card-link:visited,
.bk-card-link:hover,
.bk-card-link:focus,
.bk-card-link:active,
.bk-card-link *,
.bk-card-link:hover * {
  color: inherit;
  text-decoration: none !important;
}
.bk-card:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 10px rgba(16, 35, 63, 0.10);
}
.bk-card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 10px;
  font-size: 10.5px;
  font-weight: 800;
  letter-spacing: 0.06em;
  color: #fff;
}
.bk-blue   .bk-card-head { background: linear-gradient(135deg, #3b82f6, #1d4ed8); }
.bk-teal   .bk-card-head { background: linear-gradient(135deg, #14b8a6, #0d8a76); }
.bk-green  .bk-card-head { background: linear-gradient(135deg, #22c55e, #15803d); }
.bk-orange .bk-card-head { background: linear-gradient(135deg, #fb923c, #c2410c); }
.bk-gold   .bk-card-head { background: linear-gradient(135deg, #facc15, #a16207); }
.bk-grey   .bk-card-head { background: linear-gradient(135deg, #94a3b8, #475569); }
.bk-blue   { border-color: #93c5fd; }
.bk-teal   { border-color: #5eead4; }
.bk-green  { border-color: #86efac; }
.bk-orange { border-color: #fdba74; }
.bk-gold   { border-color: #fde68a; }
.bk-grey   { border-color: #cbd5e1; }

.bk-slot { letter-spacing: 0.10em; }
.bk-date { opacity: 0.95; }

.bk-card-meta {
  padding: 4px 10px 0;
  font-size: 10.5px;
  color: var(--muted);
  min-height: 14px;
}
.bk-venue { font-weight: 600; }

.bk-team {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  font-size: 13.5px;
  font-weight: 700;
  color: var(--ink);
}
.bk-team .bk-name { color: var(--ink); }
.bk-team img { border-radius: 3px; }

.bk-pending {
  color: var(--muted);
  font-weight: 500;
  font-style: italic;
}
.bk-flag-placeholder {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  background: var(--panel-2);
  border-radius: 3px;
  color: var(--muted);
  font-style: normal;
  font-weight: 800;
  font-size: 11px;
}

.bk-vs {
  text-align: center;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.18em;
  color: var(--muted);
  padding: 2px 0;
  margin: 0 10px;
  border-top: 1px dashed var(--line);
  border-bottom: 1px dashed var(--line);
}

/* Keep bracket horizontal on mobile (swipe-style) instead of collapsing.
   Tighter card width + visible scrollbar hint so it's discoverable. */
@media (max-width: 900px) {
  .bk-board {
    grid-template-columns: repeat(5, 180px) 180px;
    overflow-x: auto;
    padding-bottom: 6px;
    scroll-snap-type: x mandatory;
  }
  .bk-column { min-width: 180px; scroll-snap-align: start; }
  .bk-card-head { font-size: 10px; padding: 6px 10px; }
  .bk-team { font-size: 13px; padding: 8px 10px; }
  .bk-board::-webkit-scrollbar { height: 6px; }
  .bk-board::-webkit-scrollbar-thumb {
    background: var(--line); border-radius: 999px;
  }
}

/* ---- Dashboard match links (clickable rows) ---- */
.match-link {
  color: inherit !important;
  text-decoration: none !important;
  display: inline-block;
  width: 100%;
}
.match-row { transition: background 120ms ease; }
.match-row:hover { background: var(--panel-2); }

/* Mobile horizontal scroll for the upcoming-matches table */
@media (max-width: 900px) {
  .match-table-wrap::-webkit-scrollbar { height: 6px; }
  .match-table-wrap::-webkit-scrollbar-thumb {
    background: var(--line); border-radius: 999px;
  }
}

/* ---- Mercados table (Mercados y EV) ---- */
.mk-table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  margin: 4px 0 14px;
  font-size: 13px;
}
.mk-table th {
  text-align: left;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--muted);
  padding: 6px 10px;
  border-bottom: 1px solid var(--line);
  background: transparent;
}
.mk-table td {
  padding: 9px 10px;
  border-bottom: 1px solid var(--line);
  vertical-align: middle;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}
.mk-table tr:last-child td { border-bottom: none; }
.mk-table td.num { text-align: right; }
.mk-table td.center { text-align: center; }
.mk-table .market-name { font-weight: 600; }
.mk-table .market-sub { font-size: 11px; color: var(--muted); font-weight: 400; }
.mk-table .edge-pos { color: var(--success); font-weight: 700; }
.mk-table .edge-neg { color: var(--danger);  font-weight: 700; }
.mk-table .edge-neu { color: var(--muted);   font-weight: 600; }
@media (max-width: 640px) {
  .mk-table th, .mk-table td { padding: 7px 6px; font-size: 12px; }
}

/* ---- Match row (used in dashboard table) ---- */
.match-team {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}

/* ---- Player ranking table (Jugadores tab) ---- */
.player-table-wrap {
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: #fff;
  overflow: hidden;
  margin: 4px 0 8px;
}
.player-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.92rem;
}
.player-table thead th {
  text-align: left;
  background: var(--panel);
  color: var(--muted);
  text-transform: uppercase;
  font-size: 0.74rem;
  letter-spacing: 0.06em;
  font-weight: 700;
  padding: 10px 14px;
  border-bottom: 1px solid var(--line);
}
.player-table tbody td {
  padding: 10px 14px;
  border-bottom: 1px solid var(--line);
  color: var(--ink);
  vertical-align: middle;
}
.player-table tbody tr:last-child td { border-bottom: none; }
.player-table tbody tr:hover { background: var(--panel); }
.player-table .pt-name { font-weight: 600; }
.player-table .pt-num {
  text-align: right;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.player-table .pt-strong { font-weight: 700; color: var(--blue-link); }
.player-table .pt-team img { flex: 0 0 auto; }

/* ---- Knockout panel (eliminatoria identity) ---- */
@keyframes ko-bar-grow { from { width: 0%; } }

.ko-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border-radius: var(--r-pill);
  background: linear-gradient(135deg, #0e2b57, #145ebc);
  color: #fff;
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.10em;
  text-transform: uppercase;
  line-height: 1;
}
.ko-badge svg { flex: 0 0 auto; }
.ko-badge-stage {
  font-size: 11px;
  font-weight: 600;
  color: var(--muted);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.ko-advance {
  background: linear-gradient(135deg, #0e2b57 0%, #12468a 60%, #1769e0 100%);
  border-radius: var(--r-hero);
  padding: 22px 24px 20px;
  margin-bottom: 16px;
  box-shadow: 0 8px 24px rgba(19, 62, 120, 0.15);
  color: #fff;
}
.ko-advance-label {
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: rgba(255, 255, 255, 0.70);
  margin-bottom: 12px;
}
.ko-advance-bar {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 8px;
}
.ko-advance-team {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
}
.ko-advance-team img {
  width: 28px; height: 28px;
  border-radius: 5px;
  background: rgba(255, 255, 255, 0.15);
  padding: 3px;
  object-fit: contain;
}
.ko-advance-team span {
  font-size: 14px;
  font-weight: 700;
  white-space: nowrap;
}
.ko-advance-team.away span { color: rgba(255, 255, 255, 0.75); }
.ko-stacked-bar {
  flex: 1;
  height: 32px;
  border-radius: 6px;
  overflow: hidden;
  display: flex;
  background: rgba(255, 255, 255, 0.12);
}
.ko-stacked-fill-home {
  background: linear-gradient(90deg, #4d8eea, #1769e0);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 800;
  color: #fff;
  font-variant-numeric: tabular-nums;
  animation: ko-bar-grow 0.6s ease-out;
}
.ko-stacked-fill-away {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 700;
  color: rgba(255, 255, 255, 0.70);
  font-variant-numeric: tabular-nums;
}

/* Funnel: decreasing-height rows inside the advance card */
.ko-funnel {
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin-top: 14px;
}
.ko-funnel-header {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.50);
  letter-spacing: 0.04em;
  text-transform: uppercase;
  padding: 0 4px;
  margin-bottom: 2px;
}
.ko-funnel-row {
  display: flex;
  align-items: center;
  border-radius: 5px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.08);
}
.ko-funnel-row.via-90  { height: 28px; }
.ko-funnel-row.via-et  { height: 24px; }
.ko-funnel-row.via-pen { height: 20px; }
.ko-funnel-fill {
  height: 100%;
  display: flex;
  align-items: center;
  padding: 0 6px;
  font-weight: 800;
  color: #fff;
  font-variant-numeric: tabular-nums;
  min-width: 36px;
}
.ko-funnel-fill.home { background: #34d399; color: #022c1f; justify-content: flex-end; }
.ko-funnel-fill.home.via-et  { background: #6ee7b7; color: #022c1f; }
.ko-funnel-fill.home.via-pen { background: #a7f3d0; color: #022c1f; }
.ko-funnel-fill.away { background: #fb923c; color: #431407; justify-content: flex-start; }
.ko-funnel-fill.away.via-et  { background: #fdba74; color: #431407; }
.ko-funnel-fill.away.via-pen { background: #fed7aa; color: #431407; }
.ko-funnel-fill.draw { background: rgba(255,255,255,0.30); color: #fff; justify-content: center; }
.ko-funnel-fill.draw.via-et { background: rgba(255,255,255,0.22); color: #fff; }
.ko-funnel-fill .pct { font-size: 11px; }
.ko-funnel-row.via-et  .ko-funnel-fill .pct { font-size: 10px; }
.ko-funnel-row.via-pen .ko-funnel-fill .pct { font-size: 9px; }
.ko-funnel-row-label {
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: rgba(255, 255, 255, 0.55);
  padding: 6px 4px 2px;
}
.ko-funnel-row-label .hint {
  text-transform: none;
  letter-spacing: 0;
  font-weight: 500;
  color: rgba(255, 255, 255, 0.45);
  margin-left: 6px;
}
.ko-funnel-label {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  color: rgba(255, 255, 255, 0.80);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  white-space: nowrap;
}
.ko-funnel-row.via-90  .ko-funnel-label { font-size: 10px; }
.ko-funnel-row.via-et  .ko-funnel-label { font-size: 9px; color: rgba(255, 255, 255, 0.65); }
.ko-funnel-row.via-pen .ko-funnel-label { font-size: 8.5px; color: rgba(255, 255, 255, 0.55); }

.ko-advance-caption {
  margin-top: 12px;
  font-size: 11.5px;
  font-weight: 500;
  color: rgba(255, 255, 255, 0.60);
  line-height: 1.4;
}

/* Via table (6 rows: replaces st.dataframe for full style control) */
.ko-via-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}
.ko-via-table th {
  text-align: left;
  padding: 8px 12px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
  background: var(--panel-2);
}
.ko-via-table th.num { text-align: right; }
.ko-via-table td {
  padding: 7px 12px;
  border-top: 1px solid var(--line);
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}
.ko-via-table td.via-name { font-weight: 600; }
.ko-via-table td.via-pct { text-align: right; font-weight: 700; }
.ko-via-table tr.via-et td { background: var(--panel); }
.ko-via-table .via-mini-bar {
  height: 6px;
  border-radius: 3px;
  background: var(--prob-track);
  overflow: hidden;
}
.ko-via-table .via-mini-fill {
  height: 100%;
  border-radius: 3px;
}

/* Section divider with line (used for 90' / prórroga headings) */
.ko-section-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.ko-section-head h3 {
  font-size: 15px;
  font-weight: 700;
  color: var(--ink);
  margin: 0;
  white-space: nowrap;
}
.ko-section-head .ko-line { flex: 1; height: 1px; background: var(--line); }

/* Mobile adjustments for KO panel */
@media (max-width: 720px) {
  .ko-advance { padding: 18px 16px 16px; border-radius: 16px; }
  .ko-advance-team img { width: 24px; height: 24px; }
  .ko-advance-team span { font-size: 12px; }
  .ko-stacked-bar { height: 28px; }
  .ko-stacked-fill-home, .ko-stacked-fill-away { font-size: 12px; }
  .ko-funnel-fill .pct { font-size: 10px; }
  .ko-funnel-row.via-et  .ko-funnel-fill .pct { font-size: 9px; }
  .ko-funnel-row.via-pen .ko-funnel-fill .pct { font-size: 8px; }
  .ko-via-table th, .ko-via-table td { padding: 6px 10px; font-size: 12px; }
}

/* ---- Empty states ---- */
.empty-state {
  text-align: center;
  padding: 36px 24px;
  border: 1px dashed var(--line);
  border-radius: var(--r-card);
  background: var(--panel);
  color: var(--muted);
}
.empty-state .icon {
  font-size: 28px;
  display: block;
  margin-bottom: 8px;
}
.empty-state .title {
  color: var(--ink);
  font-weight: 700;
  font-size: 15px;
  margin-bottom: 4px;
}

/* ---- Tablet ---- */
@media (max-width: 980px) {
  .hero { padding: 22px; }
  .hero-title { font-size: 2rem; }
  .player-table { font-size: 0.86rem; }
  .player-table thead th,
  .player-table tbody td { padding: 8px 10px; }
}

/* ---- Mobile ---- */
@media (max-width: 720px) {
  /* Top padding leaves room for Streamlit's sticky header (sidebar toggle +
     deploy/share buttons live there). Bottom padding leaves room for the
     floating "Manage app" badge on Streamlit Cloud. */
  .block-container,
  [data-testid="stMainBlockContainer"] { padding: 3.5rem 0.75rem 6rem !important; }
  /* Make sure Streamlit's header is opaque so content scrolling underneath
     doesn't show through. */
  [data-testid="stHeader"] {
    background: #ffffff !important;
    height: 3rem;
    min-height: 3rem;
    box-shadow: 0 1px 0 var(--line);
  }
  [data-testid="stHeader"]::before { display: none; }

  /* Wide audit/comparison tables are already wrapped in .audit-table-wrap
     which provides horizontal scroll. Hint the scrollbar visually. */
  .audit-table-wrap::-webkit-scrollbar { height: 6px; }
  .audit-table-wrap::-webkit-scrollbar-thumb {
    background: rgba(16, 35, 63, 0.25); border-radius: 3px;
  }
  /* st.dataframe is already virtualized but we let it use full width. */
  [data-testid="stDataFrame"] { width: 100% !important; }

  /* Hero */
  .hero {
    padding: 18px 18px 16px;
    border-radius: 16px;
    margin-bottom: 12px;
  }
  .hero-kicker { font-size: 0.72rem; }
  .hero-title {
    font-size: 1.4rem;
    gap: 8px;
    flex-direction: row;
    flex-wrap: wrap;
  }
  .hero-team { gap: 8px; }
  .hero-team img { width: 32px; height: 32px; }
  .hero-vs { font-size: 0.65em; padding: 0 2px; }
  .hero-meta { font-size: 0.85rem; }
  .hero-crests img { width: 32px; height: 32px; }

  /* Stack columns */
  [data-testid="stHorizontalBlock"] { gap: 0.4rem; flex-wrap: wrap; }
  [data-testid="stHorizontalBlock"] > [data-testid="column"] {
    min-width: 100% !important;
    flex: 1 1 100% !important;
  }
  [data-testid="stMetric"] { padding: 10px 12px; }
  [data-testid="stMetricValue"] { font-size: 1.6rem !important; }
  [data-testid="stMetricLabel"] { font-size: 0.72rem !important; }

  /* Tabs: horizontal scroll */
  [data-baseweb="tab-list"] {
    overflow-x: auto;
    flex-wrap: nowrap !important;
    scrollbar-width: none;
  }
  [data-baseweb="tab-list"]::-webkit-scrollbar { display: none; }
  [data-baseweb="tab"] { white-space: nowrap; font-size: 0.88rem; }

  /* Status pills wrap better */
  .status-row { gap: 6px; }
  .pill { font-size: 0.78rem; padding: 4px 10px; }

  /* Callouts and empty states */
  .callout { padding: 12px 14px; font-size: 0.9rem; }
  .empty-state { padding: 24px 16px; }

  /* Sidebar: collapsed by default. Streamlit handles the toggle button. */
  [data-testid="stSidebar"][aria-expanded="true"] { width: 85vw !important; min-width: 240px; }

  /* Dashboard match table: hide non-critical columns, stack content */
  [data-testid="stDataFrame"] { font-size: 0.84rem; }
  .match-team { font-size: 0.82rem; gap: 6px; }
  .match-team img { width: 16px !important; height: 16px !important; }

  /* Player ranking → card layout on phones.
     Identify columns by class instead of nth-child because Impacto tab adds
     a Posición column that shifts the numeric indices. Each cell is placed
     into a 2-column grid (name+pos+team on the left, big number on the
     right) and the redundant ones (minutes/matches/rate) are hidden. */
  .player-table-wrap { border-radius: 12px; }
  .player-table { display: block; }
  .player-table thead { display: none; }
  .player-table tbody, .player-table tr, .player-table td { display: block; }
  .player-table tbody tr {
    padding: 12px 14px;
    border-bottom: 1px solid var(--line);
    display: grid;
    grid-template-columns: 1fr auto;
    grid-template-rows: auto auto auto;
    gap: 2px 12px;
    align-items: center;
  }
  .player-table tbody tr:last-child { border-bottom: none; }
  .player-table tbody td {
    padding: 0;
    border: none;
    text-align: left;
  }
  /* Name on top-left */
  .player-table .pt-name {
    font-size: 1rem;
    font-weight: 700;
    grid-column: 1;
    grid-row: 1;
  }
  /* Position label (impact tab only) as small grey chip below the name */
  .player-table .pt-pos {
    grid-column: 1;
    grid-row: 2;
    color: var(--muted);
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 600;
  }
  /* Team + crest below */
  .player-table .pt-team {
    grid-column: 1;
    grid-row: 3;
    color: var(--muted);
    font-size: 0.85rem;
  }
  .player-table .pt-team img { width: 18px; height: 18px; }
  /* Big number on the right, spanning full height */
  .player-table .pt-strong {
    grid-column: 2;
    grid-row: 1 / -1;
    font-size: 1.6rem;
    line-height: 1;
    text-align: right;
    align-self: center;
  }
  /* Hide the secondary numeric cells (minutes, matches, rate per 90). */
  .player-table .pt-num:not(.pt-strong) { display: none; }

  /* Buttons larger touch targets */
  .stButton button { min-height: 44px; font-size: 0.95rem; }
  .stTextInput input, .stSelectbox div[data-baseweb="select"] { min-height: 44px; }

  /* Probability bars more compact */
  .prob-row { font-size: 0.85rem; }
  .prob-bar { height: 10px; }
}

/* ---- Very small phones ---- */
@media (max-width: 400px) {
  .hero-title { font-size: 1.2rem; gap: 6px; }
  .hero-team span { max-width: 110px; overflow: hidden; text-overflow: ellipsis; }
  [data-testid="stMetricValue"] { font-size: 1.4rem !important; }
}
</style>
"""


_PWA_HEAD = """
<script>
(function() {
  // st.html injects into the app document directly; keep a parent fallback for
  // older embeddings that still wrap custom HTML.
  var doc;
  try { doc = window.parent.document; } catch (e) { doc = document; }

  // Force a useful title BEFORE Chrome/Safari snapshot it for the install
  // prompt or "Add to home screen" shortcut. Streamlit's initial title is
  // "Streamlit" which leaks into bookmarks if we don't override it eagerly.
  try { window.parent.document.title = 'Mundial 2026'; } catch (e) {}

  var v = doc.querySelector('meta[name="viewport"]');
  if (!v) { v = doc.createElement('meta'); v.name = 'viewport'; doc.head.appendChild(v); }
  v.content = 'width=device-width, initial-scale=1, viewport-fit=cover';

  function ensureMeta(name, content) {
    var el = doc.head.querySelector('meta[name="' + name + '"]');
    if (!el) {
      el = doc.createElement('meta');
      el.setAttribute('name', name);
      doc.head.appendChild(el);
    }
    el.setAttribute('content', content);
  }
  ensureMeta('apple-mobile-web-app-capable', 'yes');
  ensureMeta('apple-mobile-web-app-status-bar-style', 'black-translucent');
  ensureMeta('apple-mobile-web-app-title', 'Mundial 2026');
  ensureMeta('mobile-web-app-capable', 'yes');
  ensureMeta('application-name', 'Mundial 2026');
  ensureMeta('theme-color', '#0f2342');
  ensureMeta('color-scheme', 'light');

  // Real PNG icons served from Streamlit's static folder (enableStaticServing
  // = true). Chrome on Android refuses to show an "Install app" prompt when
  // the manifest icon is an SVG data URI — it requires a proper PNG that it
  // can fetch by URL.
  function ensureLink(rel, href, opts) {
    var sel = 'link[rel="' + rel + '"]' + ((opts && opts.sizes) ? '[sizes="' + opts.sizes + '"]' : '');
    var el = doc.head.querySelector(sel);
    if (!el) {
      el = doc.createElement('link');
      el.rel = rel;
      if (opts && opts.sizes) el.setAttribute('sizes', opts.sizes);
      if (opts && opts.type) el.setAttribute('type', opts.type);
      doc.head.appendChild(el);
    }
    el.href = href;
  }
  ensureLink('icon', '/app/static/icon-192.png', {sizes: '192x192', type: 'image/png'});
  ensureLink('icon', '/app/static/icon-512.png', {sizes: '512x512', type: 'image/png'});
  ensureLink('apple-touch-icon', '/app/static/apple-touch-icon.png', {sizes: '180x180'});
  ensureLink('manifest', '/app/static/manifest.json');

  // Register the service worker from the parent window. Chrome on Android
  // requires a registered SW with a fetch handler before it considers the app
  // installable. Scope is /app/static/ (the SW's natural path); that's enough
  // for the install criteria — we don't actually need to intercept anything.
  try {
    var w = window.parent;
    if (w && 'serviceWorker' in w.navigator) {
      w.navigator.serviceWorker
        .register('/app/static/sw.js', {scope: '/app/static/'})
        .catch(function (err) { console.warn('SW register failed', err); });
    }
  } catch (e) {}
})();
</script>
"""


def apply_theme() -> None:
    """Inject the design system. Call once at app start."""
    st.markdown(CSS, unsafe_allow_html=True)
    st.html(_PWA_HEAD, unsafe_allow_javascript=True)


def hero(kicker: str, title: str, meta: str, crests_html: str = "") -> None:
    """Page-header band with optional crests row."""
    crests_block = f'<div class="hero-crests">{crests_html}</div>' if crests_html else ""
    st.markdown(
        f'<div class="hero">'
        f'<div class="hero-kicker">{kicker}</div>'
        f'<div class="hero-title">{title}</div>'
        f'<div class="hero-meta">{meta}</div>'
        f"{crests_block}"
        f"</div>",
        unsafe_allow_html=True,
    )


def status_pill(label: str, tone: str = "blue") -> str:
    """Rounded badge. ``tone`` ∈ {blue, green, amber, red, neutral}."""
    return f'<span class="pill pill-{tone}">{label}</span>'


def callout(message: str, tone: str = "blue", title: str | None = None) -> None:
    """Highlighted callout box (info/warning/success/danger)."""
    title_block = f'<div class="callout-title">{title}</div>' if title else ""
    st.markdown(
        f'<div class="callout callout-{tone}">{title_block}{message}</div>',
        unsafe_allow_html=True,
    )


def empty_state(title: str, message: str, icon: str = "📭") -> None:
    """Designed empty state instead of a plain ``st.info``."""
    st.markdown(
        f'<div class="empty-state">'
        f'<span class="icon">{icon}</span>'
        f'<div class="title">{title}</div>'
        f'<div>{message}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )


def probability_bar(label: str, probability: float, kind: str = "win") -> str:
    """Inline probability bar HTML. ``kind`` ∈ {win, draw, loss}."""
    width = max(0.0, min(1.0, probability)) * 100
    return (
        f'<div class="prob-row">'
        f'<div class="label"><span>{label}</span><span>{probability:.1%}</span></div>'
        f'<div class="prob-track"><div class="prob-fill {kind}" style="width:{width:.1f}%"></div></div>'
        f"</div>"
    )


def section_note(text: str) -> None:
    """Muted note rendered just under a heading."""
    st.markdown(f'<div class="section-note">{text}</div>', unsafe_allow_html=True)


# ---- Knockout helpers ----

_KO_TROPHY_SVG = (
    '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M6 9H4.5a2.5 2.5 0 0 1 0-5C7 4 7 7 7 7"/>'
    '<path d="M18 9h1.5a2.5 2.5 0 0 0 0-5C17 4 17 7 17 7"/>'
    '<path d="M4 22h16"/><path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20 7 22"/>'
    '<path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20 17 22"/>'
    '<path d="M18 2H6v7a6 6 0 0 0 12 0V2Z"/>'
    '</svg>'
)

_VIA_BAR_COLORS = {
    ("home", "90"): "#34d399",
    ("home", "et"): "#6ee7b7",
    ("home", "pen"): "#a7f3d0",
    ("away", "90"): "#fb923c",
    ("away", "et"): "#fdba74",
    ("away", "pen"): "#fed7aa",
}


def knockout_badge_html(stage_label: str) -> str:
    """Gradient pill with trophy icon signalling 'this is a knockout match'."""
    return (
        '<div style="display:flex;align-items:center;gap:10px;margin-bottom:14px">'
        f'<span class="ko-badge">{_KO_TROPHY_SVG} ELIMINATORIA</span>'
        f'<span class="ko-badge-stage">{stage_label}</span>'
        '</div>'
    )


def knockout_advance_html(
    team_a: str,
    team_b: str,
    home_advances: float,
    away_advances: float,
    home_wins_90: float,
    draw_90: float,
    away_wins_90: float,
    cond_home_et: float,
    cond_draw_et: float,
    cond_away_et: float,
    cond_home_pen: float,
    cond_away_pen: float,
    crest_a: str = "",
    crest_b: str = "",
    next_fixture: str | None = None,
    pen_pending: bool = True,
) -> str:
    """Full 'who advances' card with stacked bar + conditional via funnel.

    Each funnel row uses CONDITIONAL probabilities within its phase:
      * via-90:  marginal P(home/draw/away at 90') — sum to 100%.
      * via-et:  P(home/draw/away in ET | draw at 90') — sum to 100%.
      * via-pen: P(home/away in shootout | draw after ET) — sum to 100%.
                 ``pen_pending`` flags that the dedicated model is not ready,
                 so the renderer hints the user the 50/50 is a placeholder.
    """
    from html import escape
    ha = home_advances * 100
    aa = away_advances * 100
    img_a = f'{crest_a} ' if crest_a else ''
    img_b = f' {crest_b}' if crest_b else ''

    def _three_seg_row(
        css_class: str, h: float, d: float, a: float,
    ) -> str:
        # Force a tiny minimum width so 0% segments stay visible as sliver.
        h_w = max(h * 100, 0.5)
        d_w = max(d * 100, 0.5)
        a_w = max(a * 100, 0.5)
        return (
            f'<div class="ko-funnel-row {css_class}">'
            f'<div class="ko-funnel-fill home {css_class}" style="width:{h_w:.1f}%">'
            f'<span class="pct">{h:.1%}</span></div>'
            f'<div class="ko-funnel-fill draw {css_class}" style="width:{d_w:.1f}%">'
            f'<span class="pct">{d:.1%}</span></div>'
            f'<div class="ko-funnel-fill away {css_class}" style="width:{a_w:.1f}%">'
            f'<span class="pct">{a:.1%}</span></div>'
            '</div>'
        )

    def _two_seg_row(css_class: str, h: float, a: float) -> str:
        h_w = max(h * 100, 0.5)
        a_w = max(a * 100, 0.5)
        return (
            f'<div class="ko-funnel-row {css_class}">'
            f'<div class="ko-funnel-fill home {css_class}" style="width:{h_w:.1f}%">'
            f'<span class="pct">{h:.1%}</span></div>'
            f'<div class="ko-funnel-fill away {css_class}" style="width:{a_w:.1f}%">'
            f'<span class="pct">{a:.1%}</span></div>'
            '</div>'
        )

    row_90 = (
        '<div class="ko-funnel-row-label">EN 90\''
        '<span class="hint">marginal · ganar o forzar prórroga</span></div>'
        + _three_seg_row("via-90", home_wins_90, draw_90, away_wins_90)
    )
    row_et = (
        '<div class="ko-funnel-row-label">PRÓRROGA'
        '<span class="hint">condicional · si hubo empate al 90\'</span></div>'
        + _three_seg_row("via-et", cond_home_et, cond_draw_et, cond_away_et)
    )
    pen_hint = (
        'condicional · si hubo empate tras prórroga · modelo en desarrollo (50/50)'
        if pen_pending else
        'condicional · si hubo empate tras prórroga'
    )
    row_pen = (
        f'<div class="ko-funnel-row-label">PENALTIS<span class="hint">{pen_hint}</span></div>'
        + _two_seg_row("via-pen", cond_home_pen, cond_away_pen)
    )

    caption = (
        f'<div class="ko-advance-caption">{escape(next_fixture)}</div>'
        if next_fixture else ''
    )
    return (
        '<div class="ko-advance">'
        '<div class="ko-advance-label">Quién avanza al siguiente cruce</div>'
        '<div class="ko-advance-bar">'
        f'<div class="ko-advance-team">{img_a}<span>{escape(team_a)}</span></div>'
        '<div class="ko-stacked-bar">'
        f'<div class="ko-stacked-fill-home" style="width:{ha:.1f}%">{ha:.1f}%</div>'
        f'<div class="ko-stacked-fill-away">{aa:.1f}%</div>'
        '</div>'
        f'<div class="ko-advance-team away"><span>{escape(team_b)}</span>{img_b}</div>'
        '</div>'
        '<div class="ko-funnel">'
        '<div class="ko-funnel-header">'
        f'<span>{escape(team_a)}</span>'
        '<span>Vía de avance</span>'
        f'<span>{escape(team_b)}</span>'
        '</div>'
        + row_90
        + row_et
        + row_pen
        + '</div>'
        + caption
        + '</div>'
    )


def knockout_via_table_html(
    team_a: str,
    team_b: str,
    home_wins_90: float,
    away_wins_90: float,
    home_wins_et: float,
    away_wins_et: float,
    home_wins_pen: float,
    away_wins_pen: float,
) -> str:
    """6-row table of resolution paths with mini probability bars."""
    from html import escape
    rows_data = [
        (f"{escape(team_a)} en 90'", home_wins_90, "var(--prob-win)", ""),
        (f"{escape(team_b)} en 90'", away_wins_90, "var(--prob-loss)", ""),
        (f"{escape(team_a)} en prórroga", home_wins_et, "#4d8eea", " class='via-et'"),
        (f"{escape(team_b)} en prórroga", away_wins_et, "#b8c7d8", " class='via-et'"),
        (f"{escape(team_a)} en penaltis", home_wins_pen, "#7aa8e8", ""),
        (f"{escape(team_b)} en penaltis", away_wins_pen, "#c8d4e4", ""),
    ]
    rows_html = []
    for label, prob, color, tr_attr in rows_data:
        w = max(prob * 100, 0.1)
        rows_html.append(
            f'<tr{tr_attr}>'
            f'<td class="via-name">{label}</td>'
            f'<td class="via-pct">{prob:.1%}</td>'
            f'<td><div class="via-mini-bar">'
            f'<div class="via-mini-fill" style="width:{w:.1f}%;background:{color}"></div>'
            '</div></td></tr>'
        )
    return (
        '<div style="border:1px solid var(--line);border-radius:10px;overflow:hidden;margin-bottom:10px">'
        '<table class="ko-via-table">'
        '<thead><tr><th>Vía</th><th class="num">Prob.</th><th style="width:90px"></th></tr></thead>'
        '<tbody>' + ''.join(rows_html) + '</tbody>'
        '</table></div>'
    )


def knockout_section_head(title: str) -> str:
    """Section heading with trailing line."""
    from html import escape
    return (
        '<div class="ko-section-head">'
        f'<h3>{escape(title)}</h3>'
        '<div class="ko-line"></div>'
        '</div>'
    )
