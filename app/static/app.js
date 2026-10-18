"use strict";

// Mark colours validated for lightness, chroma and colour-vision separation (dataviz validate_palette.js).
const C = {
  solar: "#e0901a", wind: "#3b86d6", load: "#15232b", held: "#d9483b", ok: "#2e9e6b",
  data: "#00939f", teal: "#0d4f5c", grid: "#ece7da", ink3: "#7b8a92", buy: "#8e6bd8",
};
// Stacked and adjacent bars: capped thickness and a 2px surface-coloured gap between segments.
const BAR = { maxBarThickness: 24, borderColor: "#ffffff", borderWidth: { top: 1, bottom: 1 } };
const pct = (x, d = 1) => (x * 100).toFixed(d) + "%";
const kwh = (x) => Math.round(x).toLocaleString("en-US") + " kWh";
const short = (h) => (h ? h.slice(0, 10) + "…" + h.slice(-6) : "");
const $ = (id) => document.getElementById(id);

Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
Chart.defaults.font.size = 11.5;
Chart.defaults.color = "#4a5a63";
Chart.defaults.plugins.legend.labels.boxWidth = 12;
Chart.defaults.animation.duration = 500;

const charts = {};
function chart(id, config) {
  if (charts[id]) charts[id].destroy();
  config.options = Object.assign({ responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false } }, config.options || {});
  charts[id] = new Chart($(id), config);
  return charts[id];
}
const axis = (title, extra = {}) => Object.assign({ grid: { color: C.grid }, title: { display: !!title, text: title } }, extra);

function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 4200);
}

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

async function busy(btn, fn) {
  btn.disabled = true;
  btn.classList.add("busy");
  try { return await fn(); }
  catch (e) { toast(e.message); throw e; }
  finally { btn.classList.remove("busy"); btn.disabled = false; refresh(); }
}

const S = { series: null, state: null, plans: {}, settled: {} };

// ------------------------------------------------------------ chain state and feed

let seen = 0;
async function refresh() {
  const st = await api("/api/state");
  S.state = st;
  $("chain-text").textContent = `local EVM · chain ${st.chain.chain_id} · block ${st.chain.block}`;
  $("where").textContent = `Antalya · zone TR (UTC+3) · market day ${st.day}`;
  $("addr").innerHTML = `SaatRegistry ${short(st.chain.registry)}<br>SaatMarket ${short(st.chain.market)}`;
  const feed = $("feed");
  const items = st.log.slice().reverse();
  feed.innerHTML = items.map((e, i) => `<li class="${i < st.log.length - seen ? "fresh" : ""}"><span class="who">${e.actor}</span> ${e.action}` +
    (e.tx ? `<div class="tx">tx ${short(e.tx)} · gas ${e.gas.toLocaleString("en-US")} · block ${e.block}</div>` : `<div class="tx">block ${e.block}</div>`) + "</li>").join("");
  seen = st.log.length;
  for (const step of st.done) {
    const map = { "issue-1": null, deliver: "btn-deliver", list: "btn-list" };
    if (map[step]) $(map[step]).disabled = true;
  }
}

// ------------------------------------------------------------ the gap

