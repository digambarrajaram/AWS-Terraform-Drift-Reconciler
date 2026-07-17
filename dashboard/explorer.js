const GITHUB_REPO = "digambarrajaram/AWS-Terraform-Drift-Reconciler";

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

/**
 * Build (but do not execute) a Supabase query for drift_events.
 *
 * filters       { severity, account, status, pr_type, search }
 * offset        0-based row offset (page size is 50)
 */
function buildQuery(filters, offset) {
  let q = _supabase()
    .from("drift_events")
    .select(
      "id,created_at,account,resource_id,severity,status,pr_type,pr_number,drift_summary,changes_jsonb,resolution"
    );

  if (filters.severity) {
    q = q.eq("severity", filters.severity);
  }
  if (filters.account) {
    q = q.eq("account", filters.account);
  }
  if (filters.status) {
    q = q.eq("status", filters.status);
  }
  if (filters.pr_type) {
    q = q.eq("pr_type", filters.pr_type);
  }
  if (filters.search) {
    q = q.ilike("resource_id", `%${filters.search}%`);
  }

  return q.order("created_at", { ascending: false }).range(offset, offset + 49);
}

/**
 * Execute the built query and return rows + pagination flag.
 */
async function fetchFindings(filters, offset = 0) {
  const { data, error } = await buildQuery(filters, offset);
  if (error) throw error;
  return { rows: data || [], hasMore: (data || []).length === 50 };
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

/**
 * Return an HTML string for one <tr> representing *row*.
 */
function renderRow(row) {
  const sev = (row.severity || "LOW").toLowerCase();
  const open = row.status === "open";
  const rowClass = open ? ` class="row-open sev-${sev}"` : "";

  const prLink = row.pr_number
    ? `<a href="https://github.com/${GITHUB_REPO}/pull/${row.pr_number}" target="_blank">#${row.pr_number}</a>`
    : "—";

  const changesJsonb = row.changes_jsonb
    ? JSON.stringify(row.changes_jsonb).replace(/"/g, "&quot;")
    : "";
  const summary = (row.drift_summary || "").replace(/"/g, "&quot;");

  return `<tr${rowClass}
    data-row-id="${row.id || ""}"
    data-drift-summary="${summary}"
    data-changes-jsonb="${changesJsonb}">
    <td>${_relativeTime(row.created_at)}</td>
    <td>\`${row.resource_id || "?"}\`</td>
    <td><span class="sev-${sev}">${row.severity || "LOW"}</span></td>
    <td><span class="badge-status-${row.status || "?"}">${row.status || "?"}</span></td>
    <td>${prLink}</td>
  </tr>`;
}

/**
 * Return an HTML string for a detail row showing drift_summary and
 * a per-field before→after diff from changes_jsonb.
 */
function renderExpanded(row) {
  let diff = "";
  const changes = row.changes_jsonb;
  if (changes && typeof changes === "object" && Object.keys(changes).length > 0) {
    const lines = Object.entries(changes).map(([field, vals]) => {
      const b = vals?.before ?? "—";
      const a = vals?.after ?? "—";
      return `${field}: ${b} → ${a}`;
    });
    diff = lines.join("<br>");
  }

  const summary = row.drift_summary || row.resolution || "";

  return `<tr class="detail-row"><td colspan="5">
    <div class="detail-summary">${summary}</div>
    ${diff ? `<div class="detail-diff">${diff}</div>` : ""}
  </td></tr>`;
}

/**
 * Populate #findings-body with *rows*.  Pass { append, hasMore } as opts.
 */
function renderTable(rows, { append = false, hasMore = false } = {}) {
  const tbody = document.getElementById("findings-body");
  const loadMore = document.getElementById("load-more");

  if (!append) {
    tbody.innerHTML = "";
  }

  if (!rows || rows.length === 0) {
    if (!append) {
      tbody.innerHTML =
        `<tr><td colspan="5" class="empty">No findings match these filters</td></tr>`;
    }
    loadMore.hidden = true;
    return;
  }

  tbody.insertAdjacentHTML("beforeend", rows.map(renderRow).join(""));
  loadMore.hidden = !hasMore;
}

/**
 * Fetch and render findings for the given filters, handling errors.
 */
async function loadFindings(filters, { append = false, offset = 0 } = {}) {
  const banner = document.getElementById("error-banner");
  try {
    const { rows, hasMore } = await fetchFindings(filters, offset);
    banner.hidden = true;
    renderTable(rows, { append, hasMore });
  } catch (err) {
    banner.hidden = false;
    banner.textContent = err.message || "Failed to load findings";
  }
}

// ---------------------------------------------------------------------------
// Expand / collapse
// ---------------------------------------------------------------------------

let _expandedId = null;

function _onRowClick(event) {
  // Find the closest <tr> (excluding detail rows themselves).
  const tr = event.target.closest("tr");
  if (!tr || tr.classList.contains("detail-row")) return;

  const rowId = tr.dataset.rowId;
  if (!rowId) return;

  // Collapse any currently-expanded row.
  const existing = document.querySelector(".detail-row");
  if (existing) existing.remove();

  // If clicking the already-expanded row, just collapse.
  if (_expandedId === rowId) {
    _expandedId = null;
    return;
  }

  // Rebuild the row data from its dataset attributes.
  const changesJsonb = tr.dataset.changesJsonb
    ? JSON.parse(tr.dataset.changesJsonb)
    : null;
  const row = {
    drift_summary: tr.dataset.driftSummary || "",
    changes_jsonb: changesJsonb,
  };

  const detailHtml = renderExpanded(row);
  tr.insertAdjacentHTML("afterend", detailHtml);
  _expandedId = rowId;
}

// ---------------------------------------------------------------------------
// Filters
// ---------------------------------------------------------------------------

const filters = {
  severity: "",
  account: "scope-a",
  status: ["open"],
  pr_type: "",
  search: "",
};

let _searchTimer = null;
let _offset = 0;

async function _onFilterChange() {
  _offset = 0;

  // Sync URL without reload.
  const url = new URL(window.location);
  if (filters.severity) url.searchParams.set("severity", filters.severity);
  else url.searchParams.delete("severity");
  if (filters.account && filters.account !== "scope-a") url.searchParams.set("account", filters.account);
  else url.searchParams.delete("account");
  if (filters.status.length === 1) url.searchParams.set("status", filters.status[0]);
  else if (filters.status.length > 1) url.searchParams.set("status", filters.status.join(","));
  else url.searchParams.delete("status");
  if (filters.search) url.searchParams.set("q", filters.search);
  else url.searchParams.delete("q");
  window.history.replaceState(null, "", url);

  const { rows, hasMore } = await fetchFindings(filters, 0);
  renderTable(rows, { append: false, hasMore });
}

function _applyFiltersFromURL() {
  const p = new URLSearchParams(window.location.search);
  const sev = p.get("severity");
  if (sev) filters.severity = sev;
  const acct = p.get("account");
  if (acct) filters.account = acct;
  const st = p.get("status");
  if (st) filters.status = st.split(",");
  const q = p.get("q");
  if (q) filters.search = q;
}

function _syncControlsFromFilters() {
  document.getElementById("filter-severity").value = filters.severity;

  document.querySelectorAll(".scope-tab").forEach(t => {
    t.classList.toggle("active", t.dataset.scope === filters.account);
  });

  document.querySelectorAll(".pill").forEach(pill => {
    const active = filters.status.includes(pill.dataset.status);
    pill.classList.toggle("active", active);
  });

  document.getElementById("search-resource").value = filters.search;
}

function _setupFilters() {
  // Severity dropdown
  document.getElementById("filter-severity").addEventListener("change", (e) => {
    filters.severity = e.target.value;
    _onFilterChange();
  });

  // Account tabs
  document.querySelectorAll(".scope-tab").forEach(tab => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".scope-tab").forEach(t => t.classList.remove("active"));
      tab.classList.add("active");
      filters.account = tab.dataset.scope;
      _onFilterChange();
    });
  });

  // Status pills (multi-select)
  document.querySelectorAll(".pill").forEach(pill => {
    pill.addEventListener("click", () => {
      const status = pill.dataset.status;
      pill.classList.toggle("active");
      if (pill.classList.contains("active")) {
        if (!filters.status.includes(status)) filters.status.push(status);
      } else {
        filters.status = filters.status.filter(s => s !== status);
      }
      _onFilterChange();
    });
  });

  // Search input (debounced 300ms)
  const searchInput = document.getElementById("search-resource");
  searchInput.addEventListener("input", (e) => {
    clearTimeout(_searchTimer);
    _searchTimer = setTimeout(() => {
      filters.search = e.target.value.trim();
      _onFilterChange();
    }, 300);
  });
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  _setupFilters();
  _applyFiltersFromURL();
  _syncControlsFromFilters();
  document.getElementById("findings-body").addEventListener("click", _onRowClick);

  document.getElementById("load-more").addEventListener("click", async () => {
    _offset += 50;
    const { rows, hasMore } = await fetchFindings(filters, _offset);
    renderTable(rows, { append: true, hasMore });
  });

  // Initial fetch.
  _onFilterChange();
});
