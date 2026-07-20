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

async function fetchMostDrifted(scope, days) {
  const { data, error } = await _supabase()
    .rpc("get_most_drifted", { p_account: scope, p_days: days });

  if (error) throw error;
  return data || [];
}

async function fetchMttr(scope, days) {
  const { data, error } = await _supabase()
    .rpc("get_mttr_by_severity", { p_account: scope, p_days: days });

  if (error) throw error;
  return data || [];
}

async function fetchRollbackFrequency(scope, days) {
  let q = _supabase()
    .from("drift_events")
    .select("*", { count: "exact", head: true })
    .eq("account", scope)
    .eq("pr_type", "rollback");

  if (days > 0) {
    const since = new Date(Date.now() - days * 86400000).toISOString();
    q = q.gte("created_at", since);
  }

  const { count, error } = await q;
  if (error) throw error;
  return count;
}

var _mostDriftedChart = null;

function _truncateLabel(text, max) {
  if (!text || text.length <= max) return text;
  return text.slice(0, max - 1) + "…";
}

function renderMostDrifted(data) {
  var canvas = document.getElementById("most-drifted-chart");
  var card = document.getElementById("most-drifted-card");
  var body = card ? card.querySelector(".card-body") : null;

  if (!data || data.length === 0) {
    if (_mostDriftedChart) { _mostDriftedChart.destroy(); _mostDriftedChart = null; }
    if (body) body.innerHTML = '<div class="empty">No drift events found for this period.</div>';
    return;
  }

  // Recreate canvas if a prior empty/error state destroyed it.
  if (!canvas) {
    canvas = document.createElement("canvas");
    canvas.id = "most-drifted-chart";
  }
  if (body) { body.innerHTML = ""; body.appendChild(canvas); }

  if (_mostDriftedChart) { _mostDriftedChart.destroy(); _mostDriftedChart = null; }

  var labels = data.map(function(r) { return _truncateLabel(r.resource_id, 30); });
  var values = data.map(function(r) { return r.drift_count; });
  var fullLabels = data.map(function(r) { return r.resource_id; });

  _mostDriftedChart = new Chart(canvas, {
    type: "bar",
    data: {
      labels: labels,
      datasets: [{
        label: "Drifts",
        data: values,
        backgroundColor: "rgba(239, 68, 68, 0.7)",
        borderColor: "rgba(239, 68, 68, 1)",
        borderWidth: 1
      }]
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        tooltip: {
          callbacks: {
            title: function(ctx) { return fullLabels[ctx[0].dataIndex]; }
          }
        },
        legend: { display: false }
      },
      scales: {
        x: {
          beginAtZero: true,
          ticks: { stepSize: 1, color: "#9ca3af" },
          grid: { color: "rgba(156, 163, 175, 0.15)" },
          title: { display: true, text: "Drift count", color: "#9ca3af" }
        },
        y: {
          ticks: { color: "#9ca3af" },
          grid: { display: false }
        }
      }
    }
  });
}

var _mttrChart = null;

var _SEVERITY_COLORS = {
  HIGH:   { bg: "rgba(248, 81, 73, 0.7)", border: "#f85149" },
  MEDIUM: { bg: "rgba(210, 153, 34, 0.7)", border: "#d29922" },
  LOW:    { bg: "rgba(139, 148, 158, 0.7)", border: "#8b949e" }
};

var _ALL_SEVERITIES = ["HIGH", "MEDIUM", "LOW"];

