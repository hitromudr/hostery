const API = "/api";
let currentStats = {};
let statsInterval, timeInterval;

// --- Navigation & View Logic ---

function showView(viewName, skipHash) {
  document.querySelectorAll(".nav-item").forEach((el) => el.classList.remove("active"));
  const nav = document.getElementById("nav-" + viewName);
  if (nav) nav.classList.add("active");
  document.querySelectorAll(".view-section").forEach((el) => el.classList.remove("active"));
  if (!skipHash) window.location.hash = viewName;
  if (typeof stopMonPolling === "function") stopMonPolling();

  if (viewName === "monitoring") {
    document.getElementById("view-monitoring").classList.add("active");
    if (typeof startMonPolling === "function") startMonPolling();
  } else if (viewName === "settings") {
    document.getElementById("view-settings").classList.add("active");
    if (typeof settingsInit === "function") settingsInit();
  } else {
    document.getElementById("view-dashboard").classList.add("active");
    fetchData();
  }
}

function updateTime() {
  const now = new Date();
  const el = document.getElementById("clock");
  if (el) el.innerText = now.toLocaleTimeString();
}

// --- Data Fetching ---

async function fetchData(btnElement = null) {
  // If not on dashboard, don't poll heavily
  if (!document.getElementById("view-dashboard").classList.contains("active")) {
    return;
  }

  let originalText = "";
  if (btnElement) {
    originalText = btnElement.innerHTML;
    btnElement.disabled = true;
    btnElement.innerHTML = '<i class="fas fa-sync fa-spin"></i> Refreshing...';
  }

  try {
    await Promise.all([fetchStats(), fetchPowerTimeline()]);
  } catch (e) {
    console.error("Fetch cycle failed:", e);
  } finally {
    if (btnElement) {
      btnElement.disabled = false;
      btnElement.innerHTML = originalText;
    }
  }
}

async function fetchStats() {
  try {
    const res = await fetch(`${API}/stats`);
    if (!res.ok) throw new Error("Stats fetch failed");
    const d = await res.json();
    currentStats = d;
    updateStatsUI(d);
  } catch (e) {
    console.warn("Stats error:", e);
  }
}

// --- UI Updates ---

