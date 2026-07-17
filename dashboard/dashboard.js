let _client = null;

function _supabase() {
  if (_client) return _client;
  const url = document.querySelector('meta[name="supabase-url"]').content;
  const key = document.querySelector('meta[name="supabase-anon-key"]').content;
  if (!url || !key || url.startsWith("__")) {
    throw new Error("Supabase URL or anon key not configured. Replace the meta tag placeholders.");
  }
  _client = window.supabase.createClient(url, key);
  return _client;
}

/**
 * Return { severity: count } for all open drift findings in *scope*,
 * grouped by severity.  Fetches from the drift_severity_summary view.
 */
async function fetchDriftSummary(scope) {
  const { data, error } = await _supabase()
    .from("drift_severity_summary")
    .select("severity, count")
    .eq("account", scope);

  if (error) throw error;

  const summary = {};
  for (const row of data || []) {
    summary[row.severity] = row.count;
  }
  return summary;
}

/**
 * Return the number of open rollback entries for *scope*.
 */
async function fetchRollbackCount(scope) {
  const { count, error } = await _supabase()
    .from("drift_events")
    .select("*", { count: "exact", head: true })
    .eq("status", "open")
    .eq("pr_type", "rollback")
    .eq("account", scope);

  if (error) throw error;
  return count;
}

/**
 * Return the ISO-8601 timestamp of the most recent drift event for
 * *scope*, or ``null`` when no events exist.
 */
async function fetchLastScan(scope) {
  const { data, error } = await _supabase()
    .from("drift_events")
    .select("created_at")
    .eq("account", scope)
    .order("created_at", { ascending: false })
    .limit(1);

  if (error) throw error;
  if (!data || data.length === 0) return null;
  return data[0].created_at;
}

/**
 * Return the summed monthly cost impact (USD) of all open drift
 * findings in *scope* that carry a cost_impact JSONB field.
 * Returns 0 when no cost data exists.
 */
async function fetchCostImpact(scope) {
  const { data, error } = await _supabase()
    .from("drift_events")
    .select("cost_impact")
    .eq("status", "open")
    .eq("account", scope);

  if (error) throw error;

  let total = 0;
  let resourceCount = 0;
  for (const row of data || []) {
    const ci = row.cost_impact;
    if (ci && ci.monthly_estimate_usd) {
      total += ci.monthly_estimate_usd;
      resourceCount++;
    }
  }
  return { total, resourceCount };
}

// ---------------------------------------------------------------------------
// Rendering helpers
// ---------------------------------------------------------------------------

function _skeleton(cardId) {
  const card = document.getElementById(cardId);
  card.querySelector(".card-body").innerHTML = `<div class="skeleton"></div>`;
  card.classList.remove("needs-attention");
}

function _render(cardId, html, needsAttention) {
  document.getElementById(cardId).querySelector(".card-body").innerHTML = html;
  document.getElementById(cardId).classList.toggle("needs-attention", !!needsAttention);
}

function _renderError(cardId, message) {
  document.getElementById(cardId).querySelector(".card-body").innerHTML =
    `<div class="error">${message}</div>`;
  document.getElementById(cardId).classList.remove("needs-attention");
}

