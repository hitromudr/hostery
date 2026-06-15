// AI Cockpit / hostery — Settings editor.
//
// Two views over one config state (`_cfg`): a Form and a raw JSON editor.
// Both share unified change tracking against `_baseline` (the last saved
// config): a dirty flag, a linear undo/redo history, Revert, and live change
// highlighting (changed form fields get a background; JSON shows a patch-style
// line diff). The action bar [Undo][Redo][Revert][Save] is duplicated top and
// bottom and is present in both views.

let _cfg = null;          // working config (current edits)
let _baseline = null;     // last SAVED config — the diff/dirty reference
let _cfgPath = "";
let _settingsLoaded = false;
let _settingsMode = "form";  // "form" | "json"
let _history = [];        // pretty-JSON snapshots of _cfg
let _histIndex = -1;      // pointer into _history
let _histTimer = null;    // debounce for typing snapshots
let _formBound = false;   // form delegation listeners attached once
let _jsonTree = false;    // JSON view showing the read-only collapsible tree

// --- small helpers ---

function esc(s) {
  return String(s == null ? "" : s)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}
function clone(o) { return JSON.parse(JSON.stringify(o)); }
function pretty(o) { return JSON.stringify(o, null, 2); }
function sortDeep(o) {
  if (Array.isArray(o)) return o.map(sortDeep);
  if (o && typeof o === "object") {
    const r = {};
    Object.keys(o).sort().forEach((k) => { r[k] = sortDeep(o[k]); });
    return r;
  }
  return o;
}
// Order-insensitive equality, so the form rebuilding key order is not "dirty".
function canon(o) { return JSON.stringify(sortDeep(o)); }
function isDirty() { return !!_baseline && canon(_cfg) !== canon(_baseline); }

// Hover help for each settings entity.
const TIPS = {
  interval: "How often the whole fleet is polled, in seconds (default 300).",
  tg_token: "Telegram bot token. Leave blank to keep the stored one. Both token and chat id are required for alerts.",
  tg_chat: "Telegram chat id that receives alerts.",
  host: "Hostname or IP of the server to monitor over SSH.",
  user: "SSH login user. Cockpit install and some checks need root or passwordless sudo.",
  key: "Path to the SSH private key (e.g. ~/.ssh/id_rsa). Blank falls back to the agent / default keys.",
  cockpit: "Override the Cockpit URL. Blank auto-detects https://<host>:9090.",
  svc_name: "Label shown for this check in Net View.",
  svc_type: "Check kind: systemctl (unit active), port (TCP open), docker (container up), interface / wireguard (link UP), http (status code).",
  svc_target: "Target for the chosen type — systemd unit, port number, container name, interface, or URL.",
  socks: "Optional SOCKS5 proxy for the SSH connection, as host:port (e.g. an ssh -D / autossh tunnel). Use when the host is only reachable through a proxy.",
};
function help(key) {
  return `<i class="help fas fa-info-circle" tabindex="0" data-tip="${esc(TIPS[key] || "")}"></i>`;
}

// --- lifecycle ---

async function settingsInit() {
  if (_settingsLoaded) return;
  _settingsLoaded = true;
  await settingsReload();
}

async function settingsReload() {
  const r = await fetch("/api/config");
  _cfg = await r.json();
  _baseline = clone(_cfg);
  _history = [pretty(_cfg)];
  _histIndex = 0;
  try { _cfgPath = (await (await fetch("/api/config/path")).json()).path || ""; }
  catch (e) { _cfgPath = ""; }
  renderToolbar();
  renderActionBars();
  renderActiveView();
}

// Re-render whichever view is active, from the current _cfg.
function renderActiveView() {
  if (_settingsMode === "json") renderJSONFromCfg();
  else renderSettings();
}

// --- history / dirty / action bar ---

function pushHistory() {
  const snap = pretty(_cfg);
  if (_history[_histIndex] === snap) { updateActionBars(); return; }
  _history = _history.slice(0, _histIndex + 1);
  _history.push(snap);
  _histIndex = _history.length - 1;
  updateActionBars();
}
function pushHistoryDebounced() {
  clearTimeout(_histTimer);
  _histTimer = setTimeout(pushHistory, 500);
  updateActionBars(); // reflect dirty immediately
}
function canUndo() { return _histIndex > 0; }
function canRedo() { return _histIndex < _history.length - 1; }