function drawGap() {
  const s = S.series;
  const short_ = s.hotel_kwh.map((l, i) => Math.max(l - s.hotel_contract_kwh[i], 0));
  $("gap-cfe").textContent = pct(s.hotel_cfe_contract_only, 0);
  $("gap-day").textContent = new Date(s.day + "T12:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
  chart("c-gap", {
    data: {
      labels: s.labels,
      datasets: [
        { type: "line", label: "Hotel load", stack: "l1", data: s.hotel_kwh, borderColor: C.load, borderWidth: 2.5, pointRadius: 0, tension: .3, order: 0 },
        { type: "bar", label: "Solar certificates", data: s.hotel_contract_solar_kwh, backgroundColor: C.solar, stack: "s", order: 1, ...BAR },
        { type: "bar", label: "Wind certificates", data: s.hotel_contract_wind_kwh, backgroundColor: C.wind, stack: "s", order: 1, ...BAR },
        { type: "bar", label: "Not covered this hour", data: short_, backgroundColor: C.held, stack: "s", order: 1, ...BAR },
      ],
    },
    options: { scales: { x: axis("", { stacked: true, grid: { display: false } }), y: axis("kWh per hour", { stacked: true, beginAtZero: true }) } },
  });
}

// ------------------------------------------------------------ issue

function drawIssue(v, committed) {
  const labels = v.hours.map((h) => h.label);
  chart("c-issue", {
    data: {
      labels,
      datasets: [
        { type: "line", label: "Upper envelope (99th pct.)", data: v.hours.map((h) => h.upper_kwh), borderColor: C.ink3, borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0, fill: false },
        { type: "line", label: "Expected from weather", data: v.hours.map((h) => h.expected_kwh), borderColor: C.teal, borderWidth: 2, pointRadius: 0, tension: .3 },
        { type: "bar", label: "Meter claim", data: v.hours.map((h) => h.claimed_kwh), backgroundColor: v.hours.map((h) => (h.held ? C.held : C.ok)), ...BAR },
      ],
    },
    options: { plugins: { tooltip: { callbacks: { afterLabel: (ctx) => (ctx.dataset.type === "bar" && v.hours[ctx.dataIndex].held ? "held: " + v.hours[ctx.dataIndex].reason : "") } } },
      scales: { x: axis("", { grid: { display: false } }), y: axis("kWh", { beginAtZero: true }) } },
  });
  const reasons = [...new Set(v.hours.filter((h) => h.held && h.claimed_kwh > 0).map((h) => h.reason))];
  const f = [];
  f.push(`<div class="fact ${v.held_hours ? "bad" : "good"}"><b>${v.held_hours}</b> hours held${reasons.length ? " · " + reasons.join(", ") : ""}</div>`);
  f.push(`<div class="fact">would issue <b>${kwh(v.issued_kwh)}</b></div>`);
  f.push(`<div class="fact">verifier report sha256 <span class="hash">${short(v.report_hash)}</span></div>`);
  if (committed) {
    f.push(`<div class="fact good">Merkle root <span class="hash">${short(committed.root)}</span></div>`);
    f.push(`<div class="fact">commit: <b>${committed.commit_gas.toLocaleString("en-US")}</b> gas for ${committed.leaves} hours</div>`);
    f.push(`<div class="fact">mint: <b>${committed.mint_gas.toLocaleString("en-US")}</b> gas · proofs of ${committed.proof_len} hashes</div>`);
  }
  $("issue-facts").innerHTML = f.join("");
}

async function verifyClick() {
  const v = await api("/api/verify", { device: +$("issue-device").value, tamper: $("issue-tamper").value });
  drawIssue(v);
  return v;
}

async function issueDevice(device) {
  const r = await api("/api/issue", { device, tamper: "none" });
  $("issue-device").value = device;
  $("issue-tamper").value = "none";
  drawIssue(r, r);
  return r;
}

// ------------------------------------------------------------ market

function drawBook(rows) {
  const names = { 1: "Solar", 2: "Wind" };
  const max = Math.max(...rows.map((r) => r.price_eth_per_mwh), 0.0011);
  const min = Math.min(...rows.map((r) => r.price_eth_per_mwh), 0.001);
  let html = `<div class="lab"></div>` + S.series.labels.map((l, i) => `<div class="hh">${i % 3 === 0 ? l.slice(0, 2) : ""}</div>`).join("");
  for (const dev of [1, 2]) {
    html += `<div class="lab">${names[dev]}</div>`;
    for (let i = 0; i < 24; i++) {
      const r = rows.find((x) => x.device === dev && x.i === i && x.kwh > 0);
      if (!r) { html += `<div class="cell empty">.</div>`; continue; }
      const t = (r.price_eth_per_mwh - min) / (max - min || 1);
      const base = dev === 1 ? [224, 144, 26] : [59, 134, 214];
      const col = `rgba(${base.join(",")},${0.35 + 0.65 * t})`;
      html += `<div class="cell" style="background:${col}" title="${r.label} · ${r.kwh} kWh · ${r.price_eth_per_mwh.toFixed(4)} ETH/MWh">${Math.round(r.kwh)}</div>`;
    }
  }
  $("book").innerHTML = html + `<div class="lab"></div><div class="hh" style="grid-column: span 24; text-align:left">kWh listed per hour · darker = higher price (scarcer hour)</div>`;
}

// ------------------------------------------------------------ plan

function drawPlan(p) {
  const labels = S.series.labels;
  const buys = Array(24).fill(0);
  p.buys.forEach((b) => (buys[b.i] += b.wh / 1000));
  chart("c-plan", {
    data: {
      labels,
      datasets: [
        { type: "line", label: "Load forecast", stack: "l1", data: p.load_fc, borderColor: C.ink3, borderDash: [5, 4], borderWidth: 1.8, pointRadius: 0, tension: .3 },
        { type: "line", label: "Planned load (after shifting)", stack: "l2", data: p.shifted_fc, borderColor: C.load, borderWidth: 2.5, pointRadius: 0, tension: .3 },
        { type: "bar", label: "Own certificates (forecast)", data: p.own_fc, backgroundColor: C.data, stack: "s", ...BAR },
        { type: "bar", label: "Buy on SaatMarket", data: buys, backgroundColor: C.buy, stack: "s", ...BAR },
      ],
    },
    options: { scales: { x: axis("", { stacked: true, grid: { display: false } }), y: axis("kWh", { stacked: true, beginAtZero: true }) } },
  });
  $("plan-facts").innerHTML = [
    `<div class="fact">moves <b>${kwh(p.shifted_kwh)}</b> of flexible load into surplus hours</div>`,
    `<div class="fact">buys <b>${kwh(p.buy_kwh)}</b> in ${p.buys.length} hourly lots</div>`,
    `<div class="fact">cost <b>${p.cost_eth.toFixed(5)}</b> test ETH</div>`,
  ].join("");
}

async function planClick(meter) {
  const p = await api("/api/plan", { meter });
  S.plans[meter] = p;
  $("plan-meter").value = meter;
  drawPlan(p);
  return p;
}

// ------------------------------------------------------------ claim

function drawClaim(meter, r) {
  const key = meter === 7 ? "hotel" : "campus";
  const contract = S.series[key + "_cfe_contract_only"];
  $("scores").innerHTML = `
    <div class="score"><div class="v">100%</div><div class="l">annual certificates say</div></div>
    <div class="score"><div class="v" style="color:var(--held)">${pct(contract)}</div><div class="l">hour by hour, contract only</div></div>
    <div class="score final"><div class="v">${pct(r.cfe)}</div><div class="l">hour by hour with Saat, computed on-chain</div></div>`;
  chart("c-claim", {
    data: {
      labels: r.hours.map((h) => h.label),
      datasets: [
        { type: "bar", label: "Matched in the same hour", data: r.hours.map((h) => h.matched_kwh), backgroundColor: C.ok, stack: "s", ...BAR },
        { type: "bar", label: "Unmatched (grid mix)", data: r.hours.map((h) => Math.max(h.load_kwh - h.matched_kwh, 0)), backgroundColor: C.held, stack: "s", ...BAR },
      ],
    },
    options: { scales: { x: axis("", { stacked: true, grid: { display: false } }), y: axis("kWh", { stacked: true, beginAtZero: true }) } },
  });
  $("claim-facts").innerHTML = [
    `<div class="fact">load <b>${kwh(r.load_wh / 1000)}</b></div>`,
    `<div class="fact good">matched <b>${kwh(r.matched_wh / 1000)}</b></div>`,
    `<div class="fact">24 proofs + retirements: <b>${r.retire_gas.toLocaleString("en-US")}</b> gas</div>`,
  ].join("");
}

async function settleClick(meter) {
  const r = await api("/api/settle", { meter });
  S.settled[meter] = r;
  $("claim-meter").value = meter;
  drawClaim(meter, r);
  return r;
}

async function mismatchClick() {
  const r = await api("/api/mismatch", { meter: +$("claim-meter").value });
  const el = document.createElement("div");
  el.className = "fact bad";
  el.innerHTML = r.reverted ? `Reverted: <b>${r.error}</b>. A ${r.certificate} certificate cannot cover ${r.load_hour} load.` : "Not reverted";
  $("claim-facts").prepend(el);
  return r;
}

// ------------------------------------------------------------ evidence

async function drawEvidence() {
  const ev = await api("/api/evidence");
  const g = ev.hourly_gap;
  if (g) {
    $("ev-gap-text").innerHTML = `Across <b>${g.balancing_authorities}</b> U.S. balancing authorities, buying 100% of annual use in wind and solar certificates covers a median of only <b>${pct(g.median_hourly_cfe)}</b> hour by hour (range ${pct(g.range_hourly_cfe[0], 0)}–${pct(g.range_hourly_cfe[1], 0)}). The rest, a median <b>${g.median_hidden_kg_per_mwh} kg CO₂e/MWh</b>, is reported as zero.`;
    const rows = g.by_ba.slice().sort((a, b) => b.hourly_cfe - a.hourly_cfe);
    chart("c-ev-gap", {
      type: "bar",
      data: { labels: rows.map((r) => r.ba), datasets: [{ label: "hourly match", data: rows.map((r) => r.hourly_cfe * 100), backgroundColor: rows.map((r) => (r.hourly_cfe < 0.5 ? C.held : C.data)), maxBarThickness: 12 }] },
      options: { indexAxis: "y", plugins: { legend: { display: false } }, scales: { x: axis("% of load matched hour by hour", { min: 0, max: 100 }), y: { grid: { display: false }, ticks: { autoSkip: false, font: { size: 9.5 } } } } },
    });
  }
  const v = ev.verifier_eval;
  if (v) {
    const show = [["after_sunset", "Power claimed after sunset"], ["spike", "One hour inflated ×1.5"], ["shift_2", "Output moved 2 h later"], ["inflate_50", "Every hour +50%"], ["replay", "Sunny day replayed on a cloudy one"], ["inflate_20", "Every hour +20%"]];
    $("ev-ver").innerHTML = `<tr><th>Fraud (simulated on real data)</th><th class="n">Capacity check</th><th class="n">Saat AI</th></tr>` +
      show.map(([k, l]) => `<tr><td>${l}</td><td class="n">${pct(v.attacks[k]["nameplate check"].fake_energy_blocked, 0)}</td><td class="n"><b>${pct(v.attacks[k]["Saat AI"].fake_energy_blocked, 0)}</b></td></tr>`).join("") +
      `<tr><td>Real energy wrongly held</td><td class="n">0%</td><td class="n">${pct(v.clean["Saat AI"].clean_energy_held, 2)}</td></tr>`;
    $("ev-ver-text").innerHTML = `Share of the fake energy that never becomes a certificate. The model's hourly error is <b>${v.accuracy.ml_mae_pct_of_ac}%</b> of capacity, against ${v.accuracy.physics_mae_pct_of_ac}% for physics alone. Small uniform inflation (≤10%) is hidden in weather noise. Settlement-grade meters remain the first line of defence.`;
  }
  const a = ev.antalya_eval;
  if (a) {
    const c = a.community;
    $("ev-ant-text").innerHTML = `Hotel and campus together, every hour of 2025. Day-ahead AI shifting recovers <b>${Math.round(((c.hourly_cfe_p2p_plus_ai_shift - c.hourly_cfe_p2p_pooled) / (c.hourly_cfe_p2p_plus_perfect_shift - c.hourly_cfe_p2p_pooled)) * 100)}%</b> of what perfect foresight would. The wind forecaster's error is ${a.forecast_skill.wind_kwh.ai_mae_pct_of_capacity}% of capacity, against ${a.forecast_skill.wind_kwh.persistence_mae_pct}% for persistence.`;
    const labels = ["Annual claim", "Hourly, each alone", "+ P2P hourly trading", "+ AI day-ahead shift", "Perfect foresight"];
    const vals = [1, c.hourly_cfe_separate, c.hourly_cfe_p2p_pooled, c.hourly_cfe_p2p_plus_ai_shift, c.hourly_cfe_p2p_plus_perfect_shift].map((x) => x * 100);
    chart("c-ev-ant", {
      type: "bar",
      data: { labels, datasets: [{ data: vals, backgroundColor: [C.data, C.data, C.data, C.buy, C.data], maxBarThickness: 22 }] },
      options: { indexAxis: "y", plugins: { legend: { display: false }, tooltip: { callbacks: { label: (ctx) => ctx.raw.toFixed(1) + "%" } } }, scales: { x: axis("% of load", { min: 50, max: 100 }), y: { grid: { display: false } } } },
    });
  }
}

// ------------------------------------------------------------ whole day

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function runDay() {
  const go = (id) => $(id).scrollIntoView({ behavior: "smooth", block: "start" });
  go("s-issue");
  $("issue-tamper").value = "shift";
  await verifyClick(); await sleep(1800);
  $("issue-tamper").value = "none";
  await verifyClick(); await sleep(1200);
  await issueDevice(1); await sleep(1200);
  await issueDevice(2); await sleep(800);
  go("s-market");
  $("deliver-facts").innerHTML = factsDeliver(await api("/api/deliver", {}));
  await sleep(600);
  drawBook(await api("/api/list", {})); drawBook(await api("/api/book"));
  await sleep(1200);
  go("s-plan");
  await planClick(7); await sleep(1500);
  await api("/api/execute", { meter: 7 });
  await planClick(8); await api("/api/execute", { meter: 8 });
  await planClick(7);
  drawBook(await api("/api/book"));
  await sleep(800);
  go("s-claim");
  await settleClick(8);
  await settleClick(7); await sleep(1200);
  await mismatchClick();
  refresh();
}

function factsDeliver(d) {
  return Object.entries(d).map(([k, v]) => `<div class="fact">${k.replace("->", " → ")} <b>${kwh(v)}</b></div>`).join("");
}

// ------------------------------------------------------------ wiring

async function init() {
  await refresh();
  S.series = await api("/api/series");
  const sel = $("issue-tamper");
  sel.innerHTML = Object.entries(S.state.tampers).map(([k, v]) => `<option value="${k}">${k === "none" ? "Honest" : "Tampered: " + v}</option>`).join("");
  drawGap();
  drawEvidence();
  $("btn-verify").onclick = (e) => busy(e.target, verifyClick);
  $("btn-issue").onclick = (e) => busy(e.target, () => issueDevice(+$("issue-device").value));
  $("btn-deliver").onclick = (e) => busy(e.target, async () => { $("deliver-facts").innerHTML = factsDeliver(await api("/api/deliver", {})); });
  $("btn-list").onclick = (e) => busy(e.target, async () => { await api("/api/list", {}); drawBook(await api("/api/book")); });
  $("btn-plan").onclick = (e) => busy(e.target, () => planClick(+$("plan-meter").value));
  $("btn-execute").onclick = (e) => busy(e.target, async () => { await api("/api/execute", { meter: +$("plan-meter").value }); drawBook(await api("/api/book")); });
  $("btn-settle").onclick = (e) => busy(e.target, () => settleClick(+$("claim-meter").value));
  $("btn-mismatch").onclick = (e) => busy(e.target, mismatchClick);
  $("btn-run").onclick = (e) => busy(e.target, runDay);
  $("btn-reset").onclick = (e) => busy(e.target, async () => { await api("/api/reset", {}); location.reload(); });
  setInterval(refresh, 2500);
}
init();