function renderMttr(data) {
  var canvas = document.getElementById("mttr-chart");
  var card = document.getElementById("mttr-card");
  var body = card ? card.querySelector(".card-body") : null;

  if (!data || data.length === 0) {
    if (_mttrChart) { _mttrChart.destroy(); _mttrChart = null; }
    if (body) body.innerHTML = '<div class="empty">No resolved drift events for this period.</div>';
    return;
  }

  if (!canvas) {
    canvas = document.createElement("canvas");
    canvas.id = "mttr-chart";
  }
  if (body) { body.innerHTML = ""; body.appendChild(canvas); }

  if (_mttrChart) { _mttrChart.destroy(); _mttrChart = null; }

  // Build a lookup from the RPC result, filling zeroes for missing severities.
  var lookup = {};
  for (var i = 0; i < data.length; i++) {
    lookup[data[i].severity] = { hours: data[i].avg_hours, count: data[i].count };
  }

  var labels = [];
  var values = [];
  var bgColors = [];
  var borderColors = [];
  var tipLines = [];

  for (var s = 0; s < _ALL_SEVERITIES.length; s++) {
    var sev = _ALL_SEVERITIES[s];
    var entry = lookup[sev];
    labels.push(sev);
    if (entry && entry.count > 0) {
      values.push(entry.hours);
      tipLines.push(entry.count + " resolved");
    } else {
      values.push(0);
      tipLines.push("No resolved incidents");
    }
    var c = _SEVERITY_COLORS[sev] || _SEVERITY_COLORS.LOW;
    bgColors.push(c.bg);
    borderColors.push(c.border);
  }

  _mttrChart = new Chart(canvas, {
    type: "bar",
    data: {
      labels: labels,
      datasets: [{
        label: "Avg Hours",
        data: values,
        backgroundColor: bgColors,
        borderColor: borderColors,
        borderWidth: 1
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        tooltip: {
          callbacks: {
            label: function(ctx) {
              var sev = _ALL_SEVERITIES[ctx.dataIndex];
              var entry = lookup[sev];
              if (entry && entry.count > 0) {
                var h = entry.avg_hours;
                var timeStr = h >= 1 ? h + " hrs" : Math.round(h * 60) + " min";
                return timeStr + " (" + entry.count + " resolved)";
              }
              return "No resolved incidents";
            }
          }
        },
        legend: { display: false }
      },
      scales: {
        x: {
          ticks: { color: "#9ca3af" },
          grid: { display: false }
        },
        y: {
          beginAtZero: true,
          ticks: { color: "#9ca3af" },
          grid: { color: "rgba(156, 163, 175, 0.15)" },
          title: { display: true, text: "Avg hours", color: "#9ca3af" }
        }
      }
    }
  });
}

function renderRollbackFrequency(count) {
  var el = document.getElementById("rollback-frequency-card");
  if (!el) return;
  if (count === null || count === undefined) {
    el.innerHTML = '<div class="error">Failed to load rollback data.</div>';
    return;
  }
  if (count === 0) {
    el.innerHTML = '<div class="empty">No rollback PRs in selected period.</div>';
    return;
  }
  var label = count === 1 ? "rollback PR" : "rollback PRs";
  el.innerHTML = '<div class="stat-value">' + count + '</div><div class="stat-label">' + label + ' in selected period</div>';
}

var _driftVolumeChart = null;

function renderDriftVolume(data) {
  var canvas = document.getElementById("drift-volume-chart");
  var card = document.getElementById("drift-volume-card");
  var body = card ? card.querySelector(".card-body") : null;

  if (!data || data.length === 0) {
    if (_driftVolumeChart) { _driftVolumeChart.destroy(); _driftVolumeChart = null; }
    if (body) body.innerHTML = '<div class="empty">No drift events in selected period.</div>';
    return;
  }

  if (!canvas) {
    canvas = document.createElement("canvas");
    canvas.id = "drift-volume-chart";
  }
  if (body) { body.innerHTML = ""; body.appendChild(canvas); }

  if (_driftVolumeChart) { _driftVolumeChart.destroy(); _driftVolumeChart = null; }

  // Build lookup + determine range bounds.
  var lookup = {};
  for (var i = 0; i < data.length; i++) {
    lookup[data[i].day] = data[i].count;
  }

  var dates = [];
  if (_currentDays > 0) {
    // Fixed lookback window — fill every day.
    for (var d = _currentDays - 1; d >= 0; d--) {
      var dt = new Date(Date.now() - d * 86400000);
      dates.push(dt.toISOString().slice(0, 10));
    }
  } else {
    // All-time — use min/max from the data as range.
    var minDay = data.length ? data[0].day : null;
    var maxDay = data.length ? data[data.length - 1].day : null;
    if (minDay && maxDay) {
      var cur = new Date(minDay + "T00:00:00Z");
      var end = new Date(maxDay + "T00:00:00Z");
      while (cur <= end) {
        dates.push(cur.toISOString().slice(0, 10));
        cur.setUTCDate(cur.getUTCDate() + 1);
      }
    }
  }

  var labels = [];
  var values = [];
  for (var j = 0; j < dates.length; j++) {
    var day = dates[j];
    labels.push(day);
    values.push(lookup[day] || 0);
  }

  _driftVolumeChart = new Chart(canvas, {
    type: "line",
    data: {
      labels: labels,
      datasets: [{
        label: "Events",
        data: values,
        fill: true,
        backgroundColor: "rgba(239, 68, 68, 0.08)",
        borderColor: "rgba(239, 68, 68, 0.8)",
        borderWidth: 2,
        pointBackgroundColor: "rgba(239, 68, 68, 1)",
        pointRadius: 3,
        pointHoverRadius: 5,
        tension: 0.2
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        tooltip: {
          callbacks: {
            label: function(ctx) {
              var n = ctx.parsed.y;
              return n + " event" + (n !== 1 ? "s" : "");
            }
          }
        },
        legend: { display: false }
      },
      scales: {
        x: {
          ticks: { color: "#9ca3af", maxTicksLimit: 14 },
          grid: { color: "rgba(156, 163, 175, 0.15)" },
          title: { display: true, text: "Date", color: "#9ca3af" }
        },
        y: {
          beginAtZero: true,
          ticks: { stepSize: 1, color: "#9ca3af" },
          grid: { color: "rgba(156, 163, 175, 0.15)" },
          title: { display: true, text: "Events", color: "#9ca3af" }
        }
      }
    }
  });
}

async function fetchDriftVolume(scope, days) {
  const { data, error } = await _supabase()
    .rpc("get_drift_volume_daily", { p_account: scope, p_days: days });

  if (error) throw error;
  return data || [];
}

async function fetchSummary(scope, days) {
  var q = _supabase()
    .from("drift_events")
    .select("id,resource_id,status")
    .eq("account", scope);

  if (days > 0) {
    var since = new Date(Date.now() - days * 86400000).toISOString();
    q = q.gte("created_at", since);
  }

  // Pull up to 1000 rows — fine for a trends summary.
  var all = [];
  var from = 0;
  var pageSize = 1000;
  while (true) {
    var { data, error } = await q.range(from, from + pageSize - 1);
    if (error) throw error;
    if (!data || data.length === 0) break;
    all = all.concat(data);
    if (data.length < pageSize) break;
    from += pageSize;
  }

  var total = all.length;
  var uniqueResources = new Set(all.map(function(r) { return r.resource_id; })).size;
  var resolved = all.filter(function(r) { return r.status === "resolved"; }).length;
  return { total: total, uniqueResources: uniqueResources, resolved: resolved, unresolved: total - resolved };
}

function renderSummary(data) {
  if (!data || data.total === null || data.total === undefined) {
    ["total-drifts-card", "unique-resources-card", "resolved-card", "unresolved-card"].forEach(function(id) {
      var card = document.getElementById(id);
      if (!card) return;
      var body = card.querySelector(".card-body");
      if (body) body.innerHTML = '<div class="error">Failed to load.</div>';
    });
    return;
  }
  _renderKpi("total-drifts-card", data.total, "drift events");
  _renderKpi("unique-resources-card", data.uniqueResources, "unique resources");
  _renderKpi("resolved-card", data.resolved, "resolved");
  _renderKpi("unresolved-card", data.unresolved, "unresolved", data.unresolved > 0);
}

function _renderKpi(cardId, value, label, needsAttention) {
  var card = document.getElementById(cardId);
  if (!card) return;
  var body = card.querySelector(".card-body");
  if (!body) return;
  if (needsAttention) card.classList.add("needs-attention");
  else card.classList.remove("needs-attention");
  body.innerHTML = '<div class="metric-large">' + value + '</div><div class="metric-sub">' + label + '</div>';
}

var _currentScope = "scope-a";
var _currentDays = 90;
var _refreshToken = 0;

async function refreshAll(scope, days) {
  _currentScope = scope;
  _currentDays = days;
  _syncURL(scope, days);

  var token = ++_refreshToken;

  var results = await Promise.allSettled([
    fetchMostDrifted(scope, days),
    fetchMttr(scope, days),
    fetchRollbackFrequency(scope, days),
    fetchDriftVolume(scope, days),
    fetchSummary(scope, days)
  ]);

  // Ignore stale renders — if a newer refreshAll fired while this one
  // was in flight, discard this run's results.
  if (token !== _refreshToken) {
    console.log("refreshAll: discarding stale results (token " + token + " vs " + _refreshToken + ")");
    return;
  }

  if (results[0].status === "fulfilled") renderMostDrifted(results[0].value);
  else _showCardError("most-drifted-card", "Failed to load most-drifted data.");

  if (results[1].status === "fulfilled") renderMttr(results[1].value);
  else _showCardError("mttr-card", "Failed to load MTTR data.");

  if (results[2].status === "fulfilled") renderRollbackFrequency(results[2].value);
  else _showCardError("rollback-card", "Failed to load rollback data.");

  if (results[3].status === "fulfilled") renderDriftVolume(results[3].value);
  else _showCardError("drift-volume-card", "Failed to load drift volume data.");

  if (results[4].status === "fulfilled") renderSummary(results[4].value);
  else {
    ["total-drifts-card", "unique-resources-card", "resolved-card", "unresolved-card"].forEach(function(id) {
      _showCardError(id, "Failed to load.");
    });
  }
}

function _showCardError(cardId, msg) {
  var card = document.getElementById(cardId);
  if (!card) return;
  var body = card.querySelector(".card-body");
  if (body) body.innerHTML = '<div class="error">' + msg + '</div>';
}

function _syncURL(scope, days) {
  var url = new URL(window.location);
  if (scope && scope !== "scope-a") url.searchParams.set("scope", scope);
  else url.searchParams.delete("scope");
  if (days !== 90) url.searchParams.set("days", String(days));
  else url.searchParams.delete("days");
  window.history.replaceState(null, "", url);
}

function _readURL() {
  var p = new URLSearchParams(window.location.search);
  var scope = p.get("scope") || "scope-a";
  var days = parseInt(p.get("days"), 10) || 90;
  return { scope: scope, days: days };
}

function _syncControls() {
  document.querySelectorAll(".scope-tab").forEach(function(t) {
    t.classList.toggle("active", t.dataset.scope === _currentScope);
  });
  var sel = document.getElementById("trends-days");
  if (sel) sel.value = String(_currentDays);
}

document.addEventListener("DOMContentLoaded", function() {
  var initial = _readURL();
  _currentScope = initial.scope;
  _currentDays = initial.days;
  _syncControls();

  // Scope tabs
  document.querySelectorAll(".scope-tab").forEach(function(tab) {
    tab.addEventListener("click", function() {
      var scope = tab.dataset.scope;
      if (scope === _currentScope) return;
      document.querySelectorAll(".scope-tab").forEach(function(t) { t.classList.remove("active"); });
      tab.classList.add("active");
      refreshAll(scope, _currentDays);
    });
  });

  // Date-range dropdown
  var daysSel = document.getElementById("trends-days");
  if (daysSel) {
    daysSel.addEventListener("change", function() {
      var days = parseInt(daysSel.value, 10);
      refreshAll(_currentScope, days);
    });
  }

  refreshAll(_currentScope, _currentDays);
});
