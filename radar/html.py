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
.bar select{font:600 .82rem var(--body);padding:6px 10px;border-radius:999px;border:1px solid var(--line);background:var(--panel);color:var(--ink);max-width:100%}
.bar select:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.badge{font:600 .68rem var(--body);padding:1px 7px;border-radius:999px;background:var(--accent-soft);color:var(--accent);margin-left:6px;vertical-align:2px;letter-spacing:.03em}
.labels{display:flex;flex-wrap:wrap;gap:4px}
.labels span{font:500 .72rem var(--mono);color:var(--muted)}
.hide{display:none;font:600 .74rem var(--body);color:var(--muted);background:none;border:1px solid var(--line);border-radius:6px;padding:2px 8px;cursor:pointer;justify-self:end;align-self:start}
.hide:hover{color:var(--warn);border-color:var(--warn)}
.hide:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.can-hide .hide{display:inline-block}
.role{grid-template-columns:auto 1fr auto}
.co-h .right{display:flex;gap:8px;align-items:center}
.views{display:flex;gap:6px}
.st{font:600 .7rem var(--body);padding:2px 8px;border-radius:999px;white-space:nowrap;align-self:start;letter-spacing:.02em;background:var(--accent-soft);color:var(--muted)}
.st.cut,.st.closed,.st.gone{color:var(--warn);background:var(--warn-bg)} .st.hidden{color:var(--review);background:var(--review-bg)}
.why{font-size:.84rem;color:var(--muted)}
.more{font:600 .82rem var(--body);background:none;border:1px solid var(--line);border-radius:8px;padding:8px 12px;color:var(--ink);cursor:pointer;align-self:center}
.more:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
body:not(.view-archive) #archive{display:none} body.view-archive .current{display:none}
#statusChips .chip[data-v="hidden"]{display:none} .can-hide #statusChips .chip[data-v="hidden"]{display:inline-block}

@media (max-width:520px){h1{font-size:1.5rem}.role{grid-template-columns:1fr auto}.role .fit{grid-column:1/-1}}
@media (prefers-reduced-motion:no-preference){.co{transition:opacity .15s}}
</style>

<div class="wrap">
  <header>
    <h1>__TITLE__</h1>
    <div class="sub" id="updated"></div>
    <div class="views chips" role="group" aria-label="View">
      <button class="chip" data-f="view" data-v="current" aria-pressed="true">Current roles</button>
      <button class="chip" data-f="view" data-v="archive" aria-pressed="false">Archive</button>
    </div>
  </header>
  <div class="stats current" id="stats"></div>
  <div class="bar">
    <input id="q" type="search" placeholder="Filter by company or role" aria-label="Filter by company or role">
    <div class="chips current" role="group" aria-label="Fit">
      <button class="chip" data-f="fit" data-v="all" aria-pressed="true">All</button>
      <button class="chip" data-f="fit" data-v="keep" aria-pressed="false">Keep</button>
      <button class="chip" data-f="fit" data-v="stretch" aria-pressed="false">Stretch</button>
      <button class="chip" data-f="fit" data-v="new" aria-pressed="false">New</button>
    </div>
    <div class="chips current" role="group" aria-label="Where">
      <button class="chip" data-f="pool" data-v="all" aria-pressed="true">Anywhere</button>
      <button class="chip" data-f="pool" data-v="local" aria-pressed="false" id="localChip">Local</button>
      <button class="chip" data-f="pool" data-v="remote" aria-pressed="false">Remote</button>
    </div>
    <div class="chips" id="statusChips" role="group" aria-label="Archive status" hidden>
      <button class="chip" data-f="status" data-v="all" aria-pressed="true">All</button>
      <button class="chip" data-f="status" data-v="hidden" aria-pressed="false">Hidden by you</button>
      <button class="chip" data-f="status" data-v="cut" aria-pressed="false">Cut</button>
      <button class="chip" data-f="status" data-v="closed" aria-pressed="false">Closed</button>
      <button class="chip" data-f="status" data-v="older" aria-pressed="false">Older</button>
    </div>
    <select id="fam" aria-label="Role type"></select>
    <select id="ind" aria-label="Industry"></select>
    <span class="sub" id="status" role="status"></span>
  </div>
  <section id="watch" class="current"><h2>Watchlist <small id="watchCount"></small></h2><div id="watchList"></div></section>
  <section id="market" class="current"><h2>Market search <small id="marketCount"></small></h2><div id="marketList"></div></section>
  <section id="pages" class="current"><h2>Careers pages</h2><div class="list" id="pageList"></div></section>
  <section id="attn" class="current"><h2>Radar status</h2><div class="list" id="attnList"></div></section>
  <section id="archive"><h2>Archive <small id="archiveCount"></small></h2>
    <p class="sub">Every role the radar has shown or judged: what you hid, what Snoopy cut, postings that closed, and market roles older than the report window.</p>
    <div id="archiveList"></div></section>
