/**
 * Shared CSRF helper for legacy pages.
 *
 * Session auth uses an HttpOnly ``session`` cookie plus a readable ``csrf``
 * cookie (double-submit). Merge ``_authHeaders()`` into mutating fetch() calls.
 */
(function () {
  "use strict";

  function _csrfFromCookie() {
    try {
      var match = document.cookie.match(/(?:^|; )csrf=([^;]*)/);
      return match ? decodeURIComponent(match[1]) : "";
    } catch (e) {
      return "";
    }
  }

  window._authHeaders = function () {
    var csrf = _csrfFromCookie();
    return csrf ? { "X-CSRF-Token": csrf } : {};
  };
})();