function undo() {
  clearTimeout(_histTimer);
  if (!canUndo()) return;
  _histIndex--;
  _cfg = JSON.parse(_history[_histIndex]);
  renderActiveView();
  updateActionBars();
}
function redo() {
  clearTimeout(_histTimer);
  if (!canRedo()) return;
  _histIndex++;
  _cfg = JSON.parse(_history[_histIndex]);
  renderActiveView();
  updateActionBars();
}
function revertSettings() {
  clearTimeout(_histTimer);
  if (!isDirty()) return;
  _cfg = clone(_baseline);
  pushHistory();
  renderActiveView();
  updateActionBars();
}

function actionBarHTML(pos) {
  return `<div class="settings-actions-bar" id="settings-actions-${pos}">
    <button class="btn btn-ghost btn-sm act-undo" title="Undo" onclick="undo()"><i class="fas fa-rotate-left"></i> Undo</button>
    <button class="btn btn-ghost btn-sm act-redo" title="Redo" onclick="redo()"><i class="fas fa-rotate-right"></i> Redo</button>
    <button class="btn btn-ghost btn-sm act-revert" title="Revert to last saved" onclick="revertSettings()"><i class="fas fa-clock-rotate-left"></i> Revert</button>
    <button class="btn btn-primary btn-sm act-save" title="Save" onclick="saveSettings()"><i class="fas fa-save"></i> Save</button>
    <span class="act-msg" id="settings-msg-${pos}"></span>
  </div>`;
}
function renderActionBars() {
  const top = document.getElementById("settings-actions-top");
  const bot = document.getElementById("settings-actions-bottom");
  if (top) top.innerHTML = actionBarHTML("top");
  if (bot) bot.innerHTML = actionBarHTML("bottom");
  updateActionBars();
}
function updateActionBars() {
  const dirty = isDirty();
  document.querySelectorAll(".settings-actions-bar").forEach((bar) => {
    const set = (cls, disabled) => { const b = bar.querySelector(cls); if (b) b.disabled = disabled; };
    set(".act-undo", !canUndo());
    set(".act-redo", !canRedo());
    set(".act-revert", !dirty);
    set(".act-save", !dirty);
  });
}
function setMsg(text, ok) {
  document.querySelectorAll(".act-msg").forEach((el) => {
    el.textContent = text || "";
    el.style.color = ok ? "var(--success-color)" : "var(--danger-color)";
  });
}

// --- toolbar (view toggle + import/export) ---

function renderToolbar() {
  const tb = document.getElementById("settings-toolbar");
  if (!tb) return;
  tb.innerHTML = `
    <div class="seg">
      <button class="seg-btn ${_settingsMode === 'form' ? 'active' : ''}" onclick="setSettingsMode('form')"><i class="fas fa-table-list"></i> Форма</button>
      <button class="seg-btn ${_settingsMode === 'json' ? 'active' : ''}" onclick="setSettingsMode('json')"><i class="fas fa-code"></i> JSON</button>
    </div>
    <span class="spacer"></span>
    <button class="btn btn-ghost btn-sm" onclick="exportConfig()"><i class="fas fa-upload"></i> Export</button>
    <button class="btn btn-ghost btn-sm" onclick="document.getElementById('settings-import-file').click()"><i class="fas fa-download"></i> Import</button>`;
}

// --- FORM view ---