function updateStatsUI(d) {
  // CPU Temp
  const tempEl = document.getElementById("cpu-temp");
  if (tempEl) {
    tempEl.innerText = d.cpu_temp.toFixed(1);
    tempEl.style.color =
      d.cpu_temp > 70 ? "var(--danger-color)" : "var(--text-color)";
  }

  // CPU Frequency
  const freqEl = document.getElementById("cpu-freq");
  if (freqEl && d.cpu_freq_mhz) {
    freqEl.textContent = `${d.cpu_freq_mhz} MHz (${d.cpu_freq_min}-${d.cpu_freq_max})`;
  }

  // Fan Speed
  const fanEl = document.getElementById("fan-speed");
  const fanIcon = document.getElementById("fan-icon");
  if (fanEl) fanEl.innerText = d.fan_speed + "%";
  if (fanIcon) {
    if (d.fan_speed > 0) {
      fanIcon.classList.add("spinning");
      // Faster spin for higher speed
      fanIcon.style.animationDuration = d.fan_speed > 50 ? "0.5s" : "1.5s";
    } else {
      fanIcon.classList.remove("spinning");
    }
  }

  // RAM
  const ramPctEl = document.getElementById("ram-pct");
  const ramDetailEl = document.getElementById("ram-detail");
  const ramBar = document.getElementById("ram-bar");

  if (ramPctEl) ramPctEl.innerText = d.ram_percent;
  if (ramDetailEl)
    ramDetailEl.innerText = `${d.ram_used_gb} / ${d.ram_total_gb} GB`;
  if (ramBar) ramBar.style.width = `${d.ram_percent}%`;

  // Cores
  const coresContainer = document.getElementById("cores-grid");
  if (coresContainer && d.cpu_per_core) {
    coresContainer.innerHTML = d.cpu_per_core
      .map(
        (usage, i) => `
            <div class="core-item">
                <div class="core-label">
                    <span>Core ${i}</span>
                    <span>${usage}%</span>
                </div>
                <div class="progress-container" style="height: 4px; background: rgba(255,255,255,0.1)">
                    <div class="progress-bar" style="width: ${usage}%; background: ${usage > 80 ? "var(--warning-color)" : "var(--accent-color)"}"></div>
                </div>
            </div>
        `,
      )
      .join("");
  }

  // Disk Space
  const diskPctEl = document.getElementById("disk-pct");
  const diskDetailEl = document.getElementById("disk-detail");
  const diskBar = document.getElementById("disk-bar");
  if (diskPctEl && d.disk_percent !== undefined) {
    diskPctEl.innerText = d.disk_percent;
    diskPctEl.style.color =
      d.disk_percent > 90 ? "var(--danger-color)" : "var(--text-color)";
  }
  if (diskDetailEl)
    diskDetailEl.innerText = `${d.disk_used_gb} / ${d.disk_total_gb} GB`;
  if (diskBar) {
    diskBar.style.width = `${d.disk_percent}%`;
    diskBar.style.backgroundColor =
      d.disk_percent > 90 ? "var(--danger-color)" : "var(--accent-color)";
  }

  // Throttle / Power
  if (d.throttle) {
    const section = document.getElementById("power-section");
    if (section) section.style.display = "block";

    const icon = document.getElementById("power-icon");
    const voltEl = document.getElementById("power-voltage");
    const statusEl = document.getElementById("power-status");

    if (voltEl) voltEl.textContent = d.throttle.voltage || "--";

    const wattsEl = document.getElementById("power-watts");
    if (wattsEl && d.throttle.power_watts !== undefined) {
      wattsEl.textContent = d.throttle.power_watts.toFixed(1) + "W";
    }

    const rangeEl = document.getElementById("power-voltage-range");
    const s24 = d.throttle.stats_24h;
    if (rangeEl && s24) {
      rangeEl.textContent = `min ${s24.min.toFixed(4)}V / max ${s24.max.toFixed(4)}V (24h)`;
    }

    const active = d.throttle.active || [];
    const uvEvents24h = s24 ? s24.undervoltage_events_24h : 0;

    if (active.length > 0) {
      icon.style.color = "var(--danger-color)";
      icon.classList.add("fa-beat");
      statusEl.innerHTML = active.map(a =>
        `<span style="color:var(--danger-color);"><i class="fas fa-exclamation-triangle"></i> ${a.replace(/_/g, " ")}</span>`
      ).join(" &nbsp; ");
    } else if (uvEvents24h > 0) {
      icon.style.color = "var(--warning-color)";
      icon.classList.remove("fa-beat");
      statusEl.innerHTML = `<span style="color:var(--warning-color);"><i class="fas fa-history"></i> ${uvEvents24h} undervoltage event(s) in last 24h</span>`;
    } else if (d.throttle.ever_under_voltage || d.throttle.ever_throttled) {
      icon.style.color = "var(--warning-color)";
      icon.classList.remove("fa-beat");
      let flags = [];
      if (d.throttle.ever_under_voltage) flags.push("undervoltage");
      if (d.throttle.ever_throttled) flags.push("throttling");
      statusEl.innerHTML = `<span style="color:var(--warning-color);"><i class="fas fa-history"></i> ${flags.join(", ")} detected at boot</span>`;
    } else {
      icon.style.color = "var(--success-color)";
      icon.classList.remove("fa-beat");
      statusEl.innerHTML = '<span style="color:var(--success-color);"><i class="fas fa-check-circle"></i> stable</span>';
    }
  }

  // I/O
  if (d.io) {
    const readSpeed = d.io.disk_read_mb_s;
    document.getElementById("disk-read").innerText =
      readSpeed.toFixed(1) + " MB/s";

    // High Disk Activity Indicator
    const headerTitle = document.querySelector("#view-dashboard h2");
    if (headerTitle) {
      if (readSpeed > 50) {
        if (!headerTitle.innerText.includes("High Load")) {
          headerTitle.innerHTML =
            'System Overview <span style="font-size: 0.6em; background: var(--warning-color); color: #1a1b26; padding: 2px 8px; border-radius: 12px; vertical-align: middle; margin-left: 8px;"><i class="fas fa-hdd fa-spin"></i> High Load</span>';
        }
      } else {
        if (headerTitle.innerText.includes("High Load")) {
          headerTitle.innerHTML = "System Overview";
        }
      }
    }

    document.getElementById("disk-write").innerText =
      d.io.disk_write_mb_s.toFixed(1) + " MB/s";
    document.getElementById("net-in").innerText =
      d.io.net_rx_mb_s.toFixed(1) + " MB/s";
    document.getElementById("net-out").innerText =
      d.io.net_tx_mb_s.toFixed(1) + " MB/s";
  }
}

