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
let MUSIC = [];
const clipHook = {};     // id -> custom hook text
const clipLayout = {};   // id -> layout override

const copyText = async (text) => {
  try { await navigator.clipboard.writeText(text); return; } catch { /* not a secure context (phone mode) */ }
  const ta = document.createElement("textarea");
  ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove();
};

// ------------------------------------------------------------ channel & AI settings
async function loadSettings() {
  let d;
  try { d = await api("/api/settings"); } catch { return; }
  const s = d.settings;
  $("#sProvider").innerHTML = Object.entries(d.providers).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  $("#sGame").value = s.game || ""; $("#sStreamer").value = s.streamer || ""; $("#sStreamerUrl").value = s.streamer_url || "";
  $("#sHandle").value = s.my_handle || ""; $("#sTags").value = s.extra_hashtags || "";
  $("#sShowHandle").checked = !!s.show_handle; $("#sShowCredit").checked = !!s.show_credit;
  $("#sProvider").value = s.ai_provider || "none"; $("#sBase").value = s.ai_base_url || "";
  $("#sKey").placeholder = s.ai_key_set ? "saved ✓ (leave empty to keep)" : "paste key";
  if (s.ai_model) $("#sModel").innerHTML = `<option value="${esc(s.ai_model)}">${esc(s.ai_model)}</option>`;
  const prov = () => $("#sBaseWrap").classList.toggle("hidden", $("#sProvider").value !== "custom");
  $("#sProvider").onchange = prov; prov();
  if (!s.streamer && !s.my_handle) $("#chanBox").open = true;
  const msg = (m) => { $("#sMsg").textContent = m; if ($("#sKey").value === "" && /✓|models/.test(m)) $("#sKey").placeholder = "saved ✓ (leave empty to keep)"; };
  const collect = () => ({
    game: $("#sGame").value.trim(), streamer: $("#sStreamer").value.trim(), streamer_url: $("#sStreamerUrl").value.trim(),
    my_handle: $("#sHandle").value.trim(), extra_hashtags: $("#sTags").value.trim(),
    show_handle: $("#sShowHandle").checked, show_credit: $("#sShowCredit").checked,
    ai_provider: $("#sProvider").value, ai_base_url: $("#sBase").value.trim(), ai_key: $("#sKey").value.trim(),
    ai_model: $("#sModel").value,
  });
  $("#sSave").onclick = async () => {
    try { const r = await api("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(collect()) });
      $("#sKey").value = ""; $("#sKey").placeholder = r.ai_key_set ? "saved ✓ (leave empty to keep)" : "paste key";
      msg("Saved ✓" + (r.ai_provider !== "none" && !r.ai_model ? " - now click Load to pick a model." : ""));
    } catch (e) { msg(e.message); }
  };
  $("#sLoadModels").onclick = async () => {
    msg("Saving and asking the provider for models…");
    try {
      await api("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(collect()) });
      $("#sKey").value = "";
      const r = await api("/api/ai/models", { method: "POST" });
      $("#sModel").innerHTML = r.models.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join("");
      $("#sModel").value = r.selected;
      await api("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ai_model: r.selected }) });
      msg(`${r.models.length} models. Picked ${r.selected} - click Test AI.`);
    } catch (e) { msg(e.message); }
  };
  $("#sTest").onclick = async () => {
    msg("Testing…");
    try {
      await api("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(collect()) });
      $("#sKey").value = "";
      const r = await api("/api/ai/test", { method: "POST" });
      msg(`Works ✓  Hook: “${r.hook}”  Title: “${r.title}”`);
    } catch (e) { msg(e.message); }
  };
}

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
    // On the PC itself, pick the file where it is (no upload). Other devices must upload.
    $(h.local ? "#localPick" : "#file").classList.remove("hidden");
  } catch (e) { $("#health").textContent = "health check failed"; $("#file").classList.remove("hidden"); }
  LANGS = await api("/api/languages");
  const sl = $("#spokenLang");
  for (const [k, v] of Object.entries(LANGS)) sl.insertAdjacentHTML("beforeend", `<option value="${k}">${esc(v)}</option>`);
  loadRecent();
  loadSettings();
  try { MUSIC = await api("/api/music"); } catch { MUSIC = []; }
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
$("#pickBtn").onclick = async () => {
  const b = $("#pickBtn");
  b.disabled = true; b.textContent = "Choose the file in the window that opened…";
  try {
    const r = await api("/api/pick-file", { method: "POST" });
    if (r.path) $("#localPath").value = r.path;
  } catch (e) { $("#formErr").textContent = e.message; }
  finally { b.disabled = false; b.textContent = "📁 Choose video on this PC"; }
};

$("#newForm").onsubmit = async (ev) => {
  ev.preventDefault();
  const fd = new FormData(ev.target);
  const local = $("#localPath").value.trim();
  const file = local ? null : $("#file").files[0];
  if (!file) fd.delete("file");
  if (!local) fd.delete("local_path");
  if (!fd.get("url") && !file && !local) { $("#formErr").textContent = "Paste a YouTube link or choose a video file."; return; }
  $("#formErr").textContent = "";
  const btn = $("#goBtn");
  btn.disabled = true; btn.textContent = file ? "Uploading file…" : "Starting…";
  try {
    const r = await api("/api/analyze", { method: "POST", body: fd });
    $("#localPath").value = ""; $("#file").value = "";
    openProject(r.id);
    loadRecent();
  } catch (e) { $("#formErr").textContent = e.message; }
  finally { btn.disabled = false; btn.textContent = "Find clips"; }
};

// ------------------------------------------------------------ project view
function openProject(id) {
  curId = id; clipsKey = ""; cur = null;
  selected.clear(); for (const k in edits) delete edits[k];
  for (const o of [clipHook, clipLayout]) for (const k in o) delete o[k];
  location.hash = id;
  const view = $("#view");
  view.innerHTML = "";
  view.appendChild($("#projTpl").content.cloneNode(true));
  $(".proj", view).appendChild($("#exportTpl").content.cloneNode(true));
  setupExport();
  setupJobControls();
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
  const working = p.status === "running" || p.render_status === "running";
  const waiting = p.status === "queued" || p.render_status === "queued";
  let stage = p.status === "error" ? "Failed" : p.stage || "";
  if (p.render_status === "running" || p.render_status === "queued") stage = p.render_status === "queued" ? "Export waiting…" : "Exporting…";
  if (p.paused) stage = "⏸ Paused - " + stage;
  $(".stage").textContent = stage;
  $(".bar").classList.toggle("paused", !!p.paused);
  $(".bar div").style.width = `${Math.round((p.progress || 0) * 100)}%`;
  if (p.render_status === "running" || p.render_status === "queued") {
    const all = Object.values(p.renders || {}), done = all.filter((x) => x.status === "done" || x.status === "error").length;
    $(".bar div").style.width = `${Math.round((done / Math.max(1, all.length)) * 100)}%`;
  }
  $("#pauseBtn").classList.toggle("hidden", !working);
  $("#pauseBtn").textContent = p.paused ? "▶ Resume" : "⏸ Pause";
  $("#stopBtn").classList.toggle("hidden", !(working || waiting));
  $("#restartBtn").classList.toggle("hidden", !(p.status === "stopped" || p.status === "error"));
  $("#ctlNote").textContent = p.paused ? "Paused - your PC is free. Nothing is lost." : working ? "Pause frees up your PC; Resume continues where it left off." : "";
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

function setupJobControls() {
  const act = async (action, btn) => {
    btn.disabled = true;
    try { await api(`/api/project/${curId}/${action}`, { method: "POST" }); }
    catch (e) { $("#ctlNote").textContent = e.message; }
    finally { btn.disabled = false; ensurePolling(); poll(); }
  };
  $("#pauseBtn").onclick = () => act(cur && cur.paused ? "resume" : "pause", $("#pauseBtn"));
  $("#stopBtn").onclick = () => {
    const exporting = cur && (cur.render_status === "running" || cur.render_status === "queued");
    const msg = exporting ? "Stop exporting? Reels already finished are kept; the one in progress is discarded."
      : "Stop finding clips? You can press “Run again” later.";
    if (confirm(msg)) act("stop", $("#stopBtn"));
  };
  $("#restartBtn").onclick = () => act("restart", $("#restartBtn"));
}

function buildReady(p) {
  const v = $("#player");
  v.src = `/media/${p.id}/source`;
  v.ontimeupdate = () => { if (stopAt !== null && v.currentTime >= stopAt) { v.pause(); stopAt = null; } };
  drawCurve(p);
  setupFraming(p);
  window.onresize = () => { drawCurve(cur); showFace(); };
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
      <div class="extra">
        <input class="hook" maxlength="60" placeholder="Hook text (empty = auto)" title="Big text shown in the first 2 seconds">
        <select class="lay" title="Layout for this clip">
          <option value="">Layout: default</option><option value="split">Split</option><option value="fill">Full screen</option>
          <option value="zoom">Zoomed</option><option value="blur">Whole + blur</option>
        </select>
      </div>
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
    const hk = $(".hook", el), ly = $(".lay", el);
    hk.value = clipHook[c.id] || ""; ly.value = clipLayout[c.id] || "";
    hk.oninput = () => { clipHook[c.id] = hk.value; };
    ly.onchange = () => { clipLayout[c.id] = ly.value; };
    $(".play", el).onclick = () => { v.currentTime = edits[c.id].start; stopAt = edits[c.id].end; v.play(); v.scrollIntoView({ behavior: "smooth", block: "center" }); };
    refresh();
  }
  $("#selAll").onchange = (e) => {
    document.querySelectorAll(".clip").forEach((el) => { const pk = $(".pick", el); pk.checked = e.target.checked; pk.onchange(); });
  };
  updateSel();
}

// ------------------------------------------------------------ framing (facecam box, gameplay position, preview)
let crop = {};
function videoRect() {
  // Where the picture actually is inside the <video> element (it is letterboxed).
  const v = $("#player"), r = v.getBoundingClientRect(), wrap = $(".vwrap").getBoundingClientRect();
  const vw = v.videoWidth || 16, vh = v.videoHeight || 9;
  const s = Math.min(r.width / vw, r.height / vh);
  const w = vw * s, h = vh * s;
  return { x: r.left - wrap.left + (r.width - w) / 2, y: r.top - wrap.top + (r.height - h) / 2, w, h };
}
function showFace() {
  const box = $("#faceBox");
  if (!box) return;
  if (!crop.face || !$("#player").videoWidth) { box.classList.add("hidden"); return; }
  const R = videoRect(), f = crop.face;
  Object.assign(box.style, { left: `${R.x + f.x * R.w}px`, top: `${R.y + f.y * R.h}px`, width: `${f.w * R.w}px`, height: `${f.h * R.h}px` });
  box.classList.remove("hidden");
}
const saveCrop = async (data) => {
  try { crop = await api(`/api/project/${curId}/crop`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }); }
  catch (e) { $("#faceMsg").textContent = e.message; }
  showFace();
};
function setupFraming(p) {
  crop = p.crop || {};
  const v = $("#player");
  v.addEventListener("loadedmetadata", showFace);
  $("#focusX").value = Math.round((crop.focus_x ?? 0.5) * 100); $("#focusVal").textContent = $("#focusX").value;
  $("#splitR").value = Math.round((crop.split ?? 0.38) * 100); $("#splitVal").textContent = $("#splitR").value;
  let tmr;
  const later = (data) => { clearTimeout(tmr); tmr = setTimeout(() => saveCrop(data), 300); };
  $("#focusX").oninput = (e) => { $("#focusVal").textContent = e.target.value; later({ focus_x: e.target.value / 100 }); };
  $("#splitR").oninput = (e) => { $("#splitVal").textContent = e.target.value; later({ split: e.target.value / 100 }); };
  $("#clearFace").onclick = () => saveCrop({ face: null });
  if (crop.face) $("#faceMsg").textContent = "Facecam marked ✓ - the split layout uses it. Mark again to change.";
  const layer = $("#drawLayer"), box = $("#faceBox");
  $("#markFace").onclick = () => { v.pause(); layer.classList.toggle("hidden"); };
  let start = null;
  const pt = (e) => { const r = $(".vwrap").getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };
  layer.onpointerdown = (e) => { start = pt(e); layer.setPointerCapture(e.pointerId); };
  layer.onpointermove = (e) => {
    if (!start) return;
    const q = pt(e);
    Object.assign(box.style, { left: `${Math.min(start.x, q.x)}px`, top: `${Math.min(start.y, q.y)}px`,
      width: `${Math.abs(q.x - start.x)}px`, height: `${Math.abs(q.y - start.y)}px` });
    box.classList.remove("hidden");
  };
  layer.onpointerup = (e) => {
    if (!start) return;
    const q = pt(e), R = videoRect();
    const nx = (a) => Math.max(0, Math.min(1, (a - R.x) / R.w)), ny = (a) => Math.max(0, Math.min(1, (a - R.y) / R.h));
    const x0 = nx(Math.min(start.x, q.x)), x1 = nx(Math.max(start.x, q.x)), y0 = ny(Math.min(start.y, q.y)), y1 = ny(Math.max(start.y, q.y));
    start = null;
    layer.classList.add("hidden");
    if (x1 - x0 < 0.03 || y1 - y0 < 0.03) { $("#faceMsg").textContent = "Box too small - drag over the whole camera."; showFace(); return; }
    saveCrop({ face: { x: x0, y: y0, w: x1 - x0, h: y1 - y0 } }).then(() => {
      $("#faceMsg").textContent = "Facecam marked ✓ - the split layout uses it.";
      if ($("#layout")) $("#layout").value = "split";
    });
  };
  $("#previewBtn").onclick = async () => {
    const o = exportOptions();
    const q = new URLSearchParams({ t: v.currentTime.toFixed(2), format: o.format, layout: o.layout, caption_style: o.caption_style,
      captions: o.captions, hook_mode: o.hook_mode, hook_text: "" });
    $("#previewMsg").textContent = "Rendering…";
    $("#previewImg").classList.remove("on");
    try {
      const r = await fetch(`/api/project/${curId}/preview.jpg?${q}`);
      if (!r.ok) throw new Error((await r.json()).error);
      const img = $("#previewImg");
      img.src = URL.createObjectURL(await r.blob());
      img.classList.add("on");
      const [a, b] = o.format.split(":").map(Number);
      $(".phone").style.aspectRatio = `${a} / ${b}`;
    } catch (e) { $("#previewMsg").textContent = e.message; }
  };
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
  capLang.value = "en";
  try { $("#mmEmail").value = localStorage.getItem("cf_mm") || ""; } catch { /* storage blocked */ }
  $("#music").insertAdjacentHTML("beforeend", MUSIC.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join(""));
  $("#music").onchange = () => $("#musicVolWrap").classList.toggle("hidden", !$("#music").value);
  const PRESETS = {
    viral: { teaser: 1, intro: 1, deadAir: 1, punch: 1, progress: 1, whoosh: 1, normalize: 1, hookMode: "auto", ending: "cta", capStyle: "yellow" },
    clean: { teaser: 0, intro: 0, deadAir: 1, punch: 0, progress: 0, whoosh: 0, normalize: 1, hookMode: "auto", ending: "fade", capStyle: "clean" },
    raw: { teaser: 0, intro: 0, deadAir: 0, punch: 0, progress: 0, whoosh: 0, normalize: 1, hookMode: "off", ending: "loop", capStyle: "yellow" },
  };
  document.querySelectorAll("[data-preset]").forEach((b) => b.onclick = () => {
    for (const [k, v] of Object.entries(PRESETS[b.dataset.preset])) {
      const el = $("#" + k);
      if (el.type === "checkbox") el.checked = !!v; else el.value = v;
    }
    try { localStorage.setItem("cf_export", JSON.stringify(exportOptions())); } catch { /* ignore */ }
  });
  // Remember the last export settings.
  try {
    const last = JSON.parse(localStorage.getItem("cf_export") || "null");
    if (last) {
      const map = { format: "format", layout: "layout", hook_mode: "hookMode", caption_style: "capStyle", captions: "captions",
        caption_lang: "capLang", whisper_size: "wSize", ending: "ending", teaser: "teaser", intro: "intro", cut_dead_air: "deadAir",
        punch_zoom: "punch", progress: "progress", normalize: "normalize", whoosh: "whoosh" };
      for (const [k, id] of Object.entries(map)) {
        if (last[k] === undefined) continue;
        const el = $("#" + id);
        if (el.type === "checkbox") el.checked = !!last[k]; else if ([...(el.options || [])].some((o) => o.value === String(last[k]))) el.value = last[k];
      }
    }
  } catch { /* ignore */ }
  $("#captions").onchange = () => $("#capLangWrap").classList.toggle("hidden", $("#captions").value !== "translated" || !!dubLang.value);
  $("#captions").onchange();
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

function exportOptions() {
  const ck = (id) => $("#" + id).checked;
  const pick = (o) => Object.fromEntries(Object.entries(o).filter(([, v]) => v));
  return {
    format: $("#format").value, layout: $("#layout").value, hook_mode: $("#hookMode").value,
    hooks: pick({ ...clipHook }), layouts: pick({ ...clipLayout }),
    teaser: ck("teaser"), intro: ck("intro"), caption_style: $("#capStyle").value, captions: $("#captions").value,
    caption_lang: $("#capLang").value, whisper_size: $("#wSize").value, cut_dead_air: ck("deadAir"), punch_zoom: ck("punch"),
    progress: ck("progress"), ending: $("#ending").value, normalize: ck("normalize"), whoosh: ck("whoosh"),
    music: $("#music").value, music_volume: Number($("#musicVol").value) / 100,
    dub_lang: $("#dubLang").value, dub_voice: $("#voice").value, original_volume: Number($("#vol").value) / 100,
    mymemory_email: $("#mmEmail").value.trim(),
  };
}

async function doExport() {
  const err = $("#exportErr");
  err.textContent = "";
  if (!cur || cur.status !== "ready") { err.textContent = "Wait until clips are found."; return; }
  if (!selected.size) { err.textContent = "Tick at least one clip."; return; }
  const options = exportOptions();
  try { localStorage.setItem("cf_mm", options.mymemory_email); localStorage.setItem("cf_export", JSON.stringify(options)); } catch { /* ignore */ }
  const body = { clips: [...selected].map((id) => ({ id, start: edits[id].start, end: edits[id].end })), options };
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
    if (x.kit) {
      const k = x.kit, blocks = [
        ["YouTube title", k.youtube.title], ["YouTube description", k.youtube.description],
        ["Instagram caption", k.instagram.caption], ["TikTok caption", k.tiktok.caption]];
      rows.push(`<details class="kit"><summary>Upload kit · hook “${esc(k.hook)}” <span class="muted">(${esc(k.writer)})</span></summary>
        ${blocks.map(([t, v], i) => `<div class="kh"><span>${t}</span><button data-copy="${id}:${i}">Copy</button></div><pre>${esc(v)}</pre>`).join("")}
      </details>`);
    }
  }
  const sig = JSON.stringify(r);
  if (res.dataset.sig !== sig) {  // don't rebuild (and close open kits) on every poll
    res.dataset.sig = sig;
    res.innerHTML = rows.join("");
    res.querySelectorAll("[data-copy]").forEach((b) => b.onclick = async () => {
      const [cid, i] = b.dataset.copy.split(":");
      const k = r[cid].kit;
      await copyText([k.youtube.title, k.youtube.description, k.instagram.caption, k.tiktok.caption][Number(i)]);
      b.textContent = "Copied ✓"; setTimeout(() => { b.textContent = "Copy"; }, 1500);
    });
  }
  $("#openFolder").classList.toggle("hidden", !p.output_dir);
}