function renderSettings() {
  const root = document.getElementById("settings-root");
  if (!root || !_cfg) return;
  const servers = _cfg.servers || {};
  const tg = _cfg.telegram || { bot_token: "", chat_id: "" };
  const bTg = (_baseline && _baseline.telegram) || {};
  root.innerHTML = `
    <div class="card settings-card">
      <div class="settings-card-head"><span class="settings-card-title">Global</span></div>
      <div class="field-grid">
        <label class="field"><span>Check interval (s)${help("interval")}</span><input id="cfg-interval" type="number" data-bl="${esc((_baseline && _baseline.check_interval) || 300)}" value="${esc(_cfg.check_interval || 300)}"></label>
        <label class="field"><span>Telegram bot token${help("tg_token")}</span><input id="cfg-tg-token" type="password" data-bl="" placeholder="(unchanged)" value=""></label>
        <label class="field"><span>Telegram chat id${help("tg_chat")}</span><input id="cfg-tg-chat" data-bl="${esc(bTg.chat_id || "")}" value="${esc(tg.chat_id || "")}"></label>
      </div>
    </div>
    <div id="cfg-servers">${Object.keys(servers).map(serverCardHTML).join("")}</div>
    <div class="settings-actions">
      <button class="btn btn-ghost" onclick="addServer()"><i class="fas fa-plus"></i> Add server</button>
    </div>
    <div class="settings-foot">
      <span>config file: <code id="cfg-path">${esc(_cfgPath)}</code></span>
    </div>`;
  if (!_formBound) {
    _formBound = true;
    root.addEventListener("input", onFormInput);
    root.addEventListener("change", onFormInput);
  }
  refreshFormHighlights();
}

function serverCardHTML(name) {
  const s = _cfg.servers[name];
  const b = (_baseline && _baseline.servers && _baseline.servers[name]) || null;
  const socksStr = (o) => o && o.socks ? (typeof o.socks === "object" ? `${o.socks.host || ""}:${o.socks.port || ""}` : o.socks) : "";
  const isNew = !b;
  const bl = b || {};
  const services = (s.services || []).map((svc, i) => serviceRowHTML(name, svc, bl)).join("");
  return `<div class="card settings-card${isNew ? " card-new" : ""}" data-server="${esc(name)}">
    <div class="settings-card-head">
      <input class="srv-name srv-name-input" data-bl="${esc(name)}" value="${esc(name)}" data-orig="${esc(name)}" placeholder="server name">
      <button class="btn btn-danger btn-icon" title="Remove server" onclick="removeServer('${esc(name)}')"><i class="fas fa-trash"></i></button>
    </div>
    <div class="field-grid">
      <label class="field"><span>host${help("host")}</span><input class="srv-host" data-bl="${esc(bl.host || "")}" value="${esc(s.host || "")}" placeholder="host or IP"></label>
      <label class="field"><span>user${help("user")}</span><input class="srv-user" data-bl="${esc(bl.user || "")}" value="${esc(s.user || "")}" placeholder="ssh user"></label>
      <label class="field"><span>key path${help("key")}</span><input class="srv-key" data-bl="${esc(bl.key || "")}" value="${esc(s.key || "")}" placeholder="~/.ssh/id_rsa"></label>
      <label class="field"><span>cockpit url${help("cockpit")}</span><input class="srv-cockpit" data-bl="${esc(bl.cockpit_url || "")}" value="${esc(s.cockpit_url || "")}" placeholder="auto"></label>
      <label class="field"><span>socks proxy${help("socks")}</span><input class="srv-socks" data-bl="${esc(socksStr(bl))}" value="${esc(socksStr(s))}" placeholder="host:port (optional)"></label>
    </div>
    <div class="svc-head">Services</div>
    <div class="svc-cols"><span>name${help("svc_name")}</span><span>type${help("svc_type")}</span><span>target${help("svc_target")}</span><span></span></div>
    <div class="srv-services">${services}</div>
    <div class="srv-actions">
      <button class="btn btn-ghost btn-sm" onclick="addService('${esc(name)}')"><i class="fas fa-plus"></i> service</button>
      <button class="btn btn-ghost btn-sm" onclick="testSsh('${esc(name)}')"><i class="fas fa-plug"></i> Test connection</button>
      <span class="ssh-result"></span>
    </div>
  </div>`;
}