</div>

<script type="application/json" id="data">__DATA__</script>
<script>
(function(){
  const D = JSON.parse(document.getElementById("data").textContent);
  const LOCAL = D.local_label || "Local";
  const state = {fit:"all", pool:"all", fam:"all", ind:"all", q:"", view:"current", status:"all"};
  const ARCH = D.archive || [];
  let archLimit = 150;
  let hidden = new Map();            // db "hidden" collection: doc id -> {kind, label}
  let hiddenCol = null;
  const keyOf = s => String(s||"").toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 120) || "x";
  const roleKey = r => "role-" + keyOf(r.id);
  const coKey = c => "company-" + keyOf(c);
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
  const opts = (sel, label, values) => {
    const counts = {}; values.forEach(v => counts[v] = (counts[v]||0) + 1);
    const keys = Object.keys(counts).sort((a,b) => (a==="Other"||a==="Unknown") - (b==="Other"||b==="Unknown") || counts[b]-counts[a]);
    sel.innerHTML = `<option value="all">${esc(label)}</option>` + keys.map(k => `<option value="${esc(k)}">${esc(k)} (${counts[k]})</option>`).join("");
  };
  opts(document.getElementById("fam"), "All role types", all.concat(ARCH).map(r => r.family || "Other"));
  opts(document.getElementById("ind"), "All industries", all.concat(ARCH).flatMap(r => r.industries && r.industries.length ? r.industries : ["Unknown"]));
  const n = f => all.filter(f).length;
  const withRoles = new Set(D.watchlist_roles.map(r => r.company)).size;
  document.getElementById("stats").innerHTML = [
    [n(r => r.fit === "keep"), "Keep"],
    [n(r => r.fit === "stretch"), "Stretch"],
    [n(r => r.new), "New in 2 days"],
    [D.awaiting_review || 0, "Awaiting judgement"],
    [withRoles + " / " + D.watchlist_size, "Watchlist companies hiring"],
    [D.market_roles.length, "Market matches, " + D.market_days + " days"],
  ].map(([b,s]) => `<div class="stat"><b>${esc(b)}</b><span>${esc(s)}</span></div>`).join("");

  function roleHTML(r){
    const k = fitKey(r);
    const flags = (r.flags||[]).map(f => `<i>${esc(f)}</i>`).join("");
    const since = r.first_seen || (r.found||"").slice(0,10);
    const labels = [r.family].concat(r.industries || []).filter(x => x && x !== "Unknown");
    return `<div class="role"><span class="fit ${k}">${FITL[k]}</span>
      <div class="r-main"><div><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a>${r.new ? '<span class="badge">New</span>' : ""}</div>
      <div class="meta">${esc(where(r))}${since ? " · since " + esc(since) : ""}</div>
      ${labels.length ? `<div class="labels">${labels.map(l => `<span>${esc(l)}</span>`).join("<span>·</span>")}</div>` : ""}
      ${r.note ? `<div class="note">${esc(r.note)}</div>` : ""}
      ${flags ? `<div class="flags">${flags}</div>` : ""}</div>
      <button class="hide" data-hide="role" data-id="${esc(r.id)}" aria-label="Hide ${esc(r.title)} at ${esc(r.company)}">Hide</button></div>`;
  }
  function groupHTML(roles){
    const groups = new Map();
    roles.forEach(r => { if(!groups.has(r.company)) groups.set(r.company, []); groups.get(r.company).push(r); });
    if (!groups.size) return `<p class="empty">No roles match these filters.</p>`;
    return [...groups].map(([c, rs]) => `<article class="co"><div class="co-h"><h3>${esc(c)}</h3>
      <span class="right"><span class="tag">${rs[0].kind === "vc" ? "VC · " : ""}${rs.length} role${rs.length>1?"s":""}</span>
      <button class="hide" data-hide="company" data-company="${esc(c)}" aria-label="Hide all roles at ${esc(c)}">Hide company</button></span></div>
      ${rs.map(roleHTML).join("")}</article>`).join("");
  }
  const isHidden = r => hidden.has(roleKey(r)) || hidden.has(coKey(r.company));
  function common(r){
    if (state.fam !== "all" && (r.family || "Other") !== state.fam) return false;
    if (state.ind !== "all" && !(r.industries && r.industries.length ? r.industries : ["Unknown"]).includes(state.ind)) return false;
    const q = state.q.trim().toLowerCase();
    return !q || (r.company + " " + r.title + " " + (r.note||"") + " " + (r.why||"")).toLowerCase().includes(q);
  }
  const STL = {hidden:"Hidden by you", cut:"Cut", closed:"Closed", gone:"No longer listed", older:"Older"};
  function archiveRows(){
    const rows = ARCH.map(r => ({...r, st: isHidden(r) ? "hidden" : r.status}))
      .concat(all.filter(isHidden).map(r => ({...r, st:"hidden"})));
    [...hidden].filter(([k, v]) => v.kind === "company" && !rows.some(r => coKey(r.company) === k))
      .forEach(([k, v]) => rows.push({id:k, company:v.label, title:"All roles at this company", st:"hidden", coOnly:true}));
    return rows.filter(r => (state.status === "all" || r.st === state.status || (state.status === "closed" && r.st === "gone")) && (r.coOnly || common(r)));
  }
  function archHTML(r){
    const key = r.coOnly ? r.id : (hidden.has(roleKey(r)) ? roleKey(r) : coKey(r.company));
    const restore = r.st === "hidden" ? `<button class="hide" data-restore="${esc(key)}" aria-label="Show ${esc(r.title)} again">Restore</button>` : "<span></span>";
    const labels = [r.family].concat(r.industries || []).filter(x => x && x !== "Unknown");
    const when = r.st === "hidden" || !r.status_on ? "" : " · " + esc((STL[r.st]||r.st).toLowerCase()) + " " + esc(r.status_on);
    return `<div class="role"><span class="st ${esc(r.st)}">${esc(STL[r.st]||r.st)}</span>
      <div class="r-main"><div>${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a>` : esc(r.title)}</div>
      <div class="meta">${esc(r.company)}${r.first_seen ? " · seen " + esc(r.first_seen) : ""}${when}${r.fit && r.fit !== "cut" ? " · " + esc(r.fit) : ""}</div>
      ${r.why || r.note ? `<div class="why">${esc(r.why || r.note)}</div>` : ""}
      ${labels.length ? `<div class="labels">${labels.map(l => `<span>${esc(l)}</span>`).join("<span>·</span>")}</div>` : ""}</div>${restore}</div>`;
  }
  function pass(r){
    if (isHidden(r)) return false;
    if (state.fit === "new" ? !r.new : (state.fit !== "all" && fitKey(r) !== state.fit)) return false;
    if (state.pool !== "all" && r.pool !== state.pool) return false;
    return common(r);
  }
  function render(){
    const w = D.watchlist_roles.filter(pass), m = D.market_roles.filter(pass);
    document.getElementById("watchList").innerHTML = groupHTML(w);
    document.getElementById("marketList").innerHTML = groupHTML(m);
    document.getElementById("watchCount").textContent = w.length + " roles";
    document.getElementById("marketCount").textContent = m.length + " roles";
    document.querySelectorAll(".chip").forEach(b => b.setAttribute("aria-pressed", String(state[b.dataset.f] === b.dataset.v)));
    document.getElementById("fam").value = state.fam; document.getElementById("ind").value = state.ind;
    document.body.classList.toggle("view-archive", state.view === "archive");
    document.getElementById("statusChips").hidden = state.view !== "archive";
    if (state.view === "archive") {
      const rows = archiveRows();
      document.getElementById("archiveCount").textContent = rows.length + " roles";
      document.getElementById("archiveList").innerHTML = rows.length
        ? `<div class="co">${rows.slice(0, archLimit).map(archHTML).join("")}</div>` +
          (rows.length > archLimit ? `<button class="more" id="more">Show ${Math.min(150, rows.length - archLimit)} more</button>` : "")
        : `<p class="empty">Nothing here with these filters.</p>`;
      const m = document.getElementById("more"); if (m) m.onclick = () => { archLimit += 150; render(); };
    }
    try { localStorage.setItem("radar-filters", JSON.stringify({fit:state.fit, pool:state.pool, fam:state.fam, ind:state.ind, view:state.view, status:state.status})); } catch(e){}
  }
  document.querySelectorAll(".chip").forEach(b => b.addEventListener("click", () => { state[b.dataset.f] = b.dataset.v; archLimit = 150; render(); }));
  document.getElementById("q").addEventListener("input", e => { state.q = e.target.value; render(); });
  ["fam","ind"].forEach(id => document.getElementById(id).addEventListener("change", e => { state[id] = e.target.value; render(); }));
  if (![...document.getElementById("fam").options].some(o => o.value === state.fam)) state.fam = "all";
  if (![...document.getElementById("ind").options].some(o => o.value === state.ind)) state.ind = "all";

  // Hide / restore: kept in the artifact's db, so it survives every republish. Without db (a saved copy,
  // the notes vault) the buttons stay hidden and the page works read-only.
  let busy = false;
  document.addEventListener("click", async e => {
    const b = e.target.closest("button[data-hide],button[data-restore]");
    if (!b || !hiddenCol || busy) return;
    busy = true; b.disabled = true;
    try {
      if (b.dataset.restore) {
        await hiddenCol.doc(b.dataset.restore).delete();
        document.getElementById("status").textContent = "Restored";
      } else if (b.dataset.hide === "company") {
        const c = b.dataset.company;
        await hiddenCol.doc(coKey(c)).set({kind:"company", label:c, company:c, at:new Date().toISOString()});
        document.getElementById("status").textContent = "Hid " + c + ". It's in Archive, under Hidden by you.";
      } else {
        const r = all.find(x => String(x.id) === b.dataset.id);
        if (r) await hiddenCol.doc(roleKey(r)).set({kind:"role", label:r.title + " · " + r.company, company:r.company, title:r.title, role_id:String(r.id), url:r.url||"", at:new Date().toISOString()});
        document.getElementById("status").textContent = "Hid " + r.title + ". It's in Archive, under Hidden by you.";
      }
    } catch(err) {
      b.disabled = false;
      document.getElementById("status").textContent = err && err.code === "invalid_argument"
        ? "You can view this page but not change it." : "Couldn't save that. Check your connection and try again.";
    } finally { busy = false; }
  });
  (async () => {
    try {
      const db = window.claude && window.claude.use ? await window.claude.use("db") : null;
      if (!db) return;
      hiddenCol = db.collection("hidden");
      hiddenCol.onSnapshot(snap => {
        hidden = new Map(snap.docs.map(d => [d.id, d.data() || {}]));
        document.body.classList.add("can-hide");
        render();
      }, () => { hiddenCol = null; document.body.classList.remove("can-hide"); });
    } catch(e) {}
  })();

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
  // only what needs the person goes first; radar housekeeping folds into one line each
  (D.unverified_roles||[]).forEach(r => A.push(`<p class="warn">Could not verify the link (page needs JavaScript), check before applying: <b>${esc(r.company)}</b> · <a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a> · ${FITL[fitKey(r)]}${r.note ? " · " + esc(r.note) : ""}</p>`));
  const F = D.failing||[], AU = D.audit||[];
  if (F.length) A.push(`<details><summary>${F.length} job board${F.length>1?"s":""} not answering. The weekly repair switches each company to its current board, or stops checking it.</summary>
    <p class="sub">${F.map(f => esc(f.company) + " (" + esc(f.board) + ")").join(", ")}</p></details>`);
  if (AU.length) A.push(`<details><summary>${AU.length} compan${AU.length>1?"ies":"y"} whose careers page links to a different job board. The weekly repair records it.</summary>
    <p class="sub">${AU.map(a => esc(a.company) + ": " + a.found_on_page.map(esc).join(", ")).join("; ")}</p></details>`);
  if (D.verified_at) A.push(`<p class="sub">Every listed link was confirmed live at ${esc((D.verified_at||"").replace("T"," ").slice(0,16))} UTC; ${D.closed_count||0} closed postings removed.</p>`);
  if (D.hidden_cut) A.push(`<p class="sub">${D.hidden_cut} reviewed roles were cut and are hidden.</p>`);
  document.getElementById("attnList").innerHTML = A.join("") || `<p class="empty">All job boards are answering.</p>`;
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
