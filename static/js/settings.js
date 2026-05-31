let _cfg = null;
let _settingsLoaded = false;

async function settingsInit() {
  if (_settingsLoaded) return;
  _settingsLoaded = true;
  await settingsReload();
}

async function settingsReload() {
  const r = await fetch("/api/config");
  _cfg = await r.json();
  renderSettings();
}

function renderSettings() {
  const root = document.getElementById("settings-root");
  if (!root || !_cfg) return;
  const servers = _cfg.servers || {};
  const tg = _cfg.telegram || { bot_token: "", chat_id: "" };
  root.innerHTML = `
    <div class="card">
      <div class="card-title"><span>Global</span></div>
      <label>Check interval (s) <input id="cfg-interval" type="number" value="${_cfg.check_interval || 300}"></label>
      <label>Telegram bot token <input id="cfg-tg-token" type="password" placeholder="(unchanged)" value=""></label>
      <label>Telegram chat id <input id="cfg-tg-chat" value="${tg.chat_id || ""}"></label>
    </div>
    <div id="cfg-servers">${Object.keys(servers).map(serverCardHTML).join("")}</div>
    <button class="btn btn-ghost" onclick="addServer()"><i class="fas fa-plus"></i> Add server</button>
    <button class="btn btn-primary" onclick="saveSettings()"><i class="fas fa-save"></i> Save</button>
    <div id="cfg-msg"></div>`;
}

function serverCardHTML(name) {
  const s = _cfg.servers[name];
  const services = (s.services || []).map((svc, i) => serviceRowHTML(name, svc, i)).join("");
  return `<div class="card" data-server="${name}">
    <div class="card-title">
      <input class="srv-name" value="${name}" data-orig="${name}">
      <button class="btn btn-danger" onclick="removeServer('${name}')"><i class="fas fa-trash"></i></button>
    </div>
    <label>host <input class="srv-host" value="${s.host || ""}"></label>
    <label>user <input class="srv-user" value="${s.user || ""}"></label>
    <label>key path <input class="srv-key" value="${s.key || ""}"></label>
    <label>cockpit_url <input class="srv-cockpit" value="${s.cockpit_url || ""}"></label>
    <div class="srv-services">${services}</div>
    <button class="btn btn-ghost" onclick="addService('${name}')">+ service</button>
    <button class="btn btn-ghost" onclick="testSsh('${name}')">Test connection</button>
    <span class="ssh-result"></span>
  </div>`;
}

function serviceRowHTML(server, svc, i) {
  const o = (typeof svc === "string") ? { name: svc, type: "systemctl", unit: svc } : svc;
  const types = ["systemctl", "port", "docker", "interface", "wireguard", "http"];
  const opts = types.map(t => `<option ${t === (o.type||"systemctl") ? "selected" : ""}>${t}</option>`).join("");
  const param = { systemctl: o.unit, port: o.port, docker: o.container,
                  interface: o.iface, wireguard: o.iface, http: o.url }[o.type || "systemctl"] || "";
  return `<div class="svc-row">
    <input class="svc-name" value="${o.name || ""}" placeholder="name">
    <select class="svc-type">${opts}</select>
    <input class="svc-param" value="${param}" placeholder="unit/port/container/iface/url">
    <button onclick="this.parentElement.remove()">×</button>
  </div>`;
}

function collect() {
  const cfg = { check_interval: parseInt(document.getElementById("cfg-interval").value) || 300,
                telegram: { bot_token: document.getElementById("cfg-tg-token").value,
                            chat_id: document.getElementById("cfg-tg-chat").value }, servers: {} };
  document.querySelectorAll("#cfg-servers .card").forEach(card => {
    const name = card.querySelector(".srv-name").value.trim();
    if (!name) return;
    const srv = { host: card.querySelector(".srv-host").value.trim(),
                  user: card.querySelector(".srv-user").value.trim(), services: [] };
    const key = card.querySelector(".srv-key").value.trim(); if (key) srv.key = key;
    const ck = card.querySelector(".srv-cockpit").value.trim(); if (ck) srv.cockpit_url = ck;
    card.querySelectorAll(".svc-row").forEach(row => {
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
  return cfg;
}

async function saveSettings() {
  const r = await fetch("/api/config", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(collect()) });
  const d = await r.json();
  document.getElementById("cfg-msg").textContent = r.ok ? "Saved." : ("Errors: " + (d.errors || []).join("; "));
}

async function testSsh(name) {
  await fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(collect()) });
  const card = [...document.querySelectorAll('#cfg-servers .card')].find(c => c.querySelector('.srv-name').value === name);
  const out = card ? card.querySelector(".ssh-result") : null;
  if (out) out.textContent = "testing…";
  const r = await fetch("/api/config/test-ssh", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ server: name }) });
  const d = await r.json();
  if (out) out.textContent = d.status === "ok" ? "✓ ok" : ("✗ " + (d.error || d.status));
}

function addServer() {
  _cfg.servers["new-server"] = { host: "", user: "root", services: [] };
  renderSettings();
}
function removeServer(name) { delete _cfg.servers[name]; renderSettings(); }
function addService(name) {
  const card = [...document.querySelectorAll('#cfg-servers .card')].find(c => c.querySelector('.srv-name').value === name);
  if (card) card.querySelector(".srv-services").insertAdjacentHTML("beforeend",
    serviceRowHTML(name, { name: "", type: "systemctl" }, 0));
}
