let _client = null;

function _supabase() {
  if (_client) return _client;
  const url = document.querySelector('meta[name="supabase-url"]').content;
  const key = document.querySelector('meta[name="supabase-anon-key"]').content;
  if (!url || !key || url.startsWith("__")) {
    throw new Error("Supabase URL or anon key not configured.");
  }
  _client = window.supabase.createClient(url, key);
  return _client;
}

async function fetchEligiblePRs(scope) {
  // Resolved PRs eligible for rollback.
  const { data: resolved, error } = await _supabase()
    .from("drift_events")
    .select("id,created_at,resource_id,severity,pr_number,pr_type,drift_summary,changes_jsonb")
    .eq("account", scope)
    .eq("status", "resolved")
    .in("pr_type", ["fix", "batch", "rollback"])
    .order("created_at", { ascending: false })
    .limit(100);

  if (error) throw error;
  if (!resolved || resolved.length === 0) return [];

  // PRs that have already been rolled back.
  const { data: rolledBack } = await _supabase()
    .from("drift_events")
    .select("rolled_back_from_pr")
    .not("rolled_back_from_pr", "is", null);

  const rolledBackSet = new Set((rolledBack || []).map(r => r.rolled_back_from_pr));

  return resolved.filter(r => !rolledBackSet.has(r.pr_number));
}

function renderRollbackList(rows) {
  const body = document.getElementById("rollback-list-body");
  body.innerHTML = "";

  if (!rows || rows.length === 0) {
    body.innerHTML = '<div class="empty">No eligible PRs for rollback — all resolved PRs have already been rolled back.</div>';
    return;
  }

  const html = rows.map(r => {
    const prLink = r.pr_number
      ? `<a href="https://github.com/digambarrajaram/AWS-Terraform-Drift-Reconciler/pull/${r.pr_number}" target="_blank">#${r.pr_number}</a>`
      : "—";
    const date = (r.created_at || "").slice(0, 10);
    const sev = (r.severity || "LOW").toLowerCase();
    return `<div class="rollback-row">
      <div class="rollback-row-main">
        <span class="sev-${sev}">${r.severity || "LOW"}</span>
        <code>${r.resource_id || "?"}</code>
        <span>PR ${prLink}</span>
        <span class="empty">${date}</span>
        <button type="button" class="btn-preview" data-pr="${r.pr_number}" data-scope="${r.account || 'scope-a'}">Preview Rollback</button>
      </div>
    </div>`;
  }).join("");

  body.innerHTML = html;

  body.querySelectorAll(".btn-preview").forEach(btn => {
    btn.addEventListener("click", () => {
      startPreview(parseInt(btn.dataset.pr), btn.dataset.scope);
    });
  });
}

async function startPreview(prNumber, scope) {
  _currentPrNumber = prNumber;
  _currentScope = scope;
  document.getElementById("rollback-confirm").hidden = true;
  const btn = document.querySelector(`.btn-preview[data-pr="${prNumber}"]`);
  if (btn) { btn.disabled = true; btn.textContent = "Loading preview..."; }

  try {
    const resp = await fetch("/api/rollback/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pr_number: prNumber, scope }),
    });
    const data = await resp.json().catch(() => ({}));

    if (resp.status !== 202 || !data.run_id) {
      _showRollbackError(data.error || `Unexpected error (${resp.status})`);
      if (btn) { btn.disabled = false; btn.textContent = "Preview Rollback"; }
      return;
    }

    // Poll until complete or failed.
    const timer = setInterval(async () => {
      try {
        const { data: row, error } = await _supabase()
          .from("rollback_runs")
          .select("status, result")
          .eq("id", data.run_id)
          .single();

        if (error || !row) return;
        if (row.status === "running") return;

        clearInterval(timer);
        if (btn) { btn.disabled = false; btn.textContent = "Preview Rollback"; }

        if (row.status === "complete" && row.result) {
          renderDiff(row.result.diff || []);
        } else {
          _showRollbackError((row.result && row.result.error) || "Unknown error");
        }
      } catch (err) {
        clearInterval(timer);
        if (btn) { btn.disabled = false; btn.textContent = "Preview Rollback"; }
      }
    }, 2000);
  } catch (err) {
    _showRollbackError(err.message || "Network error");
    if (btn) { btn.disabled = false; btn.textContent = "Preview Rollback"; }
  }
}

