"""Generate the Claude Design bundle from the app's real visual language.

Writes self-contained @dsCard preview HTML files into design_system/,
mirroring src/wcpredict/ui/theme.py (single source of truth). Re-run after
theme changes, then push with DesignSync.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "design_system"

TOKENS = """
:root {
  --ink: #10233f; --muted: #66758b; --line: #dfe7f1;
  --panel: #f7f9fc; --panel-2: #eef2f8;
  --blue-500: #1769e0; --blue-link: #0f5fc7; --sidebar: #0f2342;
  --success: #17845b; --warning: #b66b00; --danger: #c63c3c;
  --status-blue-ink: #0f5fc7; --status-blue-fill: #edf5ff; --status-blue-border: #c8ddfb;
  --status-green-ink: #0e6d4a; --status-green-fill: #eaf8f1; --status-green-border: #bde6d2;
  --status-amber-ink: #8b5200; --status-amber-fill: #fff7e8; --status-amber-border: #f2d69c;
  --status-red-ink: #a52929; --status-red-fill: #fff0f0; --status-red-border: #efc0c0;
  --prob-win: #1769e0; --prob-draw: #66758b; --prob-loss: #9fb0c6; --prob-track: #eef2f8;
  --r-card: 14px; --r-hero: 20px; --r-button: 9px; --r-pill: 999px;
  --shadow-card: 0 1px 2px rgba(16,35,63,.04), 0 0 0 1px rgba(16,35,63,.02);
  --shadow-card-hover: 0 4px 12px rgba(16,35,63,.08);
  --shadow-hero: 0 16px 42px rgba(19,62,120,.18);
}
body { font-family: 'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
  color: var(--ink); background: #fff; margin: 0; padding: 20px;
  font-feature-settings: "cv02","cv03","cv04","cv11"; }
.num { font-variant-numeric: tabular-nums lining-nums; }
"""

BASE = """
.pill { display:inline-flex; align-items:center; gap:6px; padding:5px 12px;
  border-radius:var(--r-pill); font-size:13px; font-weight:700;
  border:1px solid transparent; line-height:1.2; }
.pill-blue{color:var(--status-blue-ink);background:var(--status-blue-fill);border-color:var(--status-blue-border);}
.pill-green{color:var(--status-green-ink);background:var(--status-green-fill);border-color:var(--status-green-border);}
.pill-amber{color:var(--status-amber-ink);background:var(--status-amber-fill);border-color:var(--status-amber-border);}
.pill-red{color:var(--status-red-ink);background:var(--status-red-fill);border-color:var(--status-red-border);}
.pill-neutral{color:var(--muted);background:var(--panel-2);border-color:var(--line);}
.callout { border-left:4px solid var(--blue-500); background:var(--status-blue-fill);
  color:var(--status-blue-ink); border-radius:10px; padding:12px 16px; margin:8px 0; font-size:14px; }
.callout-amber{border-left-color:var(--warning);background:var(--status-amber-fill);color:var(--status-amber-ink);}
.callout-green{border-left-color:var(--success);background:var(--status-green-fill);color:var(--status-green-ink);}
.callout-red{border-left-color:var(--danger);background:var(--status-red-fill);color:var(--status-red-ink);}
.callout-title{font-weight:700;margin-bottom:4px;font-size:14px;}
.metric { background:var(--panel); border:1px solid var(--line); border-radius:var(--r-card);
  padding:16px 18px; box-shadow:var(--shadow-card); min-width:130px; }
