"""HTML, Markdown and JSON reports for ``gsk eyes``."""

from __future__ import annotations

import html
import json
import os
from pathlib import Path

from .runner import EyesRun, ShotResult

STATUS_LABEL = {
    "pass": "match",
    "changed": "changed",
    "new": "new",
    "updated": "reference saved",
    "error": "error",
}


def write_reports(run: EyesRun) -> dict[str, Path]:
    out = run.config.output
    out.mkdir(parents=True, exist_ok=True)
    paths = {"html": out / "report.html", "markdown": out / "report.md", "json": out / "report.json"}
    paths["html"].write_text(render_html(run), encoding="utf-8")
    paths["markdown"].write_text(render_markdown(run), encoding="utf-8")
    paths["json"].write_text(json.dumps(to_json(run), indent=2), encoding="utf-8")
    return paths


def to_json(run: EyesRun) -> dict[str, object]:
    base = run.config.output
    return {
        "project": run.project.name,
        "project_root": str(run.project.root),
        "output_dir": str(base),
        "godot": run.godot.version,
        "rendering_method": run.rendering_method,
        "started": run.started,
        "seconds": round(run.seconds, 1),
        "failed": run.failed,
        "counts": run.counts(),
        "thresholds": {
            "similarity": run.config.threshold,
            "max_changed": run.config.max_changed,
            "region_pixels": run.config.region_pixels,
            "tolerance": run.config.tolerance,
        },
        "results": [r.to_dict(base) for r in run.results],
    }


# ---------------------------------------------------------------- markdown


