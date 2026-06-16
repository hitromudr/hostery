// Monitoring tab logic
let monInterval = null;
let monCountdownInterval = null;
let monStatusData = null;
let monMutedData = {};
const _cockpitSeen = new Set();
const _cockpitCache = {}; // server -> resolved slot HTML; survives card re-render

// --- Drag-to-reorder state + order persistence ---
const MON_ORDER_KEY = "hostery_server_order";
let monDraggedCard = null;
let monIsDragging = false;

function getSavedServerOrder() {
  try {
    const arr = JSON.parse(localStorage.getItem(MON_ORDER_KEY) || "[]");
    return Array.isArray(arr) ? arr : [];
  } catch (e) { return []; }
}

function applyServerOrder(names) {
  const saved = getSavedServerOrder();
  if (!saved.length) return names;
  const known = saved.filter((n) => names.includes(n));
  const rest = names.filter((n) => !known.includes(n));
  return known.concat(rest);
}

function saveServerOrder() {
  const order = Array.from(
    document.querySelectorAll("#mon-servers .server-card[data-server]"),
  ).map((el) => el.dataset.server);
  localStorage.setItem(MON_ORDER_KEY, JSON.stringify(order));
}

function monArmDrag(e) {
  const card = e.currentTarget.closest(".server-card");
  if (!card) return;
  card.draggable = true;
  document.addEventListener("mouseup", () => { card.draggable = false; }, { once: true });
}
function monDragStart(e) {
  monDraggedCard = e.currentTarget;
  monIsDragging = true;
  monDraggedCard.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move";
}
function monDragOver(e) {
  if (!monDraggedCard || e.currentTarget === monDraggedCard) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  e.currentTarget.classList.add("drag-over");
}
function monDragLeave(e) { e.currentTarget.classList.remove("drag-over"); }
function monDrop(e) {
  e.preventDefault();
  const target = e.currentTarget;
  target.classList.remove("drag-over");
  if (!monDraggedCard || target === monDraggedCard) return;
  const rect = target.getBoundingClientRect();
  const before = e.clientX < rect.left + rect.width / 2;
  target.parentNode.insertBefore(monDraggedCard, before ? target : target.nextSibling);
  saveServerOrder();
}
function monDragEnd() {
  if (monDraggedCard) {
    monDraggedCard.classList.remove("dragging");
    monDraggedCard.draggable = false;
    monDraggedCard = null;
  }
  monIsDragging = false;
  document.querySelectorAll("#mon-servers .drag-over").forEach((el) => el.classList.remove("drag-over"));
}

function formatDurationShort(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return s + "s";
  const m = Math.floor(s / 60);
  if (m < 60) return m + "m";
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return rem ? h + "h " + rem + "m" : h + "h";
}

// Map bucket bad-seconds to an HSL color. Green is reserved for "ok"
// (no incident, no gradient). Once any incident exists:
//   0–5  min  → green-yellow
//   5–10 min  → yellow
//   10–30 min → yellow → vivid red
//   30–60 min → vivid red darkens and warms toward brown
function severityColor(sev, badSeconds) {
  const bs = (typeof badSeconds === "number") ? badSeconds : sev * 3600;
  // On the light theme the cards are white, so darken the cells for contrast.
  const isLight = document.documentElement.classList.contains("theme-light");
  const hsl = (h, s, l) =>
    `hsl(${h}, ${s}%, ${isLight ? Math.max(20, l - 16) : l}%)`;
  // Saturation tuned to sit near the base "ok" green (~50% sat)
  // so incident bars don't visually outweigh the timeline.
  if (bs < 600) {
    // 0–10 min: anchored at bs=0 to CSS --success-color (#9ece6a ≈
    // hsl(89, 51%, 61%)) so dots/blocks with a tiny bit of badness are
    // visually continuous with the bs=0 "ok" appearance.
    const t = bs / 600;
    const hue = Math.round(89 - t * 29);  // 89 → 60
    const sat = Math.round(51 + t * 4);   // 51 → 55
    const light = Math.round(61 - t * 6); // 61 → 55
    return hsl(hue, sat, light);
  }
  if (bs < 1800) {
    // 10–30 min: yellow → red
    const t = (bs - 600) / 1200;
    const hue = Math.round(60 - t * 60);
    const sat = Math.round(55 + t * 10); // 55% → 65%
    const light = Math.round(55 - t * 5); // 55% → 50%
    return hsl(hue, sat, light);
  }
  // 30–60 min: red darkens to brown
  const bsClamp = Math.min(3600, bs);
  const t = (bsClamp - 1800) / 1800;
  const hue = Math.round(t * 25); // 0 → 25
  const sat = Math.round(65 - t * 25); // 65% → 40%
  const light = Math.round(50 - t * 30); // 50% → 20%
  return `hsl(${hue}, ${sat}%, ${light}%)`;
}

