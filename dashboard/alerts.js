var _client = null;

function _supabase() {
  if (_client) return _client;
  var url = document.querySelector('meta[name="supabase-url"]').content;
  var key = document.querySelector('meta[name="supabase-anon-key"]').content;
  if (!url || !key || url.startsWith("__")) {
    throw new Error("Supabase URL or anon key not configured.");
  }
  _client = window.supabase.createClient(url, key);
  return _client;
}

async function fetchRoutingRules() {
  var { data, error } = await _supabase()
    .from("severity_routing_rules")
    .select("severity,channel,scope")
    .filter("scope", "is", null)
    .order("severity");

  if (error) throw error;
  return data || [];
}

function renderRoutingTable(rows) {
  if (!rows || rows.length === 0) return;
  var lookup = {};
  for (var i = 0; i < rows.length; i++) {
    lookup[rows[i].severity] = rows[i].channel;
  }
  document.querySelectorAll("#routing-body tr").forEach(function(row) {
    var sev = row.dataset.severity;
    if (lookup[sev]) {
      row.querySelector(".routing-select").value = lookup[sev];
    }
  });
}

async function fetchStatus() {
  try {
    var resp = await fetch("/api/notification-settings");
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    var data = await resp.json();

    // PagerDuty
    document.getElementById("pd-status").textContent = data.pagerduty_configured ? "Configured" : "Not configured";
    document.getElementById("pd-status").className = data.pagerduty_configured ? "badge-status-open" : "badge-status-fail";
    document.getElementById("pd-masked").textContent = data.pagerduty_masked || "—";
    var pdBtn = document.getElementById("pd-save-btn");
    pdBtn.textContent = data.pagerduty_configured ? "Replace" : "Save";

    // Slack
    document.getElementById("slack-status").textContent = data.slack_configured ? "Configured" : "Not configured";
    document.getElementById("slack-status").className = data.slack_configured ? "badge-status-open" : "badge-status-fail";
    document.getElementById("slack-masked").textContent = data.slack_masked || "—";
    var slackBtn = document.getElementById("slack-save-btn");
    slackBtn.textContent = data.slack_configured ? "Replace" : "Save";
  } catch (err) {
    document.getElementById("pd-status").textContent = "Error";
    document.getElementById("slack-status").textContent = "Error";
  }
}

function _setupSecretForm(formId, field, resultId) {
  var form = document.getElementById(formId);
  if (!form) return;

  form.addEventListener("submit", async function(e) {
    e.preventDefault();
    var input = form.querySelector("input[name=value]");
    var value = input.value.trim();
    if (!value) return;

    var resultEl = document.getElementById(resultId);
    resultEl.innerHTML = "";

    try {
      var resp = await fetch("/api/notification-settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ field: field, value: value })
      });
      var data = await resp.json().catch(function() { return {}; });
      if (resp.ok && data.success) {
        resultEl.innerHTML = '<span style="color:#3fb950">Saved.</span>';
        input.value = "";
        fetchStatus();
      } else {
        resultEl.innerHTML = '<span class="error">' + (data.error || "Failed") + '</span>';
      }
    } catch (err) {
      resultEl.innerHTML = '<span class="error">Network error: ' + err.message + '</span>';
    }
  });
}

function _setupTestButton(btnId, channel, resultId) {
  var btn = document.getElementById(btnId);
  if (!btn) return;

  btn.addEventListener("click", async function() {
    var resultEl = document.getElementById(resultId);
    resultEl.innerHTML = '<span style="color:#8b949e">Sending...</span>';
    btn.disabled = true;

    try {
      var resp = await fetch("/api/notification-settings/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ channel: channel })
      });
      var data = await resp.json().catch(function() { return {}; });
      if (resp.ok && data.success) {
        resultEl.innerHTML = '<span style="color:#3fb950">Test alert sent.</span>';
      } else {
        resultEl.innerHTML = '<span class="error">' + (data.error || "Failed") + '</span>';
      }
    } catch (err) {
      resultEl.innerHTML = '<span class="error">Network error: ' + err.message + '</span>';
    } finally {
      btn.disabled = false;
    }
  });
}

function _setupRoutingRows() {
  document.querySelectorAll(".routing-save").forEach(function(btn) {
    btn.addEventListener("click", async function() {
      var row = btn.closest("tr");
      var severity = row.dataset.severity;
      var channel = row.querySelector(".routing-select").value;
      var resultEl = document.getElementById("routing-result");

      btn.disabled = true;
      btn.textContent = "Saving...";

      try {
        var resp = await fetch("/api/routing-rules", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ severity: severity, channel: channel })
        });
        var data = await resp.json().catch(function() { return {}; });
        if (resp.ok && data.success) {
          resultEl.innerHTML = '<span style="color:#3fb950">' + severity + ' routing updated.</span>';
          try {
            var rules = await fetchRoutingRules();
            renderRoutingTable(rules);
          } catch (err) {}
        } else {
          resultEl.innerHTML = '<span class="error">' + (data.error || "Failed") + '</span>';
        }
      } catch (err) {
        resultEl.innerHTML = '<span class="error">Network error: ' + err.message + '</span>';
      } finally {
        btn.disabled = false;
        btn.textContent = "Save";
      }
    });
  });
}

document.addEventListener("DOMContentLoaded", async function() {
  fetchStatus();
  try {
    var rules = await fetchRoutingRules();
    renderRoutingTable(rules);
  } catch (err) { /* table shows defaults */ }
  _setupSecretForm("pd-form", "pagerduty_routing_key", "pd-result");
  _setupSecretForm("slack-form", "slack_webhook_url", "slack-result");
  _setupTestButton("pd-test-btn", "pagerduty", "pd-test-result");
  _setupTestButton("slack-test-btn", "slack", "slack-test-result");
  _setupRoutingRows();
});