// --- Power Timeline ---

let _lastPowerFetch = 0;

async function fetchPowerTimeline() {
  // Only fetch every 30s, not every 2s
  const now = Date.now();
  if (now - _lastPowerFetch < 30000) return;
  _lastPowerFetch = now;

  try {
    const res = await fetch(`${API}/power`);
    if (!res.ok) return;
    const data = await res.json();
    renderPowerTimeline(data);
  } catch (e) {
    console.warn("Power timeline error:", e);
  }
}

function renderPowerTimeline(data) {
  const container = document.getElementById("power-timeline");
  if (!container) return;

  container.innerHTML = data.timeline.map((block, i) => {
    const totalEvents = block.details ? block.details.length : 0;
    let cls = "empty";
    if (block.events > 0) {
      cls = block.events >= 3 ? "bad" : "warn";
    } else if (totalEvents > 0) {
      cls = "warn";
    } else if (data.timeline.slice(0, i + 1).some(b => b.events > 0 || (b.details && b.details.length > 0))) {
      cls = "ok";
    }

    // Convert UTC timestamp to local hour
    const localHour = block.timestamp ? new Date(block.timestamp).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : block.hour;
    const tooltip = `${localHour} — ${totalEvents > 0 ? totalEvents + " event(s)" : "ok"}`;
    return `<div class="pwr-block ${cls}" onclick="showPowerBlock(${i})" data-hour="${localHour}">
      <div class="pwr-tooltip">${tooltip}</div>
    </div>`;
  }).join("");

  window._powerData = data;
  renderPowerEvents(data.recent);
}

function pwrEventMeta(e) {
  // voltage field may contain "0.9457V 2.1W" combined format
  const v = e.voltage || "";
  const parts = v.split(" ");
  const volt = parts[0] || "";
  const watts = parts[1] || "";
  return [watts, e.temp ? `${e.temp.toFixed(1)}°C` : "", volt].filter(Boolean).join("  ");
}

function renderPowerEvents(events) {
  const container = document.getElementById("power-events");
  if (!container) return;

  if (!events || events.length === 0) {
    container.innerHTML = '<div style="color:var(--text-muted);font-size:0.8rem;">No power events recorded</div>';
    return;
  }

  const eventLabels = {
    under_voltage: ["Undervoltage", "var(--danger-color)"],
    under_voltage_resolved: ["Voltage normalised", "var(--success-color)"],
    under_voltage_at_boot: ["Undervoltage (boot)", "var(--warning-color)"],
    throttled: ["Throttled", "var(--danger-color)"],
    throttled_resolved: ["Throttle released", "var(--success-color)"],
    throttled_at_boot: ["Throttled (boot)", "var(--warning-color)"],
    arm_freq_capped: ["Freq capped", "var(--warning-color)"],
    arm_freq_capped_resolved: ["Freq restored", "var(--success-color)"],
    arm_freq_capped_at_boot: ["Freq capped (boot)", "var(--warning-color)"],
    soft_temp_limit: ["Temp limit", "var(--warning-color)"],
    soft_temp_limit_resolved: ["Temp normalised", "var(--success-color)"],
    soft_temp_limit_at_boot: ["Temp limit (boot)", "var(--warning-color)"],
  };

  container.innerHTML = events.slice().reverse().map(e => {
    const ts = new Date(e.timestamp);
    const time = ts.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    const [label, color] = eventLabels[e.event_type] || [e.event_type, "var(--text-color)"];
    const meta = pwrEventMeta(e);
    return `<div class="pwr-event-item">
      <span class="pwr-event-time">${time}</span>
      <span class="pwr-event-type" style="color:${color}">${label}</span>
      <span class="pwr-event-meta">${meta}</span>
    </div>`;
  }).join("");
}