function _showRollbackError(msg) {
  const detail = document.getElementById("rollback-detail");
  detail.hidden = false;
  document.getElementById("rollback-detail-body").innerHTML =
    `<div class="error">${msg}</div>`;
}

function renderDiff(diff) {
  const detail = document.getElementById("rollback-detail");
  detail.hidden = false;

  if (!diff || diff.length === 0) {
    document.getElementById("rollback-detail-body").innerHTML =
      "<p>No differences found — live state already matches the rollback target.</p>";
    document.getElementById("rollback-confirm").hidden = true;
    return;
  }

  // Group by resource_id.
  const byResource = {};
  for (const d of diff) {
    const rid = d.resource_id || "?";
    if (!byResource[rid]) byResource[rid] = [];
    byResource[rid].push(d);
  }

  let html = "";
  for (const [rid, fields] of Object.entries(byResource)) {
    html += `<h3>${rid}</h3>`;
    html += `<table class="diff-table"><thead><tr><th>Field</th><th>Original</th><th>Fixed</th><th>Current Live</th></tr></thead><tbody>`;
    for (const f of fields) {
      const stale = JSON.stringify(f.current_live) !== JSON.stringify(f.fixed);
      const rowClass = stale ? "stale" : "match";
      html += `<tr class="${rowClass}">
        <td>${f.field}</td>
        <td>${JSON.stringify(f.original) || "—"}</td>
        <td>${JSON.stringify(f.fixed) || "—"}</td>
        <td>${JSON.stringify(f.current_live) || "—"}</td>
      </tr>`;
    }
    html += "</tbody></table>";
  }

  document.getElementById("rollback-detail-body").innerHTML = html;
  document.getElementById("rollback-confirm").hidden = false;
}

let _currentPrNumber = null;
let _currentScope = "";

document.addEventListener("DOMContentLoaded", async () => {
  document.getElementById("rollback-confirm").addEventListener("click", async () => {
    if (!_currentPrNumber) return;
    const btn = document.getElementById("rollback-confirm");
    btn.disabled = true;
    btn.textContent = "Rolling back...";

    try {
      const resp = await fetch("/api/rollback/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pr_number: _currentPrNumber, scope: _currentScope }),
      });
      const data = await resp.json().catch(() => ({}));

      if (resp.status === 409) {
        _showRollbackError("A rollback is already in progress for this PR.");
        btn.disabled = false;
        btn.textContent = "Confirm Rollback";
        return;
      }
      if (resp.status !== 202 || !data.run_id) {
        _showRollbackError(data.error || `Unexpected error (${resp.status})`);
        btn.disabled = false;
        btn.textContent = "Confirm Rollback";
        return;
      }

      const timer = setInterval(async () => {
        try {
          const { data: row, error } = await _supabase()
            .from("rollback_runs")
            .select("status, result, rollback_pr_url")
            .eq("id", data.run_id)
            .single();

          if (error || !row) return;
          if (row.status === "running") return;

          clearInterval(timer);
          btn.disabled = false;
          btn.textContent = "Confirm Rollback";

          if (row.status === "complete" && row.rollback_pr_url) {
            document.getElementById("rollback-detail-body").innerHTML +=
              `<p>Rollback PR created: <a href="${row.rollback_pr_url}" target="_blank">${row.rollback_pr_url}</a></p>`;
            document.getElementById("rollback-confirm").hidden = true;
            // Remove the entry from the eligible list.
            const rowEl = document.querySelector(`.btn-preview[data-pr="${_currentPrNumber}"]`);
            if (rowEl) {
              const container = rowEl.closest(".rollback-row");
              if (container) container.remove();
            }
            _currentPrNumber = null;
          } else {
            _showRollbackError((row.result && row.result.error) || "Unknown error");
          }
        } catch (err) {
          clearInterval(timer);
          btn.disabled = false;
          btn.textContent = "Confirm Rollback";
        }
      }, 2000);
    } catch (err) {
      _showRollbackError(err.message || "Network error");
      btn.disabled = false;
      btn.textContent = "Confirm Rollback";
    }
  });
  try {
    const rows = await fetchEligiblePRs(window.EnvSelector ? window.EnvSelector.getDefaultEnvironment() : "scope-a");
    renderRollbackList(rows);
  } catch (err) {
    document.getElementById("rollback-list-body").innerHTML =
      `<div class="error">Failed to load: ${err.message}</div>`;
  }
});
