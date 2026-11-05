/*
 * Glaka chat widget. Embed with:
 *   <script src="https://YOUR-GLAKA/widget.js" data-glaka-key="wk_..." async></script>
 * Optional: data-glaka-color="#2f5d50"
 *
 * Renders in a Shadow DOM (the page's CSS can't touch it), writes all text with
 * textContent (no HTML injection), and keeps the private conversation token in
 * this site's localStorage only.
 */
(function () {
  "use strict";
  var script = document.currentScript;
  if (!script || window.__glakaWidget) return;
  window.__glakaWidget = true;

  var key = script.getAttribute("data-glaka-key");
  if (!key) return;
  var color = script.getAttribute("data-glaka-color") || "#2f5d50";
  var base = new URL(script.src).origin + "/widget/" + encodeURIComponent(key);
  var storeKey = "glaka:" + key;
  var POLL_MS = 15000, POLL_FOR_MS = 30 * 60 * 1000;

  function load() { try { return JSON.parse(localStorage.getItem(storeKey) || "{}"); } catch (e) { return {}; } }
  function save(state) { try { localStorage.setItem(storeKey, JSON.stringify(state)); } catch (e) {} }
  var state = load();

  function api(path, options) {
    return fetch(base + path, Object.assign({ headers: { "Content-Type": "application/json" } }, options || {}))
      .then(function (r) {
        if (!r.ok) { var err = new Error("HTTP " + r.status); err.status = r.status; throw err; }
        return r.json();
      });
  }

  api("/config").then(build).catch(function () { /* unknown/disabled widget: show nothing */ });

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function build(config) {
    var host = el("div");
    host.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483000;";
    document.body.appendChild(host);
    var root = host.attachShadow({ mode: "open" });

    var style = el("style");
    style.textContent = [
      ":host{all:initial}",
      "*{box-sizing:border-box;font:14px/1.45 system-ui,-apple-system,'Segoe UI',sans-serif}",
      ".launch{width:56px;height:56px;border-radius:50%;border:0;cursor:pointer;background:" + color + ";color:#fff;",
      "box-shadow:0 4px 16px rgba(0,0,0,.2);display:flex;align-items:center;justify-content:center}",
      ".launch svg{width:26px;height:26px}",
      ".panel{position:absolute;right:0;bottom:68px;width:360px;max-width:calc(100vw - 32px);height:520px;",
      "max-height:calc(100vh - 100px);background:#fff;color:#1d1d1b;border-radius:14px;display:none;",
      "flex-direction:column;overflow:hidden;box-shadow:0 8px 32px rgba(0,0,0,.22)}",
      ".panel.open{display:flex}",
      ".head{background:" + color + ";color:#fff;padding:12px 14px;display:flex;justify-content:space-between;align-items:center}",
      ".head strong{font-weight:600}",
      ".close{background:none;border:0;color:#fff;font-size:20px;cursor:pointer;line-height:1}",
      ".log{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:8px;background:#f6f6f3}",
      ".msg{max-width:85%;padding:8px 11px;border-radius:12px;white-space:pre-wrap;word-wrap:break-word}",
      ".visitor{align-self:flex-end;background:" + color + ";color:#fff;border-bottom-right-radius:4px}",
      ".business{align-self:flex-start;background:#fff;border:1px solid #e2e1dc;border-bottom-left-radius:4px}",
      ".note{align-self:center;color:#6b6b66;font-size:12px;text-align:center;max-width:90%}",
      ".foot{border-top:1px solid #e2e1dc;padding:10px;display:flex;flex-direction:column;gap:6px;background:#fff}",
      ".foot input,.foot textarea{width:100%;border:1px solid #e2e1dc;border-radius:8px;padding:8px;color:#1d1d1b;background:#fff}",
      ".foot textarea{resize:none;height:60px}",
      ".row{display:flex;gap:6px}",
      ".send{border:0;border-radius:8px;padding:8px 14px;background:" + color + ";color:#fff;font-weight:600;cursor:pointer}",
      ".send:disabled{opacity:.6;cursor:wait}",
      ".small{font-size:11px;color:#6b6b66}",
      "@media (max-width:480px){.panel{position:fixed;right:8px;left:8px;bottom:84px;width:auto;height:70vh}}"
    ].join("");
    root.appendChild(style);

    var panel = el("div", "panel");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "Chat with " + config.business_name);
    var head = el("div", "head");
    head.appendChild(el("strong", null, config.business_name));
    var close = el("button", "close", "×");
    close.setAttribute("aria-label", "Close chat");
    head.appendChild(close);
    var log = el("div", "log");
    log.setAttribute("aria-live", "polite");
    var foot = el("div", "foot");
    var email = el("input");
    email.type = "email";
    email.placeholder = "Your email (optional, so we can follow up)";
    email.value = state.email || "";
    var row = el("div", "row");
    var text = el("textarea");
    text.placeholder = "Type your question…";
    text.maxLength = 2000;
    text.setAttribute("aria-label", "Your message");
    var send = el("button", "send", "Send");
    row.appendChild(text);
    row.appendChild(send);
    foot.appendChild(email);
    foot.appendChild(row);
    foot.appendChild(el("div", "small", "Answers come from " + config.business_name + "'s own information. Powered by Glaka."));
    panel.appendChild(head);
    panel.appendChild(log);
    panel.appendChild(foot);

    var launch = el("button", "launch");
    launch.setAttribute("aria-label", "Chat with " + config.business_name);
    launch.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
      'stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
    root.appendChild(panel);
    root.appendChild(launch);

    function add(role, message) {
      log.appendChild(el("div", "msg " + role, message));
      log.scrollTop = log.scrollHeight;
    }
    function note(message) {
      log.appendChild(el("div", "note", message));
      log.scrollTop = log.scrollHeight;
    }

    var shownBusiness = 0, pollTimer = null, pollUntil = 0;
    function render(messages) {
      log.textContent = "";
      add("business", config.greeting);
      shownBusiness = 0;
      var waiting = false;
      messages.forEach(function (m) {
        add(m.role, m.text);
        if (m.role === "business") { shownBusiness++; waiting = false; } else { waiting = true; }
      });
      if (waiting) note("Waiting for the team to reply…");
      return waiting;
    }
    function refresh() {
      if (!state.conversation) { render([]); return Promise.resolve(false); }
      return api("/conversations/" + encodeURIComponent(state.conversation)).then(render);
    }
    function poll() {
      clearTimeout(pollTimer);
      if (Date.now() > pollUntil) return;
      pollTimer = setTimeout(function () {
        refresh().then(function (waiting) { if (waiting) poll(); }).catch(poll);
      }, POLL_MS);
    }

    function toggle(open) {
      panel.classList.toggle("open", open);
      if (open) {
        refresh().then(function (waiting) { if (waiting) { pollUntil = Date.now() + POLL_FOR_MS; poll(); } })
          .catch(function () { render([]); });
        text.focus();
      }
    }
    launch.addEventListener("click", function () { toggle(!panel.classList.contains("open")); });
    close.addEventListener("click", function () { toggle(false); launch.focus(); });
    panel.addEventListener("keydown", function (e) { if (e.key === "Escape") { toggle(false); launch.focus(); } });
    text.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
    });
    send.addEventListener("click", submit);

    function submit() {
      var message = text.value.trim();
      if (!message || send.disabled) return;
      var mail = email.value.trim();
      state.email = mail;
      save(state);
      add("visitor", message);
      text.value = "";
      send.disabled = true;
      send.textContent = "…";
      api("/messages", {
        method: "POST",
        body: JSON.stringify({ message: message, email: mail || null, conversation: state.conversation || null })
      }).then(function (res) {
        state.conversation = res.conversation;
        save(state);
        if (res.status === "answered" && res.reply) { add("business", res.reply); }
        else { note(res.note || "Thanks! We'll reply here shortly."); pollUntil = Date.now() + POLL_FOR_MS; poll(); }
      }).catch(function (err) {
        note(err.status === 429 ? "You've sent a lot of messages. Please try again a bit later."
          : err.status === 422 ? "Please check your email address." : "Sorry, something went wrong. Please try again.");
      }).then(function () { send.disabled = false; send.textContent = "Send"; });
    }
  }
})();