function showPowerBlock(idx) {
  if (!window._powerData) return;
  const block = window._powerData.timeline[idx];
  if (!block) return;

  const container = document.getElementById("power-events");
  if (!container) return;

  // Toggle: click same block again to close
  if (container.dataset.blockIdx === String(idx) && container.dataset.mode === "block") {
    container.dataset.mode = "";
    renderPowerEvents(window._powerData.recent);
    return;
  }

  container.dataset.blockIdx = String(idx);
  container.dataset.mode = "block";

  if (!block.details || block.details.length === 0) {
    container.innerHTML = `<div style="background:var(--card-bg);border:1px solid var(--border-color);border-radius:8px;padding:12px;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
        <strong>${block.timestamp ? new Date(block.timestamp).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"}) : block.hour}</strong>
        <span style="color:var(--success-color);font-size:0.85rem;">No events</span>
        <button onclick="document.getElementById('power-events').dataset.mode='';renderPowerEvents(window._powerData.recent)" style="background:none;border:none;color:var(--text-muted);cursor:pointer;"><i class="fas fa-times"></i></button>
      </div>
    </div>`;
    return;
  }

  const eventLabels = {
    under_voltage: ["Undervoltage", "var(--danger-color)"],
    under_voltage_resolved: ["Voltage normalised", "var(--success-color)"],
    under_voltage_at_boot: ["Undervoltage (boot)", "var(--warning-color)"],
    throttled: ["Throttled", "var(--danger-color)"],
    throttled_resolved: ["Throttle released", "var(--success-color)"],
    throttled_at_boot: ["Throttled (boot)", "var(--warning-color)"],
    arm_freq_capped: ["Freq capped", "var(--warning-color)"],
    arm_freq_capped_resolved: ["Freq restored", "var(--success-color)"],
    arm_freq_capped_at_boot: ["Freq capped (boot)", "var(--warning-color)"],
    soft_temp_limit: ["Temp limit", "var(--warning-color)"],
    soft_temp_limit_resolved: ["Temp normalised", "var(--success-color)"],
    soft_temp_limit_at_boot: ["Temp limit (boot)", "var(--warning-color)"],
  };

  const eventsHtml = block.details.map(e => {
    const ts = new Date(e.timestamp);
    const time = ts.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    const [label, color] = eventLabels[e.event_type] || [e.event_type, "var(--text-color)"];
    const meta = pwrEventMeta(e);
    return `<div class="pwr-event-item">
      <span class="pwr-event-time">${time}</span>
      <span class="pwr-event-type" style="color:${color}">${label}</span>
      <span class="pwr-event-meta">${meta}</span>
    </div>`;
  }).join("");

  container.innerHTML = `<div style="background:var(--card-bg);border:1px solid var(--border-color);border-radius:8px;padding:12px;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
      <strong>${block.timestamp ? new Date(block.timestamp).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"}) : block.hour}</strong>
      <span style="color:var(--danger-color);font-size:0.85rem;">${block.details.length} event(s)</span>
      <button onclick="document.getElementById('power-events').dataset.mode='';renderPowerEvents(window._powerData.recent)" style="background:none;border:none;color:var(--text-muted);cursor:pointer;"><i class="fas fa-times"></i></button>
    </div>
    ${eventsHtml}
  </div>`;
}

// --- Compact / Collapsible ---

function toggleDashCompact() {
  const view = document.getElementById("view-dashboard");
  if (!view) return;
  const isCompact = view.classList.toggle("dash-compact");
  localStorage.setItem("dash-compact", isCompact ? "1" : "");
  const btn = document.getElementById("dash-compact-btn");
  if (btn) btn.innerHTML = isCompact
    ? '<i class="fas fa-expand-alt"></i> Full'
    : '<i class="fas fa-compress-alt"></i> Compact';
}

function togglePowerExpand() {
  const el = document.getElementById("power-expand");
  const chevron = document.getElementById("power-chevron");
  if (!el) return;
  const show = el.style.display === "none";
  el.style.display = show ? "" : "none";
  if (chevron) chevron.className = show ? "fas fa-chevron-up" : "fas fa-chevron-down";
  chevron.style.fontSize = "0.6rem";
  chevron.style.color = "var(--text-muted)";
  chevron.style.marginLeft = "4px";
  localStorage.setItem("power-expand", show ? "1" : "");
}

