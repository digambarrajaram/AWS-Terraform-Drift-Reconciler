/**
 * Shared environment selector — replaces hardcoded scope-tab HTML across
 * all dashboard pages with a single data-driven component.
 *
 * Usage:
 *   <script src="env-selector.js"></script>
 *   <script>
 *     window.EnvSelector.render(".scope-tabs", function(slug) {
 *       console.log("Selected:", slug);
 *     });
 *   </script>
 */
(function () {
  "use strict";

  var _cache = null;

  async function fetchEnvironments() {
    if (_cache) return _cache;
    var resp = await fetch("/api/environments");
    if (!resp.ok) throw new Error("Failed to fetch environments: " + resp.status);
    _cache = await resp.json();
    return _cache;
  }

  function getDefaultEnvironment(list) {
    return list && list.length > 0 ? list[0].slug : "";
  }

  function renderEnvTabs(containerSelector, onSelect, activeSlug) {
    var containers = document.querySelectorAll(containerSelector);
    if (containers.length === 0) return Promise.resolve();

    return fetchEnvironments().then(function (envs) {
      if (!envs || envs.length === 0) return;

      if (!activeSlug) activeSlug = getDefaultEnvironment(envs);

      containers.forEach(function (container) {
        container.innerHTML = "";
        envs.forEach(function (env) {
          var btn = document.createElement("button");
          btn.className = "scope-tab" + (env.slug === activeSlug ? " active" : "");
          btn.setAttribute("data-scope", env.slug);
          btn.textContent = env.name;
          btn.addEventListener("click", function () {
            container.querySelectorAll(".scope-tab").forEach(function (b) {
              b.classList.remove("active");
            });
            btn.classList.add("active");
            if (onSelect) onSelect(env.slug);
          });
          container.appendChild(btn);
        });
      });
    }).catch(function (err) {
      console.error("env-selector:", err);
    });
  }

  window.EnvSelector = {
    fetchEnvironments: fetchEnvironments,
    renderEnvTabs: renderEnvTabs,
    getDefaultEnvironment: getDefaultEnvironment,
  };
})();
