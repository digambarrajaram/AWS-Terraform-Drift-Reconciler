/**
 * Shared auth helper — one-time API token prompt, persisted in localStorage.
 *
 * When the server has API_ACCESS_TOKEN configured, every fetch() to /api/*
 * must carry the matching X-Api-Access-Token header.  This script asks the
 * user once per browser, stores the answer, and exposes _authHeaders() for
 * every page to merge into its fetch() calls.
 *
 * Usage:
 *   <script src="auth.js"></script>
 *   <script>
 *     fetch("/api/...", {
 *       headers: Object.assign({}, _authHeaders(), { "Content-Type": "application/json" })
 *     });
 *   </script>
 */
(function () {
  "use strict";

  var _token = null;

  window._authHeaders = function () {
    // Already resolved (token set, or user explicitly skipped).
    if (_token !== null) return _token ? { "X-Api-Access-Token": _token } : {};

    // Check localStorage for a previously-saved token.
    try {
      var saved = (window.localStorage || {}).getItem("drift_api_token") || "";
      if (saved) {
        _token = saved;
        return { "X-Api-Access-Token": _token };
      }
    } catch (e) { /* localStorage unavailable — prompt every load */ }

    // First visit — prompt once.
    var input = window.prompt(
      "API Access Token\n\n" +
      "Paste the API_ACCESS_TOKEN value configured on the server.\n" +
      "Leave blank to skip (requests will fail if the server requires auth).\n\n" +
      "This prompt appears once per browser.",
      ""
    );

    if (input && input.trim()) {
      _token = input.trim();
      try { window.localStorage.setItem("drift_api_token", _token); } catch (e) {}
      return { "X-Api-Access-Token": _token };
    }

    // User skipped — don't prompt again this session, but don't persist.
    _token = "";
    return {};
  };
})();
