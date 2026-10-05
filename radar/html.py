"""Render report/report.json as a self-contained HTML page (report/index.html).

Usage: python -m radar.html --data PATH [--title "Job Radar"]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

TEMPLATE = r"""<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Schibsted+Grotesk:wght@500;700&family=Source+Sans+3:wght@400;600&family=JetBrains+Mono:wght@400;500&display=swap">
<style>
/* Layout: a radar console. Summary strip, filter bar, then company groups with their open roles. */
:root{
  --bg:#f3f5f4; --panel:#ffffff; --ink:#15211d; --muted:#5d6b66; --line:#d8dfdc;
  --accent:#1d6b57; --accent-soft:#e1efea;
  --keep:#1f7a4d; --keep-bg:#e3f2e9; --stretch:#9a6a00; --stretch-bg:#f7eed8; --review:#4b5d79; --review-bg:#e6ebf3;
  --warn:#a2452f; --warn-bg:#f6e4df;
  --display:"Schibsted Grotesk", "Helvetica Neue", Arial, sans-serif;
  --body:"Source Sans 3", "Segoe UI", system-ui, sans-serif;
  --mono:"JetBrains Mono", ui-monospace, Menlo, monospace;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --bg:#0f1715; --panel:#16211e; --ink:#e4ece9; --muted:#93a39d; --line:#2a3833;
  --accent:#5fc3a4; --accent-soft:#1b322b;
  --keep:#6fd39d; --keep-bg:#173226; --stretch:#e7b94f; --stretch-bg:#33290f; --review:#a9bbdb; --review-bg:#1f2838;
  --warn:#f08f78; --warn-bg:#3a1f19; color-scheme:dark }}
:root[data-theme="dark"]{
  --bg:#0f1715; --panel:#16211e; --ink:#e4ece9; --muted:#93a39d; --line:#2a3833;
  --accent:#5fc3a4; --accent-soft:#1b322b;
  --keep:#6fd39d; --keep-bg:#173226; --stretch:#e7b94f; --stretch-bg:#33290f; --review:#a9bbdb; --review-bg:#1f2838;
  --warn:#f08f78; --warn-bg:#3a1f19; color-scheme:dark }
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font:16px/1.45 var(--body);padding-inline:16px;padding-block:20px 48px}
.wrap{max-width:1040px;margin:0 auto;display:flex;flex-direction:column;gap:22px}
h1,h2,h3{font-family:var(--display);margin:0;text-wrap:balance}
h1{font-size:1.9rem;letter-spacing:-.01em}
h2{font-size:1.25rem;display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
h2 small{font:500 .8rem var(--mono);color:var(--muted)}
.sub{color:var(--muted);font:400 .82rem var(--mono)}
header{display:flex;flex-direction:column;gap:6px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;display:flex;flex-direction:column;gap:2px}
.stat b{font:700 1.6rem var(--display);font-variant-numeric:tabular-nums}
.stat span{font-size:.82rem;color:var(--muted);letter-spacing:.02em}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;position:sticky;top:env(safe-area-inset-top,0px);background:var(--bg);padding-block:8px;z-index:2}
.bar input{flex:1 1 220px;min-width:0;font:inherit;padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--ink)}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{font:600 .82rem var(--body);padding:6px 11px;border-radius:999px;border:1px solid var(--line);background:var(--panel);color:var(--ink);cursor:pointer}
.chip[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);color:var(--bg)}
.chip:focus-visible,.bar input:focus-visible,a:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
section{display:flex;flex-direction:column;gap:12px}
.co{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.co-h{display:flex;justify-content:space-between;gap:12px;padding:10px 14px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.co-h h3{font-size:1.02rem}
.tag{font:500 .72rem var(--mono);text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.role{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;padding:10px 14px;border-top:1px solid var(--line)}
.role:first-of-type{border-top:0}
.fit{font:600 .74rem var(--body);padding:2px 9px;border-radius:999px;align-self:start;white-space:nowrap;letter-spacing:.02em}
.fit.keep{color:var(--keep);background:var(--keep-bg)} .fit.stretch{color:var(--stretch);background:var(--stretch-bg)} .fit.review{color:var(--review);background:var(--review-bg)}
.r-main{min-width:0;display:flex;flex-direction:column;gap:2px}
.r-main a{color:var(--ink);font-weight:600;text-decoration:none;border-bottom:1px solid var(--line)}
.r-main a:hover{border-color:var(--accent);color:var(--accent)}
.meta{font:400 .8rem var(--mono);color:var(--muted);overflow-wrap:anywhere}
.note{font-size:.88rem;color:var(--ink);opacity:.85}
.flags{display:flex;flex-wrap:wrap;gap:4px}
.flags i{font:normal .72rem var(--mono);color:var(--muted);border:1px dashed var(--line);border-radius:4px;padding:0 5px}
.list{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;display:flex;flex-direction:column;gap:8px}
.list p{margin:0}
.list a{color:var(--accent)}
.warn{color:var(--warn)}
.empty{color:var(--muted);font-style:italic}
details summary{cursor:pointer;font-weight:600}
@media (max-width:520px){h1{font-size:1.5rem}.role{grid-template-columns:1fr}}
@media (prefers-reduced-motion:no-preference){.co{transition:opacity .15s}}
</style>

<div class="wrap">
  <header>
    <h1>__TITLE__</h1>
    <div class="sub" id="updated"></div>
  </header>
  <div class="stats" id="stats"></div>
  <div class="bar">
    <input id="q" type="search" placeholder="Filter by company or role" aria-label="Filter by company or role">
    <div class="chips" role="group" aria-label="Fit">
      <button class="chip" data-f="fit" data-v="all" aria-pressed="true">All</button>
      <button class="chip" data-f="fit" data-v="keep" aria-pressed="false">Keep</button>
      <button class="chip" data-f="fit" data-v="stretch" aria-pressed="false">Stretch</button>
      <button class="chip" data-f="fit" data-v="review" aria-pressed="false">To review</button>
    </div>
    <div class="chips" role="group" aria-label="Where">
      <button class="chip" data-f="pool" data-v="all" aria-pressed="true">Anywhere</button>
      <button class="chip" data-f="pool" data-v="local" aria-pressed="false" id="localChip">Local</button>
      <button class="chip" data-f="pool" data-v="remote" aria-pressed="false">Remote</button>
    </div>
  </div>
  <section id="watch"><h2>Watchlist <small id="watchCount"></small></h2><div id="watchList"></div></section>
  <section id="market"><h2>Market search <small id="marketCount"></small></h2><div id="marketList"></div></section>
  <section id="pages"><h2>Careers pages</h2><div class="list" id="pageList"></div></section>
  <section id="attn"><h2>Needs attention</h2><div class="list" id="attnList"></div></section>
</div>

<script type="application/json" id="data">__DATA__</script>
<script>
(function(){
  const D = JSON.parse(document.getElementById("data").textContent);
  const LOCAL = D.local_label || "Local";
  const state = {fit:"all", pool:"all", q:""};
  try { const s = JSON.parse(localStorage.getItem("radar-filters")||"{}"); Object.assign(state, s, {q:""}); } catch(e){}
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const fitKey = r => r.fit === "keep" ? "keep" : r.fit === "stretch" ? "stretch" : "review";
  const FITL = {keep:"Keep", stretch:"Stretch", review:"To review"};
  const where = r => {
    const l = (r.locations||[]).join("; ");
    if (r.pool === "remote") return "Remote · " + l;
    return (r.workplace && r.workplace !== "remote" ? LOCAL + " · " + r.workplace : l);
  };
  document.getElementById("localChip").textContent = LOCAL;
  document.getElementById("updated").textContent =
    "Updated " + (D.updated||"").replace("T"," ").slice(0,16) + " UTC · " + D.boards + " job boards · " + D.watchlist_size + " watchlist companies";

  const all = D.watchlist_roles.concat(D.market_roles);
  const n = f => all.filter(f).length;
  const withRoles = new Set(D.watchlist_roles.map(r => r.company)).size;
  document.getElementById("stats").innerHTML = [
    [n(r => r.fit === "keep"), "Keep"],
    [n(r => r.fit === "stretch"), "Stretch"],
    [n(r => !r.fit), "To review"],
    [withRoles + " / " + D.watchlist_size, "Watchlist companies hiring"],
    [D.market_roles.length, "Market matches, " + D.market_days + " days"],
  ].map(([b,s]) => `<div class="stat"><b>${esc(b)}</b><span>${esc(s)}</span></div>`).join("");

  function roleHTML(r){
    const k = fitKey(r);
    const flags = (r.flags||[]).map(f => `<i>${esc(f)}</i>`).join("");
    const since = r.first_seen || (r.found||"").slice(0,10);
    return `<div class="role"><span class="fit ${k}">${FITL[k]}</span>
      <div class="r-main"><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a>
      <div class="meta">${esc(where(r))}${since ? " · since " + esc(since) : ""}</div>
      ${r.note ? `<div class="note">${esc(r.note)}</div>` : ""}
      ${flags ? `<div class="flags">${flags}</div>` : ""}</div></div>`;
  }
  function groupHTML(roles){
    const groups = new Map();
    roles.forEach(r => { if(!groups.has(r.company)) groups.set(r.company, []); groups.get(r.company).push(r); });
    if (!groups.size) return `<p class="empty">No roles match these filters.</p>`;
    return [...groups].map(([c, rs]) => `<article class="co"><div class="co-h"><h3>${esc(c)}</h3>
      <span class="tag">${rs[0].kind === "vc" ? "VC · " : ""}${rs.length} role${rs.length>1?"s":""}</span></div>
      ${rs.map(roleHTML).join("")}</article>`).join("");
  }
  function pass(r){
    if (state.fit !== "all" && fitKey(r) !== state.fit) return false;
    if (state.pool !== "all" && r.pool !== state.pool) return false;
    const q = state.q.trim().toLowerCase();
    return !q || (r.company + " " + r.title).toLowerCase().includes(q);
  }
  function render(){
    const w = D.watchlist_roles.filter(pass), m = D.market_roles.filter(pass);
    document.getElementById("watchList").innerHTML = groupHTML(w);
    document.getElementById("marketList").innerHTML = groupHTML(m);
    document.getElementById("watchCount").textContent = w.length + " roles";
    document.getElementById("marketCount").textContent = m.length + " roles";
    document.querySelectorAll(".chip").forEach(b => b.setAttribute("aria-pressed", String(state[b.dataset.f] === b.dataset.v)));
    try { localStorage.setItem("radar-filters", JSON.stringify({fit:state.fit, pool:state.pool})); } catch(e){}
  }
  document.querySelectorAll(".chip").forEach(b => b.addEventListener("click", () => { state[b.dataset.f] = b.dataset.v; render(); }));
  document.getElementById("q").addEventListener("input", e => { state.q = e.target.value; render(); });

  const P = D.pages || [], label = {unchanged:"Unchanged", first_check:"First check, baseline saved",
    js_only:"Jobs load with JavaScript: check in a browser", error:"Couldn't load", not_checked:"No feed or careers page: covered by web search"};
  const link = p => p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.company)}</a>` : esc(p.company);
  const changed = P.filter(p => p.status === "changed");
  let html = changed.map(p => `<p><b>${link(p)}</b> changed. New lines: ${esc((p.added||[]).slice(0,8).join("; "))}</p>`).join("");
  const groups = {};
  P.filter(p => p.status !== "changed").forEach(p => (groups[p.status] = groups[p.status] || []).push(p));
  html += Object.entries(groups).map(([s, ps]) => `<details><summary>${esc(label[s]||s)} (${ps.length})</summary><p>${ps.map(link).join(", ")}</p></details>`).join("");
  document.getElementById("pageList").innerHTML = html || `<p class="empty">No careers pages to watch.</p>`;

  const A = [];
  (D.failing||[]).forEach(f => A.push(`<p class="warn">Job board not responding: ${esc(f.company)} (${esc(f.board)})</p>`));
  (D.audit||[]).forEach(a => A.push(`<p>${esc(a.company)}: careers page links to ${a.found_on_page.map(b => "<code>"+esc(b)+"</code>").join(", ")}${a.configured ? " (configured: <code>"+esc(a.configured)+"</code>)" : ""}</p>`));
  if (D.hidden_cut) A.push(`<p class="sub">${D.hidden_cut} reviewed roles were cut and are hidden.</p>`);
  document.getElementById("attnList").innerHTML = A.join("") || `<p class="empty">Nothing needs attention.</p>`;
  render();
})();
</script>
"""


def build(data: Path, title: str = "Job Radar") -> Path:
    rep = json.loads((data / "report/report.json").read_text())
    try:
        import yaml
        cfg = yaml.safe_load((data / "settings.yaml").read_text()) or {}
        rep["local_label"] = ((cfg.get("locations") or {}).get("local") or {}).get("label") or "Local"
    except Exception:
        rep["local_label"] = "Local"
    blob = json.dumps(rep, ensure_ascii=False).replace("</", "<\\/")
    out = data / "report/index.html"
    out.write_text(TEMPLATE.replace("__TITLE__", title).replace("__DATA__", blob))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=".")
    ap.add_argument("--title", default="Job Radar")
    a = ap.parse_args()
    print(build(Path(a.data).resolve(), a.title))
