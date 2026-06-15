/* HomeGuard — Widget de armado/presencia (autocontenido) */
(function () {
  var MODES = [
    { id: "disarmed", label: "Desarmado", color: "#6b7280" },
    { id: "partial",  label: "Parcial",   color: "#d97706" },
    { id: "full",     label: "Armado",    color: "#dc2626" },
  ];
  var box = document.createElement("div");
  box.id = "hg-arm-widget";
  box.style.cssText = "position:fixed;bottom:calc(60px + env(safe-area-inset-bottom, 0px) + 8px);right:12px;z-index:9999;" +
    "background:#1f2937;color:#f9fafb;border-radius:12px;padding:10px 12px;" +
    "font-family:system-ui,sans-serif;font-size:13px;box-shadow:0 4px 12px rgba(0,0,0,.4);" +
    "display:flex;flex-direction:column;gap:8px;min-width:180px";
  box.innerHTML =
    '<div id="hg-arm-status" style="display:flex;align-items:center;gap:6px">' +
    '<span id="hg-arm-dot" style="width:10px;height:10px;border-radius:50%;background:#6b7280"></span>' +
    '<span id="hg-arm-label">Cargando…</span></div>' +
    '<div id="hg-arm-home" style="font-size:11px;color:#9ca3af"></div>' +
    '<div id="hg-arm-btns" style="display:flex;gap:6px"></div>';
  document.body.appendChild(box);

  var btns = document.getElementById("hg-arm-btns");
  MODES.forEach(function (m) {
    var b = document.createElement("button");
    b.textContent = m.label;
    b.dataset.mode = m.id;
    b.style.cssText = "flex:1;border:none;border-radius:8px;padding:6px 4px;" +
      "font-size:11px;cursor:pointer;background:#374151;color:#e5e7eb";
    b.onclick = function () { setMode(m.id); };
    btns.appendChild(b);
  });

  function paint(data) {
    var mode = MODES.find(function (m) { return m.id === data.mode; }) || MODES[0];
    document.getElementById("hg-arm-dot").style.background = mode.color;
    document.getElementById("hg-arm-label").textContent = mode.label;
    var home = (data.devices || []).filter(function (d) { return d.is_home; })
      .map(function (d) { return d.person_name; });
    document.getElementById("hg-arm-home").textContent =
      home.length ? "En casa: " + home.join(", ") : "Casa vacía";
    btns.querySelectorAll("button").forEach(function (b) {
      var active = b.dataset.mode === data.mode;
      b.style.background = active ? mode.color : "#374151";
      b.style.fontWeight = active ? "600" : "400";
    });
  }

  function refresh() {
    fetch("/api/presence").then(function (r) { return r.json(); })
      .then(paint).catch(function () {});
  }

  function setMode(mode) {
    fetch("/api/arm-state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: mode, by: "dashboard" }),
    }).then(refresh).catch(function () {});
  }

  refresh();
  setInterval(refresh, 30000);
})();