def render_markdown(run: EyesRun) -> str:
    c = run.counts()
    lines = [
        f"# gsk eyes: {run.project.name}",
        "",
        f"Godot {run.godot.version}, renderer `{run.rendering_method}`, {run.started}, {run.seconds:.0f}s.",
        "",
        f"**{c['pass']} match, {c['changed']} changed, {c['new']} new, {c['updated']} saved as reference, {c['error']} errors.**",
        "",
        "| Shot | Device | Status | Similarity | Changed pixels | Alerts |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in run.results:
        sim = f"{r.diff.similarity:.4f}" if r.diff else "-"
        chg = f"{r.diff.changed_ratio:.3%}" if r.diff else "-"
        alerts = sum(1 for a in r.alerts if a.level != "info")
        lines.append(f"| {r.shot} | {r.device} | {STATUS_LABEL[r.status]} | {sim} | {chg} | {alerts or ''} |")
    lines.append("")
    for r in run.results:
        lines += [f"## {r.shot} on {r.device}: {STATUS_LABEL[r.status]}", "", f"Scene `{r.scene}`"]
        if r.image:
            lines.append(f"- Screenshot: `{r.image}`")
        if r.reference:
            lines.append(f"- Reference: `{r.reference}`")
        if r.diff_image:
            lines.append(f"- Diff (changed pixels in red, regions boxed): `{r.diff_image}`")
        if r.diff and r.diff.regions:
            regs = "; ".join("x={:.2f} y={:.2f} w={:.2f} h={:.2f}".format(*reg) for reg in r.diff.regions)
            lines.append(f"- Changed regions (fractions of the frame): {regs}")
        for p in r.region_crops:
            lines.append(f"- Zoom on a changed region (reference left, current right): `{p}`")
        for name, p in r.zooms:
            lines.append(f"- Zoom `{name}`: `{p}`")
        if r.stats:
            lines.append(f"- Mean brightness {r.stats.mean_luma:.0f}/255, contrast {r.stats.std_luma:.0f}")
        f = r.facts
        if f.get("has_3d"):
            lines.append(
                f"- 3D: camera `{f.get('camera_3d') or 'none'}`, {f.get('meshes_on_screen', 0)} of {f.get('meshes', 0)} meshes on screen"
            )
        for a in r.alerts:
            lines.append(f"- **{a.level}**: {a.message}")
        lines.append("")
    lines += [
        "Tip: the full-size screenshots are large; read the zoom and region crops to check small text and details.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- html

CSS = """
:root{
  --bg:#f6f5f2;--panel:#ffffff;--ink:#17171c;--muted:#5d5d6b;--line:#e3e1dc;
  --pass:#14804a;--pass-bg:#e3f4ea;--chg:#b42318;--chg-bg:#fde8e6;--new:#1d5fd1;--new-bg:#e5eefc;
  --warn:#9a6700;--warn-bg:#fff4d6;--frame:#0e0e12;--accent:#478cbf;
  --shadow:0 1px 2px rgba(20,20,30,.06),0 8px 24px rgba(20,20,30,.06);
}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
  --bg:#0f1014;--panel:#17181e;--ink:#ececf1;--muted:#9a9aa8;--line:#2a2b33;
  --pass:#4ade80;--pass-bg:#11291c;--chg:#ff7a6e;--chg-bg:#3a1714;--new:#7fb0ff;--new-bg:#14233d;
  --warn:#f5c451;--warn-bg:#352a0e;--frame:#000;--shadow:none;}}
:root[data-theme=dark]{
  --bg:#0f1014;--panel:#17181e;--ink:#ececf1;--muted:#9a9aa8;--line:#2a2b33;
  --pass:#4ade80;--pass-bg:#11291c;--chg:#ff7a6e;--chg-bg:#3a1714;--new:#7fb0ff;--new-bg:#14233d;
  --warn:#f5c451;--warn-bg:#352a0e;--frame:#000;--shadow:none;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.86em}
header{padding:32px 24px 8px;max-width:1280px;margin:0 auto}
.brand{display:flex;align-items:center;gap:10px;color:var(--muted);font-weight:600;letter-spacing:.02em;font-size:13px;text-transform:uppercase}
.brand svg{width:22px;height:22px}
h1{font-size:30px;line-height:1.2;margin:10px 0 6px;letter-spacing:-.01em}
.meta{color:var(--muted);font-size:13.5px}
.summary{display:flex;flex-wrap:wrap;gap:8px;margin:18px 0 6px}
.pill{border-radius:999px;padding:4px 12px;font-size:13px;font-weight:600;border:1px solid transparent}
.pill.pass{background:var(--pass-bg);color:var(--pass)}.pill.changed,.pill.error{background:var(--chg-bg);color:var(--chg)}
.pill.new,.pill.updated{background:var(--new-bg);color:var(--new)}.pill.zero{background:transparent;color:var(--muted);border-color:var(--line)}
nav{max-width:1280px;margin:0 auto;padding:8px 24px 0;display:flex;gap:6px;flex-wrap:wrap}
nav button{font:inherit;font-size:13.5px;border:1px solid var(--line);background:var(--panel);color:var(--ink);padding:6px 14px;border-radius:8px;cursor:pointer}
nav button[aria-pressed=true]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
main{max-width:1280px;margin:0 auto;padding:16px 24px 64px;display:grid;gap:20px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);overflow:hidden}
.card-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:10px 14px;padding:16px 20px;border-bottom:1px solid var(--line)}
.card-head h2{font-size:17px;margin:0}
.card-head .scene{color:var(--muted)}
.card-head .pill{margin-left:auto}
.body{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:0}
@media (max-width:900px){.body{grid-template-columns:1fr}}
.stage{padding:18px;display:flex;flex-direction:column;align-items:center;gap:12px;background:repeating-conic-gradient(var(--bg) 0 25%,transparent 0 50%) 0 0/18px 18px}
.views{display:flex;gap:6px}
.views button{font:inherit;font-size:12.5px;border:1px solid var(--line);background:var(--panel);color:var(--muted);padding:4px 10px;border-radius:7px;cursor:pointer}
.views button[aria-pressed=true]{color:var(--ink);border-color:var(--ink)}
.frame{position:relative;max-width:100%;border-radius:18px;overflow:hidden;background:var(--frame);border:6px solid var(--frame);box-shadow:0 10px 30px rgba(0,0,0,.25)}
.frame img{display:block;max-width:100%;max-height:72vh;width:auto;height:auto}
.frame .over{position:absolute;inset:0;overflow:hidden;clip-path:inset(0 0 0 50%)}
.frame .over img{position:absolute;top:0;left:0;width:100%;height:100%;max-height:none}
.frame .handle{position:absolute;top:0;bottom:0;left:50%;width:2px;background:#fff;box-shadow:0 0 0 1px rgba(0,0,0,.35);pointer-events:none}
.frame .tag{position:absolute;top:10px;padding:2px 8px;border-radius:6px;background:rgba(0,0,0,.6);color:#fff;font-size:12px}
.frame .tag.l{left:10px}.frame .tag.r{right:10px}
.slider{width:min(420px,100%)}
.pair{display:flex;gap:12px;flex-wrap:wrap;justify-content:center}
.pair figure{margin:0;text-align:center;font-size:12.5px;color:var(--muted)}
.pair .frame img{max-height:60vh}
aside{padding:18px 20px;border-left:1px solid var(--line);font-size:14px}
@media (max-width:900px){aside{border-left:0;border-top:1px solid var(--line)}}
dl{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;margin:0 0 14px}
dt{color:var(--muted)}dd{margin:0;font-variant-numeric:tabular-nums}
.alerts{list-style:none;padding:0;margin:0 0 14px;display:grid;gap:8px}
.alerts li{padding:8px 10px;border-radius:8px;white-space:pre-wrap;font-size:13.5px}
.alerts .error{background:var(--chg-bg);color:var(--chg)}.alerts .warn{background:var(--warn-bg);color:var(--warn)}
.alerts .info{background:var(--new-bg);color:var(--new)}
h3{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:16px 0 8px}
.thumbs{display:grid;gap:8px}
.thumbs a{display:block;text-decoration:none;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:var(--frame)}
.thumbs img{display:block;width:100%}
.thumbs span{display:block;font-size:12px;color:var(--muted);padding:4px 8px;background:var(--panel)}
.hidden{display:none!important}
footer{max-width:1280px;margin:0 auto;padding:0 24px 40px;color:var(--muted);font-size:12.5px}
"""

JS = """
document.querySelectorAll('.card').forEach(card=>{
  const r=card.querySelector('input[type=range]');
  if(r){const o=card.querySelector('.over'),h=card.querySelector('.handle');
    const set=v=>{o.style.clipPath=`inset(0 0 0 ${v}%)`;h.style.left=v+'%'};
    r.addEventListener('input',e=>set(e.target.value));set(r.value);}
  card.querySelectorAll('.views button').forEach(b=>b.addEventListener('click',()=>{
    card.querySelectorAll('.views button').forEach(x=>x.setAttribute('aria-pressed',x===b));
    card.querySelectorAll('[data-view]').forEach(v=>v.classList.toggle('hidden',v.dataset.view!==b.dataset.show));
  }));
});
document.querySelectorAll('nav button').forEach(b=>b.addEventListener('click',()=>{
  document.querySelectorAll('nav button').forEach(x=>x.setAttribute('aria-pressed',x===b));
  const f=b.dataset.filter;
  document.querySelectorAll('.card').forEach(c=>c.classList.toggle('hidden',f!=='all'&&!c.dataset.tags.includes(f)));
}));
"""

LOGO = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/>'
    '<circle cx="12" cy="12" r="3"/></svg>'
)


def _rel(path: Path | None, base: Path) -> str:
    return html.escape(os.path.relpath(path, base).replace(os.sep, "/")) if path else ""


def _card(r: ShotResult, base: Path) -> str:
    e = html.escape
    tags = [r.status]
    if r.failed:
        tags.append("failing")
    if any(a.level in ("warn", "error") for a in r.alerts):
        tags.append("alerts")
    head = (
        f'<div class="card-head"><h2>{e(r.shot)} <span class="mono" style="color:var(--muted)">· {e(r.device)}</span></h2>'
        f'<span class="scene mono">{e(r.scene)}</span><span class="pill {r.status}">{STATUS_LABEL[r.status]}</span></div>'
    )
    if r.image and r.reference and r.diff and not r.diff.size_mismatch:
        cur, ref, dif = _rel(r.image, base), _rel(r.reference, base), _rel(r.diff_image, base)
        stage = f"""
<div class="views" role="group" aria-label="View">
 <button data-show="slider" aria-pressed="true">Slider</button>
 <button data-show="pair" aria-pressed="false">Side by side</button>
 <button data-show="diff" aria-pressed="false">Diff</button>
</div>
<div data-view="slider" style="display:contents">
 <div class="frame"><img src="{ref}" alt="reference"><div class="over"><img src="{cur}" alt="current"></div>
 <div class="handle"></div><span class="tag l">reference</span><span class="tag r">current</span></div>
 <input class="slider" type="range" min="0" max="100" value="50" aria-label="Reveal current">
</div>
<div data-view="pair" class="pair hidden">
 <figure><div class="frame"><img src="{ref}" alt="reference" loading="lazy"></div>reference</figure>
 <figure><div class="frame"><img src="{cur}" alt="current" loading="lazy"></div>current</figure>
</div>
<div data-view="diff" class="hidden"><div class="frame"><img src="{dif}" alt="difference" loading="lazy"></div></div>"""
    elif r.image:
        stage = f'<div class="frame"><img src="{_rel(r.image, base)}" alt="{e(r.shot)}"></div>'
    else:
        stage = '<p style="color:var(--muted)">No screenshot was captured.</p>'

    rows = []
    if r.diff:
        rows.append(("Similarity", f"{r.diff.similarity:.4f}"))
        rows.append(("Changed pixels", f"{r.diff.changed_ratio:.3%}"))
    if r.stats:
        rows.append(("Brightness", f"{r.stats.mean_luma:.0f} / 255"))
        rows.append(("Contrast", f"{r.stats.std_luma:.0f}"))
    f = r.facts
    if f.get("size"):
        rows.append(("Size", f"{f['size'][0]} × {f['size'][1]}"))
    if f.get("has_3d"):
        rows.append(("Camera", f.get("camera_3d") or "none"))
        rows.append(("Meshes in view", f"{f.get('meshes_on_screen', 0)} of {f.get('meshes', 0)}"))
    if f.get("seconds") is not None:
        rows.append(("Capture time", f"{float(f['seconds']):.1f}s"))
    dl = "".join(f"<dt>{e(k)}</dt><dd>{e(str(v))}</dd>" for k, v in rows)
    alerts = "".join(f'<li class="{a.level}">{e(a.message)}</li>' for a in r.alerts)
    thumbs = ""
    crops = [(f"changed region {i}", p) for i, p in enumerate(r.region_crops, 1)] + [(f"zoom: {n}", p) for n, p in r.zooms]
    if crops:
        items = "".join(
            f'<a href="{_rel(p, base)}" target="_blank"><img src="{_rel(p, base)}" alt="{e(label)}" loading="lazy"><span>{e(label)}</span></a>'
            for label, p in crops
        )
        thumbs = f"<h3>Zoom</h3><div class='thumbs'>{items}</div>"
    side = f"<aside><dl>{dl}</dl>{f'<ul class=alerts>{alerts}</ul>' if alerts else ''}{thumbs}</aside>"
    return (
        f'<section class="card" data-tags="{" ".join(tags)}">{head}<div class="body"><div class="stage">{stage}</div>{side}</div></section>'
    )


def render_html(run: EyesRun) -> str:
    e = html.escape
    base = run.config.output
    c = run.counts()
    pills = "".join(
        f'<span class="pill {k if v else "zero"}">{v} {STATUS_LABEL[k]}</span>' for k, v in c.items() if v or k in ("pass", "changed")
    )
    devices = ", ".join(run.config.devices)
    cards = "\n".join(_card(r, base) for r in run.results)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>gsk eyes · {e(run.project.name)}</title><style>{CSS}</style></head>
<body>
<header>
 <div class="brand">{LOGO}<span>gsk eyes</span></div>
 <h1>{e(run.project.name)}</h1>
 <div class="meta">Godot {e(run.godot.version)} · renderer <code>{e(run.rendering_method)}</code> · {e(devices)} · {e(run.started)} · {run.seconds:.0f}s</div>
 <div class="summary">{pills}</div>
</header>
<nav aria-label="Filter">
 <button data-filter="all" aria-pressed="true">All ({len(run.results)})</button>
 <button data-filter="failing" aria-pressed="false">Failing</button>
 <button data-filter="alerts" aria-pressed="false">With alerts</button>
 <button data-filter="new" aria-pressed="false">New</button>
</nav>
<main>
{cards}
</main>
<footer>Thresholds: similarity ≥ {run.config.threshold}, changed pixels ≤ {run.config.max_changed:.2%} (per-pixel tolerance {run.config.tolerance}). Software rendering shows layout, colour and composition, not real-device performance.</footer>
<script>{JS}</script>
</body></html>
"""