.metric .label{color:var(--muted);font-size:12.5px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;}
.metric .value{color:var(--ink);font-size:28px;font-weight:700;line-height:1.1;}
.soft-panel{background:var(--panel);border:1px solid var(--line);border-radius:var(--r-card);padding:16px 18px;}
"""


def card(path: str, group: str, name: str, subtitle: str, body: str, extra_css: str = "",
         width: int = 720) -> tuple[str, str]:
    html = (
        f'<!-- @dsCard group="{group}" name="{name}" subtitle="{subtitle}" width="{width}" -->\n'
        "<!doctype html><html lang=\"es\"><head><meta charset=\"utf-8\">\n"
        '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">\n'
        f"<style>{TOKENS}{BASE}{extra_css}</style></head>\n"
        f"<body>{body}</body></html>\n"
    )
    return path, html


SWATCH_CSS = """
.sw-row{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0;}
.sw{width:132px;border:1px solid var(--line);border-radius:10px;overflow:hidden;box-shadow:var(--shadow-card);}
.sw .chip{height:56px;}
.sw .meta{padding:8px 10px;font-size:11.5px;}
.sw .meta b{display:block;font-size:12px;}
h4{margin:18px 0 6px;font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);}
"""

COLORS_BODY = """
<h4>Marca e interaccion</h4>
<div class="sw-row">
  <div class="sw"><div class="chip" style="background:#1769e0"></div><div class="meta"><b>--blue-500</b>#1769e0 · primario</div></div>
  <div class="sw"><div class="chip" style="background:#0f5fc7"></div><div class="meta"><b>--blue-link</b>#0f5fc7 · enlaces</div></div>
  <div class="sw"><div class="chip" style="background:#0f2342"></div><div class="meta"><b>--sidebar</b>#0f2342 · sidebar</div></div>
  <div class="sw"><div class="chip" style="background:linear-gradient(125deg,#0e2b57 0%,#145ebc 72%,#1674d9 100%)"></div><div class="meta"><b>hero</b>gradiente 125º</div></div>
</div>
<h4>Tinta y superficies</h4>
<div class="sw-row">
  <div class="sw"><div class="chip" style="background:#10233f"></div><div class="meta"><b>--ink</b>#10233f · texto</div></div>
  <div class="sw"><div class="chip" style="background:#66758b"></div><div class="meta"><b>--muted</b>#66758b · secundario</div></div>
  <div class="sw"><div class="chip" style="background:#f7f9fc;border-bottom:1px solid var(--line)"></div><div class="meta"><b>--panel</b>#f7f9fc · tarjetas</div></div>
  <div class="sw"><div class="chip" style="background:#eef2f8"></div><div class="meta"><b>--panel-2</b>#eef2f8 · anidado</div></div>
  <div class="sw"><div class="chip" style="background:#dfe7f1"></div><div class="meta"><b>--line</b>#dfe7f1 · bordes</div></div>
</div>
<h4>Estado (tinta / relleno / borde)</h4>
<div class="sw-row">
  <div class="sw"><div class="chip" style="background:#edf5ff;border-bottom:3px solid #0f5fc7"></div><div class="meta"><b>blue</b>info / activo</div></div>
  <div class="sw"><div class="chip" style="background:#eaf8f1;border-bottom:3px solid #0e6d4a"></div><div class="meta"><b>green</b>correcto / listo</div></div>
  <div class="sw"><div class="chip" style="background:#fff7e8;border-bottom:3px solid #8b5200"></div><div class="meta"><b>amber</b>parcial / aviso</div></div>
  <div class="sw"><div class="chip" style="background:#fff0f0;border-bottom:3px solid #a52929"></div><div class="meta"><b>red</b>error / falta</div></div>
</div>
<h4>Rampa de probabilidad</h4>
<div class="sw-row">
  <div class="sw"><div class="chip" style="background:#1769e0"></div><div class="meta"><b>--prob-win</b>victoria</div></div>
  <div class="sw"><div class="chip" style="background:#66758b"></div><div class="meta"><b>--prob-draw</b>empate</div></div>
  <div class="sw"><div class="chip" style="background:#9fb0c6"></div><div class="meta"><b>--prob-loss</b>derrota</div></div>
  <div class="sw"><div class="chip" style="background:linear-gradient(90deg,#6ee7b7,#34d399)"></div><div class="meta"><b>avance home</b>embudo KO</div></div>
  <div class="sw"><div class="chip" style="background:linear-gradient(90deg,#fdba74,#fb923c)"></div><div class="meta"><b>avance away</b>embudo KO</div></div>