function svcParam(o) {
  return { systemctl: o.unit, port: o.port, docker: o.container,
           interface: o.iface, wireguard: o.iface, http: o.url }[o.type || "systemctl"] || "";
}
function serviceRowHTML(server, svc, baselineSrv) {
  const o = (typeof svc === "string") ? { name: svc, type: "systemctl", unit: svc } : svc;
  // Match the baseline service by name to compare against.
  const bsvcRaw = (baselineSrv.services || []).find((x) => (typeof x === "string" ? x : x.name) === o.name);
  const bsvc = bsvcRaw == null ? null : (typeof bsvcRaw === "string" ? { name: bsvcRaw, type: "systemctl", unit: bsvcRaw } : bsvcRaw);
  const types = ["systemctl", "port", "docker", "interface", "wireguard", "http"];
  const opts = types.map((t) => `<option ${t === (o.type || "systemctl") ? "selected" : ""}>${t}</option>`).join("");
  const param = svcParam(o);
  const blName = bsvc ? (bsvc.name || "") : " new";  // sentinel for new row
  const blType = bsvc ? (bsvc.type || "systemctl") : " new";
  const blParam = bsvc ? svcParam(bsvc) : " new";
  return `<div class="svc-row">
    <input class="svc-name" data-bl="${esc(blName)}" value="${esc(o.name || "")}" placeholder="name">
    <select class="svc-type" data-bl="${esc(blType)}">${opts}</select>
    <input class="svc-param" data-bl="${esc(blParam)}" value="${esc(param)}" placeholder="unit / port / container / iface / url">
    <button class="svc-del" title="Remove" onclick="this.closest('.svc-row').remove(); onFormInput();">×</button>
  </div>`;
}

// Owned by the form; everything else (custom_checks, jump, ssh_port, muted, …)
// is preserved untouched on a Save.
const MANAGED_KEYS = ["host", "user", "key", "cockpit_url", "socks", "services"];

function collect() {
  const cfg = { check_interval: parseInt(document.getElementById("cfg-interval").value) || 300,
                telegram: { bot_token: document.getElementById("cfg-tg-token").value,
                            chat_id: document.getElementById("cfg-tg-chat").value }, servers: {} };
  document.querySelectorAll("#cfg-servers .card").forEach((card) => {
    const name = card.querySelector(".srv-name").value.trim();
    if (!name) return;
    const orig = card.querySelector(".srv-name").dataset.orig;
    const base = (_cfg.servers && _cfg.servers[orig]) ? { ..._cfg.servers[orig] } : {};
    MANAGED_KEYS.forEach((k) => delete base[k]);
    const srv = { ...base,
                  host: card.querySelector(".srv-host").value.trim(),
                  user: card.querySelector(".srv-user").value.trim(), services: [] };
    const key = card.querySelector(".srv-key").value.trim(); if (key) srv.key = key;
    const ck = card.querySelector(".srv-cockpit").value.trim(); if (ck) srv.cockpit_url = ck;
    const sx = card.querySelector(".srv-socks").value.trim(); if (sx) srv.socks = sx;
    card.querySelectorAll(".svc-row").forEach((row) => {
      const sn = row.querySelector(".svc-name").value.trim();
      const t = row.querySelector(".svc-type").value;
      const p = row.querySelector(".svc-param").value.trim();
      if (!sn) return;
      const o = { name: sn, type: t };
      if (t === "systemctl") o.unit = p || sn;
      else if (t === "port") o.port = parseInt(p) || 0;
      else if (t === "docker") o.container = p;
      else if (t === "interface" || t === "wireguard") o.iface = p;
      else if (t === "http") o.url = p;
      srv.services.push(o);
    });
    cfg.servers[name] = srv;
  });
  // Preserve a blanked telegram token: keep whatever baseline had (the form
  // never exposes the real one); the backend also re-applies it on save.
  if (cfg.telegram.bot_token === "" && _baseline && _baseline.telegram && _baseline.telegram.bot_token) {
    cfg.telegram.bot_token = _baseline.telegram.bot_token;
  }
  return cfg;
}

// Form edited → pull DOM into _cfg, snapshot, repaint highlights.
function onFormInput() {
  _cfg = collect();
  pushHistoryDebounced();
  refreshFormHighlights();
}

