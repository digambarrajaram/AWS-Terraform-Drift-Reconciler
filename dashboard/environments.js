async function fetchEnvironments() {
  var resp = await fetch("/api/environments");
  if (!resp.ok) throw new Error("HTTP " + resp.status);
  return await resp.json();
}

function renderEnvironmentsTable(rows) {
  var tbody = document.getElementById("env-body");
  if (!tbody) return;
  tbody.innerHTML = "";

  if (!rows || rows.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">No environments registered.</td></tr>';
    return;
  }

  var html = "";
  for (var i = 0; i < rows.length; i++) {
    var r = rows[i];
    html += '<tr class="sev-low">' +
      '<td>' + (r.name || "") + '</td>' +
      '<td><code>' + (r.slug || "") + '</code></td>' +
      '<td>' + (r.region || "—") + '</td>' +
      '<td>' + (r.aws_account_id || "—") + '</td>' +
      '<td><span class="' + (r.is_active ? 'badge-status-open' : 'badge-status-fail') + '">' + (r.is_active ? 'active' : 'inactive') + '</span></td>' +
      '<td>' +
        '<button class="btn-expire env-edit" data-id="' + r.id + '">Edit</button> ' +
        (r.is_active
          ? '<button class="btn-delete env-deactivate" data-id="' + r.id + '">Deactivate</button>'
          : '<button class="env-reactivate" data-id="' + r.id + '" style="color:#3fb950;background:transparent;border:1px solid #30363d;padding:2px 10px;border-radius:4px;cursor:pointer;font-size:11px">Reactivate</button>') +
      '</td>' +
      '</tr>';
  }
  tbody.innerHTML = html;

  tbody.querySelectorAll(".env-reactivate").forEach(function(btn) {
    btn.addEventListener("click", async function() {
      btn.disabled = true;
      try {
        var resp = await fetch("/api/environments/" + btn.dataset.id, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ is_active: true })
        });
        if (resp.ok) refreshAll();
        else alert("Failed to reactivate.");
      } catch (e) { alert("Network error: " + e.message); }
      btn.disabled = false;
    });
  });

  tbody.querySelectorAll(".env-deactivate").forEach(function(btn) {
    btn.addEventListener("click", async function() {
      if (!window.confirm("Deactivate this environment? It will stop appearing in scope selectors.")) return;
      btn.disabled = true;
      try {
        var resp = await fetch("/api/environments/" + btn.dataset.id, { method: "DELETE" });
        if (resp.ok) refreshAll();
        else alert("Failed to deactivate.");
      } catch (e) { alert("Network error: " + e.message); }
      btn.disabled = false;
    });
  });

  tbody.querySelectorAll(".env-edit").forEach(function(btn) {
    btn.addEventListener("click", function() {
      _showEditForm(btn.dataset.id, rows);
    });
  });
}

function _showEditForm(id, rows) {
  var env = rows.find(function(r) { return r.id === id; });
  if (!env) return;

  var fields = ["name", "aws_account_id", "aws_profile", "region", "tf_state_bucket", "tf_lock_table", "tf_directory_path", "scan_role_variable", "apply_role_secret_name", "apply_environment_name", "repo_url", "repo_branch", "git_auth_type"];
  var msg = "Edit " + env.name + " (" + env.slug + ")\n\nEnter new values (leave blank to keep current):";
  var updates = {};
  var anyChange = false;

  for (var i = 0; i < fields.length; i++) {
    var f = fields[i];
    var cur = env[f] || "";
    var val = window.prompt("Edit " + f + ":", cur);
    if (val === null) return; // cancelled
    if (val !== cur) {
      updates[f] = val;
      anyChange = true;
    }
  }
  if (!anyChange) return;

  fetch("/api/environments/" + id, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates)
  }).then(function(resp) {
    if (resp.ok) refreshAll();
    else alert("Failed to update.");
  }).catch(function(e) { alert("Network error: " + e.message); });
}

async function refreshAll() {
  var tbody = document.getElementById("env-body");
  if (tbody) tbody.innerHTML = '<tr><td colspan="7"><div class="skeleton" style="height:20px"></div></td></tr>';

  try {
    var data = await fetchEnvironments();
    renderEnvironmentsTable(data);
  } catch (err) {
    if (tbody) tbody.innerHTML = '<tr><td colspan="7" class="error">' + err.message + '</td></tr>';
  }
}

document.addEventListener("DOMContentLoaded", function() {
  refreshAll();

  document.getElementById("add-form").addEventListener("submit", async function(e) {
    e.preventDefault();
    var form = e.target;
    var btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    btn.textContent = "Adding...";

    var entry = {};
    var githubToken = null;
    form.querySelectorAll("input, select").forEach(function(inp) {
      if (inp.name === "github_token") {
        if (inp.value.trim() !== "") githubToken = inp.value.trim();
      } else if (inp.value !== "") {
        entry[inp.name] = inp.value;
      }
    });

    var resultEl = document.getElementById("add-result");
    try {
      var payload = entry;
      if (githubToken) payload._github_token = githubToken;
      var resp = await fetch("/api/environments", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      var data = await resp.json().catch(function() { return {}; });
      if (resp.status === 201) {
        resultEl.innerHTML = '<span style="color:#3fb950">Environment added.</span>';
        form.reset();
        refreshAll();
      } else {
        resultEl.innerHTML = '<span class="error">' + (data.error || "Failed") + '</span>';
      }
    } catch (err) {
      resultEl.innerHTML = '<span class="error">Network error: ' + err.message + '</span>';
    } finally {
      btn.disabled = false;
      btn.textContent = "Add Environment";
    }
  });

  // git_auth_type toggle: show/hide github_token input.
  var gitAuthSel = document.getElementById("git-auth-type-add");
  if (gitAuthSel) {
    gitAuthSel.addEventListener("change", function() {
      var group = document.getElementById("github-token-add-group");
      if (group) group.style.display = this.value === "token" ? "" : "none";
    });
  }
});