function startMonPolling() {
  fetchMonAll();
  if (monInterval) clearInterval(monInterval);
  monInterval = setInterval(fetchMonAll, 30000);
  if (monCountdownInterval) clearInterval(monCountdownInterval);
  monCountdownInterval = setInterval(() => {
    if (monStatusData) updateNextCheck(monStatusData);
  }, 1000);
  // Restore incidents collapsed state
  if (localStorage.getItem("mon-incidents-collapsed") === "1") {
    const inc = document.getElementById("mon-incidents");
    if (inc) inc.style.display = "none";
    const chevron = document.querySelector("#view-monitoring h2 i");
    if (chevron) chevron.className = "fas fa-chevron-down";
  }
  // Restore compact button state
  const btn = document.getElementById("mon-compact-btn");
  if (btn && localStorage.getItem("mon-compact") === "1") {
    btn.innerHTML = '<i class="fas fa-expand-alt"></i> Full';
  }
}

function stopMonPolling() {
  if (monInterval) {
    clearInterval(monInterval);
    monInterval = null;
  }
  if (monCountdownInterval) {
    clearInterval(monCountdownInterval);
    monCountdownInterval = null;
  }
}

async function fetchMonAll() {
  await Promise.all([fetchMonStatus(), fetchMonIncidents(), fetchMonMuted()]);
}

async function fetchMonStatus() {
  if (monIsDragging) return;  // defer status refresh while user is dragging
  try {
    // Snapshot expanded panels so we can restore them after re-render
    const expandedSvc = [];
    document.querySelectorAll(".svc-detail-panel").forEach((p) => {
      if (p.style.display !== "none" && p.dataset.service) {
        const m = /^svc-detail-(.+)$/.exec(p.id);
        if (m) expandedSvc.push({ server: m[1], service: p.dataset.service });
      }
    });
    const expandedTimeline = [];
    document.querySelectorAll('[id^="timeline-detail-"]').forEach((p) => {
      if (p.style.display !== "none" && p.dataset.idx != null && p.dataset.idx !== "") {
        const m = /^timeline-detail-(.+)$/.exec(p.id);
        if (m) expandedTimeline.push({ server: m[1], idx: parseInt(p.dataset.idx, 10) });
      }
    });

    const res = await fetch("/api/monitoring/status");
    if (!res.ok) throw new Error("Status fetch failed");
    monStatusData = await res.json();
    renderMonServers(monStatusData);

    // Restore previously expanded panels
    for (const { server, service } of expandedSvc) {
      const p = document.getElementById("svc-detail-" + server);
      if (p) p.dataset.service = ""; // bypass toggle's same-service close branch
      toggleSvcDetail(server, service);
    }
    for (const { server, idx } of expandedTimeline) {
      const p = document.getElementById("timeline-detail-" + server);
      if (p) p.dataset.idx = ""; // bypass same-idx close branch
      showTimelineIncident(server, idx);
    }

    updateNextCheck(monStatusData);
  } catch (e) {
    console.warn("Monitoring status error:", e);
  }
}

async function fetchMonIncidents() {
  try {
    const res = await fetch("/api/monitoring/incidents?limit=20");
    if (!res.ok) throw new Error("Incidents fetch failed");
    const data = await res.json();
    renderMonIncidents(data.incidents);
  } catch (e) {
    console.warn("Monitoring incidents error:", e);
  }
}

async function fetchMonMuted() {
  try {
    const res = await fetch("/api/monitoring/muted");
    if (!res.ok) return;
    const data = await res.json();
    monMutedData = data.muted || {};
    renderMutedPanel();
  } catch (e) {
    console.warn("Muted fetch error:", e);
  }
}

// --- Server Cards ---

