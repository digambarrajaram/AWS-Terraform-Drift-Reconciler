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

function _errorMsg(message) {
  const el = document.getElementById("scan-result");
  el.hidden = false;
  document.getElementById("scan-result-body").textContent = message;
}

function _resetStages() {
  document.querySelectorAll(".stage").forEach(s => {
    s.classList.remove("active", "done", "failed");
  });
}

const _STAGE_ORDER = ["unmanaged_scan", "reconcile_agent", "trivy_gate", "drift_alert", "drift_pr"];

function updateTracker(row) {
  const current = row.current_stage;
  const status = row.status;

  document.querySelectorAll(".stage").forEach(el => {
    const stage = el.dataset.stage;
    const idx = _STAGE_ORDER.indexOf(stage);
    const curIdx = _STAGE_ORDER.indexOf(current);

    el.classList.remove("active", "done", "failed");

    if (status === "failed" && idx === curIdx) {
      el.classList.add("failed");
    } else if (status === "complete") {
      el.classList.add("done");
    } else if (idx < curIdx) {
      el.classList.add("done");
    } else if (idx === curIdx) {
      el.classList.add("active");
    }
  });

  const resultEl = document.getElementById("scan-result");
  const bodyEl = document.getElementById("scan-result-body");

  if (status === "complete") {
    resultEl.hidden = false;
    const summary = row.result_summary || {};
    const prLinks = row.pr_links || [];
    let html = "";
    if (summary.report_path) {
      html += `<p>Report: <code>${summary.report_path}</code></p>`;
    }
    if (prLinks.length > 0) {
      html += "<ul>" + prLinks.map(url =>
        `<li><a href="${url}" target="_blank">${url}</a></li>`
      ).join("") + "</ul>";
    }
    if (!html) html = "<p>Scan completed successfully.</p>";
    bodyEl.innerHTML = html;
  } else if (status === "failed") {
    resultEl.hidden = false;
    const summary = row.result_summary || {};
    bodyEl.innerHTML = `<div class="error">Scan failed: ${summary.error || "unknown error"}</div>`;
  }
}

function startPolling(runId) {
  let fallbackTimer = null;
  let stopped = false;

  function _fallbackPoll() {
    clearTimeout(fallbackTimer);
    fallbackTimer = setTimeout(async () => {
      if (stopped) return;
      try {
        const { data } = await _supabase()
          .from("scan_runs")
          .select("status, current_stage, result_summary, pr_links")
          .eq("id", runId)
          .single();
        if (data) updateTracker(data);
      } catch (err) { /* silent */ }
    }, 5000);
  }

  function _stop() {
    stopped = true;
    clearTimeout(fallbackTimer);
  }

  const channel = _supabase()
    .channel(`scan_run_${runId}`)
    .on(
      "postgres_changes",
      { event: "UPDATE", schema: "public", table: "scan_runs", filter: `id=eq.${runId}` },
      (payload) => {
        if (stopped) return;
        updateTracker(payload.new);
        _fallbackPoll(); // reset fallback timer on every realtime event
        if (payload.new.status === "complete" || payload.new.status === "failed") {
          _stop();
          const btn = document.getElementById("scan-submit");
          btn.disabled = false;
          btn.textContent = "Run Scan";
        }
      }
    )
    .subscribe((status) => {
      if (status === "SUBSCRIBED") {
        _fallbackPoll(); // start fallback once subscribed
      }
    });
}

document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("scan-form");
  const btn = document.getElementById("scan-submit");

  form.addEventListener("submit", async (e) => {
    e.preventDefault();

    const scope = document.getElementById("scan-scope").value;
    const unmanaged = document.getElementById("scan-unmanaged").checked;

    if (!scope) return;

    btn.disabled = true;
    btn.textContent = "Starting...";
    _resetStages();
    document.getElementById("scan-result").hidden = true;

    try {
      const resp = await fetch("/api/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope, unmanaged_flag: unmanaged }),
      });

      const data = await resp.json().catch(() => ({}));

      if (resp.status === 202 && data.run_id) {
        startPolling(data.run_id);
      } else if (resp.status === 409) {
        _errorMsg(`Scan already running for ${scope} (run: ${data.run_id || "unknown"})`);
        btn.disabled = false;
        btn.textContent = "Run Scan";
      } else {
        _errorMsg(data.error || `Unexpected error (${resp.status})`);
        btn.disabled = false;
        btn.textContent = "Run Scan";
      }
    } catch (err) {
      _errorMsg(err.message || "Network error — is the server running?");
      btn.disabled = false;
      btn.textContent = "Run Scan";
    }
  });
});
