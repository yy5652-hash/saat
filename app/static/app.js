"use strict";

// Mark colours validated on the dark card surface #102a31 (dataviz validate_palette.js, --mode dark).
const C = {
  solar: "#c98016", wind: "#3b86d6", red: "#e0563f", green: "#2fa871", teal: "#1aa3ad", purple: "#9b7be0",
  ink: "#f2efe6", ink2: "#b4c9cc", ink3: "#7c979c", grid: "rgba(255,255,255,0.06)", surface: "#0f2a31",
};
const pct = (x, d = 1) => (x * 100).toFixed(d) + "%";
const kwh = (x) => Math.round(x).toLocaleString("en-US") + " kWh";
const short = (h) => (h ? h.slice(0, 10) + "…" + h.slice(-6) : "");
const $ = (id) => document.getElementById(id);
const rgba = (hex, a) => `rgba(${parseInt(hex.slice(1, 3), 16)},${parseInt(hex.slice(3, 5), 16)},${parseInt(hex.slice(5, 7), 16)},${a})`;
const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const easeOut = (p) => 1 - Math.pow(1 - p, 3);

// ------------------------------------------------------------ chart defaults: dark, quiet axes, glowing marks

Chart.defaults.font.family = "Inter, -apple-system, sans-serif";
Chart.defaults.font.size = 11.5;
Chart.defaults.color = C.ink3;
Chart.defaults.borderColor = C.grid;
Chart.defaults.plugins.legend.labels.boxWidth = 11;
Chart.defaults.plugins.legend.labels.boxHeight = 11;
Chart.defaults.plugins.legend.labels.color = C.ink2;
Chart.defaults.plugins.tooltip.backgroundColor = "rgba(4,17,15,0.92)";
Chart.defaults.plugins.tooltip.borderColor = "rgba(255,255,255,0.12)";
Chart.defaults.plugins.tooltip.borderWidth = 1;
Chart.defaults.animation.duration = 650;
Chart.register({
  id: "glow",
  beforeDatasetDraw(chart, args) {
    const ds = chart.data.datasets[args.index];
    if (!ds.glow) return;
    chart.ctx.save();
    chart.ctx.shadowColor = ds.glow;
    chart.ctx.shadowBlur = ds.glowBlur || 16;
  },
  afterDatasetDraw(chart, args) {
    if (chart.data.datasets[args.index].glow) chart.ctx.restore();
  },
});
// A gradient of one hue: opaque at the data end, softer towards the baseline.
const grad = (hex, top = 1, bottom = 0.45) => (ctx) => {
  const a = ctx.chart.chartArea;
  if (!a) return hex;
  const g = ctx.chart.ctx.createLinearGradient(0, a.top, 0, a.bottom);
  g.addColorStop(0, rgba(hex, top));
  g.addColorStop(1, rgba(hex, bottom));
  return g;
};
const BAR = { maxBarThickness: 22, borderColor: C.surface, borderWidth: { top: 1, bottom: 1 }, borderSkipped: false };
const TOP = { borderRadius: { topLeft: 4, topRight: 4 } };
const axis = (title, extra = {}) => Object.assign({ grid: { color: C.grid }, border: { display: false }, title: { display: !!title, text: title, color: C.ink3 } }, extra);

const charts = {};
function chart(id, config) {
  if (charts[id]) charts[id].destroy();
  config.options = Object.assign({ responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false } }, config.options || {});
  charts[id] = new Chart($(id), config);
  return charts[id];
}

// ------------------------------------------------------------ the 24-hour ring (the project's signature view)

function sector(r0, r1, a0, a1) {
  const p = (r, a) => `${(Math.cos(a) * r).toFixed(2)} ${(Math.sin(a) * r).toFixed(2)}`;
  const large = a1 - a0 > Math.PI ? 1 : 0;
  return `M ${p(r0, a0)} A ${r0} ${r0} 0 ${large} 1 ${p(r0, a1)} L ${p(r1, a1)} A ${r1} ${r1} 0 ${large} 0 ${p(r1, a0)} Z`;
}
const angle = (h) => (h / 24) * 2 * Math.PI - Math.PI / 2;

