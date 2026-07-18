"use strict";

const PALETTE = [
  "#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed",
  "#0891b2", "#db2777", "#65a30d", "#ea580c", "#4f46e5",
  "#0d9488", "#c026d3",
];

const state = {
  meta: null,
  season: null,
  activeTab: "standings",
};

async function api(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

function fmt(v, digits = 3) {
  if (v === null || v === undefined) return "-";
  if (typeof v === "number") return Number.isInteger(v) ? v : v.toFixed(digits);
  return v;
}

// ---------------------------------------------------------------------
// Generic sortable table
// ---------------------------------------------------------------------

function renderTable(container, columns, rows, opts = {}) {
  if (!rows.length) {
    container.innerHTML = `<div class="empty">${opts.emptyText || "No data yet."}</div>`;
    return;
  }
  let sortKey = opts.defaultSort || columns[0].key;
  let sortAsc = opts.defaultAsc || false;

  function draw() {
    const sorted = [...rows].sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      if (av === bv) return 0;
      if (av === null || av === undefined) return 1;
      if (bv === null || bv === undefined) return -1;
      return (av > bv ? 1 : -1) * (sortAsc ? 1 : -1);
    });

    const thead = columns
      .map((c) => {
        const cls = c.key === sortKey ? `sorted${sortAsc ? " asc" : ""}` : "";
        return `<th data-key="${c.key}" class="${cls}">${c.label}</th>`;
      })
      .join("");

    const tbody = sorted
      .map((row) => {
        const cells = columns.map((c) => `<td>${c.render ? c.render(row) : fmt(row[c.key])}</td>`).join("");
        return opts.rowWrap ? opts.rowWrap(row, cells, columns) : `<tr>${cells}</tr>`;
      })
      .join("");

    container.innerHTML = `<table><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table>`;

    container.querySelectorAll("th").forEach((th) => {
      th.onclick = () => {
        const key = th.dataset.key;
        if (key === sortKey) sortAsc = !sortAsc;
        else {
          sortKey = key;
          sortAsc = true;
        }
        draw();
        if (opts.afterDraw) opts.afterDraw(container);
      };
    });
    if (opts.afterDraw) opts.afterDraw(container);
  }

  draw();
}

// ---------------------------------------------------------------------
// Tooltip (shared across charts)
// ---------------------------------------------------------------------

let tooltipEl = null;
function tooltip() {
  if (!tooltipEl) {
    tooltipEl = document.createElement("div");
    tooltipEl.style.cssText =
      "position:fixed;pointer-events:none;background:#111;color:#fff;padding:4px 8px;" +
      "border-radius:4px;font-size:12px;z-index:1000;display:none;white-space:nowrap;";
    document.body.appendChild(tooltipEl);
  }
  return tooltipEl;
}
function showTooltip(x, y, text) {
  const el = tooltip();
  el.textContent = text;
  el.style.left = `${x + 12}px`;
  el.style.top = `${y + 12}px`;
  el.style.display = "block";
}
function hideTooltip() {
  if (tooltipEl) tooltipEl.style.display = "none";
}

// ---------------------------------------------------------------------
// Line chart (rank-over-time)
// ---------------------------------------------------------------------

