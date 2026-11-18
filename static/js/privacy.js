// hostery — "hide IPs" screenshot mode.
//
// A toggle (eye button in the Net View header) puts `hide-ips` on <html>.
// Independently of the toggle, a MutationObserver wraps every IPv4 address and
// every configured server host found in text nodes into <span class="ip-redact">,
// so flipping the mode is pure CSS — no re-render, and panels opened later
// (service details, error log, incidents) are covered too. While hidden,
// `title` attributes carrying an address are rewritten to "•••" (the original
// is kept in data-ip-title and restored on show). Settings inputs are blurred
// by CSS. The choice persists in localStorage ("hostery_hide_ips"); the
// pre-paint <head> script applies it before the first frame.

(function () {
  var KEY = "hostery_hide_ips";
  var MASK = "•••";
  var IPV4 = "\\b(?:25[0-5]|2[0-4]\\d|1?\\d?\\d)(?:\\.(?:25[0-5]|2[0-4]\\d|1?\\d?\\d)){3}\\b";
  var hosts = new Set(); // configured hostnames that are not plain IPv4
  var re = null;

  function escRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

  function buildRe() {
    var parts = [IPV4];
    Array.from(hosts)
      .sort(function (a, b) { return b.length - a.length; })
      .forEach(function (h) { parts.push("(?<![\\w.-])" + escRe(h) + "(?![\\w.-])"); });
    re = new RegExp("(" + parts.join("|") + ")", "g");
  }
  buildRe();

  function isHidden() {
    return document.documentElement.classList.contains("hide-ips");
  }

  function redactText(str) {
    return String(str).replace(re, MASK);
  }

  function wrapTextNode(node) {
    var p = node.parentNode;
    if (!p || p.nodeType !== 1) return;
    if (p.classList && p.classList.contains("ip-redact")) return;
    var tag = p.nodeName;
    if (tag === "SCRIPT" || tag === "STYLE" || tag === "TEXTAREA") return;
    var text = node.nodeValue;
    re.lastIndex = 0;
    if (!text || !re.test(text)) return;
    re.lastIndex = 0;
    var frag = document.createDocumentFragment();
    var last = 0, m;
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      var span = document.createElement("span");
      span.className = "ip-redact";
      span.textContent = m[0];
      frag.appendChild(span);
      last = m.index + m[0].length;
    }
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    p.replaceChild(frag, node);
  }

  function maskTitle(el) {
    if (!isHidden() || !el.getAttribute) return;
    var t = el.getAttribute("title");
    if (!t) return;
    re.lastIndex = 0;
    if (!re.test(t)) return;
    el.setAttribute("data-ip-title", t);
    el.setAttribute("title", redactText(t));
  }

  function scan(root) {
    if (!root) return;
    if (root.nodeType === 3) { wrapTextNode(root); return; }
    if (root.nodeType !== 1) return;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    var nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(wrapTextNode);
    maskTitle(root);
    root.querySelectorAll("[title]").forEach(maskTitle);
  }

  function restoreTitles() {
    document.querySelectorAll("[data-ip-title]").forEach(function (el) {
      el.setAttribute("title", el.getAttribute("data-ip-title"));
      el.removeAttribute("data-ip-title");
    });
  }

  function syncButton() {
    var hidden = isHidden();
    document.querySelectorAll(".ip-toggle-btn").forEach(function (b) {
      var i = b.querySelector("i");
      if (i) i.className = hidden ? "fas fa-eye-slash" : "fas fa-eye";
      b.classList.toggle("active", hidden);
      b.title = hidden ? "IP-адреса скрыты — показать" : "Скрыть IP-адреса (для скриншота)";
    });
  }

  function setHidden(hidden) {
    document.documentElement.classList.toggle("hide-ips", hidden);
    try { localStorage.setItem(KEY, hidden ? "1" : ""); } catch (e) {}
    if (hidden) scan(document.body); else restoreTitles();
    syncButton();
  }

  // Server hosts from /api/monitoring/status — hostnames are not IPv4-shaped,
  // so they need explicit registration to be redacted.
  function registerHosts(list) {
    var changed = false;
    (list || []).forEach(function (h) {
      h = String(h || "").trim();
      if (!h || hosts.has(h)) return;
      re.lastIndex = 0;
      if (new RegExp("^" + IPV4 + "$").test(h)) return;
      hosts.add(h);
      changed = true;
    });
    if (changed) { buildRe(); scan(document.body); }
  }

  window.hosteryPrivacy = {
    toggle: function () { setHidden(!isHidden()); },
    setHidden: setHidden,
    isHidden: isHidden,
    registerHosts: registerHosts,
    redactText: redactText,
    scan: function () { scan(document.body); },
  };
  window.toggleHideIps = window.hosteryPrivacy.toggle;

  function start() {
    scan(document.body);
    syncButton();
    new MutationObserver(function (muts) {
      muts.forEach(function (m) {
        if (m.type === "characterData") wrapTextNode(m.target);
        else if (m.type === "attributes") maskTitle(m.target);
        else m.addedNodes.forEach(scan);
      });
    }).observe(document.body, {
      childList: true, subtree: true, characterData: true,
      attributes: true, attributeFilter: ["title"],
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
