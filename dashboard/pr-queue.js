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

async function fetchPrQueue(filters, offset = 0) {
  let q = _supabase()
    .from("drift_events")
    .select(
      "id,created_at,account,resource_id,severity,status,pr_type,pr_number,cost_impact,trivy_passed,trivy_summary,freshness_gate_status,drift_summary"
    )
    .not("pr_number", "is", null);

  if (filters.pr_type) q = q.eq("pr_type", filters.pr_type);
  if (filters.status) q = q.eq("status", filters.status);

  const { data, error } = await q
    .order("created_at", { ascending: false })
    .range(offset, offset + 49);

  if (error) throw error;
  return { rows: data || [], hasMore: (data || []).length === 50 };
}

function renderRow(row) {
  const prLink = row.pr_number
    ? `<a href="https://github.com/digambarrajaram/AWS-Terraform-Drift-Reconciler/pull/${row.pr_number}" target="_blank">#${row.pr_number}</a>`
    : "—";

  const sev = (row.severity || "LOW").toLowerCase();
  const type = row.pr_type || "fix";
  const st = row.status || "?";

  let cost = "—";
  if (row.cost_impact) {
    const ci = row.cost_impact;
    cost = ci.monthly_estimate_usd != null ? `$${ci.monthly_estimate_usd.toFixed(2)}` : "—";
  }

  let trivy;
  if (row.trivy_passed === true) {
    trivy = '<span class="badge-status-open">&#x2713; Pass</span>';
  } else if (row.trivy_passed === false) {
    trivy = '<span class="badge-status-fail">&#x2717; Fail</span>';
  } else {
    trivy = '<span class="empty">—</span>';
  }

  let freshness;
  if (row.freshness_gate_status === "pass") {
    freshness = '<span class="badge-status-open">Pass</span>';
  } else if (row.freshness_gate_status === "fail") {
    freshness = '<span class="badge-status-fail">Fail</span>';
  } else {
    freshness = '<span class="empty">N/A</span>';
  }

  const dataset = [
    `data-row-id="${row.id || ""}"`,
    `data-drift-summary="${(row.drift_summary || "").replace(/"/g, "&quot;")}"`,
    `data-trivy-summary="${row.trivy_summary ? JSON.stringify(row.trivy_summary).replace(/"/g, "&quot;") : ""}"`,
    `data-freshness-checked-at="${row.freshness_gate_checked_at || ""}"`,
  ].join(" ");

  return `<tr ${dataset}>
    <td>${prLink}</td>
    <td><span class="badge-type-${type}">${type}</span></td>
    <td><span class="sev-${sev}">${row.severity || "LOW"}</span></td>
    <td><span class="badge-status-${st}">${st}</span></td>
    <td>${cost}</td>
    <td>${trivy}</td>
    <td>${freshness}</td>
  </tr>`;
}

function renderTable(rows) {
  const tbody = document.getElementById("pr-body");
  tbody.innerHTML = "";
  if (!rows || rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty">No PRs match these filters</td></tr>`;
    return;
  }
  tbody.insertAdjacentHTML("beforeend", rows.map(renderRow).join(""));
}

const filters = { pr_type: "", status: "" };
const PAGE_SIZE = 50;
let _page = 1;
let _hasMore = false;

function _updatePagination() {
  document.getElementById("btn-prev").disabled = _page <= 1;
  document.getElementById("btn-next").disabled = !_hasMore;
  document.getElementById("page-info").textContent = `Page ${_page}`;
}

async function _loadPage() {
  try {
    const offset = (_page - 1) * PAGE_SIZE;
    const { rows, hasMore } = await fetchPrQueue(filters, offset);
    _hasMore = hasMore;
    renderTable(rows);
    _updatePagination();
    document.getElementById("error-banner").hidden = true;
  } catch (err) {
    document.getElementById("error-banner").hidden = false;
    document.getElementById("error-banner").textContent = err.message || "Failed to load";
  }
}