</div>
"""

TYPE_BODY = """
<div style="max-width:640px">
  <div style="font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);font-weight:700">KICKER · MUNDIAL 2026</div>
  <div style="font-size:2.3rem;font-weight:760;letter-spacing:-.018em;line-height:1.08;margin:8px 0">Decidir con probabilidades, no con ruido.</div>
  <h2 style="font-weight:700;letter-spacing:-.01em;margin:20px 0 4px">H2 · Partidos de hoy</h2>
  <h3 style="font-weight:700;margin:14px 0 4px">H3 · Auditoría por fases</h3>
  <p style="font-size:15px;line-height:1.55;margin:8px 0">Cuerpo 15px/1.55 — Inter con cv02–cv11. El estado de cobertura se calcula por partido; una fuente parcial no bloquea el resto.</p>
  <p style="font-size:13px;color:var(--muted);margin:6px 0">Secundario 13px — notas, captions y ayudas contextuales.</p>
  <div class="num" style="font-size:28px;font-weight:700;margin-top:12px">76.8% · 2-1 · 0.8428</div>
  <p style="font-size:12px;color:var(--muted)">Numerales tabulares (tnum) en métricas, tablas y barras.</p>
</div>
"""

PILLS_BODY = """
<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px">
  <span class="pill pill-green">Datos diarios: Actual</span>
  <span class="pill pill-amber">Datos diarios: Parcial</span>
  <span class="pill pill-red">Con error 1</span>
  <span class="pill pill-blue">Actualizadas 4</span>
  <span class="pill pill-neutral">Sin jugar</span>
</div>
<div style="display:flex;gap:8px;flex-wrap:wrap">
  <span class="pill pill-green">✅ Importado</span>
  <span class="pill pill-amber">Falta estadísticas</span>
  <span class="pill pill-red">🔴 No cuadra</span>
  <span class="pill pill-blue">Confianza alta</span>
</div>
"""

HERO_BODY = """
<div style="padding:28px 30px;border-radius:20px;color:#fff;background:linear-gradient(125deg,#0e2b57 0%,#145ebc 72%,#1674d9 100%);box-shadow:var(--shadow-hero);max-width:680px">
  <div style="font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;opacity:.85;font-weight:700">MUNDIAL 2026 · MESA DE ANÁLISIS</div>
  <div style="font-size:2.1rem;font-weight:760;line-height:1.08;margin:8px 0 6px;letter-spacing:-.018em;display:flex;align-items:center;gap:14px;flex-wrap:wrap">
    <span style="display:inline-flex;align-items:center;gap:12px"><img src="../assets/crests/portugal.png" style="width:44px;height:44px;border-radius:6px;background:rgba(255,255,255,.12);padding:4px;object-fit:contain">Portugal</span>
    <span style="opacity:.7;font-weight:600;font-size:.7em;letter-spacing:.05em">VS</span>
    <span style="display:inline-flex;align-items:center;gap:12px"><img src="../assets/crests/spain.png" style="width:44px;height:44px;border-radius:6px;background:rgba(255,255,255,.12);padding:4px;object-fit:contain">España</span>
  </div>
  <div style="opacity:.88;font-size:.96rem">Octavos de final · 5 jul · 19:00 · Dallas Stadium</div>
</div>
"""

METRICS_BODY = """
<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px">
  <div class="metric"><div class="label">Partidos</div><div class="value num">96</div></div>
  <div class="metric"><div class="label">Selecciones</div><div class="value num">48</div></div>
  <div class="metric"><div class="label">Importaciones</div><div class="value num">217</div></div>
  <div class="metric"><div class="label">Deep stats</div><div class="value num">3.618</div></div>
