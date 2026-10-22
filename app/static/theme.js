// Runs before first paint: saved choice, else the device setting. (External file: CSP forbids inline scripts.)
(function () {
  var saved = null;
  try { saved = localStorage.getItem("mh-theme"); } catch (e) {}
  var dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.setAttribute("data-theme", saved || (dark ? "dark" : "light"));
})();
