// Progressive enhancement only. The app works without JS; CSP forbids inline scripts.
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-theme-toggle]").forEach((b) => {
    b.addEventListener("click", () => {
      const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      try { localStorage.setItem("mh-theme", next); } catch (e) {}
    });
  });
  document.querySelectorAll("[data-fill-email]").forEach((b) => {
    b.addEventListener("click", () => {
      const f = document.getElementById("login-email");
      if (f) { f.value = b.dataset.fillEmail; const pw = document.querySelector('input[name="password"]'); if (pw) pw.focus(); }
    });
  });
  document.querySelectorAll("form[data-confirm]").forEach((f) => {
    f.addEventListener("submit", (e) => { if (!window.confirm(f.dataset.confirm)) e.preventDefault(); });
  });
});