function createLineChart(canvas, legendEl, opts = {}) {
  let seriesList = [];
  const hidden = new Set();
  let hitPoints = [];

  function renderLegend() {
    legendEl.innerHTML = "";
    seriesList.forEach((s) => {
      const span = document.createElement("span");
      span.className = hidden.has(s.key) ? "off" : "";
      span.innerHTML = `<span class="swatch" style="background:${s.color}"></span>${s.name}`;
      span.onclick = () => {
        hidden.has(s.key) ? hidden.delete(s.key) : hidden.add(s.key);
        renderLegend();
        draw();
      };
      legendEl.appendChild(span);
    });
  }

  function draw() {
    const ctx = canvas.getContext("2d");
    const W = canvas.width,
      H = canvas.height;
    ctx.clearRect(0, 0, W, H);
    hitPoints = [];

    const visible = seriesList.filter((s) => !hidden.has(s.key));
    if (!visible.length) {
      ctx.fillStyle = "#888";
      ctx.font = "13px sans-serif";
      ctx.fillText("No data yet.", W / 2 - 30, H / 2);
      return;
    }

    const allDates = [...new Set(visible.flatMap((s) => s.points.map((p) => p.x)))].sort();
    const allY = visible.flatMap((s) => s.points.map((p) => p.y));
    let yMin = Math.min(...allY),
      yMax = Math.max(...allY);
    if (yMin === yMax) {
      yMin -= 1;
      yMax += 1;
    }
    if (opts.invert) {
      yMin -= 0.5;
      yMax += 0.5;
    }

    const pad = { left: 40, right: 20, top: 16, bottom: 30 };
    const plotW = W - pad.left - pad.right;
    const plotH = H - pad.top - pad.bottom;

    const xPos = (d) => {
      const idx = allDates.indexOf(d);
      return pad.left + (allDates.length <= 1 ? plotW / 2 : (idx / (allDates.length - 1)) * plotW);
    };
    const yPos = (v) => {
      const t = (v - yMin) / (yMax - yMin);
      return opts.invert ? pad.top + t * plotH : pad.top + (1 - t) * plotH;
    };

    ctx.strokeStyle = "#8888";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad.left, pad.top);
    ctx.lineTo(pad.left, H - pad.bottom);
    ctx.lineTo(W - pad.right, H - pad.bottom);
    ctx.stroke();

    ctx.fillStyle = "#888";
    ctx.font = "11px sans-serif";
    const ticks = 5;
    for (let i = 0; i <= ticks; i++) {
      const v = yMin + ((yMax - yMin) * i) / ticks;
      const y = yPos(v);
      ctx.fillText(Math.round(v * 10) / 10, 4, y + 3);
      ctx.strokeStyle = "#88888822";
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(W - pad.right, y);
      ctx.stroke();
    }

    const labelEvery = Math.ceil(allDates.length / 8) || 1;
    allDates.forEach((d, i) => {
      if (i % labelEvery === 0) ctx.fillText(d.slice(5), xPos(d) - 15, H - pad.bottom + 15);
    });

    visible.forEach((s) => {
      ctx.strokeStyle = s.color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      s.points.forEach((p, i) => {
        const x = xPos(p.x),
          y = yPos(p.y);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.fillStyle = s.color;
      s.points.forEach((p) => {
        const x = xPos(p.x),
          y = yPos(p.y);
        ctx.beginPath();
        ctx.arc(x, y, 3, 0, Math.PI * 2);
        ctx.fill();
        hitPoints.push({ x, y, label: `${s.name} · ${p.x} · ${opts.yLabel || ""} ${p.y}` });
      });
    });
  }

  canvas.addEventListener("mousemove", (e) => {
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    const mx = (e.clientX - rect.left) * scaleX;
    const my = (e.clientY - rect.top) * scaleY;
    let nearest = null,
      bestDist = 100;
    for (const p of hitPoints) {
      const d = Math.hypot(p.x - mx, p.y - my);
      if (d < bestDist) {
        bestDist = d;
        nearest = p;
      }
    }
    if (nearest) showTooltip(e.clientX, e.clientY, nearest.label);
    else hideTooltip();
  });
  canvas.addEventListener("mouseleave", hideTooltip);

  return {
    setData(list) {
      seriesList = list.map((s, i) => ({ ...s, color: PALETTE[i % PALETTE.length] }));
      hidden.clear();
      renderLegend();
      draw();
    },
  };
}

// ---------------------------------------------------------------------
// Bar chart (category win rates)
// ---------------------------------------------------------------------

function drawBarChart(canvas, bars) {
  const ctx = canvas.getContext("2d");
  const W = canvas.width,
    H = canvas.height;
  ctx.clearRect(0, 0, W, H);

  if (!bars.length) {
    ctx.fillStyle = "#888";
    ctx.font = "13px sans-serif";
    ctx.fillText("No data yet.", W / 2 - 30, H / 2);
    return;
  }

  const pad = { left: 40, right: 20, top: 16, bottom: 60 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;
  const barW = (plotW / bars.length) * 0.7;
  const gap = (plotW / bars.length) * 0.3;
  const maxVal = 1;

  ctx.strokeStyle = "#8888";
  ctx.beginPath();
  ctx.moveTo(pad.left, pad.top);
  ctx.lineTo(pad.left, H - pad.bottom);
  ctx.lineTo(W - pad.right, H - pad.bottom);
  ctx.stroke();

  ctx.fillStyle = "#888";
  ctx.font = "11px sans-serif";
  for (let i = 0; i <= 4; i++) {
    const v = (maxVal * i) / 4;
    const y = pad.top + (1 - v) * plotH;
    ctx.fillText(`${Math.round(v * 100)}%`, 4, y + 3);
    ctx.strokeStyle = "#88888822";
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(W - pad.right, y);
    ctx.stroke();
  }

  const hitBars = [];
  bars.forEach((b, i) => {
    const x = pad.left + i * (barW + gap) + gap / 2;
    const h = (b.value / maxVal) * plotH;
    const y = H - pad.bottom - h;
    ctx.fillStyle = PALETTE[i % PALETTE.length];
    ctx.fillRect(x, y, barW, h);
    hitBars.push({ x, y, w: barW, h, label: `${b.label}: ${Math.round(b.value * 100)}%` });

    ctx.save();
    ctx.translate(x + barW / 2, H - pad.bottom + 8);
    ctx.rotate((-30 * Math.PI) / 180);
    ctx.fillStyle = "#888";
    ctx.textAlign = "right";
    ctx.fillText(b.label, 0, 0);
    ctx.restore();
  });

  canvas.onmousemove = (e) => {
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    const mx = (e.clientX - rect.left) * scaleX;
    const my = (e.clientY - rect.top) * scaleY;
    const hit = hitBars.find((b) => mx >= b.x && mx <= b.x + b.w && my >= b.y && my <= H - pad.bottom);
    if (hit) showTooltip(e.clientX, e.clientY, hit.label);
    else hideTooltip();
  };
  canvas.onmouseleave = hideTooltip;
}

// ---------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------

function setupTabs() {
  document.querySelectorAll("nav.tabs button").forEach((btn) => {
    btn.onclick = () => {
      document.querySelectorAll("nav.tabs button").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById(`panel-${btn.dataset.tab}`).classList.add("active");
      state.activeTab = btn.dataset.tab;
      renderActiveTab();
    };
  });
}

function renderActiveTab() {
  const fns = {
    standings: renderStandings,
    matchups: renderMatchups,
    h2h: renderH2H,
    categories: renderCategories,
    transactions: renderTransactions,
    history: renderHistory,
  };
  (fns[state.activeTab] || (() => {}))();
}

// ---------------------------------------------------------------------
// Standings tab
// ---------------------------------------------------------------------

let standingsChart = null;

async function renderStandings() {
  const container = document.getElementById("standings-table");
  const data = await api(`/api/standings?season=${state.season}`);
  const columns = [
    { key: "rank", label: "Rank" },
    { key: "name", label: "Team" },
    { key: "manager_nickname", label: "Manager" },
    { key: "wins", label: "W" },
    { key: "losses", label: "L" },
    { key: "ties", label: "T" },
    { key: "pct", label: "Pct", render: (r) => fmt(r.pct, 3) },
    { key: "games_back", label: "GB" },
    { key: "playoff_seed", label: "Seed" },
  ];
  renderTable(container, columns, data.standings, { defaultSort: "rank", defaultAsc: true });

  if (!standingsChart) {
    standingsChart = createLineChart(
      document.getElementById("standings-chart"),
      document.getElementById("standings-legend"),
      { invert: true, yLabel: "rank" }
    );
  }
  const timeline = await api(`/api/standings/timeline?season=${state.season}`);
  const series = Object.entries(timeline.teams).map(([key, t]) => ({
    key,
    name: t.name,
    points: t.points.map((p) => ({ x: p.date, y: p.rank })),
  }));
  standingsChart.setData(series);
}

// ---------------------------------------------------------------------
// Matchups tab
// ---------------------------------------------------------------------

async function renderMatchups() {
  const weekSel = document.getElementById("matchup-week-filter");
  const teamSel = document.getElementById("matchup-team-filter");
  populateTeamFilter(teamSel);

  const week = weekSel.value;
  const team = teamSel.value;
  let url = `/api/matchups?season=${state.season}`;
  if (week) url += `&week=${week}`;
  if (team) url += `&team=${team}`;
  const data = await api(url);

  if (!weekSel.dataset.populated) {
    const weeks = [...new Set(data.matchups.map((m) => m.week))].sort((a, b) => a - b);
    weeks.forEach((w) => weekSel.insertAdjacentHTML("beforeend", `<option value="${w}">Week ${w}</option>`));
    weekSel.dataset.populated = "1";
    weekSel.onchange = renderMatchups;
  }
  teamSel.onchange = renderMatchups;

  const container = document.getElementById("matchups-table");
  const columns = [
    { key: "week", label: "Wk" },
    { key: "team1_name", label: "Team 1" },
    { key: "team2_name", label: "Team 2" },
    {
      key: "winner_team_key",
      label: "Result",
      render: (r) => {
        if (r.is_tied) return "Tie";
        if (!r.winner_team_key) return r.status;
        const winnerIsTeam1 = r.winner_team_key === r.team1_key;
        return winnerIsTeam1 ? `<span class="win">${r.team1_name}</span> won` : `<span class="win">${r.team2_name}</span> won`;
      },
    },
    { key: "status", label: "Status" },
  ];
  renderTable(container, columns, data.matchups, {
    defaultSort: "week",
    defaultAsc: true,
    emptyText: "No matchups for this season yet -- run a pull or backfill.",
    rowWrap: (row, cells, columns) => {
      const catLines = (row.categories || [])
        .map(
          (c) =>
            `<div>${c.name}: ${c.team1_value} vs ${c.team2_value} ` +
            `${c.team1_won === 1 ? `(${row.team1_name} won)` : c.team1_won === 0 ? `(${row.team2_name} won)` : ""}</div>`
        )
        .join("");
      return (
        `<tr class="matchup-row">${cells}</tr>` +
        `<tr class="matchup-detail" style="display:none"><td colspan="${columns.length}">${catLines}</td></tr>`
      );
    },
    afterDraw: (el) => {
      el.querySelectorAll("tr.matchup-row").forEach((tr) => {
        tr.onclick = () => {
          const detail = tr.nextElementSibling;
          detail.style.display = detail.style.display === "none" ? "table-row" : "none";
        };
      });
    },
  });
}

// ---------------------------------------------------------------------
// Head-to-head tab
// ---------------------------------------------------------------------

async function renderH2H() {
  const allSeasons = document.getElementById("h2h-all-seasons");
  allSeasons.onchange = renderH2H;
  const url = allSeasons.checked ? "/api/h2h" : `/api/h2h?season=${state.season}`;
  const data = await api(url);
  const container = document.getElementById("h2h-table");
  const columns = [
    { key: "manager_a", label: "Manager A" },
    { key: "manager_b", label: "Manager B" },
    {
      key: "wins_a",
      label: "Record (A-B-T)",
      render: (r) => `${r.wins_a}-${r.wins_b}-${r.ties}`,
    },
  ];
  renderTable(container, columns, data.matchups, { emptyText: "No head-to-head history yet." });
}

// ---------------------------------------------------------------------
// Categories tab
// ---------------------------------------------------------------------

let categoryTrendChart = null;

async function renderCategories() {
  const statSel = document.getElementById("category-stat-filter");
  const cats = (state.meta.stat_categories || []).filter((c) => c.season_year === state.season);
  if (!statSel.dataset.season || statSel.dataset.season !== String(state.season)) {
    statSel.innerHTML = cats.map((c) => `<option value="${c.stat_id}">${c.display_name}</option>`).join("");
    statSel.dataset.season = String(state.season);
    statSel.onchange = renderCategories;
  }

  if (!categoryTrendChart) {
    categoryTrendChart = createLineChart(
      document.getElementById("category-trend-chart"),
      document.getElementById("category-trend-legend"),
      { invert: false, yLabel: "" }
    );
  }
  const statId = statSel.value ? Number(statSel.value) : null;
  if (statId) {
    const timeline = await api(`/api/stats/timeline?season=${state.season}&stat_id=${statId}`);
    const series = Object.entries(timeline.teams).map(([key, t]) => ({
      key,
      name: t.name,
      points: t.points.map((p) => ({ x: p.date, y: p.value })),
    }));
    categoryTrendChart.setData(series);
  } else {
    categoryTrendChart.setData([]);
  }

  const data = await api(`/api/categories?season=${state.season}`);
  const container = document.getElementById("categories-table");
  const columns = [
    { key: "name", label: "Team" },
    { key: "display_name", label: "Category" },
    { key: "wins", label: "Wins" },
    { key: "ties", label: "Ties" },
    { key: "played", label: "Played" },
    { key: "win_pct", label: "Win %", render: (r) => `${Math.round((r.wins / r.played) * 100) || 0}%` },
  ];
  renderTable(container, columns, data.categories, { emptyText: "No matchup category data yet." });

  const bars = data.categories
    .filter((c) => (statId ? c.stat_id === statId : true))
    .filter((c) => c.played > 0)
    .map((c) => ({ label: c.name, value: c.wins / c.played }));
  drawBarChart(document.getElementById("categories-chart"), statId ? bars : []);
}

// ---------------------------------------------------------------------
// Transactions tab
// ---------------------------------------------------------------------

async function renderTransactions() {
  const teamSel = document.getElementById("tx-team-filter");
  const typeSel = document.getElementById("tx-type-filter");
  const search = document.getElementById("tx-search");
  populateTeamFilter(teamSel);
  teamSel.onchange = renderTransactions;
  typeSel.onchange = renderTransactions;
  search.oninput = debounce(renderTransactions, 300);

  let url = `/api/transactions?season=${state.season}`;
  if (teamSel.value) url += `&team=${teamSel.value}`;
  if (typeSel.value) url += `&type=${encodeURIComponent(typeSel.value)}`;
  if (search.value) url += `&q=${encodeURIComponent(search.value)}`;
  const data = await api(url);

  const container = document.getElementById("transactions-table");
  const columns = [
    { key: "timestamp", label: "When", render: (r) => (r.timestamp ? new Date(r.timestamp * 1000).toLocaleDateString() : "-") },
    { key: "type", label: "Type" },
    {
      key: "players",
      label: "Players",
      render: (r) => r.players.map((p) => `${p.player_name} (${p.movement})`).join(", "),
    },
    { key: "status", label: "Status" },
  ];
  renderTable(container, columns, data.transactions, {
    defaultSort: "timestamp",
    emptyText: "No transactions recorded yet.",
  });
}

function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

// ---------------------------------------------------------------------
// History tab
// ---------------------------------------------------------------------

async function renderHistory() {
  const data = await api("/api/history");
  const container = document.getElementById("history-list");
  if (!data.seasons.length) {
    container.innerHTML = `<div class="empty">No seasons recorded yet -- run a backfill.</div>`;
    return;
  }
  container.innerHTML = data.seasons
    .map((s) => {
      const rows = s.final_standings
        .map(
          (f, i) =>
            `<tr><td>${i === 0 ? "🏆" : f.final_rank}</td><td>${f.name}</td><td>${f.manager_nickname || ""}</td></tr>`
        )
        .join("");
      return `
        <h3>${s.season_year} - ${s.league_name || ""}</h3>
        <table><thead><tr><th>Rank</th><th>Team</th><th>Manager</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="3">Season in progress or standings not yet finalized.</td></tr>'}</tbody></table>
      `;
    })
    .join("<hr style='border-color:var(--border);margin:16px 0'>");
}

// ---------------------------------------------------------------------
// Shared filter population + pull status
// ---------------------------------------------------------------------

function populateTeamFilter(select) {
  if (select.dataset.season === String(state.season)) return;
  const teams = (state.meta.teams || []).filter((t) => t.season_year === state.season);
  select.innerHTML =
    `<option value="">All teams</option>` +
    teams.map((t) => `<option value="${t.team_key}">${t.name}</option>`).join("");
  select.dataset.season = String(state.season);
}

function updatePullStatus() {
  const pill = document.getElementById("pull-status");
  const last = state.meta.last_successful_pull;
  if (state.meta.pull_running) {
    pill.textContent = "Pulling now...";
    pill.classList.remove("stale");
    return;
  }
  if (!last) {
    pill.textContent = "No data pulled yet";
    pill.classList.add("stale");
    return;
  }
  const finished = new Date(last.run_finished_at + "Z");
  const hoursAgo = (Date.now() - finished.getTime()) / 3.6e6;
  pill.textContent = `Last pull: ${finished.toLocaleString()}`;
  pill.classList.toggle("stale", hoursAgo > 48);
}

async function refreshMeta() {
  state.meta = await api("/api/meta");
  updatePullStatus();
}

async function init() {
  await refreshMeta();
  const seasonSelect = document.getElementById("season-select");
  const seasons = state.meta.seasons.map((s) => s.season_year);
  seasonSelect.innerHTML = seasons.length
    ? seasons.map((y) => `<option value="${y}">${y}</option>`).join("")
    : `<option value="">No seasons yet</option>`;
  state.season = seasons[0] || null;
  seasonSelect.onchange = () => {
    state.season = Number(seasonSelect.value);
    renderActiveTab();
  };

  setupTabs();

  document.getElementById("pull-now").onclick = async (e) => {
    e.target.disabled = true;
    await fetch("/api/pull", { method: "POST" });
    await refreshMeta();
    e.target.disabled = false;
    renderActiveTab();
  };

  renderActiveTab();

  setInterval(async () => {
    await refreshMeta();
    if (state.activeTab === "standings") renderActiveTab();
  }, 30000);
}

init();
