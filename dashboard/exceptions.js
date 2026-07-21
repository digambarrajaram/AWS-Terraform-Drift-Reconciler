var _currentScope = "";

async function fetchExceptions(scope) {
  var resp = await fetch("/api/exceptions?scope=" + encodeURIComponent(scope));
  if (!resp.ok) {
    var err = { error: "Unexpected error (" + resp.status + ")" };
    try { err = await resp.json(); } catch (e) {}
    throw new Error(err.error || "Failed to fetch exceptions");
  }
  return await resp.json();
}

function _expiryBadge(expires) {
  if (!expires) return "";
  var expDate = new Date(expires + "T00:00:00Z");
  var now = new Date();
  var ms = expDate.getTime() - now.getTime();
  var days = Math.ceil(ms / 86400000);
  if (days < 0) {
    return ' <span class="badge-status-fail">expired</span>';
  }
  if (days <= 30) {
    return ' <span class="badge-status-suppressed">' + days + 'd left</span>';
  }
  return "";
}

function renderExceptionsTable(containerId, entries, schemaType) {
  var tbody = document.getElementById(containerId);
  if (!tbody) return;

  tbody.innerHTML = "";

  if (!entries || entries.length === 0) {
    tbody.innerHTML = '<tr><td colspan="10" class="empty">No exceptions configured.</td></tr>';
    return;
  }

  var html = "";
  for (var i = 0; i < entries.length; i++) {
    var e = entries[i];
    var idJson;
    if (schemaType === "drift") {
      idJson = JSON.stringify({ resource_address: e.resource_address || "" });
      html += '<tr class="sev-low">' +
        '<td><code>' + (e.resource_address || "?") + '</code></td>' +
        '<td>' + (e.drift_type || "?") + '</td>' +
        '<td>' + (e.reason || "—") + '</td>' +
        '<td>' + (e.approved_by || "—") + '</td>' +
        '<td>' + (e.expires || "—") + _expiryBadge(e.expires) + '</td>' +
        '<td>' + (e.auto ? "yes" : "—") + '</td>' +
        '<td><button class="btn-expire" data-type="' + schemaType + '" data-id=\'' + idJson + '\'>Expire</button> <button class="btn-delete" data-type="' + schemaType + '" data-id=\'' + idJson + '\'>Delete</button></td>' +
        '</tr>';
    } else {
      idJson = JSON.stringify({ resource_type: e.resource_type || "", resource_id_pattern: e.resource_id_pattern || "" });
      html += '<tr class="sev-low">' +
        '<td>' + (e.resource_type || "?") + '</td>' +
        '<td><code>' + (e.resource_id_pattern || "?") + '</code></td>' +
        '<td>' + (e.reason || "—") + '</td>' +
        '<td>' + (e.approved_by || "—") + '</td>' +
        '<td>' + (e.max_monthly_cost_usd != null ? "$" + e.max_monthly_cost_usd : "—") + '</td>' +
        '<td><button class="btn-expire" data-type="' + schemaType + '" data-id=\'' + idJson + '\'>Expire</button> <button class="btn-delete" data-type="' + schemaType + '" data-id=\'' + idJson + '\'>Delete</button></td>' +
        '</tr>';
    }
  }
  tbody.innerHTML = html;

  // Wire expire and delete buttons.
  tbody.querySelectorAll(".btn-expire").forEach(function(btn) {
    btn.addEventListener("click", function() {
      var schemaType = btn.dataset.type;
      var idents = JSON.parse(btn.dataset.id);
      _expireEntry(schemaType, idents);
    });
  });
  tbody.querySelectorAll(".btn-delete").forEach(function(btn) {
    btn.addEventListener("click", function() {
      var schemaType = btn.dataset.type;
      var idents = JSON.parse(btn.dataset.id);
      _deleteEntry(schemaType, idents);
    });
  });
}

async function _expireEntry(schemaType, idents) {
  var today = new Date().toISOString().slice(0, 10);
  var newExpires = window.prompt("Set expiration date (YYYY-MM-DD):", today);
  if (!newExpires) return; // user cancelled

  var entry = JSON.parse(JSON.stringify(idents));
  entry.expires = newExpires;

  try {
    var resp = await fetch("/api/exceptions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scope: _currentScope,
        exception_type: schemaType,
        action: "expire",
        entry: entry
      })
    });

    var data;
    try { data = await resp.json(); } catch (e) { data = { error: "Empty response (" + resp.status + ")" }; }

    if (resp.ok) {
      refreshExceptions(_currentScope);
    } else {
      window.alert("Error: " + (data.error || "Unexpected error (" + resp.status + ")"));
    }
  } catch (err) {
    window.alert("Network error: " + err.message);
  }
}