function refreshFormHighlights() {
  document.querySelectorAll("#settings-root input[data-bl], #settings-root select[data-bl]").forEach((el) => {
    const changed = String(el.value) !== String(el.dataset.bl) && el.id !== "cfg-tg-token";
    el.closest(".field, .svc-row, .settings-card-head")?.classList.toggle("changed", changed);
    el.classList.toggle("changed", changed);
  });
}

async function saveSettings() {
  if (_settingsMode === "json") { await saveJSON(); return; }
  _cfg = collect();
  const r = await fetch("/api/config", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(_cfg) });
  const d = await r.json();
  if (r.ok) {
    _baseline = clone(_cfg);
    pushHistory();
    setMsg("Saved.", true);
    refreshFormHighlights();
    updateActionBars();
  } else {
    setMsg("Errors: " + (d.errors || ["save failed"]).join("; "));
  }
}

async function testSsh(name) {
  await fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(collect()) });
  const card = [...document.querySelectorAll("#cfg-servers .card")].find((c) => c.querySelector(".srv-name").value === name);
  const out = card ? card.querySelector(".ssh-result") : null;
  if (out) { out.className = "ssh-result"; out.textContent = "testing…"; }
  const r = await fetch("/api/config/test-ssh", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ server: name }) });
  const d = await r.json();
  if (out) {
    const cls = d.status === "ok" ? "ok" : (d.status === "weird" ? "warn" : "fail");
    out.className = "ssh-result " + cls;
    out.textContent = d.status === "ok" ? "✓ ok"
      : d.status === "weird" ? "△ connected, unexpected output"
      : "✗ " + (d.error || d.status);
  }
}

function addServer() {
  _cfg = collect();
  _cfg.servers["new-server"] = { host: "", user: "root", services: [] };
  renderSettings();
  pushHistory();
}
function removeServer(name) {
  _cfg = collect();
  delete _cfg.servers[name];
  renderSettings();
  pushHistory();
}
function addService(name) {
  const card = [...document.querySelectorAll("#cfg-servers .card")].find((c) => c.querySelector(".srv-name").value === name);
  if (card) {
    card.querySelector(".srv-services").insertAdjacentHTML("beforeend",
      serviceRowHTML(name, { name: "", type: "systemctl" }, {}));
    onFormInput();
  }
}

// --- JSON view ---

function setSettingsMode(mode) {
  const root = document.getElementById("settings-root");
  const json = document.getElementById("settings-json");
  if (!root || !json) return;
  if (mode === "json") {
    if (_settingsMode === "form") _cfg = collect();
    root.style.display = "none";
    json.style.display = "";
    _settingsMode = "json";
    renderJSONFromCfg();
  } else {
    if (_settingsMode === "json") {
      const ta = document.getElementById("settings-json-input");
      if (ta) {
        try { _cfg = JSON.parse(ta.value); }
        catch (e) { showJSONError("Невалидный JSON — исправьте перед переключением на форму:\n" + e.message); return; }
      }
    }
    json.style.display = "none";
    root.style.display = "";
    _settingsMode = "form";
    renderSettings();
  }
  renderToolbar();
  updateActionBars();
}

// (Re)build the JSON editor from _cfg.
function renderJSONFromCfg() {
  buildJSONEditor(pretty(_cfg));
}

function buildJSONEditor(text) {
  const json = document.getElementById("settings-json");
  json.innerHTML = `
    <div class="json-subbar">
      <button class="btn btn-ghost btn-sm" id="json-tree-toggle" onclick="toggleJSONTree()"><i class="fas fa-sitemap"></i> Дерево</button>
      <span class="json-subbar-hint" id="json-tree-hint"></span>
    </div>
    <div class="json-edit-wrap">
      <pre id="settings-json-hl" aria-hidden="true"></pre>
      <textarea id="settings-json-input" spellcheck="false" autocomplete="off"
                oninput="onJSONInput()"></textarea>
    </div>
    <div id="settings-json-tree" class="json-tree" style="display:none"></div>
    <div class="settings-json-foot"><span id="settings-json-error"></span></div>`;
  document.getElementById("settings-json-input").value = text;
  _jsonTree = false;
  renderJSONHighlight();
}