</div>
<div class="callout"><div class="callout-title">Ajuste por forma del torneo activo</div>
score +0.34 · peso 0.57 (4 partidos) · α 1.50 → +0.29 logit</div>
<div class="callout callout-green"><div class="callout-title">Verificación cruzada</div>
El marcador del dataset externo coincide con tu captura (2-1).</div>
<div class="callout callout-amber"><div class="callout-title">Discrepancia con el acumulado de 120'</div>
Egypt: shots_on_target suma 4, pero el acumulado indica 3; se usa el acumulado (3).</div>
"""

PROB_CSS = """
.prob-row{margin:10px 0;max-width:560px}
.prob-row .label{display:flex;justify-content:space-between;font-size:13.5px;font-weight:600;margin-bottom:4px}
.prob-track{height:10px;border-radius:999px;background:var(--prob-track);overflow:hidden}
.prob-fill{height:100%;border-radius:999px}
"""

PROB_BODY = """
<div class="prob-row"><div class="label"><span>España</span><span class="num">51.2%</span></div>
<div class="prob-track"><div class="prob-fill" style="width:51.2%;background:var(--prob-win)"></div></div></div>
<div class="prob-row"><div class="label"><span>Empate</span><span class="num">26.3%</span></div>
<div class="prob-track"><div class="prob-fill" style="width:26.3%;background:var(--prob-draw)"></div></div></div>
<div class="prob-row"><div class="label"><span>Portugal</span><span class="num">22.5%</span></div>
<div class="prob-track"><div class="prob-fill" style="width:22.5%;background:var(--prob-loss)"></div></div></div>
"""

SCORE_CSS = """
.score-cards{display:flex;gap:12px;flex-wrap:wrap}
.score-card{background:var(--panel);border:1px solid var(--line);border-radius:var(--r-card);
  padding:14px 18px;box-shadow:var(--shadow-card);min-width:120px;text-align:center}