function restoreDashSections() {
  if (localStorage.getItem("dash-compact") === "1") {
    const view = document.getElementById("view-dashboard");
    if (view) view.classList.add("dash-compact");
    const btn = document.getElementById("dash-compact-btn");
    if (btn) btn.innerHTML = '<i class="fas fa-expand-alt"></i> Full';
  }
  if (localStorage.getItem("power-expand") === "1") {
    const el = document.getElementById("power-expand");
    const chevron = document.getElementById("power-chevron");
    if (el) el.style.display = "";
    if (chevron) chevron.className = "fas fa-chevron-up";
  }
}

// --- Resizable Panels ---

function setupResizable(handleId, panelId, storageKey, minW, maxW) {
  const handle = document.getElementById(handleId);
  const panel = document.getElementById(panelId);
  if (!handle || !panel) return;

  const isMainSidebar = panelId === "main-sidebar";
  const COLLAPSE_THRESHOLD = 140;

  // Restore from localStorage. For the main sidebar we still set width here
  // so that a later expand has something to restore to; the collapsed-state
  // restore happens in DOMContentLoaded and clears the inline width.
  const saved = localStorage.getItem(storageKey);
  if (saved) panel.style.width = saved + "px";

  let startX, startW;
  handle.addEventListener("mousedown", (e) => {
    e.preventDefault();
    startX = e.clientX;
    startW = panel.offsetWidth;
    handle.classList.add("dragging");
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    const setCollapsedClass = (collapsed) => {
      panel.classList.toggle("collapsed", collapsed);
      const icon = document.getElementById("sidebar-toggle-icon");
      if (icon) {
        icon.classList.toggle("fa-chevron-left", !collapsed);
        icon.classList.toggle("fa-chevron-right", collapsed);
      }
    };

    const onMove = (e) => {
      const newW = Math.min(maxW, Math.max(minW, startW + e.clientX - startX));
      panel.style.width = newW + "px";
      if (isMainSidebar) setCollapsedClass(newW < COLLAPSE_THRESHOLD);
    };
    const onUp = () => {
      handle.classList.remove("dragging");
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      if (isMainSidebar && panel.classList.contains("collapsed")) {
        panel.style.width = "";
        localStorage.setItem("sidebar-collapsed", "1");
      } else {
        if (isMainSidebar) localStorage.setItem("sidebar-collapsed", "");
        localStorage.setItem(storageKey, panel.offsetWidth);
      }
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

// --- Initialization ---

window.addEventListener("DOMContentLoaded", () => {
  // Start Clock
  timeInterval = setInterval(updateTime, 1000);
  updateTime();

  // Start Polling (2 seconds)
  statsInterval = setInterval(() => fetchData(), 2000);

  // Restore view from hash
  const hash = window.location.hash.replace("#", "");
  if (hash && hash !== "dashboard") {
    showView(hash, true);
  }

  // Restore collapsed sections
  restoreDashSections();

  // Resizable panels
  setupResizable("sidebar-resize-handle", "main-sidebar", "sidebar-width", 60, 400);

  // Restore sidebar collapsed state
  if (localStorage.getItem("sidebar-collapsed") === "1") {
    applySidebarCollapsed(true);
  }

  // Initial Fetch
  fetchData();
});

function applySidebarCollapsed(collapsed) {
  const sb = document.getElementById("main-sidebar");
  const icon = document.getElementById("sidebar-toggle-icon");
  if (!sb) return;
  sb.classList.toggle("collapsed", collapsed);
  if (collapsed) {
    sb.style.width = "";
  } else {
    const saved = localStorage.getItem("sidebar-width");
    sb.style.width = saved ? saved + "px" : "";
  }
  if (icon) {
    icon.classList.toggle("fa-chevron-left", !collapsed);
    icon.classList.toggle("fa-chevron-right", collapsed);
    icon.title = collapsed ? "Expand sidebar" : "Collapse sidebar";
  }
}

function toggleSidebar() {
  const sb = document.getElementById("main-sidebar");
  if (!sb) return;
  const next = !sb.classList.contains("collapsed");
  applySidebarCollapsed(next);
  localStorage.setItem("sidebar-collapsed", next ? "1" : "");
}