// Read-only collapsible tree (native <details>) for navigating large configs.
// Editing stays in the raw textarea / form — the tree never mutates the config.
function toggleJSONTree() {
  const wrap = document.querySelector("#settings-json .json-edit-wrap");
  const tree = document.getElementById("settings-json-tree");
  const btn = document.getElementById("json-tree-toggle");
  const hint = document.getElementById("json-tree-hint");
  const ta = document.getElementById("settings-json-input");
  if (!wrap || !tree || !ta) return;
  if (!_jsonTree) {
    let obj;
    try { obj = JSON.parse(ta.value); }
    catch (e) { showJSONError("Невалидный JSON — дерево недоступно: " + e.message); return; }
    tree.innerHTML = jsonTreeNode(null, obj, true);
    wrap.style.display = "none";
    tree.style.display = "";
    _jsonTree = true;
    btn.innerHTML = '<i class="fas fa-code"></i> Текст';
    if (hint) hint.textContent = "только просмотр — для правки вернитесь в «Текст»";
  } else {
    tree.style.display = "none";
    wrap.style.display = "";
    _jsonTree = false;
    btn.innerHTML = '<i class="fas fa-sitemap"></i> Дерево';
    if (hint) hint.textContent = "";
    renderJSONHighlight();
  }
}

function jsonTreeNode(key, val, last) {
  const comma = last ? "" : ",";
  const k = key === null ? "" : `<span class="j-key">"${esc(key)}"</span>: `;
  if (val !== null && typeof val === "object") {
    const isArr = Array.isArray(val);
    const entries = isArr ? val.map((v) => [null, v]) : Object.entries(val);
    const open = isArr ? "[" : "{", close = isArr ? "]" : "}";
    const inner = entries.map((e, i) => jsonTreeNode(e[0], e[1], i === entries.length - 1)).join("");
    return `<details open class="jt"><summary>${k}${open}<span class="jt-count">${entries.length}</span></summary>`
      + `<div class="jt-body">${inner}</div><div class="jt-close">${close}${comma}</div></details>`;
  }
  const cls = typeof val === "number" ? "j-num" : typeof val === "boolean" ? "j-bool" : val === null ? "j-null" : "j-str";
  const disp = typeof val === "string" ? `"${esc(val)}"` : String(val);
  return `<div class="jt-leaf">${k}<span class="${cls}">${disp}</span>${comma}</div>`;
}

// Tokenize one line of JSON into syntax-highlighted HTML.
function highlightJSONLine(src) {
  const e = String(src).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return e.replace(
    /("(?:\\.|[^"\\])*"(?:\s*:)?|\b(?:true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g,
    (m) => {
      let cls = "j-num";
      if (m[0] === '"') cls = /:\s*$/.test(m) ? "j-key" : "j-str";
      else if (m === "true" || m === "false") cls = "j-bool";
      else if (m === "null") cls = "j-null";
      return `<span class="${cls}">${m}</span>`;
    }
  );
}

// LCS line diff: tag each CURRENT line as same/add, and mark a line as
// `delBefore` when one or more baseline lines were removed just before it.
function lineDiff(curLines, baseLines) {
  const n = curLines.length, m = baseLines.length;
  const dp = Array.from({ length: n + 1 }, () => new Int32Array(m + 1));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i][j] = curLines[i] === baseLines[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out = [];
  let i = 0, j = 0;
  let pendingDel = false;
  while (i < n && j < m) {
    if (curLines[i] === baseLines[j]) {
      out.push({ text: curLines[i], type: "same", delBefore: pendingDel }); pendingDel = false; i++; j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ text: curLines[i], type: "add", delBefore: pendingDel }); pendingDel = false; i++;  // added
    } else { pendingDel = true; j++; }  // removed baseline line
  }
  while (i < n) { out.push({ text: curLines[i], type: "add", delBefore: pendingDel }); pendingDel = false; i++; }
  if (j < m) { if (out.length) out[out.length - 1].delAfter = true; else out.push({ text: "", type: "same", delAfter: true }); }
  return out;
}