.score-card.rank-1{border-color:var(--status-blue-border);background:var(--status-blue-fill)}
.rank-tag{font-size:10.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.score-card.rank-1 .rank-tag{color:var(--status-blue-ink)}
.score-value{font-size:26px;font-weight:760;margin:4px 0}
.score-prob{font-size:13px;color:var(--muted)}
"""

SCORE_BODY = """
<div class="score-cards">
  <div class="score-card rank-1"><div class="rank-tag">Más probable</div><div class="score-value num">0-0</div><div class="score-prob num">16.7%</div></div>
  <div class="score-card"><div class="rank-tag">#2</div><div class="score-value num">0-1</div><div class="score-prob num">14.8%</div></div>
  <div class="score-card"><div class="rank-tag">#3</div><div class="score-value num">1-1</div><div class="score-prob num">11.9%</div></div>
  <div class="score-card"><div class="rank-tag">Si gana Marruecos</div><div class="score-value num">0-1</div><div class="score-prob num">14.8%</div></div>
</div>
<p style="font-size:12.5px;color:var(--muted);max-width:560px;margin-top:12px">
"Exact Score (favorito)": aunque Marruecos es el 1X2 más probable, su masa se reparte
entre muchos marcadores mientras el empate se concentra en pocos.</p>
"""

KO_CSS = """
@keyframes ko-bar-grow{from{width:0%}}
.ko-advance{background:linear-gradient(135deg,#0e2b57 0%,#12468a 60%,#1769e0 100%);
  border-radius:20px;padding:22px 24px 20px;box-shadow:0 8px 24px rgba(19,62,120,.15);color:#fff;max-width:680px}
.ko-advance-label{font-size:10.5px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:rgba(255,255,255,.7);margin-bottom:12px}
.ko-advance-bar{display:flex;align-items:center;gap:14px;margin-bottom:8px}
.ko-advance-team span{font-size:14px;font-weight:700;white-space:nowrap}
.ko-advance-team.away span{color:rgba(255,255,255,.75)}
.ko-stacked-bar{flex:1;height:32px;border-radius:6px;overflow:hidden;display:flex;background:rgba(255,255,255,.12)}
.ko-stacked-fill-home{background:linear-gradient(90deg,#6ee7b7,#34d399);display:flex;align-items:center;justify-content:center;
  font-size:13px;font-weight:800;color:#022c1f;animation:ko-bar-grow .6s ease-out}
.ko-stacked-fill-away{flex:1;background:linear-gradient(90deg,#fdba74,#fb923c);display:flex;align-items:center;justify-content:center;
  font-size:13px;font-weight:800;color:#431407}
.ko-funnel{display:flex;flex-direction:column;gap:8px;margin-top:14px}
.ko-funnel-row-label{font-size:10px;font-weight:600;color:rgba(255,255,255,.5);letter-spacing:.04em;text-transform:uppercase;margin-bottom:2px}
.ko-funnel-row-label .hint{margin-left:8px;text-transform:none;letter-spacing:0;opacity:.8}
.ko-funnel-row{display:flex;border-radius:5px;overflow:hidden;height:26px}
.ko-funnel-fill{display:flex;align-items:center;justify-content:center;font-size:11.5px;font-weight:700}
.ko-f-home{background:linear-gradient(90deg,#6ee7b7,#34d399);color:#022c1f}
.ko-f-draw{background:rgba(255,255,255,.28)}
.ko-f-away{background:linear-gradient(90deg,#fdba74,#fb923c);color:#431407}
"""

KO_BODY = """
<div class="ko-advance">
  <div class="ko-advance-label">Quién avanza al siguiente cruce</div>
  <div class="ko-advance-bar">
    <div class="ko-advance-team"><img src="../assets/crests/paraguay.png" style="width:28px;height:28px;border-radius:5px;background:rgba(255,255,255,.15);padding:3px;object-fit:contain"><span>Paraguay</span></div>
    <div class="ko-stacked-bar">
      <div class="ko-stacked-fill-home num" style="width:6.2%"></div>
      <div class="ko-stacked-fill-away num">93.8%</div>
    </div>
    <div class="ko-advance-team away"><span>France</span><img src="../assets/crests/france.png" style="width:28px;height:28px;border-radius:5px;background:rgba(255,255,255,.15);padding:3px;object-fit:contain"></div>
  </div>
  <div class="ko-funnel">
    <div><div class="ko-funnel-row-label">EN 90' <span class="hint">marginal · ganar o forzar prórroga</span></div>
      <div class="ko-funnel-row">
        <div class="ko-funnel-fill ko-f-home num" style="width:12%">2.7%</div>
        <div class="ko-funnel-fill ko-f-draw num" style="width:20%">12.2%</div>
        <div class="ko-funnel-fill ko-f-away num" style="width:68%">85.2%</div>
      </div></div>
    <div><div class="ko-funnel-row-label">PRÓRROGA <span class="hint">condicional · si hubo empate al 90'</span></div>
      <div class="ko-funnel-row" style="width:86%">
        <div class="ko-funnel-fill ko-f-home num" style="width:10%">4.1%</div>
        <div class="ko-funnel-fill ko-f-draw num" style="width:42%">42.1%</div>
        <div class="ko-funnel-fill ko-f-away num" style="width:48%">53.9%</div>
      </div></div>
    <div><div class="ko-funnel-row-label">PENALTIS <span class="hint">condicional · si hubo empate tras prórroga</span></div>
      <div class="ko-funnel-row" style="width:72%">
        <div class="ko-funnel-fill ko-f-home num" style="width:59%">59.0%</div>
        <div class="ko-funnel-fill ko-f-away num" style="width:41%">41.0%</div>
      </div></div>
  </div>
</div>
"""

AUDIT_CSS = """
.audit-row{display:flex;gap:12px;flex-wrap:wrap;max-width:680px}
.audit-card{flex:1;min-width:150px;background:var(--panel);border:1px solid var(--line);
  border-radius:var(--r-card);padding:14px 16px;box-shadow:var(--shadow-card)}
.audit-card .k{font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.audit-card .v{font-size:24px;font-weight:760;margin-top:4px}
.audit-hit{border-color:var(--status-green-border);background:var(--status-green-fill)}
.audit-hit .v{color:var(--status-green-ink)}
"""

AUDIT_BODY = """
<h3 style="margin:0 0 10px">Auditoría por fases · 90 minutos</h3>
<div class="audit-row">
  <div class="audit-card"><div class="k">Real</div><div class="v num">0-1</div></div>
  <div class="audit-card audit-hit"><div class="k">Resultado previsto</div><div class="v">France</div></div>
  <div class="audit-card"><div class="k">Probabilidad de lo ocurrido</div><div class="v num">85.2%</div></div>
</div>
<p style="font-size:12.5px;color:var(--muted);margin-top:10px">Retro-predicción con el modelo actual
bajo corte estricto prepartido; verde = acierto de la fase.</p>
"""

DATASET_CSS = """
.expander{max-width:680px;border:1px solid var(--line);border-radius:var(--r-card);overflow:hidden}
.expander summary{background:var(--panel);padding:12px 16px;font-weight:600;cursor:pointer;list-style:none}
.expander .bd{padding:14px 16px;font-size:14px}
.cand{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px solid var(--line)}
.btn{border-radius:var(--r-button);font-weight:700;padding:8px 16px;border:1px solid var(--blue-500);
  background:var(--blue-500);color:#fff;cursor:pointer}
.btn:hover{background:var(--blue-link)}
.btn-secondary{background:#fff;color:var(--ink);border-color:var(--line)}
"""

DATASET_BODY = """
<details class="expander" open><summary>Dataset externo (mominullptr)</summary>
<div class="bd">
  <div style="margin-bottom:10px">Cierres candidatos pendientes: <b>2</b></div>
  <div class="cand"><span>España <b class="num">3-0</b> Austria</span><button class="btn">Confirmar</button></div>
  <div class="cand"><div><span>Portugal <b class="num">2-1</b> Croacia</span>
    <div style="font-size:12px;color:var(--muted)">Eventos hasta el minuto 103: confirma solo si terminó en los 90 (descuento largo).</div></div>
    <button class="btn">Confirmar</button></div>
  <div style="margin-top:12px;color:var(--muted);font-size:13px">
    Discrepancias de marcador pendientes: 0 · Alias de equipos pendientes: 0 · Alias de jugadores pendientes: 1060</div>
</div></details>
<div style="margin-top:14px;display:flex;gap:10px">
  <button class="btn">Cerrar eliminatoria y recalibrar</button>
  <button class="btn btn-secondary">Guardar borrador</button>
</div>
"""


CARDS = [
    card("guidelines/colors.card.html", "Colors", "Paleta y tokens", "Marca · tinta · estado · probabilidad", COLORS_BODY, SWATCH_CSS, 760),
    card("guidelines/type.card.html", "Type", "Tipografía", "Inter · kicker/hero/cuerpo · tnum", TYPE_BODY, "", 700),
    card("components/pills.card.html", "Components", "Pills de estado", "5 tonos · datos diarios y periodos", PILLS_BODY, "", 640),
    card("components/hero.card.html", "Components", "Hero de partido", "Gradiente 125º · kicker + título + meta", HERO_BODY, "", 740),
    card("components/metrics-callouts.card.html", "Components", "Métricas y callouts", "KPI tiles · forma/verificación/discrepancia", METRICS_BODY, "", 740),
    card("dataviz/probability.card.html", "DataViz", "Barras 1X2", "win/draw/loss sobre track", PROB_BODY, PROB_CSS, 640),
    card("dataviz/score-cards.card.html", "DataViz", "Marcadores exactos", "modal · alternativos · favorito condicional", SCORE_BODY, SCORE_CSS, 700),
    card("match/ko-advance.card.html", "Match", "Embudo de eliminatoria", "avance + 90'/prórroga/penaltis", KO_BODY, KO_CSS, 740),
    card("match/phase-audit.card.html", "Match", "Auditoría por fases", "real / previsto / probabilidad", AUDIT_BODY, AUDIT_CSS, 740),
    card("data/external-dataset.card.html", "Data", "Dataset externo y cierres", "candidatos · confirmación · botones", DATASET_BODY, DATASET_CSS, 740),
]

README = """# Analista del Mundial 2026 — Design System (jul 2026)

Bundle generado desde el codigo real de la app (src/wcpredict/ui/theme.py es la
fuente de verdad). Regenerar con:

    python scripts/build_design_bundle.py

y sincronizar con DesignSync desde Claude Code.

Grupos: Colors, Type, Components, DataViz, Match, Data.
Incluye las piezas nuevas de julio: embudo KO, auditoria por fases,
dataset externo con cierres candidatos, callout de forma del torneo y
marcador favorito condicional.
"""


def main() -> None:
    import shutil
    crests_src = ROOT / "data" / "crests"
    crests_out = OUT / "assets" / "crests"
    crests_out.mkdir(parents=True, exist_ok=True)
    for crest in crests_src.glob("*.png"):
        shutil.copy2(crest, crests_out / crest.name)
    for path, html in CARDS:
        target = OUT / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8")
    (OUT / "readme.md").write_text(README, encoding="utf-8")
    print(f"{len(CARDS)} cards + readme + crests -> {OUT}")


if __name__ == "__main__":
    main()