// layers: [{ color, values[24], glow? }] stacked outwards from the inner radius; ghost: values[24] drawn as a
// dashed outline past the stack; daylight: booleans[24] for the outer sun band; center: { value(p), label, sub }.
class Ring {
  constructor(svg, { layers, ghost, daylight, max, center }) {
    Object.assign(this, { svg, layers, ghost, daylight, max, center, r0: 132, r1: 285 });
    const id = svg.id;
    svg.innerHTML = `
      <defs>
        <radialGradient id="${id}-disc"><stop offset="0" stop-color="#14414b"/><stop offset="1" stop-color="#0a2229"/></radialGradient>
        <filter id="${id}-glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="6" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
      </defs>
      <circle r="${this.r1 + 26}" fill="none" stroke="rgba(255,255,255,0.06)"/>
      <g class="band"></g><g class="grid"></g><g class="wedges"></g><g class="ghosts"></g>
      <circle r="${this.r0 - 10}" fill="url(#${id}-disc)" stroke="rgba(255,255,255,0.10)"/>
      <g class="labels"></g>
      <line class="hand" x1="0" y1="${-(this.r0 - 14)}" x2="0" y2="${-(this.r1 + 18)}" stroke="#fff" stroke-width="2.5" stroke-linecap="round" filter="url(#${id}-glow)"/>
      <text class="cv" y="8" text-anchor="middle" font-family="Space Grotesk" font-weight="700" font-size="68" fill="${C.ink}"></text>
      <text class="cl" y="40" text-anchor="middle" font-family="Inter" font-size="15" fill="${C.ink2}"></text>
      <text class="cs" y="62" text-anchor="middle" font-family="Inter" font-size="13" fill="${C.ink3}"></text>`;
    const q = (s) => svg.querySelector(s);
    this.g = { band: q(".band"), grid: q(".grid"), wedges: q(".wedges"), ghosts: q(".ghosts"), labels: q(".labels"), hand: q(".hand"), cv: q(".cv"), cl: q(".cl"), cs: q(".cs") };
    let grid = "";
    for (const f of [0.25, 0.5, 0.75, 1]) grid += `<circle r="${this.r0 + f * (this.r1 - this.r0)}" fill="none" stroke="rgba(255,255,255,0.05)" stroke-dasharray="2 5"/>`;
    this.g.grid.innerHTML = grid;
    let labels = "";
    for (let h = 0; h < 24; h++) {
      const a = angle(h), major = h % 3 === 0;
      if (major) labels += `<text x="${(Math.cos(a) * (this.r1 + 44)).toFixed(1)}" y="${(Math.sin(a) * (this.r1 + 44) + 4).toFixed(1)}" text-anchor="middle" font-family="JetBrains Mono" font-size="12" fill="${C.ink3}">${String(h).padStart(2, "0")}</text>`;
      const ro = this.r1 + (major ? 31 : 26);
      labels += `<line x1="${(Math.cos(a) * (this.r1 + 22)).toFixed(1)}" y1="${(Math.sin(a) * (this.r1 + 22)).toFixed(1)}" x2="${(Math.cos(a) * ro).toFixed(1)}" y2="${(Math.sin(a) * ro).toFixed(1)}" stroke="rgba(255,255,255,${major ? 0.4 : 0.18})"/>`;
    }
    this.g.labels.innerHTML = labels;
    if (daylight) {
      let band = "";
      for (let h = 0; h < 24; h++) band += `<path d="${sector(this.r1 + 10, this.r1 + 16, angle(h) + 0.004, angle(h + 1) - 0.004)}" fill="${daylight[h] ? "rgba(242,176,74,0.75)" : "rgba(59,134,214,0.28)"}"/>`;
      this.g.band.innerHTML = band;
    }
  }
  r(v) { return this.r0 + (Math.max(0, v) / this.max) * (this.r1 - this.r0); }
  draw(p) {
    const shown = p * 24;
    let w = "", gh = "";
    for (let h = 0; h < 24; h++) {
      const f = easeOut(clamp(shown - h));
      if (f <= 0) continue;
      const a0 = angle(h) + 0.018, a1 = angle(h + 1) - 0.018;
      let acc = 0;
      for (const L of this.layers) {
        const v = L.values[h] * f;
        if (v <= 0) continue;
        w += `<path d="${sector(this.r(acc), this.r(acc + v), a0, a1)}" fill="${L.color}"${L.glow ? ` filter="url(#${this.svg.id}-glow)"` : ""}/>`;
        acc += v;
      }
      if (this.ghost && this.ghost[h] > 0) {
        gh += `<path d="${sector(this.r(acc), this.r(acc + this.ghost[h] * f), a0, a1)}" fill="rgba(242,239,230,0.06)" stroke="rgba(242,239,230,0.55)" stroke-width="1.2" stroke-dasharray="3 3"/>`;
      }
    }
    this.g.wedges.innerHTML = w;
    this.g.ghosts.innerHTML = gh;
    this.g.hand.setAttribute("transform", `rotate(${(shown / 24) * 360})`);
    this.g.hand.style.opacity = p > 0 && p < 1 ? 1 : 0;
    this.g.cv.textContent = this.center.value(p);
    this.g.cl.textContent = this.center.label;
    this.g.cs.textContent = this.center.sub || "";
  }
  animate(ms = 1800) {
    if (window.FILM) { this.draw(1); return; }
    const t0 = performance.now();
    const step = (t) => { const p = clamp((t - t0) / ms); this.draw(p); if (p < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  }
}
const RINGS = {};
function ringProgress(id, p) { RINGS[id]?.draw(p); return true; }

// Running share of load matched up to hour p*24, so the centre number moves with the hand.
const running = (load, matched) => (p) => {
  const n = p * 24;
  let L = 0, M = 0;
  for (let h = 0; h < 24; h++) { const f = clamp(n - h); L += load[h] * f; M += matched[h] * f; }
  return L > 0 ? pct(M / L, 0) : "—";
};

// ------------------------------------------------------------ plumbing

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

// ------------------------------------------------------------ chain state and the live feed

let seen = 0;
async function refresh() {
  const st = await api("/api/state");
  S.state = st;
  $("chain-text").textContent = `local EVM · chain ${st.chain.chain_id} · block ${st.chain.block}`;
  $("where").textContent = `Antalya · zone TR (UTC+3) · market day ${st.day}`;
  $("addr").innerHTML = `SaatRegistry ${short(st.chain.registry)}<br>SaatMarket&nbsp;&nbsp; ${short(st.chain.market)}`;
  const items = st.log.slice().reverse();
  $("feed").innerHTML = items.map((e, i) => `<li class="${i < st.log.length - seen ? "fresh" : ""}"><div class="blk">#${e.block}</div><div><span class="who">${e.actor}</span>${e.action}` +
    (e.tx ? `<div class="tx">tx ${short(e.tx)} · ${e.gas.toLocaleString("en-US")} gas</div>` : "") + "</div></li>").join("");
  seen = st.log.length;
  for (const step of st.done) {
    const map = { deliver: "btn-deliver", list: "btn-list" };
    if (map[step]) $(map[step]).disabled = true;
  }
}

// ------------------------------------------------------------ the gap

function drawGap() {
  const s = S.series;
  const solarM = [], windM = [], uncovered = [], surplus = [], matched = [];
  for (let h = 0; h < 24; h++) {
    const L = s.hotel_kwh[h], sol = s.hotel_contract_solar_kwh[h], win = s.hotel_contract_wind_kwh[h];
    const sm = Math.min(L, sol), wm = Math.min(L - sm, win);
    solarM.push(sm); windM.push(wm); uncovered.push(Math.max(L - sm - wm, 0)); surplus.push(Math.max(sol + win - L, 0)); matched.push(sm + wm);
  }
  $("gap-cfe").textContent = pct(s.hotel_cfe_contract_only, 0);
  $("gap-day").textContent = new Date(s.day + "T12:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
  const max = Math.max(...s.hotel_kwh.map((l, h) => Math.max(l, s.hotel_contract_kwh[h])));
  RINGS["ring-gap"] = new Ring($("ring-gap"), {
    layers: [{ color: C.solar, values: solarM }, { color: C.wind, values: windM }, { color: C.red, values: uncovered, glow: true }],
    ghost: surplus, daylight: s.solar_kwh.map((x) => x > 1), max,
    center: { value: running(s.hotel_kwh, matched), label: "matched in the same hour", sub: "hotel · " + $("gap-day").textContent },
  });
  RINGS["ring-gap"].animate();
}

// ------------------------------------------------------------ issue

function drawIssue(v, committed) {
  chart("c-issue", {
    data: {
      labels: v.hours.map((h) => h.label),
      datasets: [
        { type: "line", label: "Expected from weather", data: v.hours.map((h) => h.expected_kwh), borderColor: C.teal, borderWidth: 2, pointRadius: 0, tension: 0.35, glow: rgba(C.teal, 0.8), order: 1 },
        { type: "line", label: "99th-percentile envelope", data: v.hours.map((h) => h.upper_kwh), borderColor: rgba(C.teal, 0.55), borderDash: [4, 4], borderWidth: 1.2, pointRadius: 0, tension: 0.35, fill: "-1", backgroundColor: rgba(C.teal, 0.12), order: 2 },
        { type: "bar", label: "Issued", data: v.hours.map((h) => (h.held ? 0 : h.claimed_kwh)), backgroundColor: grad(C.green, 1, 0.55), grouped: false, order: 3, ...BAR, ...TOP },
        { type: "bar", label: "Held for review", data: v.hours.map((h) => (h.held ? h.claimed_kwh : 0)), backgroundColor: grad(C.red, 1, 0.6), glow: rgba(C.red, 0.7), grouped: false, order: 3, ...BAR, ...TOP },
      ],
    },
    options: { plugins: { tooltip: { callbacks: { afterBody: (items) => { const h = v.hours[items[0].dataIndex]; return h.held ? "held: " + h.reason : ""; } } } },
      scales: { x: axis("", { grid: { display: false } }), y: axis("kWh", { beginAtZero: true }) } },
  });
  const reasons = [...new Set(v.hours.filter((h) => h.held && h.claimed_kwh > 0).map((h) => h.reason))];
  const vd = $("issue-verdict");
  vd.hidden = false;
  vd.className = "verdict " + (v.held_hours ? "held" : "passed");
  vd.innerHTML = v.held_hours
    ? `<span class="badge">HELD</span><div class="what"><b>${v.held_hours} hours</b> held for review · ${reasons.join(", ")}<br>${v.tamper_text}: nothing from this export becomes a certificate.</div>`
    : `<span class="badge">PASSED</span><div class="what"><b>0 hours</b> held · ${kwh(v.issued_kwh)} may be issued<br>Every hour sits inside what the weather and the sun allowed.</div>`;
  const f = [`<div class="fact">verifier report sha256 <span class="hash">${short(v.report_hash)}</span></div>`];
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
  const prices = rows.map((r) => r.price_eth_per_mwh);
  const max = Math.max(...prices, 0.0011), min = Math.min(...prices, 0.001);
  let html = `<div class="lab"></div>` + S.series.labels.map((l, i) => `<div class="hh">${i % 3 === 0 ? l.slice(0, 2) : ""}</div>`).join("");
  for (const dev of [1, 2]) {
    html += `<div class="lab">${names[dev]}</div>`;
    for (let i = 0; i < 24; i++) {
      const r = rows.find((x) => x.device === dev && x.i === i && x.kwh > 0);
      if (!r) { html += `<div class="cell empty">.</div>`; continue; }
      const t = (r.price_eth_per_mwh - min) / (max - min || 1);
      const hex = dev === 1 ? C.solar : C.wind;
      html += `<div class="cell" style="background:linear-gradient(180deg, ${rgba(hex, 0.55 + 0.45 * t)}, ${rgba(hex, 0.25 + 0.35 * t)});box-shadow:0 0 ${Math.round(4 + 14 * t)}px ${rgba(hex, 0.25 + 0.4 * t)}" title="${r.label} · ${r.kwh} kWh · ${r.price_eth_per_mwh.toFixed(4)} test ETH/MWh">${Math.round(r.kwh)}<span class="p" style="transform:scaleX(${(0.15 + 0.85 * t).toFixed(2)})"></span></div>`;
    }
  }
  $("book").innerHTML = html + `<div class="legend"><span>kWh listed per hour · bar under each lot = its price</span><span>cheaper ← → scarcer (evening, night)</span></div>`;
}

function factsDeliver(d) {
  return Object.entries(d).map(([k, v]) => `<div class="fact">${k.replace("->", " → ")} <b>${kwh(v)}</b></div>`).join("");
}

// ------------------------------------------------------------ plan

function drawPlan(p) {
  const buys = Array(24).fill(0);
  p.buys.forEach((b) => (buys[b.i] += b.wh / 1000));
  chart("c-plan", {
    data: {
      labels: S.series.labels,
      datasets: [
        { type: "line", label: "Load forecast", stack: "l1", data: p.load_fc, borderColor: C.ink3, borderDash: [5, 4], borderWidth: 1.6, pointRadius: 0, tension: 0.35 },
        { type: "line", label: "Planned load (after shifting)", stack: "l2", data: p.shifted_fc, borderColor: C.ink, borderWidth: 2.5, pointRadius: 0, tension: 0.35, glow: "rgba(242,239,230,0.55)" },
        { type: "bar", label: "Own certificates (forecast)", data: p.own_fc, backgroundColor: grad(C.teal, 0.95, 0.4), stack: "s", ...BAR },
        { type: "bar", label: "Buy on SaatMarket", data: buys, backgroundColor: grad(C.purple, 1, 0.6), glow: rgba(C.purple, 0.7), stack: "s", ...BAR, ...TOP },
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
  $("score-contract").textContent = pct(S.series[key + "_cfe_contract_only"]);
  $("score-final").textContent = pct(r.cfe);
  const load = r.hours.map((h) => h.load_kwh), matched = r.hours.map((h) => h.matched_kwh);
  RINGS["ring-claim"] = new Ring($("ring-claim"), {
    layers: [{ color: C.green, values: matched, glow: true }, { color: C.red, values: load.map((l, i) => Math.max(l - matched[i], 0)) }],
    daylight: S.series.solar_kwh.map((x) => x > 1), max: Math.max(...load),
    center: { value: (p) => (p >= 1 ? pct(r.cfe) : running(load, matched)(p)), label: "on-chain hourly score", sub: meter === 7 ? "seaside hotel" : "office campus" },
  });
  RINGS["ring-claim"].animate(1400);
  $("claim-facts").innerHTML = [
    `<div class="fact">load <b>${kwh(r.load_wh / 1000)}</b></div>`,
    `<div class="fact good">matched in the same hour <b>${kwh(r.matched_wh / 1000)}</b></div>`,
    `<div class="fact">settled in <b>1</b> transaction · ${r.retire_gas.toLocaleString("en-US")} gas</div>`,
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
  const meter = +$("claim-meter").value;
  const r = await api("/api/mismatch", { meter });
  const t = $("revert");
  t.hidden = false;
  t.innerHTML = r.reverted
    ? `<span class="dim">&gt;</span> registry.retire(meter ${meter}, hour 21:00, [solar · 12:00], [1 Wh])<br><span class="err">✖ reverted: ${r.error}()</span> <span class="dim">· a ${r.certificate} certificate cannot cover ${r.load_hour} load</span>`
    : "not reverted";
  return r;
}

// ------------------------------------------------------------ evidence

async function drawEvidence() {
  const ev = await api("/api/evidence");
  const tr = ev.turkiye_gap;
  if (tr) {
    const row = (pname) => tr.table.find((r) => r.load === "flat 24/7" && r.portfolio === pname);
    $("ev-tr-text").innerHTML = `On Türkiye's real hourly generation, a 24/7 consumer holding wind and solar certificates for 100% of its annual use is matched only <b>${pct(tr.flat_wind_solar_hourly_cfe)}</b> hour by hour. The rest, about <b>${tr.flat_wind_solar_hidden_kg_per_mwh} kg CO₂e/MWh</b> on a ${tr.grid_kg_per_mwh} kg/MWh grid, is reported as zero. Reaching 90% would take ${tr.flat_wind_solar_volume_for_90pct.toFixed(1)}× the annual volume.`;
    const labels = ["Wind + solar", "Wind only", "Solar only", "U.S. median (22 regions)"];
    const vals = [row("wind+solar").hourly_cfe, row("wind").hourly_cfe, row("solar").hourly_cfe, tr.us_median_hourly_cfe].map((x) => x * 100);
    chart("c-ev-tr", {
      type: "bar",
      data: { labels, datasets: [{ data: vals, backgroundColor: [C.teal, C.wind, C.solar, "rgba(242,239,230,0.35)"], maxBarThickness: 24, borderRadius: { topRight: 5, bottomRight: 5 }, borderSkipped: false }] },
      options: { indexAxis: "y", plugins: { legend: { display: false }, tooltip: { callbacks: { label: (ctx) => ctx.raw.toFixed(1) + "% matched hour by hour" } } }, scales: { x: axis("% of a flat load matched hour by hour, 100% annual certificates", { min: 0, max: 100 }), y: { grid: { display: false }, border: { display: false } } } },
    });
  }
  const g = ev.hourly_gap;
  if (g) {
    $("ev-gap-text").innerHTML = `Across <b>${g.balancing_authorities}</b> U.S. balancing authorities, buying 100% of annual use in wind and solar certificates covers a median of only <b>${pct(g.median_hourly_cfe)}</b> hour by hour (range ${pct(g.range_hourly_cfe[0], 0)}–${pct(g.range_hourly_cfe[1], 0)}). The rest, a median <b>${g.median_hidden_kg_per_mwh} kg CO₂e/MWh</b>, is reported as zero. Red bars are regions below half.`;
    const rows = g.by_ba.slice().sort((a, b) => b.hourly_cfe - a.hourly_cfe);
    chart("c-ev-gap", {
      type: "bar",
      data: { labels: rows.map((r) => r.ba), datasets: [{ label: "hourly match", data: rows.map((r) => r.hourly_cfe * 100), backgroundColor: rows.map((r) => (r.hourly_cfe < 0.5 ? C.red : C.teal)), maxBarThickness: 11, borderRadius: { topRight: 4, bottomRight: 4 }, borderSkipped: false }] },
      options: { indexAxis: "y", plugins: { legend: { display: false } }, scales: { x: axis("% of load matched hour by hour", { min: 0, max: 100 }), y: { grid: { display: false }, border: { display: false }, ticks: { autoSkip: false, font: { size: 9.5 } } } } },
    });
  }
  const v = ev.verifier_eval;
  if (v) {
    const show = [["after_sunset", "Power claimed after sunset"], ["shift_2", "Output moved 2 h later"], ["spike", "One hour inflated ×1.5"], ["inflate_50", "Every hour +50%"], ["replay", "Sunny day replayed on a cloudy one"], ["inflate_20", "Every hour +20%"]];
    $("ev-ver").innerHTML = `<tr><th>Fraud (simulated on real data)</th><th class="n">Capacity check</th><th class="n">Saat AI blocks</th></tr>` +
      show.map(([k, l]) => { const x = v.attacks[k]["Saat AI"].fake_energy_blocked; return `<tr><td>${l}</td><td class="n">${pct(v.attacks[k]["nameplate check"].fake_energy_blocked, 0)}</td><td class="n"><b>${pct(x, 0)}</b><span class="bar" style="width:${Math.round(x * 70)}px"></span></td></tr>`; }).join("") +
      `<tr><td>Real energy wrongly held back</td><td class="n">0%</td><td class="n">${pct(v.clean["Saat AI"].clean_energy_held, 2)}</td></tr>`;
    $("ev-ver-text").innerHTML = `The share of fake energy that never becomes a certificate. The model's hourly error is <b>${v.accuracy.ml_mae_pct_of_ac}%</b> of capacity, against ${v.accuracy.physics_mae_pct_of_ac}% for physics alone. Small uniform inflation (≤10%) hides in weather noise, so settlement-grade meters stay the first line of defence.`;
  }
  const a = ev.antalya_eval;
  if (a) {
    const h = a.hotel, mk = a.community.with_hourly_market;
    $("ev-ant-text").innerHTML = `The hotel, every hour of 2025, doing what it does on the demo day. With day-ahead shifting and the hourly market it is matched <b>${pct(h.hourly_cfe_with_ai_shift_and_market)}</b> hour by hour, buying ${pct(h.bought_share_of_annual_load, 0)} more certificates, only in the hours it is short. Its median day is ${pct(h.daily_cfe_p10_p50_p90[1], 0)}, so the demo day is typical. If the community could reach only a quarter of the plants' spare output, it would still be ${pct(mk["0.25"].hourly_cfe)}.`;
    const labels = ["Annual claim", "Hourly, contract only", "+ AI day-ahead shift", "+ hourly market"];
    const vals = [1, h.hourly_cfe_contract_only, h.hourly_cfe_with_ai_shift, h.hourly_cfe_with_ai_shift_and_market].map((x) => x * 100);
    chart("c-ev-ant", {
      type: "bar",
      data: { labels, datasets: [{ data: vals, backgroundColor: [C.teal, C.red, C.teal, C.purple], maxBarThickness: 24, borderRadius: { topRight: 5, bottomRight: 5 }, borderSkipped: false }] },
      options: { indexAxis: "y", plugins: { legend: { display: false }, tooltip: { callbacks: { label: (ctx) => ctx.raw.toFixed(1) + "%" } } }, scales: { x: axis("% of the hotel's 2025 load", { min: 50, max: 100 }), y: { grid: { display: false }, border: { display: false } } } },
    });
  }
}

// ------------------------------------------------------------ the whole day

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function runDay() {
  const go = (id) => $(id).scrollIntoView({ behavior: "smooth", block: "start" });
  go("s-issue");
  $("issue-tamper").value = "shift";
  await verifyClick(); await sleep(1800);
  $("issue-tamper").value = "none";
  await verifyClick(); await sleep(1000);
  await issueDevice(2); await sleep(600);
  await issueDevice(1); await sleep(1200);
  go("s-market");
  $("deliver-facts").innerHTML = factsDeliver(await api("/api/deliver", {}));
  await sleep(600);
  await api("/api/list", {}); drawBook(await api("/api/book"));
  await sleep(1200);
  go("s-plan");
  await planClick(7); await sleep(1500);
  await api("/api/execute", { meter: 7 });
  await api("/api/plan", { meter: 8 }); await api("/api/execute", { meter: 8 });
  drawBook(await api("/api/book"));
  await sleep(800);
  go("s-claim");
  await settleClick(7); await sleep(1600);
  await mismatchClick();
  refresh();
}

// ------------------------------------------------------------ wiring

async function init() {
  await refresh();
  S.series = await api("/api/series");
  $("issue-tamper").innerHTML = Object.entries(S.state.tampers).map(([k, v]) => `<option value="${k}">${k === "none" ? "Honest" : "Tampered: " + v}</option>`).join("");
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