function _relativeTime(iso) {
  if (!iso) return "never";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "just now";
  const mins = Math.floor(diff / 60);
  if (mins < 60) return `${mins} minute${mins === 1 ? "" : "s"} ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.floor(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

// ---------------------------------------------------------------------------
// Renderers
// ---------------------------------------------------------------------------

function renderDriftSummary(data) {
  const card = "drift-summary-card";
  if (!data) return _skeleton(card);
  const entries = Object.entries(data);
  if (entries.length === 0) {
    return _render(card, "<div class=\"empty\">No open drift</div>");
  }
  const total = entries.reduce((s, [, c]) => s + c, 0);
  const bars = entries
    .map(([sev, cnt]) => `<span class="sev-${sev.toLowerCase()}">${sev}: ${cnt}</span>`)
    .join(" ");
  _render(card, `<div class="metric-large">${total}</div><div class="metric-sub">${bars}</div>`, total > 0);
}

function renderCostImpact(data) {
  const card = "cost-impact-card";
  if (!data) return _skeleton(card);
  if (data.resourceCount === 0) {
    return _render(card, "<div class=\"empty\">No cost data</div>");
  }
  _render(card,
    `<div class="metric-large">$${data.total.toFixed(2)}</div>
     <div class="metric-sub">across ${data.resourceCount} resource${data.resourceCount === 1 ? "" : "s"}</div>`,
    data.total > 0);
}

function renderRollbackCount(count) {
  const card = "rollback-card";
  if (count === null || count === undefined) return _skeleton(card);
  _render(card,
    `<div class="metric-large">${count}</div>
     <div class="metric-sub">pending rollback${count === 1 ? "" : "s"}</div>`,
    count > 0);
}

function renderLastScan(iso) {
  const card = "last-scan-card";
  if (iso === undefined) return _skeleton(card);
  if (!iso) return _render(card, "<div class=\"empty\">No scans yet</div>");
  _render(card,
    `<div class="metric-sub">${new Date(iso).toLocaleString()}</div>
     <div class="metric-large">${_relativeTime(iso)}</div>`);
}

// ---------------------------------------------------------------------------
// Orchestration
// ---------------------------------------------------------------------------

let _channel = null;
let _pollTimer = null;

async function refreshAll(scope) {
  _skeleton("drift-summary-card");
  _skeleton("cost-impact-card");
  _skeleton("rollback-card");
  _skeleton("last-scan-card");

  const results = await Promise.allSettled([
    fetchDriftSummary(scope),
    fetchCostImpact(scope),
    fetchRollbackCount(scope),
    fetchLastScan(scope),
  ]);

  const [drift, cost, rollback, lastScan] = results;

  if (drift.status   === "fulfilled") renderDriftSummary(drift.value);
  else _renderError("drift-summary-card", drift.reason?.message || "Fetch failed");

  if (cost.status     === "fulfilled") renderCostImpact(cost.value);
  else _renderError("cost-impact-card", cost.reason?.message || "Fetch failed");

  if (rollback.status === "fulfilled") renderRollbackCount(rollback.value);
  else _renderError("rollback-card", rollback.reason?.message || "Fetch failed");

  if (lastScan.status  === "fulfilled") renderLastScan(lastScan.value);
  else _renderError("last-scan-card", lastScan.reason?.message || "Fetch failed");
}

function _subscribeRealtime(scope) {
  _unsubscribeRealtime();

  _channel = _supabase()
    .channel("drift_events_changes")
    .on(
      "postgres_changes",
      { event: "INSERT", schema: "public", table: "drift_events", filter: `account=eq.${scope}` },
      () => refreshAll(scope)
    )
    .on(
      "postgres_changes",
      { event: "UPDATE", schema: "public", table: "drift_events", filter: `account=eq.${scope}` },
      () => refreshAll(scope)
    )
    .subscribe((status) => {
      if (status === "CHANNEL_ERROR" || status === "TIMED_OUT") {
        console.warn("[realtime] Channel disconnected — falling back to poll-only");
        _channel = null;
      }
    });
}

function _unsubscribeRealtime() {
  if (_channel) {
    _supabase().removeChannel(_channel);
    _channel = null;
  }
}

function _setScope(scope) {
  const url = new URL(window.location);
  url.searchParams.set("scope", scope);
  window.history.replaceState(null, "", url);

  document.querySelectorAll(".scope-tab").forEach(t => {
    t.classList.toggle("active", t.dataset.scope === scope);
  });

  _subscribeRealtime(scope);
  refreshAll(scope);
}

function _readScopeFromURL() {
  const p = new URLSearchParams(window.location.search);
  const scope = p.get("scope");
  return scope === "scope-b" ? "scope-b" : "scope-a";
}

// Bootstrap
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".scope-tab").forEach(tab => {
    tab.addEventListener("click", () => _setScope(tab.dataset.scope));
  });

  const scope = _readScopeFromURL();
  _setScope(scope);

  // Fallback poll — keeps cards fresh if realtime disconnects.
  _pollTimer = setInterval(() => refreshAll(scope), 60_000);
});