function renderMonServers(data) {
  const container = document.getElementById("mon-servers");
  if (!container) return;

  if (monIsDragging) return;  // never re-render mid-drag (root cause of dup bug)
  const servers = data.servers || {};
  const names = applyServerOrder(Object.keys(servers));

  if (!names.length) {
    container.innerHTML =
      '<div class="card" style="grid-column: 1/-1;">No monitoring data yet. Waiting for first check cycle...</div>';
    return;
  }

  const buildCard = (name) => {
      const s = servers[name];
      const services = s.services || {};
      const svcNames = Object.keys(services);
      const indicator = s.status || "unknown";

      const checks = s.uptime_checks || 0;
      let uptimeColor = "var(--success-color)";
      if (checks >= 10) {
        if (s.uptime_30d < 99) uptimeColor = "var(--warning-color)";
        if (s.uptime_30d < 95) uptimeColor = "var(--danger-color)";
      }
      const timeline = s.uptime_timeline || [];

      const lastCheck = s.last_check
        ? new Date(s.last_check).toLocaleTimeString()
        : "--:--";

      const failCount = svcNames.filter(
        (svc) => services[svc]?.status === "fail",
      ).length;
      const warnCount = svcNames.filter(
        (svc) => services[svc]?.status === "warning",
      ).length;
      // Collect problem services for badge click
      const problemSvcs = svcNames.filter(
        (svc) => services[svc]?.status === "fail" || services[svc]?.status === "warning",
      );
      let badges = "";
      if (failCount > 0) {
        badges += ` <span class="badge-clickable badge-fail" onclick="event.stopPropagation(); showProblems('${name}')">${failCount} down</span>`;
      }
      if (warnCount > 0) {
        badges += ` <span class="badge-clickable badge-warn" onclick="event.stopPropagation(); showProblems('${name}')">${warnCount} warn</span>`;
      }

      const compact = localStorage.getItem("mon-compact") === "1";

      return `
        <div class="card server-card ${compact ? "compact" : ""}" data-server="${name}"
             ondragstart="monDragStart(event)" ondragover="monDragOver(event)"
             ondragleave="monDragLeave(event)" ondrop="monDrop(event)" ondragend="monDragEnd(event)">
          <div class="server-header" onmousedown="monArmDrag(event)">
            <div>
              <div class="server-name">${name}${badges}</div>
              <div class="server-ip">${s.host || ""}</div>
            </div>
            <div style="display: flex; align-items: center; gap: 8px;">
              <div id="cockpit-${name}" class="cockpit-ctl">${_cockpitCache[name] || ""}</div>
              <span style="font-size: 0.85rem; font-weight: bold; color: ${uptimeColor};">${s.uptime_30d}%</span>
              <span class="mon-help" tabindex="0"><i class="fa-solid fa-circle-info"></i><span class="mon-help-pop">
                <b>${s.uptime_30d}% — аптайм за 30 дней</b><br>
                Доля успешных проверок <b>по всем сервисам</b> ноды (ok из ok+fail; <span class="mh-dim">warnings не учитываются</span>). Сейчас ${s.uptime_ok || 0}/${checks}.<br>
                <span class="mh-dim">Полоса ниже — последние 24 часа по всем сервисам; точка справа — состояние прямо сейчас.</span>
              </span></span>
              <div class="server-indicator ${indicator}"></div>
            </div>
          </div>
          <div class="uptime-bar" id="bar-${name}">
            ${timeline
              .map((e, i) => {
                const sev = typeof e.severity === "number" ? e.severity : (e.status === "fail" ? 1 : e.status === "warning" ? 0.5 : 0);
                const isEmpty = e.status === "empty";
                const isIncident = !isEmpty && sev > 0;
                const cls = isEmpty ? "empty" : isIncident ? "incident" : "ok";
                let style = "";
                if (isIncident) {
                  style = ` style="background: ${severityColor(sev, e.bad_seconds)};"`;
                }
                const clickAttr = isIncident ? `onclick="event.stopPropagation(); showTimelineIncident('${name}', ${i})"` : "";
                const hh = new Date(e.hour).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
                let tip = hh;
                if (isIncident) {
                  tip += " \u2014 " + e.incidents + " incident" + (e.incidents > 1 ? "s" : "");
                  if (typeof e.bad_seconds === "number" && e.bad_seconds > 0) {
                    tip += " (" + formatDurationShort(e.bad_seconds) + ")";
                  }
                } else if (cls === "ok") tip += " \u2014 OK";
                else tip += " \u2014 no data";
                return `<div class="uptime-block ${cls}"${style} ${clickAttr} title="${tip}"></div>`;
              })
              .join("")}
          </div>
          <div class="uptime-bar-labels">
            <span>${timeline.length ? new Date(timeline[0].hour).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : ""}</span>
            <span>${timeline.length > 12 ? new Date(timeline[12].hour).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : ""}</span>
            <span>${timeline.length > 1 ? new Date(timeline[timeline.length - 1].hour).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : ""}</span>
          </div>
          <div id="timeline-detail-${name}" style="display: none;"></div>
          <div class="server-stats" ${compact ? 'style="display:none;"' : ""}>
            <span title="SSH response time">${s.response_time_ms || 0}ms</span>
            <span title="Last check">${lastCheck}</span>
          </div>
          <div class="svc-grid">
            ${svcNames
              .filter((svc) => {
                if (!compact) return true;
                const st = (services[svc] || {}).status || "unknown";
                return st === "fail" || st === "warning";
              })
              .map((svc) => {
                const si = services[svc] || {};
                const st = si.status || "unknown";
                const ms = si.response_time_ms
                  ? `${si.response_time_ms}ms`
                  : "";
                const labels = {ok: "Operational", fail: "Failed", inactive: "Inactive", warning: "Activating", unknown: "Unknown"};
                const tipParts = [labels[st] || st];
                if (si.error) tipParts.push(si.error);
                if (ms) tipParts.push(ms);
                if (typeof si.bad_seconds_24h === "number" && si.bad_seconds_24h > 0) {
                  const m = Math.round(si.bad_seconds_24h / 60);
                  tipParts.push(`24h down: ${m}m`);
                }
                const tip = tipParts.join(" \u2014 ").replace(/"/g, "&quot;");
                // Color from same severityColor used for timeline cells. Feed
                // it the per-hour average so the scale matches: a service
                // that lost 1h total over 24h reads like a single fully-red
                // bucket spread thin.
                let dotStyle = "";
                if (typeof si.severity_24h === "number" && si.severity_24h > 0) {
                  const avgBadPerHour = (si.bad_seconds_24h || 0) / 24;
                  dotStyle = ` style="background:${severityColor(si.severity_24h, avgBadPerHour)};"`;
                }
                return `
                  <div class="svc-item svc-${st}" title="${tip}" onclick="event.stopPropagation(); toggleSvcDetail('${name}', '${svc}')">
                    <span class="svc-dot ${st}"${dotStyle}></span>
                    <span class="svc-name">${svc}</span>
                    <span class="svc-ms">${ms}</span>
                  </div>`;
              })
              .join("")}
          </div>
          <div id="svc-detail-${name}" class="svc-detail-panel" style="display: none;"></div>
        </div>
      `;
  };

  // Reconcile keyed by data-server: update-in-place, add/remove, reorder.
  // Never wipe the whole list, so a card being dragged is never orphaned
  // (that orphaning was the root cause of the duplicate-card bug).
  const seen = new Set();
  for (const name of names) {
    seen.add(name);
    const tmp = document.createElement("div");
    tmp.innerHTML = buildCard(name);
    const fresh = tmp.firstElementChild;
    const card = container.querySelector(`.server-card[data-server="${CSS.escape(name)}"]`);
    if (!card) {
      container.appendChild(fresh);
    } else {
      card.className = fresh.className;
      card.innerHTML = fresh.innerHTML;
    }
  }
  container.querySelectorAll(".server-card[data-server]").forEach((el) => {
    if (!seen.has(el.dataset.server)) el.remove();
  });
  names.forEach((name) => {
    const el = container.querySelector(`.server-card[data-server="${CSS.escape(name)}"]`);
    if (el) container.appendChild(el);
  });

  // After the card list DOM is in place, populate each server's Cockpit slot.
  names.forEach((name) => {
    if (!_cockpitSeen.has(name)) { _cockpitSeen.add(name); loadCockpit(name); }
  });
}

