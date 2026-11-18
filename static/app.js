"use strict";
const $ = (s, el = document) => el.querySelector(s);
const api = async (url, opts = {}) => {
  const r = await fetch(url, opts);
  let data = null;
  try { data = await r.json(); } catch { /* non-json */ }
  if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
  return data;
};
const fmt = (s) => {
  s = Math.max(0, s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = (s % 60).toFixed(1).padStart(4, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
};
const parseT = (v) => {
  const p = String(v).trim().split(":").map(Number);
  if (p.some(isNaN)) return NaN;
  return p.reduce((a, b) => a * 60 + b, 0);
};
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let LANGS = {};
let cur = null;          // current project data
let curId = null;
let pollTimer = null;
let clipsKey = "";       // to know when to rebuild the clip grid
const edits = {};        // id -> {start, end}
const selected = new Set();
let stopAt = null;       // preview end time

// ------------------------------------------------------------ boot
(async function boot() {
  try {
    const h = await api("/api/health");
    const bits = [
      h.ffmpeg ? '<span class="ok">ffmpeg ✓</span>' : '<span class="bad">ffmpeg missing</span>',
      h.node ? '<span class="ok">node ✓</span>' : '<span class="bad">node missing</span>',
      `encoder: ${h.encoder === "h264_qsv" ? "Intel GPU" : esc(h.encoder)}`,
    ];
    $("#health").innerHTML = bits.join(" · ");
  } catch (e) { $("#health").textContent = "health check failed"; }
  LANGS = await api("/api/languages");
  const sl = $("#spokenLang");
  for (const [k, v] of Object.entries(LANGS)) sl.insertAdjacentHTML("beforeend", `<option value="${k}">${esc(v)}</option>`);
  loadRecent();
  const hash = location.hash.slice(1);
  if (hash) openProject(hash);
})();

async function loadRecent() {
  const list = await api("/api/projects");
  const ul = $("#recent");
  if (!list.length) { ul.innerHTML = '<li class="muted">Nothing yet</li>'; return; }
  ul.innerHTML = list.map((p) => `<li data-id="${p.id}"><span>${esc(p.title || p.id)}</span>
    <span class="muted">${p.status === "ready" ? p.n_clips + " clips" : esc(p.status)}</span></li>`).join("");
  ul.querySelectorAll("li[data-id]").forEach((li) => li.onclick = () => openProject(li.dataset.id));
}

// ------------------------------------------------------------ new project
$("#newForm").onsubmit = async (ev) => {
  ev.preventDefault();
  const fd = new FormData(ev.target);
  const file = $("#file").files[0];
  if (!file) fd.delete("file");
  if (!fd.get("url") && !file) { $("#formErr").textContent = "Paste a YouTube link or attach a video file."; return; }
  $("#formErr").textContent = "";
  const btn = $("#goBtn");
  btn.disabled = true; btn.textContent = file ? "Uploading file…" : "Starting…";
  try {
    const r = await api("/api/analyze", { method: "POST", body: fd });
    openProject(r.id);
    loadRecent();
  } catch (e) { $("#formErr").textContent = e.message; }
  finally { btn.disabled = false; btn.textContent = "Find clips"; }
};

// ------------------------------------------------------------ project view
function openProject(id) {
  curId = id; clipsKey = ""; cur = null;
  selected.clear(); for (const k in edits) delete edits[k];
  location.hash = id;
  const view = $("#view");
  view.innerHTML = "";
  view.appendChild($("#projTpl").content.cloneNode(true));
  $(".proj", view).appendChild($("#exportTpl").content.cloneNode(true));
  setupExport();
  clearInterval(pollTimer);
  poll();
  pollTimer = setInterval(poll, 1200);
}

async function poll() {
  if (!curId) return;
  const id = curId;
  let p;
  try { p = await api(`/api/project/${id}`); } catch (e) { return; }
  if (id !== curId) return;
  cur = p;
  $(".title").textContent = p.title || "Working…";
  $(".sub").innerHTML = [p.duration ? fmt(p.duration) : "", p.source_lang ? "language: " + esc(LANGS[p.source_lang] || p.source_lang) : "",
    p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noopener" style="color:inherit">source ↗</a>` : ""].filter(Boolean).join(" · ");
  $(".stage").textContent = p.status === "error" ? "Failed" : p.stage || "";
  $(".bar div").style.width = `${Math.round((p.progress || 0) * 100)}%`;
  $(".perr").textContent = p.status === "error" ? p.error || "" : "";
  $(".warns").innerHTML = (p.warnings || []).map((w) => `<div class="warn">⚠ ${esc(w)}</div>`).join("");
  const logEl = $(".log");
  const atBottom = logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 4;
  logEl.textContent = (p.log || []).join("\n");
  if (atBottom) logEl.scrollTop = logEl.scrollHeight;
  if (p.status === "error") $(".logbox").open = true;

  if (p.status === "ready") {
    $(".status").classList.toggle("hidden", p.render_status !== "running" && p.render_status !== "queued" ? true : false);
    $(".ready").classList.remove("hidden");
    const key = p.id + ":" + p.clips.length;
    if (key !== clipsKey) { clipsKey = key; buildReady(p); }
    updateRenders(p);
  }
  const busy = p.status === "queued" || p.status === "running" || p.render_status === "queued" || p.render_status === "running";
  if (!busy) {
    if (pollTimer) loadRecent();
    clearInterval(pollTimer); pollTimer = null;
  }
}

function ensurePolling() { if (!pollTimer) pollTimer = setInterval(poll, 1200); }

function buildReady(p) {
  const v = $("#player");
  v.src = `/media/${p.id}/source`;
  v.ontimeupdate = () => { if (stopAt !== null && v.currentTime >= stopAt) { v.pause(); stopAt = null; } };
  drawCurve(p);
  window.onresize = () => drawCurve(cur);
  const wrap = $(".clips");
  wrap.innerHTML = "";
  for (const c of p.clips) {
    edits[c.id] = edits[c.id] || { start: c.start, end: c.end };
    const cls = c.score >= 80 ? "hi" : c.score >= 55 ? "mid" : "";
    const el = document.createElement("div");
    el.className = "clip"; el.dataset.id = c.id;
    el.innerHTML = `
      <div class="top">
        <label class="check"><input type="checkbox" class="pick"> <b>#${c.id}</b></label>
        <span class="score ${cls}" title="Relative hype score">${c.score}</span>
      </div>
      <div class="chips">${(c.reasons || []).map((r) => `<span class="chip">${esc(r)}</span>`).join("") || '<span class="chip">Audio energy</span>'}</div>
      ${c.top_comments && c.top_comments.length ? `<div class="quote">💬 ${esc(c.top_comments[0].text)} <span class="muted">(${c.top_comments[0].likes} likes)</span></div>` : ""}
      ${c.text ? `<div class="quote">“${esc(c.text.slice(0, 160))}${c.text.length > 160 ? "…" : ""}”</div>` : ""}
      <div class="times">
        <button data-n="s-1">−1</button><input class="ts" value="${fmt(edits[c.id].start)}"><button data-n="s+1">+1</button>
        →
        <button data-n="e-1">−1</button><input class="te" value="${fmt(edits[c.id].end)}"><button data-n="e+1">+1</button>
        <span class="muted dur"></span>
      </div>
      <div class="row"><button class="play">▶ Preview</button></div>
      <div class="rstat"></div>`;
    wrap.appendChild(el);
    const pick = $(".pick", el);
    pick.checked = selected.has(c.id);
    el.classList.toggle("sel", pick.checked);
    pick.onchange = () => { pick.checked ? selected.add(c.id) : selected.delete(c.id); el.classList.toggle("sel", pick.checked); updateSel(); };
    const ts = $(".ts", el), te = $(".te", el);
    const refresh = () => { ts.value = fmt(edits[c.id].start); te.value = fmt(edits[c.id].end);
      $(".dur", el).textContent = `${(edits[c.id].end - edits[c.id].start).toFixed(1)}s`; drawCurve(cur); };
    const setT = (which, val) => {
      if (isNaN(val)) return refresh();
      const e = edits[c.id];
      if (which === "s") e.start = Math.max(0, Math.min(val, e.end - 3));
      else e.end = Math.min(p.duration, Math.max(val, e.start + 3));
      refresh();
    };
    ts.onchange = () => setT("s", parseT(ts.value));
    te.onchange = () => setT("e", parseT(te.value));
    el.querySelectorAll("[data-n]").forEach((b) => b.onclick = () => {
      const [w, d] = [b.dataset.n[0], Number(b.dataset.n.slice(1))];
      setT(w, (w === "s" ? edits[c.id].start : edits[c.id].end) + d);
    });
    $(".play", el).onclick = () => { v.currentTime = edits[c.id].start; stopAt = edits[c.id].end; v.play(); v.scrollIntoView({ behavior: "smooth", block: "center" }); };
    refresh();
  }
  $("#selAll").onchange = (e) => {
    document.querySelectorAll(".clip").forEach((el) => { const pk = $(".pick", el); pk.checked = e.target.checked; pk.onchange(); });
  };
  updateSel();
}

function updateSel() { $("#selCount").textContent = selected.size ? `${selected.size} selected` : "Pick clips to export"; }

function drawCurve(p) {
  const cv = $("#curve");
  if (!cv || !p || !p.signal_curve) return;
  const w = cv.clientWidth, h = 70, dpr = window.devicePixelRatio || 1;
  cv.width = w * dpr; cv.height = h * dpr;
  const g = cv.getContext("2d");
  g.scale(dpr, dpr);
  g.clearRect(0, 0, w, h);
  const d = p.duration || 1;
  for (const c of p.clips) {
    const e = edits[c.id] || c;
    g.fillStyle = selected.has(c.id) ? "rgba(255,204,0,.28)" : "rgba(255,255,255,.07)";
    g.fillRect((e.start / d) * w, 0, ((e.end - e.start) / d) * w, h);
  }
  const s = p.signal_curve, mx = Math.max(...s, 1e-6);
  g.beginPath(); g.moveTo(0, h);
  s.forEach((y, i) => g.lineTo((i / (s.length - 1 || 1)) * w, h - (y / mx) * (h - 6)));
  g.lineTo(w, h); g.closePath();
  const grad = g.createLinearGradient(0, 0, 0, h); grad.addColorStop(0, "rgba(255,77,109,.85)"); grad.addColorStop(1, "rgba(255,204,0,.15)");
  g.fillStyle = grad; g.fill();
  cv.onclick = (ev) => { const r = cv.getBoundingClientRect(); const v = $("#player"); stopAt = null; v.currentTime = ((ev.clientX - r.left) / r.width) * d; v.play(); };
}

// ------------------------------------------------------------ export
function setupExport() {
  const capLang = $("#capLang"), dubLang = $("#dubLang");
  for (const [k, v] of Object.entries(LANGS)) {
    capLang.insertAdjacentHTML("beforeend", `<option value="${k}">${esc(v)}</option>`);
    dubLang.insertAdjacentHTML("beforeend", `<option value="${k}">${esc(v)}</option>`);
  }
  capLang.value = "hi";
  try { $("#mmEmail").value = localStorage.getItem("cf_mm") || ""; } catch { /* storage blocked */ }
  $("#captions").onchange = () => $("#capLangWrap").classList.toggle("hidden", $("#captions").value !== "translated" || !!dubLang.value);
  dubLang.onchange = async () => {
    const on = !!dubLang.value;
    ["#voiceWrap", "#volWrap", ".dubnote"].forEach((s) => $(s).classList.toggle("hidden", !on));
    $("#captions").onchange();
    if (!on) return;
    const vs = $("#voice");
    vs.innerHTML = "<option>loading…</option>";
    try {
      const list = await api(`/api/voices/${dubLang.value}`);
      vs.innerHTML = list.length ? list.map((x) => `<option value="${x.name}">${esc(x.name.split("-").slice(2).join("-").replace("Neural", ""))} · ${x.gender} · ${x.locale}</option>`).join("")
        : "<option value=''>No voice for this language</option>";
    } catch (e) { vs.innerHTML = `<option value="">${esc(e.message)}</option>`; }
  };
  $("#exportBtn").onclick = doExport;
  $("#openFolder").onclick = () => api(`/api/project/${curId}/open-folder`, { method: "POST" }).catch((e) => alert(e.message));
}

async function doExport() {
  const err = $("#exportErr");
  err.textContent = "";
  if (!cur || cur.status !== "ready") { err.textContent = "Wait until clips are found."; return; }
  if (!selected.size) { err.textContent = "Tick at least one clip."; return; }
  const email = $("#mmEmail").value.trim();
  try { localStorage.setItem("cf_mm", email); } catch { /* ignore */ }
  const body = {
    clips: [...selected].map((id) => ({ id, start: edits[id].start, end: edits[id].end })),
    options: {
      layout: $("#layout").value, captions: $("#captions").value, caption_lang: $("#capLang").value,
      dub_lang: $("#dubLang").value, dub_voice: $("#voice").value,
      original_volume: Number($("#vol").value) / 100, mymemory_email: email,
    },
  };
  try {
    await api(`/api/project/${curId}/render`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    ensurePolling(); poll();
  } catch (e) { err.textContent = e.message; }
}

function updateRenders(p) {
  const res = $(".results");
  const r = p.renders || {};
  const running = p.render_status === "running" || p.render_status === "queued";
  $("#exportBtn").disabled = running;
  $("#exportBtn").textContent = running ? "Exporting…" : "Export selected";
  const rows = [];
  for (const [id, x] of Object.entries(r)) {
    const card = document.querySelector(`.clip[data-id="${id}"] .rstat`);
    let label = x.status;
    if (x.status === "done") label = `✓ exported (${x.secs}s)`;
    if (x.status === "error") label = "✗ " + (x.error || "failed");
    if (card) { card.className = "rstat " + x.status; card.textContent = label; }
    rows.push(`<div class="r"><span>#${id} <span class="rstat ${x.status}">${esc(x.status === "error" ? "failed" : x.status)}</span></span>
      ${x.file ? `<a href="/media/${p.id}/out/${encodeURIComponent(x.file)}" target="_blank">${esc(x.file)}</a>` : ""}</div>`);
  }
  res.innerHTML = rows.join("");
  $("#openFolder").classList.toggle("hidden", !p.output_dir);
}
