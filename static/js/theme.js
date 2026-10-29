/* Runs in <head> before paint so a saved theme never flashes. */
(function () {
  try {
    var saved = localStorage.getItem("cutacq-theme");
    if (saved === "light" || saved === "dark") {
      document.documentElement.setAttribute("data-theme", saved);
    }
  } catch (e) {
    /* Storage blocked: follow the system setting. */
  }
})();