function toggleMonCompact() {
  const current = localStorage.getItem("mon-compact") === "1";
  localStorage.setItem("mon-compact", current ? "" : "1");
  if (monStatusData) renderMonServers(monStatusData);
  // Update button
  const btn = document.getElementById("mon-compact-btn");
  if (btn) {
    btn.innerHTML = current
      ? '<i class="fas fa-compress-alt"></i> Compact'
      : '<i class="fas fa-expand-alt"></i> Full';
  }
}

// --- Service Detail Panel ---

async function toggleSvcDetail(server, service) {
  const panel = document.getElementById("svc-detail-" + server);
  if (!panel) return;

  // If already showing this service, close
  if (panel.style.display !== "none" && panel.dataset.service === service) {
    panel.style.display = "none";
    panel.dataset.service = "";
    return;
  }

  panel.dataset.service = service;
  panel.style.display = "block";
  panel.innerHTML = '<div style="padding: 8px; color: var(--text-muted);"><i class="fas fa-spinner fa-spin"></i> Loading...</div>';

  // Get current status
  const s = monStatusData?.servers?.[server];
  const svcInfo = s?.services?.[service] || {};

  // Fetch history. Backend returns both raw checks (for the error log) and
  // a ready 24h hourly timeline computed by the same code path as the
  // server-wide uptime bar — frontend just renders.
  let history = [];
  let svcTimeline = [];
  try {
    const res = await fetch(
      `/api/monitoring/history?server=${server}&service=${service}&hours=24`,
    );
    if (res.ok) {
      const data = await res.json();
      history = data.checks || [];
      svcTimeline = data.timeline || [];
    }
  } catch (e) {
    console.warn("History fetch error:", e);
  }

  // Status header
  const statusLabels = {
    ok: '<span style="color: var(--success-color);">Operational</span>',
    inactive: '<span style="color: var(--text-muted);">Inactive</span>',
    fail: '<span style="color: var(--danger-color);">Failed</span>',
    warning: '<span style="color: var(--warning-color);">Activating</span>',
  };
  const statusLabel = statusLabels[svcInfo.status] || '<span style="color: var(--text-muted);">Unknown</span>';

  // Error block
  const errorBlock =
    svcInfo.error
      ? `<div class="svc-detail-error">${svcInfo.error}</div>`
      : "";

  // Action buttons
  let actions = "";
  if (svcInfo.status === "fail") {
    actions = `
      <div class="svc-detail-actions">
        <button class="btn-micro" onclick="resolveIncident('${server}', '${service}')">
          <i class="fas fa-check"></i> Resolve (suppress 1h)
        </button>
        <button class="btn-micro btn-micro-mute" onclick="muteService('${server}', '${service}')">
          <i class="fas fa-bell-slash"></i> Mute
        </button>
      </div>`;
  }

  // 24h per-service timeline: same shape as the server-wide bar above,
  // bucketed by the backend with real interval-merging (no estimation).
  const timeline = svcTimeline.length
    ? `<div class="svc-timeline">
      <div style="font-size: 0.7rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 6px;">Last 24h (hourly)</div>
      <div class="uptime-bar svc-uptime-bar">
        ${svcTimeline
          .map((b) => {
            const sev = typeof b.severity === "number" ? b.severity : 0;
            const isEmpty = b.status === "empty";
            const isIncident = !isEmpty && sev > 0;
            const cls = isEmpty ? "empty" : isIncident ? "incident" : "ok";
            const style = isIncident ? ` style="background: ${severityColor(sev, b.bad_seconds)};"` : "";
            const hh = new Date(b.hour).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
            let tip = hh;
            if (isEmpty) tip += " — no checks";
            else if (isIncident) tip += ` — ${Math.round((b.bad_seconds || 0) / 60)}m down`;
            else tip += " — ok";
            return `<div class="uptime-block ${cls}"${style} title="${tip.replace(/"/g, "&quot;")}"></div>`;
          })
          .join("")}
      </div>
    </div>`
    : "";

  // Error log (only show entries with errors)
  const errors = history.filter((c) => c.error_msg);
  const errorLog = errors.length
    ? `<div class="svc-error-log">
        <div style="font-size: 0.7rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 6px;">Error log</div>
        ${errors
          .slice(0, 5)
          .map(
            (c) => `
          <div class="error-log-entry">
            <span class="error-log-time">${new Date(c.timestamp).toLocaleTimeString()}</span>
            <span class="error-log-msg">${c.error_msg}</span>
          </div>
        `,
          )
          .join("")}
      </div>`
    : "";

  panel.innerHTML = `
    <div class="svc-detail-content">
      <div class="svc-detail-header">
        <strong>${service}</strong>
        ${statusLabel}
        <span style="margin-left: auto; color: var(--text-muted); font-size: 0.8rem;">${svcInfo.response_time_ms || 0}ms</span>
        <button onclick="document.getElementById('svc-detail-${server}').style.display='none'" style="background:none; border:none; color: var(--text-muted); cursor:pointer; margin-left: 8px;">
          <i class="fas fa-times"></i>
        </button>
      </div>
      ${errorBlock}
      ${actions}
      ${timeline}
      ${errorLog}
    </div>
  `;
}

// --- Badge Click: Show Problem Services ---

function showProblems(server) {
  const panel = document.getElementById("svc-detail-" + server);
  if (!panel || !monStatusData) return;

  const s = monStatusData.servers[server];
  if (!s) return;

  const services = s.services || {};
  const problems = Object.entries(services).filter(
    ([, v]) => v.status === "fail" || v.status === "warning",
  );

  if (!problems.length) {
    panel.style.display = "none";
    return;
  }

  // If already open, close
  if (panel.style.display !== "none" && panel.dataset.service === "__problems") {
    panel.style.display = "none";
    return;
  }
  panel.dataset.service = "__problems";

  const labels = {fail: "Failed", warning: "Activating"};

  panel.innerHTML = `
    <div class="svc-detail-content">
      <div class="svc-detail-header">
        <strong>Problem services</strong>
        <span style="color: var(--danger-color);">${problems.length}</span>
        <button onclick="document.getElementById('svc-detail-${server}').style.display='none'" style="background:none; border:none; color: var(--text-muted); cursor:pointer; margin-left: auto;">
          <i class="fas fa-times"></i>
        </button>
      </div>
      ${problems
        .map(
          ([svc, info]) => `
        <div class="fail-detail" style="margin-top: 6px;">
          <div class="fail-detail-header">
            <span class="svc-dot ${info.status}"></span>
            <strong>${svc}</strong>
            <span style="color: var(--text-muted); font-size: 0.8rem; margin-left: auto;">${labels[info.status] || info.status}</span>
          </div>
          ${info.error ? `<div class="fail-detail-error">${info.error}</div>` : ""}
          <div class="fail-detail-actions">
            <button class="btn-micro" onclick="event.stopPropagation(); toggleSvcDetail('${server}', '${svc}')">
              <i class="fas fa-search"></i> Details
            </button>
            ${info.status === "fail" ? `
            <button class="btn-micro" onclick="resolveIncident('${server}', '${svc}')">
              <i class="fas fa-check"></i> Resolve
            </button>
            <button class="btn-micro btn-micro-mute" onclick="muteService('${server}', '${svc}')">
              <i class="fas fa-bell-slash"></i> Mute
            </button>` : ""}
          </div>
        </div>`,
        )
        .join("")}
    </div>
  `;
  panel.style.display = "block";
}

// --- Timeline Incident Detail ---

function showTimelineIncident(server, idx) {
  const panel = document.getElementById("timeline-detail-" + server);
  if (!panel || !monStatusData) return;

  const s = monStatusData.servers[server];
  if (!s || !s.uptime_timeline) return;

  const entry = s.uptime_timeline[idx];
  if (!entry || (entry.status !== "fail" && entry.status !== "warning")) return;

  if (panel.style.display !== "none" && panel.dataset.idx === String(idx)) {
    panel.style.display = "none";
    return;
  }
  panel.dataset.idx = String(idx);

  const failed = entry.failed || [];
  const incCount = entry.incidents || 0;
  const svcCount = new Set(failed.map((f) => f.svc)).size;
  const headerColor = entry.status === "fail" ? "var(--danger-color)" : "var(--warning-color)";

  panel.innerHTML = `
    <div class="svc-detail-content">
      <div class="svc-detail-header">
        <strong>${new Date(entry.hour).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})}</strong>
        <span style="color: ${headerColor};">${incCount} incident${incCount > 1 ? "s" : ""}, ${svcCount} service${svcCount > 1 ? "s" : ""}</span>
        <button onclick="document.getElementById('timeline-detail-${server}').style.display='none'" style="background:none; border:none; color: var(--text-muted); cursor:pointer; margin-left: auto;">
          <i class="fas fa-times"></i>
        </button>
      </div>
      ${failed
        .map(
          (f) => {
            const fmt = (iso) => iso ? new Date(iso).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"}) : "";
            const startTime = fmt(f.started_at || f.time);
            const endTime = fmt(f.resolved_at);
            const recoveredLine = f.resolved_at
              ? `<div style="display: flex; align-items: center; gap: 6px; margin-top: 4px; font-size: 0.8rem;">
                   <span style="color: var(--success-color);">&#x25CF;</span>
                   <span>recovered${f.downtime ? ` <span style="color: var(--text-muted);">(${f.downtime})</span>` : ""}</span>
                   <span style="color: var(--text-muted); margin-left: auto;">${endTime}</span>
                 </div>`
              : `<div style="margin-top: 4px; font-size: 0.8rem; color: var(--warning-color);">
                   <i class="fas fa-circle-notch fa-spin"></i> still ongoing
                 </div>`;
            const sev = f.severity || "fail";
            return `
        <div class="fail-detail" style="margin-top: 6px;">
          <div class="fail-detail-header">
            <span class="svc-dot ${sev}"></span>
            <strong>${f.svc}</strong>
            ${startTime ? `<span style="color: var(--text-muted); font-size: 0.8rem; margin-left: auto;">${startTime}</span>` : ""}
          </div>
          <div class="fail-detail-error">${f.err || "Unknown error"}</div>
          ${recoveredLine}
        </div>`;
          },
        )
        .join("")}
    </div>
  `;
  panel.style.display = "block";
}

// --- Next Check Timer ---

function updateNextCheck(data) {
  const el = document.getElementById("mon-next-check");
  if (!el) return;

  if (data.next_check) {
    const next = new Date(data.next_check);
    const now = new Date();
    const diffSec = Math.max(0, Math.floor((next - now) / 1000));
    const min = Math.floor(diffSec / 60);
    const sec = diffSec % 60;
    el.textContent = `Next check in ${min}:${String(sec).padStart(2, "0")}`;
  } else if (data.last_check) {
    el.textContent = `Last: ${new Date(data.last_check).toLocaleTimeString()}`;
  } else {
    el.textContent = "Waiting for first check...";
  }
}

// --- Incidents ---

function renderMonIncidents(incidents) {
  const container = document.getElementById("mon-incidents");
  if (!container) return;

  if (!incidents || !incidents.length) {
    container.innerHTML =
      '<div style="color: var(--success-color); padding: 12px;"><i class="fas fa-check-circle"></i> No incidents</div>';
    return;
  }

  const hasResolved = incidents.some((g) => g.resolved && g.resolved.length);
  const clearAllBtn = hasResolved
    ? `<div style="text-align: right; margin-bottom: 10px;">
         <button class="btn-micro" onclick="dismissAllResolved()" style="font-size: 0.8rem;">
           <i class="fas fa-broom"></i> Clear resolved
         </button>
       </div>`
    : "";

  container.innerHTML =
    clearAllBtn +
    incidents
      .map((group) => {
        const srv = group.server;
        const hasActive = group.active && group.active.length;

        // Active incidents
        const activeHtml = (group.active || [])
          .map((a) => {
            const time = new Date(a.started_at).toLocaleString();
            const errorText = a.error
              ? `<div class="incident-error">${a.error}</div>`
              : "";
            const duration = a.duration
              ? ` <span style="color: var(--warning-color); font-size: 0.8rem;">(${a.duration})</span>`
              : "";
            return `
              <div class="incident-item incident-ongoing">
                <span class="incident-icon">\u{1F534}</span>
                <div style="flex: 1;">
                  <div><b>${srv}</b> &mdash; ${a.service}${duration}</div>
                  ${errorText}
                </div>
                <button class="btn-micro" onclick="resolveIncident('${srv}', '${a.service}')" style="flex-shrink: 0;">
                  <i class="fas fa-check"></i> Resolve
                </button>
                <span style="flex-shrink: 0; color: var(--text-muted); font-size: 0.8rem; margin-left: 8px;">${time}</span>
              </div>`;
          })
          .join("");

        // Resolved as sub-items under the server group
        const resolvedHtml = (group.resolved || [])
          .map((r) => {
            const startTime = new Date(r.started_at).toLocaleString();
            const endTime = new Date(r.resolved_at).toLocaleString();
            const errorLine = r.error
              ? `<div style="color: var(--danger-color); font-family: monospace; font-size: 0.8rem; padding-left: 18px; margin: 1px 0 3px;">${r.error}</div>`
              : "";
            return `
            <div class="incident-history-item" style="flex-direction: column; align-items: flex-start; gap: 1px;">
              <div style="display: flex; align-items: center; gap: 6px; width: 100%;">
                <span style="color: var(--danger-color);">&#x25CF;</span>
                <span>${r.service} failed &mdash; ${startTime}</span>
              </div>
              ${errorLine}
              <div style="display: flex; align-items: center; gap: 6px; width: 100%;">
                <span style="color: var(--success-color);">&#x25CF;</span>
                <span>recovered (${r.downtime}) &mdash; ${endTime}</span>
                <button class="btn-micro btn-micro-dismiss" onclick="dismissIncident(${r.id})" style="padding: 1px 4px; font-size: 0.7rem; margin-left: auto;" title="Remove">
                  <i class="fas fa-times"></i>
                </button>
              </div>
            </div>`;
          })
          .join("");

        const resolvedBlock = resolvedHtml
          ? `<div class="incident-history">${resolvedHtml}</div>`
          : "";

        const resolvedIds = (group.resolved || []).map(r => r.id);
        const resolvedCount = resolvedIds.length;
        const collapseId = `inc-collapse-${srv}`;
        const isCollapsed = localStorage.getItem(`inc-${srv}`) === "1";

        const groupActions = resolvedCount > 0 ? `
          <div style="display:flex;gap:6px;margin-left:auto;flex-shrink:0;">
            <button class="btn-micro" onclick="toggleIncidentGroup('${srv}','${collapseId}')" title="Collapse/Expand" style="padding:2px 6px;">
              <i class="fas fa-chevron-${isCollapsed ? 'down' : 'up'}" id="chevron-${collapseId}"></i>
            </button>
            <button class="btn-micro btn-micro-dismiss" onclick="event.stopPropagation();dismissServerIncidents([${resolvedIds.join(',')}])" title="Dismiss all for ${srv}" style="padding:2px 6px;">
              <i class="fas fa-trash-alt"></i>
            </button>
          </div>` : "";

        if (!hasActive && resolvedHtml) {
          return `
            <div style="margin-bottom: 8px;">
              <div class="incident-item incident-resolved" style="padding-bottom: 4px; cursor:pointer;" onclick="toggleIncidentGroup('${srv}','${collapseId}')">
                <span class="incident-icon">\u{1F7E2}</span>
                <div style="flex: 1;"><b>${srv}</b> &mdash; all recovered <span style="color:var(--text-muted);font-size:0.8rem;">(${resolvedCount})</span></div>
                ${groupActions}
              </div>
              <div id="${collapseId}" ${isCollapsed ? 'style="display:none;"' : ""}>${resolvedBlock}</div>
            </div>`;
        }

        return `<div style="margin-bottom: 8px;">${activeHtml}<div id="${collapseId}" ${isCollapsed ? 'style="display:none;"' : ""}>${resolvedBlock}</div></div>`;
      })
      .join("");
}

function toggleIncidentGroup(srv, collapseId) {
  const el = document.getElementById(collapseId);
  const chevron = document.getElementById("chevron-" + collapseId);
  if (!el) return;
  const show = el.style.display === "none";
  el.style.display = show ? "" : "none";
  if (chevron) {
    chevron.classList.toggle("fa-chevron-up", show);
    chevron.classList.toggle("fa-chevron-down", !show);
  }
  localStorage.setItem(`inc-${srv}`, show ? "" : "1");
}

async function dismissServerIncidents(ids) {
  for (const id of ids) {
    await fetch("/api/monitoring/dismiss", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: id }),
    });
  }
  fetchMonAll();
}

// --- Muted Panel ---

function renderMutedPanel() {
  const container = document.getElementById("mon-muted");
  if (!container) return;

  const entries = [];
  for (const [server, services] of Object.entries(monMutedData)) {
    for (const svc of services) {
      entries.push({ server, service: svc });
    }
  }

  if (!entries.length) {
    container.style.display = "none";
    return;
  }

  container.style.display = "block";
  container.innerHTML =
    '<h3 style="margin-bottom: 12px; font-size: 1rem; color: var(--text-muted);"><i class="fas fa-bell-slash"></i> Muted Services</h3>' +
    '<div style="display: flex; flex-wrap: wrap; gap: 8px;">' +
    entries
      .map(
        (e) => `
        <div class="muted-tag">
          <span>${e.server}/${e.service}</span>
          <button onclick="unmuteService('${e.server}', '${e.service}')" title="Unmute">
            <i class="fas fa-undo"></i>
          </button>
        </div>
      `,
      )
      .join("") +
    "</div>";
}

// --- Actions ---

async function resolveIncident(server, service) {
  try {
    const res = await fetch("/api/monitoring/resolve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ server, service }),
    });
    if (!res.ok) throw new Error("Resolve failed");
    await fetchMonAll();
  } catch (e) {
    console.error("Resolve error:", e);
  }
}

async function muteService(server, service) {
  try {
    const res = await fetch("/api/monitoring/mute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ server, service }),
    });
    if (!res.ok) throw new Error("Mute failed");
    await fetchMonAll();
  } catch (e) {
    console.error("Mute error:", e);
  }
}

async function unmuteService(server, service) {
  try {
    const res = await fetch("/api/monitoring/unmute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ server, service }),
    });
    if (!res.ok) throw new Error("Unmute failed");
    await fetchMonAll();
  } catch (e) {
    console.error("Unmute error:", e);
  }
}

async function dismissIncident(id) {
  try {
    await fetch("/api/monitoring/dismiss", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    await fetchMonIncidents();
  } catch (e) {
    console.error("Dismiss error:", e);
  }
}

async function dismissAllResolved() {
  try {
    await fetch("/api/monitoring/dismiss", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    await fetchMonIncidents();
  } catch (e) {
    console.error("Dismiss all error:", e);
  }
}

async function monCheckNow(btn) {
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<i class="fas fa-sync fa-spin"></i> Checking...';

  try {
    await fetch("/api/monitoring/check", { method: "POST" });
    await new Promise((r) => setTimeout(r, 3000));
    await fetchMonAll();
  } catch (e) {
    console.error("Check now failed:", e);
  } finally {
    btn.disabled = false;
    btn.innerHTML = orig;
  }
}

// Write the cockpit slot into the cache AND the live DOM. buildCard renders the
// cached HTML on every re-render, so the button no longer vanishes on the next
// status poll (the bug was: card.innerHTML rebuild wiped an async-only slot).
function _setCockpit(server, html) {
  _cockpitCache[server] = html;
  const el = document.getElementById("cockpit-" + server);
  if (el) el.innerHTML = html;
}

async function loadCockpit(server) {
  let html = "";
  try {
    const r = await fetch(`/api/server/${encodeURIComponent(server)}/cockpit-status`);
    const d = await r.json();
    if (d.status === "running") {
      html = `<a class="btn btn-ghost" href="${d.url}" target="_blank" rel="noopener"><i class="fas fa-up-right-from-square"></i> System console</a>`;
    } else if (d.status === "installed-but-stopped") {
      html = `<span class="cockpit-chip warn">Cockpit installed (stopped)</span>`;
    } else if (d.status === "absent") {
      html = `<button class="btn btn-primary" onclick="installCockpit('${server}')"><i class="fas fa-download"></i> Install Cockpit</button>`;
    } else {
      html = `<span class="cockpit-chip">Cockpit: ${d.status}</span>`;
    }
  } catch (e) { html = ""; }
  _setCockpit(server, html);
}

async function installCockpit(server) {
  if (!confirm(`Install Red Hat Cockpit on "${server}"? This installs packages over SSH `
    + `(needs root / passwordless sudo) and enables cockpit.socket on port 9090.`)) return;
  _setCockpit(server, `<span class="cockpit-chip">installing…</span>`);
  await fetch(`/api/server/${encodeURIComponent(server)}/install-cockpit`, { method: "POST" });
  const poll = setInterval(async () => {
    const r = await fetch(`/api/server/${encodeURIComponent(server)}/install-cockpit/log`);
    const d = await r.json();
    const last = (d.log || []).join("\n");
    if (last.includes("DONE")) { clearInterval(poll); loadCockpit(server); }
    else if (last.includes("ERROR")) { clearInterval(poll);
      _setCockpit(server, `<span class="cockpit-chip bad" title="${last.replace(/"/g, "'")}">install failed</span>`); }
  }, 2000);
}