function renderJSONHighlight() {
  const ta = document.getElementById("settings-json-input");
  const hl = document.getElementById("settings-json-hl");
  if (!ta || !hl) return;
  const cur = ta.value.split("\n");
  // No real change vs the saved config → no diff marks (avoids spurious marks
  // from key-order differences when the form rebuilds the config).
  let diff;
  if (isDirty()) {
    const base = (_baseline ? pretty(_baseline) : ta.value).split("\n");
    diff = lineDiff(cur, base);
  } else {
    diff = cur.map((t) => ({ text: t, type: "same" }));
  }
  hl.innerHTML = diff.map((d) => {
    const cls = "jl" + (d.type === "add" ? " jl-add" : "") + (d.delBefore ? " jl-delb" : "") + (d.delAfter ? " jl-dela" : "");
    return `<div class="${cls}">${highlightJSONLine(d.text) || "&nbsp;"}</div>`;
  }).join("");
  // Grow both layers to content height; they live inside a fixed-height
  // scrolling wrap, so growing never moves the page (no edit "jump"). The wrap
  // scrolls both layers together, so no per-element scroll sync is needed.
  ta.style.height = "auto";
  const h = ta.scrollHeight;
  ta.style.height = h + "px";
  hl.style.height = h + "px";
}

// JSON edited → if valid, adopt into _cfg + snapshot; always repaint diff.
function onJSONInput() {
  const ta = document.getElementById("settings-json-input");
  if (!ta) return;
  let parsed, ok = true;
  try { parsed = JSON.parse(ta.value); } catch (e) { ok = false; }
  if (ok) { _cfg = parsed; pushHistoryDebounced(); showJSONError(""); }
  else { showJSONError("Невалидный JSON — исправьте перед сохранением"); updateActionBars(); }
  renderJSONHighlight();
}

function showJSONError(msg, ok) {
  const err = document.getElementById("settings-json-error");
  if (!err) return;
  err.style.color = ok ? "var(--success-color)" : "var(--danger-color)";
  err.textContent = msg || "";
}

async function saveJSON() {
  const ta = document.getElementById("settings-json-input");
  if (!ta) return;
  let cfg;
  try { cfg = JSON.parse(ta.value); }
  catch (e) { showJSONError("Невалидный JSON: " + e.message); return; }
  const r = await fetch("/api/config", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cfg) });
  const d = await r.json();
  if (r.ok) {
    _cfg = cfg;
    _baseline = clone(cfg);
    pushHistory();
    setMsg("Saved.", true);
    renderJSONHighlight();
    updateActionBars();
  } else {
    showJSONError("Errors: " + (d.errors || [d.error || "save failed"]).join("; "));
  }
}

// --- import / export ---

function exportConfig() {
  let obj;
  if (_settingsMode === "json") {
    const ta = document.getElementById("settings-json-input");
    try { obj = JSON.parse(ta.value); }
    catch (e) { showJSONError("Невалидный JSON, экспорт отменён: " + e.message); return; }
  } else {
    obj = collect();
  }
  // telegram token is blank in this view, so the export carries no secret.
  const safe = clone(obj);
  if (safe.telegram) safe.telegram.bot_token = "";
  downloadJSON(`hostery-config-${todayStamp()}.json`, pretty(safe));
}

function importConfig(event) {
  const file = event.target.files && event.target.files[0];
  event.target.value = "";
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const text = String(reader.result);
    document.getElementById("settings-root").style.display = "none";
    document.getElementById("settings-json").style.display = "";
    _settingsMode = "json";
    buildJSONEditor(text);
    renderToolbar();
    try { _cfg = JSON.parse(text); pushHistory(); }
    catch (e) { showJSONError("Импортированный файл — невалидный JSON: " + e.message); }
    updateActionBars();
  };
  reader.readAsText(file);
}

function downloadJSON(filename, text) {
  const blob = new Blob([text], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  // When embedded in the same-origin AI Cockpit iframe, anchor the download in
  // the top document so the browser doesn't drop a frame-initiated download.
  let doc = document;
  try { if (window.top && window.top.document) doc = window.top.document; } catch (e) { /* cross-origin */ }
  const a = doc.createElement("a");
  a.href = url; a.download = filename;
  doc.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 0);
}

function todayStamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
