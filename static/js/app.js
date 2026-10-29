/* CUT ICT Acquisition System: small progressive enhancements.
   Every page works without this file; it adds convenience only. */
(function () {
  "use strict";

  var root = document.documentElement;

  /* Theme toggle ------------------------------------------------------- */
  function currentTheme() {
    var set = root.getAttribute("data-theme");
    if (set === "light" || set === "dark") return set;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function syncThemeButtons() {
    var dark = currentTheme() === "dark";
    document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
      btn.setAttribute("aria-pressed", dark ? "true" : "false");
      btn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
      btn.setAttribute("title", dark ? "Switch to light theme" : "Switch to dark theme");
    });
  }
  document.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-theme-toggle]");
    if (!btn) return;
    var next = currentTheme() === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try {
      localStorage.setItem("cutacq-theme", next);
    } catch (e) {}
    syncThemeButtons();
  });

  /* Navigation drawer on small screens ------------------------------------ */
  var shell = document.querySelector(".shell");
  var toggle = document.querySelector("[data-nav-toggle]");
  function setNav(open) {
    if (!shell || !toggle) return;
    shell.classList.toggle("nav-open", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      var first = document.querySelector(".sidebar a");
      if (first) first.focus();
    }
  }
  if (toggle) {
    toggle.addEventListener("click", function () {
      setNav(!shell.classList.contains("nav-open"));
    });
  }
  document.addEventListener("click", function (event) {
    if (event.target.closest("[data-nav-close]")) setNav(false);
  });

  /* Close open menus on outside click and Escape --------------------------- */
  document.addEventListener("click", function (event) {
    document.querySelectorAll("details[data-menu][open]").forEach(function (menu) {
      if (!menu.contains(event.target)) menu.removeAttribute("open");
    });
  });
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    document.querySelectorAll("details[data-menu][open]").forEach(function (menu) {
      menu.removeAttribute("open");
      var summary = menu.querySelector("summary");
      if (summary) summary.focus();
    });
    if (shell && shell.classList.contains("nav-open")) {
      setNav(false);
      if (toggle) toggle.focus();
    }
  });

  /* Dismissable messages ---------------------------------------------------- */
  document.addEventListener("click", function (event) {
    var close = event.target.closest("[data-dismiss]");
    if (!close) return;
    var box = close.closest(".alert");
    if (box) box.remove();
  });

  /* Prevent double submission and show progress ------------------------------ */
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (form.dataset.submitted === "true") {
      event.preventDefault();
      return;
    }
    form.dataset.submitted = "true";
    var submitter = event.submitter;
    form.querySelectorAll("button[type=submit], input[type=submit]").forEach(function (btn) {
      if (btn === submitter) btn.setAttribute("aria-busy", "true");
      btn.setAttribute("aria-disabled", "true");
    });
    /* Re-enable if the browser returns to the page from history. */
    window.addEventListener("pageshow", function () {
      form.dataset.submitted = "false";
      form.querySelectorAll("[aria-busy], [aria-disabled]").forEach(function (btn) {
        btn.removeAttribute("aria-busy");
        btn.removeAttribute("aria-disabled");
      });
    }, { once: true });
  });

  /* Print buttons -------------------------------------------------------------- */
  document.addEventListener("click", function (event) {
    if (event.target.closest("[data-print]")) window.print();
  });

  /* Dynamic formset rows (request items, delivered assets) ----------------------- */
  function renumber(container) {
    var rows = container.querySelectorAll("[data-formset-row]:not(.is-removed)");
    rows.forEach(function (row, index) {
      var label = row.querySelector("[data-row-number]");
      if (label) label.textContent = String(index + 1);
    });
  }
  document.querySelectorAll("[data-formset]").forEach(function (container) {
    var prefix = container.getAttribute("data-formset");
    var total = document.getElementById("id_" + prefix + "-TOTAL_FORMS");
    var max = document.getElementById("id_" + prefix + "-MAX_NUM_FORMS");
    var template = container.querySelector("template[data-formset-template]");
    var list = container.querySelector("[data-formset-list]");
    var addButton = container.querySelector("[data-formset-add]");
    if (!total || !template || !list || !addButton) return;
    addButton.hidden = false;
    addButton.addEventListener("click", function () {
      var count = parseInt(total.value, 10);
      if (max && count >= parseInt(max.value, 10)) return;
      var html = template.innerHTML.replace(/__prefix__/g, String(count));
      var wrapper = document.createElement("div");
      wrapper.innerHTML = html.trim();
      var row = wrapper.firstElementChild;
      list.appendChild(row);
      total.value = String(count + 1);
      renumber(container);
      var firstInput = row.querySelector("select, input, textarea");
      if (firstInput) firstInput.focus();
    });
    container.addEventListener("click", function (event) {
      var remove = event.target.closest("[data-formset-remove]");
      if (!remove) return;
      var row = remove.closest("[data-formset-row]");
      var deleteBox = row.querySelector("input[type=checkbox][name$='-DELETE']");
      if (deleteBox) {
        deleteBox.checked = !deleteBox.checked;
        row.classList.toggle("is-removed", deleteBox.checked);
        remove.textContent = deleteBox.checked ? "Undo remove" : "Remove";
      } else {
        row.remove();
        var rows = list.querySelectorAll("[data-formset-row]");
        /* Renumber form indexes so Django receives a continuous set. */
        rows.forEach(function (r, i) {
          r.querySelectorAll("[name], [id], [for]").forEach(function (el) {
            ["name", "id", "for"].forEach(function (attr) {
              var v = el.getAttribute(attr);
              if (v) el.setAttribute(attr, v.replace(new RegExp(prefix + "-\\d+-"), prefix + "-" + i + "-"));
            });
          });
        });
        total.value = String(rows.length);
      }
      renumber(container);
    });
    renumber(container);
  });

  /* Chart tooltips: values are also printed in each chart's table view ------------- */
  var tip = null;
  function showTip(target, x, y) {
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "tooltip";
      tip.setAttribute("role", "status");
      document.body.appendChild(tip);
    }
    tip.textContent = "";
    var value = document.createElement("strong");
    value.textContent = target.getAttribute("data-tip-value") || "";
    var label = document.createElement("span");
    label.textContent = target.getAttribute("data-tip") || "";
    tip.appendChild(value);
    tip.appendChild(label);
    tip.hidden = false;
    var pad = 14;
    var rect = tip.getBoundingClientRect();
    var left = Math.min(x + pad, window.innerWidth - rect.width - 8);
    var top = Math.max(8, y - rect.height - pad);
    tip.style.left = left + "px";
    tip.style.top = top + "px";
  }
  function hideTip() {
    if (tip) tip.hidden = true;
  }
  document.addEventListener("pointermove", function (event) {
    var target = event.target.closest && event.target.closest("[data-tip]");
    if (target) showTip(target, event.clientX, event.clientY);
    else hideTip();
  });
  document.addEventListener("focusin", function (event) {
    var target = event.target.closest && event.target.closest("[data-tip]");
    if (!target) return;
    var box = target.getBoundingClientRect();
    showTip(target, box.left + box.width / 2, box.top);
  });
  document.addEventListener("focusout", hideTip);

  syncThemeButtons();
})();