async function _onPrFilterChange() {
  _page = 1;
  const url = new URL(window.location);
  if (filters.pr_type) url.searchParams.set("pr_type", filters.pr_type);
  else url.searchParams.delete("pr_type");
  if (filters.status) url.searchParams.set("status", filters.status);
  else url.searchParams.delete("status");
  window.history.replaceState(null, "", url);
  await _loadPage();
}

function _applyPrFiltersFromURL() {
  const p = new URLSearchParams(window.location.search);
  const pt = p.get("pr_type");
  if (pt) filters.pr_type = pt;
  const st = p.get("status");
  if (st) filters.status = st;
}

function _syncPrControls() {
  document.getElementById("filter-pr-type").value = filters.pr_type;
  document.querySelectorAll(".pill").forEach(pill => {
    pill.classList.toggle("active", filters.status === pill.dataset.status);
  });
}

let _expandedId = null;

function _expandRow(row, tr) {
  let detail = "";
  if (row.drift_summary) detail += `<div class="detail-summary">${row.drift_summary}</div>`;
  if (row.trivy_summary) {
    const ts = row.trivy_summary;
    const parts = [];
    if (ts.trivy_error) parts.push("Error: yes");
    if (ts.trivy_security_fixes) parts.push(`Security fixes: ${ts.trivy_security_fixes}`);
    if (ts.trivy_pre_existing_count) parts.push(`Pre-existing: ${ts.trivy_pre_existing_count}`);
    if (ts.trivy_newly_introduced_count) parts.push(`Newly-introduced: ${ts.trivy_newly_introduced_count}`);
    if (parts.length > 0) detail += `<div class="detail-diff">Trivy: ${parts.join(", ")}</div>`;
  }
  if (row.freshness_gate_checked_at) {
    detail += `<div class="detail-diff">Freshness checked ${_relativeTime(row.freshness_gate_checked_at)}</div>`;
  }
  if (!detail) detail = '<div class="empty">No additional details</div>';
  tr.insertAdjacentHTML("afterend", `<tr class="detail-row"><td colspan="7">${detail}</td></tr>`);
}

function _relativeTime(iso) {
  if (!iso) return "—";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "just now";
  const mins = Math.floor(diff / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("pr-body").addEventListener("click", event => {
    const tr = event.target.closest("tr");
    if (!tr || tr.classList.contains("detail-row")) return;
    const rowId = tr.dataset.rowId;
    if (!rowId) return;

    const existing = document.querySelector(".detail-row");
    if (existing) existing.remove();

    if (_expandedId === rowId) { _expandedId = null; return; }

    const trivySummary = tr.dataset.trivySummary ? JSON.parse(tr.dataset.trivySummary) : null;
    const row = {
      drift_summary: tr.dataset.driftSummary || "",
      trivy_summary: trivySummary,
      freshness_gate_checked_at: tr.dataset.freshnessCheckedAt || null,
    };
    _expandRow(row, tr);
    _expandedId = rowId;
  });

  document.getElementById("btn-prev").addEventListener("click", async () => {
    if (_page <= 1) return;
    _page--;
    await _loadPage();
  });

  document.getElementById("btn-next").addEventListener("click", async () => {
    if (!_hasMore) return;
    _page++;
    await _loadPage();
  });

  document.getElementById("filter-pr-type").addEventListener("change", e => {
    filters.pr_type = e.target.value;
    _onPrFilterChange();
  });

  document.querySelectorAll(".pill").forEach(pill => {
    pill.addEventListener("click", () => {
      const st = pill.dataset.status;
      if (filters.status === st) {
        filters.status = "";
        pill.classList.remove("active");
      } else {
        filters.status = st;
        document.querySelectorAll(".pill").forEach(p => p.classList.remove("active"));
        pill.classList.add("active");
      }
      _onPrFilterChange();
    });
  });

  _applyPrFiltersFromURL();
  _syncPrControls();
  _onPrFilterChange();
});
