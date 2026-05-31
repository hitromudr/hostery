let _cfg = null;
let _settingsLoaded = false;

function esc(s) {
  return String(s == null ? "" : s)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

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
};

function help(key) {
  return `<i class="help fas fa-info-circle" tabindex="0" data-tip="${esc(TIPS[key] || "")}"></i>`;
}

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
    <div class="card settings-card">
      <div class="settings-card-head"><span class="settings-card-title">Global</span></div>
      <div class="field-grid">
        <label class="field"><span>Check interval (s)${help("interval")}</span><input id="cfg-interval" type="number" value="${esc(_cfg.check_interval || 300)}"></label>
        <label class="field"><span>Telegram bot token${help("tg_token")}</span><input id="cfg-tg-token" type="password" placeholder="(unchanged)" value=""></label>
        <label class="field"><span>Telegram chat id${help("tg_chat")}</span><input id="cfg-tg-chat" value="${esc(tg.chat_id || "")}"></label>
      </div>
    </div>
    <div id="cfg-servers">${Object.keys(servers).map(serverCardHTML).join("")}</div>
    <div class="settings-actions">
      <button class="btn btn-ghost" onclick="addServer()"><i class="fas fa-plus"></i> Add server</button>
      <button class="btn btn-primary" onclick="saveSettings()"><i class="fas fa-save"></i> Save</button>
    </div>
    <div id="cfg-msg"></div>`;
}

function serverCardHTML(name) {
  const s = _cfg.servers[name];
  const services = (s.services || []).map((svc, i) => serviceRowHTML(name, svc, i)).join("");
  return `<div class="card settings-card" data-server="${esc(name)}">
    <div class="settings-card-head">
      <input class="srv-name srv-name-input" value="${esc(name)}" data-orig="${esc(name)}" placeholder="server name">
      <button class="btn btn-danger btn-icon" title="Remove server" onclick="removeServer('${esc(name)}')"><i class="fas fa-trash"></i></button>
    </div>
    <div class="field-grid">
      <label class="field"><span>host${help("host")}</span><input class="srv-host" value="${esc(s.host || "")}" placeholder="host or IP"></label>
      <label class="field"><span>user${help("user")}</span><input class="srv-user" value="${esc(s.user || "")}" placeholder="ssh user"></label>
      <label class="field"><span>key path${help("key")}</span><input class="srv-key" value="${esc(s.key || "")}" placeholder="~/.ssh/id_rsa"></label>
      <label class="field"><span>cockpit url${help("cockpit")}</span><input class="srv-cockpit" value="${esc(s.cockpit_url || "")}" placeholder="auto"></label>
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

function serviceRowHTML(server, svc, i) {
  const o = (typeof svc === "string") ? { name: svc, type: "systemctl", unit: svc } : svc;
  const types = ["systemctl", "port", "docker", "interface", "wireguard", "http"];
  const opts = types.map(t => `<option ${t === (o.type||"systemctl") ? "selected" : ""}>${t}</option>`).join("");
  const param = { systemctl: o.unit, port: o.port, docker: o.container,
                  interface: o.iface, wireguard: o.iface, http: o.url }[o.type || "systemctl"] || "";
  return `<div class="svc-row">
    <input class="svc-name" value="${esc(o.name || "")}" placeholder="name">
    <select class="svc-type">${opts}</select>
    <input class="svc-param" value="${esc(param)}" placeholder="unit / port / container / iface / url">
    <button class="svc-del" title="Remove" onclick="this.closest('.svc-row').remove()">×</button>
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
  if (out) { out.className = "ssh-result"; out.textContent = "testing…"; }
  const r = await fetch("/api/config/test-ssh", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ server: name }) });
  const d = await r.json();
  if (out) {
    // ok -> green, connected-but-odd-output -> yellow, anything else -> red
    const cls = d.status === "ok" ? "ok" : (d.status === "weird" ? "warn" : "fail");
    out.className = "ssh-result " + cls;
    out.textContent = d.status === "ok" ? "✓ ok"
      : d.status === "weird" ? "△ connected, unexpected output"
      : "✗ " + (d.error || d.status);
  }
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