async function _deleteEntry(schemaType, idents) {
  if (!window.confirm("Delete this exception? This can be undone from Supabase.")) return;

  try {
    var resp = await fetch("/api/exceptions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scope: _currentScope,
        exception_type: schemaType,
        action: "delete",
        entry: idents
      })
    });
    if (resp.ok) {
      refreshExceptions(_currentScope);
    } else {
      var data;
      try { data = await resp.json(); } catch (e) { data = {}; }
      window.alert("Error: " + (data.error || "Unexpected error (" + resp.status + ")"));
    }
  } catch (err) {
    window.alert("Network error: " + err.message);
  }
}

async function refreshExceptions(scope) {
  _currentScope = scope;
  var bodyEls = ["drift-exceptions-body", "unmanaged-exceptions-body"];
  bodyEls.forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.innerHTML = '<tr><td colspan="10"><div class="skeleton" style="height:20px"></div></td></tr>';
  });

  try {
    var data = await fetchExceptions(scope);
    renderExceptionsTable("drift-exceptions-body", data.drift_exceptions, "drift");
    renderExceptionsTable("unmanaged-exceptions-body", data.unmanaged_exceptions, "unmanaged");
  } catch (err) {
    bodyEls.forEach(function(id) {
      var tbody = document.getElementById(id);
      if (tbody) tbody.innerHTML = '<tr><td colspan="10" class="error">' + err.message + '</td></tr>';
    });
  }
}

function _setupForm(formId, exceptionType) {
  var form = document.getElementById(formId);
  if (!form) return;

  var cancelBtn = form.querySelector(".btn-cancel");
  if (cancelBtn) {
    cancelBtn.addEventListener("click", function() {
      form.reset();
      var resultEl = form.querySelector(".form-result");
      if (resultEl) resultEl.innerHTML = "";
    });
  }

  form.addEventListener("submit", async function(e) {
    e.preventDefault();
    var submitBtn = form.querySelector("button[type=submit]");
    submitBtn.disabled = true;
    submitBtn.textContent = "Submitting...";

    var resultEl = form.querySelector(".form-result");
    if (!resultEl) {
      resultEl = document.createElement("div");
      resultEl.className = "form-result";
      form.appendChild(resultEl);
    }
    resultEl.innerHTML = "";

    var entry = {};
    var inputs = form.querySelectorAll("input");
    inputs.forEach(function(inp) {
      var name = inp.name;
      if (!name) return;
      if (inp.type === "checkbox") {
        if (inp.checked) entry[name] = true;
      } else if (inp.value !== "") {
        entry[name] = inp.type === "number" ? parseFloat(inp.value) : inp.value;
      }
    });

    try {
      var resp = await fetch("/api/exceptions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scope: _currentScope,
          exception_type: exceptionType,
          action: "add",
          entry: entry
        })
      });

      var data;
      try { data = await resp.json(); } catch (e) { data = { error: "Empty response (" + resp.status + ")" }; }

      if (resp.ok) {
        resultEl.innerHTML = '<div class="empty" style="padding-top:8px;color:#3fb950">Exception added.</div>';
        form.reset();
        refreshExceptions(_currentScope);
      } else {
        resultEl.innerHTML = '<div class="error">' + (data.error || "Unexpected error") + '</div>';
      }
    } catch (err) {
      resultEl.innerHTML = '<div class="error">Network error: ' + err.message + '</div>';
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Submit";
    }
  });
}

document.addEventListener("DOMContentLoaded", function() {
  _setupForm("drift-form", "drift");
  _setupForm("unmanaged-form", "unmanaged");

  window.EnvSelector.renderEnvTabs(".scope-tabs", function(scope) {
    refreshExceptions(scope);
  }).then(function() {
    var scope = window.EnvSelector.getDefaultEnvironment();
    if (scope) refreshExceptions(scope);
  });
});
