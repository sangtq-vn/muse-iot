/* Muse-IoT Web UI — vanilla JS, no dependencies (works offline) */

// ── Sensor presentation ─────────────────────────────────────────────────────
const PALETTE = ['#f0a35c', '#5aa9ff', '#46d18c', '#e8c33d', '#ef6f6f', '#4fd1c5'];
const UNIT_DISPLAY = {
  celsius: '°C', fahrenheit: '°F', percent: '%', mmHg: 'mmHg',
  bpm: 'bpm', lux: 'lux', ppm: 'ppm', '°C': '°C', '°F': '°F', '%': '%',
};
const COLOR_BY_SID = {};   // assigned stable colors per sensor id
let colorIdx = 0;

function unitLabel(unit) { return UNIT_DISPLAY[unit] || unit || ''; }

function sensorIcon(sid) {
  const s = sid.toLowerCase();
  if (s.includes('temp')) return 'i-therm';
  if (s.includes('humid')) return 'i-drop';
  if (s.includes('soil') || s.includes('moist')) return 'i-leaf';
  if (s.includes('lux') || s.includes('light')) return 'i-bulb';
  if (s.includes('press')) return 'i-pulse';
  return 'i-pulse';
}

function sensorColor(sid) {
  if (!COLOR_BY_SID[sid]) COLOR_BY_SID[sid] = PALETTE[colorIdx++ % PALETTE.length];
  return COLOR_BY_SID[sid];
}

function actuatorIcon(aid) {
  const s = aid.toLowerCase();
  if (s.includes('pump') || s.includes('water')) return 'i-drop';
  if (s.includes('fan')) return 'i-fan';
  if (s.includes('light') || s.includes('lamp') || s.includes('pwm')) return 'i-bulb';
  return 'i-plug';
}

// ── State ───────────────────────────────────────────────────────────────────
const state = {
  view: 'dashboard',
  status: null,
  profile: null,
  sensors: {},
  sensorMeta: {},   // bare sid -> {name, unit, min, max, hint, plugin}
  metaOrder: [],    // bare sids in domain order
  nodes: [],
  actuators: [],
  events: [],
  decisions: [],
  history: {},      // sid -> {points, unit}
  historyHours: 24,
  chat: [],
  evFilter: 'all',
  decFilter: 'all',
  activeNode: null,
};

// ── Helpers ─────────────────────────────────────────────────────────────────
const $ = s => document.querySelector(s);

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

function esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function timeAgo(sec) {
  sec = Math.max(0, Math.floor(sec));
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

function fmtDateTime(ts) {
  return new Date(ts * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function isTempSid(sid, unit) {
  return sid.toLowerCase().includes('temp') || ['celsius', 'fahrenheit', '°C', '°F'].includes(unit);
}

/** Bare aggregate ids only — drops {node}_* and *_min/*_max keys. */
function aggregateIds(allIds) {
  if (state.metaOrder.length) return state.metaOrder.filter(id => id in allIds);
  return Object.keys(allIds).filter(id => !id.endsWith('_min') && !id.endsWith('_max'));
}

function metaFor(sid) {
  return state.sensorMeta[sid] || { name: sid, unit: '', min: null, max: null };
}

function displayName(sid) {
  const m = state.sensorMeta[sid];
  if (m) return m.name;
  return sid.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function statusClass(sid, s) {
  if (!s || s.age_sec > 600) return 'stale';
  if (s.status === 'low') return 'warn';
  if (s.status === 'high') return sid.toLowerCase().includes('temp') ? 'bad' : 'warn';
  return 'ok';
}

function statusLabel(sid, s) {
  const c = statusClass(sid, s);
  if (c === 'ok') return 'in range';
  if (c === 'stale') return 'stale';
  return s.value < (s.target ? s.target[0] : Infinity) ? 'low' : 'high';
}

// ── Router ──────────────────────────────────────────────────────────────────
const TITLES = { dashboard: 'Dashboard', nodes: 'Nodes', profile: 'Profile', chat: 'Chat', decisions: 'Decisions', events: 'Events', readings: 'Readings' };
const LOADERS = {
  dashboard: loadDashboard, nodes: loadNodes, profile: loadProfile, chat: loadChat,
  decisions: loadDecisions, events: loadEvents, readings: loadReadings,
};

async function goto(view) {
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.toggle('active', b.dataset.view === view));
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + view));
  state.view = view;
  $('#view-title').textContent = TITLES[view];
  window.scrollTo({ top: 0, behavior: 'auto' });
  try { await LOADERS[view](); } catch (e) { console.error(`${view} load failed:`, e); }
  if (view === 'chat') { const log = $('#chat-log'); log.scrollTop = log.scrollHeight; }
}

// ── Dashboard ───────────────────────────────────────────────────────────────
async function loadDashboard() {
  const [status, sensors, profile, actuators, nodes, events, decisions] = await Promise.all([
    api('/api/status'), api('/api/sensors/latest'), api('/api/profile'),
    api('/api/actuators'), api('/api/nodes'), api('/api/events?limit=5&hours=48'),
    api('/api/decisions?limit=30'),
  ]);
  state.status = status; state.sensors = sensors; state.profile = profile;
  state.actuators = actuators; state.nodes = nodes; state.decisions = decisions;

  $('#brand-box').textContent = status.box_id;
  $('#domain-tag').textContent = `${status.domain} · ${status.profile}`;
  $('#day-chip').textContent = `Day ${status.day}`;
  $('#phase-chip').textContent = status.phase;
  const model = (status.last_decision && status.last_decision.model) || (decisions[0] && decisions[0].model) || '—';
  $('#model-chip').textContent = model;

  renderStatusStrip(status, sensors, nodes, decisions);
  renderProfileBanner(profile, sensors);
  renderNodeSummary(nodes);
  renderLastDecision(status.last_decision);
  renderActuators(actuators);
  renderRecentFeed(events);
  updateEventBadge();
  await loadChart(state.historyHours, true);
}

function renderStatusStrip(status, sensors, nodes, decisions) {
  const ids = aggregateIds(sensors);
  const fresh = ids.filter(id => sensors[id].age_sec <= 300);
  const health = $('#st-health');
  if (!ids.length) { health.textContent = 'No data'; health.style.color = 'var(--faint)'; }
  else if (fresh.length === ids.length) { health.textContent = 'Healthy'; health.style.color = 'var(--green)'; }
  else { health.textContent = 'Degraded'; health.style.color = 'var(--amber)'; }
  const online = nodes.filter(n => n.online).length;
  $('#st-health-s').textContent = `${ids.length} sensor${ids.length === 1 ? '' : 's'} · ${online}/${nodes.length} nodes online`;

  if (ids.length) {
    const newest = Math.min(...ids.map(id => sensors[id].age_sec));
    $('#st-poll').innerHTML = `${timeAgo(newest).replace(' ago', '')} <span class="unit">ago</span>`;
    $('#poll-note').textContent = `Poll loop alive · last reading ${timeAgo(newest)}.`;
  } else {
    $('#st-poll').textContent = '—';
    $('#poll-note').textContent = 'No sensor data yet — is the device connected?';
  }
  $('#st-poll-s').textContent = 'auto-refresh via live stream';

  const d = status.last_decision;
  if (d) {
    $('#st-llm').innerHTML = `${d.latency_ms} <span class="unit">ms</span>`;
    $('#st-llm-s').textContent = d.model;
  } else { $('#st-llm').textContent = '—'; $('#st-llm-s').textContent = 'no calls yet'; }

  const ovr = decisions.filter(x => x.was_overridden).length;
  const safe = $('#st-safe');
  if (ovr) { safe.textContent = `${ovr} override${ovr === 1 ? '' : 's'}`; safe.style.color = 'var(--amber)'; }
  else { safe.textContent = 'Clear'; safe.style.color = 'var(--green)'; }
  $('#st-safe-s').textContent = 'in last 30 polls';
}

function renderProfileBanner(profile, sensors) {
  if (!profile || !profile.phases || !profile.phases.length) {
    $('#pb-name').textContent = 'No profile loaded';
    $('#pb-sub').textContent = ''; $('#pb-countdown').innerHTML = '';
    $('#phase-timeline').innerHTML = ''; $('#target-chips').innerHTML = '';
    $('#phase-note').hidden = true;
    return;
  }
  $('#pb-name').textContent = profile.name;
  $('#pb-sub').textContent = [profile.cultivar, state.status && state.status.box_id].filter(Boolean).join(' · ');
  const next = profile.phases.find(p => p.status === 'upcoming');
  $('#pb-countdown').innerHTML = (next && profile.days_until_next_phase != null)
    ? `<strong>${profile.days_until_next_phase} day${profile.days_until_next_phase === 1 ? '' : 's'}</strong>until ${esc(next.name)}`
    : '';

  const dayMax = Math.min(profile.expected_duration_days || 90, 90);
  const day = profile.current_day;
  $('#phase-timeline').innerHTML = profile.phases.map(p => {
    const pct = Math.max(((Math.min(p.days[1], dayMax) - p.days[0]) / dayMax) * 100, 6);
    const hi = p.days[1] > 900 ? '∞' : p.days[1];
    return `<div class="tseg ${p.status}" style="flex:${pct}" title="${esc(p.name)}: day ${p.days[0]}–${hi}">${esc(p.name)}</div>`;
  }).join('') + `<div class="tmarker" style="left:${Math.min((day / dayMax) * 100, 99)}%"><span>Day ${day}</span></div>`;

  const active = profile.phases.find(p => p.status === 'active') || {};
  const targets = active.targets || {};
  const sids = Object.keys(targets).filter(sid => state.sensorMeta[sid] || sensors[sid]);
  $('#target-chips').innerHTML = sids.map(sid => {
    const s = sensors[sid], r = targets[sid], m = metaFor(sid);
    const unit = unitLabel(m.unit || (s && s.unit));
    let cls = '', val = '—';
    if (s) {
      cls = statusClass(sid, s) === 'ok' ? 'ok' : statusClass(sid, s);
      if (cls === 'stale') cls = '';
      val = s.value.toFixed(1);
    }
    return `<div class="target ${cls}"><div class="t-k">${esc(displayName(sid))}</div>
      <div class="t-v num">${val} <small>${esc(unit)}</small></div>
      <div class="t-r num">target ${r[0]}–${r[1]} ${esc(unit)}</div></div>`;
  }).join('') || '<div class="dim" style="font-size:12px">No targets for this phase</div>';

  const note = $('#phase-note');
  const activeNotes = (profile.phases.find(p => p.status === 'active') || {}).notes;
  note.hidden = !activeNotes;
  note.textContent = activeNotes || '';
}

// ── Node summary (dashboard) ──────────────────────────────────────────────────
function renderNodeSummary(nodes) {
  const online = nodes.filter(n => n.online).length;
  const chip = $('#nodes-online-chip');
  chip.textContent = `${online}/${nodes.length} online`;
  chip.className = online === nodes.length && nodes.length ? 'chip green' : 'chip amber';
  const badge = $('#nodes-badge');
  const off = nodes.length - online;
  badge.hidden = off === 0;
  badge.textContent = off;
  $('#node-summary').innerHTML = nodes.length ? nodes.map(n => `
    <button class="node-sum-row" data-nodesum="${esc(n.id)}">
      <span class="node-dot${n.online ? '' : ' stale'}"></span>
      <span><span class="n-id">${esc(n.id)}</span><br>
      <span class="n-sub">${esc(n.adapter || 'unknown adapter')} · ${Object.keys(n.sensors).length} sensors · ${n.actuators.length} actuator${n.actuators.length === 1 ? '' : 's'}</span></span>
      <span class="go num">${n.last_seen ? timeAgo(Date.now() / 1000 - n.last_seen) : 'never'} · Details →</span>
    </button>`).join('')
    : '<div class="empty-block">No nodes reporting yet</div>';
  document.querySelectorAll('[data-nodesum]').forEach(b => b.addEventListener('click', () => {
    state.activeNode = b.dataset.nodesum;
    goto('nodes');
  }));
}

// ── Sensor cards ────────────────────────────────────────────────────────────
function sparkline(points, color) {
  if (!points || points.length < 2) return '<svg class="spark" aria-hidden="true"></svg>';
  const vals = points.map(p => p.value);
  const lo = Math.min(...vals), hi = Math.max(...vals), span = (hi - lo) || 1;
  const pts = vals.map((v, i) => `${(i / (vals.length - 1) * 100).toFixed(1)},${(96 - ((v - lo) / span) * 92).toFixed(1)}`).join(' ');
  return `<svg class="spark" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
    <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" opacity=".9"/></svg>`;
}

function renderSensorCards(sensors) {
  const ids = aggregateIds(sensors);
  const multi = state.nodes.length > 1;
  $('#sensors-sub').textContent = multi
    ? `fleet averages across ${state.nodes.length} nodes · per-node values in Nodes`
    : 'live values with 7-day trend and target band';
  if (!ids.length) {
    $('#sensor-grid').innerHTML = '<div class="panel dim" style="grid-column:1/-1;text-align:center">No sensor data yet — is the device connected and publishing to MQTT?</div>';
    return;
  }
  $('#sensor-grid').innerHTML = ids.map(sid => {
    const s = sensors[sid], m = metaFor(sid);
    const unit = unitLabel(m.unit || s.unit);
    const cls = statusClass(sid, s);
    const pill = cls === 'stale' ? '<span class="stale-tag">stale</span>' : `<span class="st ${cls}">${statusLabel(sid, s)}</span>`;
    const tr = s.trend || 0;
    const trCls = tr > 0 ? 'up' : tr < 0 ? 'down' : '';
    const trIc = tr !== 0 ? `<svg class="ic" style="width:11px;height:11px"><use href="#${tr > 0 ? 'i-up' : 'i-down'}"/></svg>` : '';
    const lo = (m.min != null ? m.min : 0), hi = (m.max != null && m.max !== m.min ? m.max : 100);
    const sc = v => Math.min(Math.max((v - lo) / (hi - lo) * 100, 0), 100);
    let bar = '';
    if (s.target) {
      const tL = sc(s.target[0]), tW = sc(s.target[1]) - tL;
      bar = `<div class="rbar"><div class="zone" style="left:${tL}%;width:${tW}%"></div>
        <div class="val ${cls === 'ok' || cls === 'stale' ? '' : cls}" style="left:${sc(s.value)}%"></div></div>`;
    }
    const hist = state.history[sid];
    const pts = hist ? hist.points.slice(-40) : null;
    return `<div class="sensor"><div class="sensor-top"><svg class="ic"><use href="#${sensorIcon(sid)}"/></svg>${esc(displayName(sid))}${pill}</div>
      <div class="sensor-val num">${s.value.toFixed(1)}<small>${esc(unit)}</small></div>
      <div class="sensor-trend ${trCls}">${trIc}${tr !== 0 ? `${Math.abs(tr).toFixed(1)}/day` : 'stable'} · 7d</div>
      ${sparkline(pts, sensorColor(sid))}
      ${bar}
      <div class="sensor-meta num">${multi ? `avg · ` : ''}${s.target ? `target ${s.target[0]}–${s.target[1]} ${esc(unit)} · ` : ''}${timeAgo(s.age_sec)}</div>
    </div>`;
  }).join('');
}

// ── Chart (hand-rolled SVG, dynamic dual axis) ──────────────────────────────
async function loadChart(hours, skipRender) {
  state.historyHours = hours;
  document.querySelectorAll('#range-seg button')
    .forEach(b => b.classList.toggle('active', +b.dataset.h === hours));
  try {
    state.history = await api(`/api/history?hours=${hours}`);
  } catch (e) {
    console.error('History load failed:', e);
    state.history = {};
  }
  drawChart();
  if (!skipRender) renderSensorCards(state.sensors);
}

function drawChart() {
  const svg = $('#chart'), tip = $('#chart-tip');
  const ids = aggregateIds(state.history).filter(sid => (state.history[sid].points || []).length > 1);
  if (!ids.length) {
    const box = svg.parentElement;
    const fresh = svg.cloneNode(false);
    fresh.setAttribute('id', 'chart');
    fresh.setAttribute('role', 'img');
    fresh.setAttribute('aria-label', 'Sensor history chart');
    svg.replaceWith(fresh);
    $('#chart-legend').innerHTML = '';
    tip.style.display = 'none';
    let empty = box.querySelector('.chart-empty');
    if (!empty) { empty = document.createElement('div'); empty.className = 'chart-empty empty-block'; box.appendChild(empty); }
    empty.textContent = 'No history for this range yet';
    return;
  }
  const old = svg.parentElement.querySelector('.chart-empty');
  if (old) old.remove();

  const W = svg.clientWidth || 900, H = 260, P = { l: 40, r: 40, t: 12, b: 26 };
  const iw = W - P.l - P.r, ih = H - P.t - P.b;
  const series = ids.map(sid => {
    const m = metaFor(sid);
    const unit = unitLabel(m.unit || state.history[sid].unit);
    let points = state.history[sid].points;
    if (points.length > 240) {
      const step = Math.ceil(points.length / 240);
      points = points.filter((_, i) => i % step === 0);
    }
    return { sid, label: displayName(sid), unit, color: sensorColor(sid), points,
      temp: isTempSid(sid, m.unit || state.history[sid].unit) };
  });
  const left = series.filter(s => s.temp), right = series.filter(s => !s.temp);
  if (!left.length && right.length) { left.push(...right.splice(0, right.length)); }
  const extent = list => {
    const vs = list.flatMap(s => s.points.map(p => p.value));
    const lo = Math.min(...vs), hi = Math.max(...vs), pad = ((hi - lo) || Math.abs(hi) || 1) * 0.12;
    return [lo - pad, hi + pad];
  };
  const [lLo, lHi] = extent(left), [rLo, rHi] = right.length ? extent(right) : [0, 1];
  const t0 = Math.min(...series.map(s => s.points[0].ts));
  const t1 = Math.max(...series.map(s => s.points[s.points.length - 1].ts));
  const X = ts => P.l + ((ts - t0) / ((t1 - t0) || 1)) * iw;
  const Y = (v, isLeft) => {
    const [lo, hi] = isLeft ? [lLo, lHi] : [rLo, rHi];
    return P.t + (1 - (v - lo) / ((hi - lo) || 1)) * ih;
  };
  const line = s => s.points.map((p, i) => `${i ? 'L' : 'M'}${X(p.ts).toFixed(1)},${Y(p.value, left.includes(s)).toFixed(1)}`).join(' ');

  let grid = '';
  for (let g = 0; g <= 4; g++) {
    const y = P.t + (g / 4) * ih;
    grid += `<line x1="${P.l}" y1="${y}" x2="${W - P.r}" y2="${y}" stroke="#1a221c" stroke-width="1"/>
      <text x="${P.l - 7}" y="${y + 4}" text-anchor="end" font-size="10" fill="#68736a">${(lHi - (lHi - lLo) * g / 4).toFixed(0)}</text>`;
    if (right.length) grid += `<text x="${W - P.r + 7}" y="${y + 4}" font-size="10" fill="#68736a">${(rHi - (rHi - rLo) * g / 4).toFixed(0)}</text>`;
  }
  for (let g = 0; g < 6; g++) {
    const ts = t0 + ((t1 - t0) * g) / 5, d = new Date(ts);
    const lbl = state.historyHours > 48
      ? d.toLocaleDateString([], { month: 'short', day: 'numeric' })
      : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    grid += `<text x="${P.l + (g / 5) * iw}" y="${H - 6}" text-anchor="middle" font-size="10" fill="#68736a">${lbl}</text>`;
  }
  $('#chart-legend').innerHTML = series.map(s =>
    `<span><i style="background:${s.color}"></i>${esc(s.label)}${s.unit ? ` (${esc(s.unit)})` : ''}</span>`).join('');

  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.innerHTML = grid + series.map((s, i) =>
    `<path d="${line(s)}" fill="none" stroke="${s.color}" stroke-width="${s.temp ? 2.2 : 1.8}" opacity=".9"/>`
  ).join('') + `<line id="ch-x" y1="${P.t}" y2="${P.t + ih}" stroke="#3a453d" stroke-width="1" style="display:none"/>`
    + series.map((s, i) => `<circle id="ch-d${i}" r="3.5" fill="${s.color}" style="display:none"/>`).join('');

  const lx = svg.querySelector('#ch-x');
  const dots = series.map((_, i) => svg.querySelector(`#ch-d${i}`));
  const flat = series[0].points;
  svg.onmousemove = ev => {
    const r = svg.getBoundingClientRect(), mx = (ev.clientX - r.left) * (W / r.width);
    let best = 0, bd = Infinity;
    flat.forEach((p, i) => { const d = Math.abs(X(p.ts) - mx); if (d < bd) { bd = d; best = i; } });
    const cx = X(flat[best].ts);
    lx.setAttribute('x1', cx); lx.setAttribute('x2', cx); lx.style.display = '';
    let rows = '';
    series.forEach((s, i) => {
      let bi = 0, bdd = Infinity;
      s.points.forEach((p, j) => { const d = Math.abs(p.ts - flat[best].ts); if (d < bdd) { bdd = d; bi = j; } });
      const v = s.points[bi].value;
      dots[i].setAttribute('cx', cx); dots[i].setAttribute('cy', Y(v, left.includes(s))); dots[i].style.display = '';
      rows += `<span style="color:${s.color}">●</span> <span class="num">${v.toFixed(1)}${s.unit ? esc(s.unit) : ''}</span><br>`;
    });
    tip.innerHTML = `<div class="tt num">${new Date(flat[best].ts).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</div>${rows}`;
    tip.style.display = 'block';
    tip.style.left = Math.min((cx / W) * r.width + 14, r.width - 160) + 'px';
    tip.style.top = '10px';
  };
  svg.onmouseleave = () => { tip.style.display = 'none'; lx.style.display = 'none'; dots.forEach(c => c.style.display = 'none'); };
}

// ── Actuators ───────────────────────────────────────────────────────────────
function renderActuators(actuators) {
  const el = $('#actuator-list');
  if (!actuators || !actuators.length) {
    el.innerHTML = '<div class="empty-block">No actuators configured</div>';
    return;
  }
  el.innerHTML = actuators.map(a => {
    const isOn = a.state === 'on', known = a.state === 'on' || a.state === 'off';
    return `<div class="act-row ${isOn ? 'is-on' : ''}">
      <span class="act-ic"><svg class="ic"><use href="#${actuatorIcon(a.id)}"/></svg></span>
      <div class="act-info"><div class="act-name">${esc(a.name)}</div>
        <div class="act-sub num">${known ? (isOn ? 'ON' : 'OFF') : esc(a.state)}${a.ts ? ` · ${timeAgo(Date.now() / 1000 - a.ts)}` : ''} · ${esc(a.id)}</div></div>
      <label class="switch"><input type="checkbox" data-act="${esc(a.id)}" ${isOn ? 'checked' : ''} aria-label="${esc(a.name)}">
        <span class="sw-track"></span><span class="sw-thumb"></span></label>
    </div>`;
  }).join('');
  el.querySelectorAll('[data-act]').forEach(sw => sw.addEventListener('change', () => controlActuator(sw.dataset.act, sw.checked ? 'on' : 'off', sw)));
}

async function controlActuator(id, value, sw) {
  sw.disabled = true;
  try {
    const resp = await api(`/api/actuator/${id}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }),
    });
    if (resp.error) throw new Error(resp.error);
    setTimeout(async () => {
      try {
        state.actuators = await api('/api/actuators');
        renderActuators(state.actuators);
        if (state.view === 'nodes') { state.nodes = await api('/api/nodes'); renderNodeDetail(); }
      } catch (e) { console.error('Actuator refresh failed:', e); }
    }, 800);
  } catch (e) {
    sw.checked = !sw.checked;
    alert(`Failed to control ${id}: ${e.message}`);
    sw.disabled = false;
  }
}

// ── Last decision + feeds ───────────────────────────────────────────────────
function cmdBadge(key, val) {
  const isPwm = typeof val === 'number';
  const cls = isPwm ? 'pwm' : val === 'on' ? 'on' : 'off';
  const label = isPwm ? `${key}: ${val}%` : `${key}: ${val}`;
  return `<span class="cmd ${cls}">${esc(label)}</span>`;
}

function renderLastDecision(d) {
  const el = $('#last-decision');
  if (!d) { el.innerHTML = '<div class="empty-block">No decisions yet</div>'; return; }
  el.innerHTML = `
    <div class="dec-meta"><span class="num">${fmtDateTime(d.ts)}</span><span>·</span><span class="num">${esc(d.model)}</span><span>·</span><span class="num">${d.latency_ms}ms</span></div>
    <div class="cmds">${Object.entries(d.commands).map(([k, v]) => cmdBadge(k, v)).join('')}</div>
    <p class="dec-reason">${esc(d.reason)}</p>
    ${d.was_overridden ? '<div class="dec-flag"><svg class="ic"><use href="#i-shield"/></svg>Safety override applied</div>' : ''}`;
}

const EVENT_ICON = {
  SENSOR_ANOMALY: 'i-warn', SAFETY_OVERRIDE: 'i-shield', ACTUATOR_FIRED: 'i-plug',
  DOMAIN_STARTED: 'i-info', POLL_ERROR: 'i-x', LLM_FAILURE_SAFE_OFF: 'i-shield',
  DURATION_LIMIT: 'i-clock', ACTUATOR_NO_EFFECT: 'i-eye', OBSERVATION: 'i-eye',
  REFLECTION: 'i-chat', PHASE_CHANGE: 'i-sprout',
};
function feedRow(e) {
  const sev = e.severity === 'error' ? 'err' : e.severity === 'warn' ? 'warn' : 'info';
  return `<div class="feed-row"><span class="feed-ic fi-${sev}"><svg class="ic"><use href="#${EVENT_ICON[e.type] || 'i-info'}"/></svg></span>
    <div class="feed-body"><span class="feed-type">${esc(e.type.replace(/_/g, ' '))}</span>
    <div class="feed-note">${esc(e.note || '')}</div></div>
    <span class="feed-ts num">${fmtDateTime(e.ts)}</span></div>`;
}

function renderRecentFeed(events) {
  $('#recent-feed').innerHTML = events.length ? events.map(feedRow).join('')
    : '<div class="empty-block">No recent activity</div>';
}

async function updateEventBadge() {
  try {
    const events = await api('/api/events?limit=50&hours=48');
    const n = events.filter(e => e.severity === 'warn' || e.severity === 'error').length;
    const badge = $('#events-badge');
    badge.hidden = n === 0;
    badge.textContent = n;
  } catch (e) { /* badge stays hidden */ }
}

// ── Nodes view ──────────────────────────────────────────────────────────────
async function loadNodes() {
  const [nodes, profile] = await Promise.all([api('/api/nodes'), api('/api/profile')]);
  state.nodes = nodes; state.profile = profile;
  if (!state.activeNode || !nodes.some(n => n.id === state.activeNode)) {
    state.activeNode = nodes.length ? nodes[0].id : null;
  }
  renderNodeList(); renderNodeDetail(); renderMatrix();
  renderNodeSummary(nodes);
}

function activeTargets() {
  const p = (state.profile && state.profile.phases || []).find(x => x.status === 'active') || {};
  return p.targets || {};
}

function nodeSensClass(sid, value) {
  const t = activeTargets()[sid];
  if (!t) return '';
  if (value < t[0]) return 'warn';
  if (value > t[1]) return sid.toLowerCase().includes('temp') ? 'bad' : 'warn';
  return 'ok';
}

function renderNodeList() {
  const el = $('#node-list');
  if (!state.nodes.length) { el.innerHTML = '<div class="panel dim" style="text-align:center">No nodes yet</div>'; return; }
  el.innerHTML = state.nodes.map(n => `
    <button class="node-item ${n.id === state.activeNode ? 'active' : ''}" data-node="${esc(n.id)}">
      <span class="node-dot${n.online ? '' : ' stale'}"></span>
      <span><span class="n-id">${esc(n.id)}</span>
      <span class="n-sub">${Object.keys(n.sensors).length} sensors · seen ${n.last_seen ? timeAgo(Date.now() / 1000 - n.last_seen) : 'never'}</span></span>
    </button>`).join('');
  el.querySelectorAll('[data-node]').forEach(b => b.addEventListener('click', () => {
    state.activeNode = b.dataset.node;
    renderNodeList(); renderNodeDetail();
  }));
}

function renderNodeDetail() {
  const el = $('#node-detail');
  const n = state.nodes.find(x => x.id === state.activeNode);
  if (!n) { el.innerHTML = '<div class="empty-block">Select a node</div>'; return; }
  const targets = activeTargets();
  const cards = Object.entries(n.sensors).map(([sid, s]) => {
    const m = metaFor(sid), unit = unitLabel(m.unit || s.unit);
    const t = targets[sid], cls = nodeSensClass(sid, s.value);
    return `<div class="nsensor ${cls}"><div class="t-k"><svg class="ic" style="width:13px;height:13px"><use href="#${sensorIcon(sid)}"/></svg>${esc(displayName(sid))}</div>
      <div class="t-v num">${s.value.toFixed(1)} <small>${esc(unit)}</small></div>
      <div class="t-r num">${t ? `target ${t[0]}–${t[1]} ${esc(unit)} · ` : ''}${timeAgo(s.age_sec)}</div></div>`;
  }).join('') || '<div class="dim" style="font-size:13px">No readings from this node yet.</div>';
  const acts = n.actuators.map(ref => {
    const full = state.actuators.find(a => a.id === ref.id);
    const st = full ? full.state : ref.state;
    const isOn = st === 'on', known = st === 'on' || st === 'off';
    const name = full ? full.name : ref.id;
    const ts = full ? full.ts : ref.ts;
    return `<div class="act-row ${isOn ? 'is-on' : ''}">
      <span class="act-ic"><svg class="ic"><use href="#${actuatorIcon(ref.id)}"/></svg></span>
      <div class="act-info"><div class="act-name">${esc(name)}</div>
        <div class="act-sub num">${known ? (isOn ? 'ON' : 'OFF') : esc(st)}${ts ? ` · ${timeAgo(Date.now() / 1000 - ts)}` : ''} · ${esc(ref.id)}</div></div>
      <label class="switch"><input type="checkbox" data-nact="${esc(ref.id)}" ${isOn ? 'checked' : ''} aria-label="${esc(name)}">
        <span class="sw-track"></span><span class="sw-thumb"></span></label>
    </div>`;
  }).join('') || '<div class="dim" style="font-size:13px">No actuators assigned to this node.</div>';
  const topicRoot = n.id === 'local'
    ? `${n.topic_prefix || '…'}/#`
    : `${n.topic_prefix || '…'}/nodes/${n.id}/#`;
  el.innerHTML = `
    <div class="nd-head"><span class="node-dot${n.online ? '' : ' stale'}"></span><h2>${esc(n.id)}</h2>
      <span class="chip ${n.online ? 'green' : 'amber'}" style="font-size:11px">${n.online ? 'online' : 'stale'}</span>
      <span class="dim num" style="font-size:12px;margin-left:auto">seen ${n.last_seen ? timeAgo(Date.now() / 1000 - n.last_seen) : 'never'}</span></div>
    <div class="nd-meta">
      <div class="m"><div class="k">Adapter</div><div class="v">${esc(n.adapter || '—')}</div></div>
      <div class="m"><div class="k">Broker</div><div class="v">${esc(n.broker || '—')}</div></div>
      <div class="m"><div class="k">Topic root</div><div class="v">${esc(topicRoot)}</div></div>
      <div class="m"><div class="k">Sensors</div><div class="v">${Object.keys(n.sensors).length}</div></div>
      <div class="m"><div class="k">Actuators</div><div class="v">${n.actuators.length}</div></div>
      <div class="m"><div class="k">Mode</div><div class="v">${state.nodes.length > 1 ? 'multi-node' : 'single'}</div></div>
    </div>
    <div class="nd-sub">Sensors on this node</div>
    <div class="node-sensors">${cards}</div>
    <div class="nd-sub">Actuators on this node</div>
    <div>${acts}</div>`;
  el.querySelectorAll('[data-nact]').forEach(sw => sw.addEventListener('change', async () => {
    const id = sw.dataset.nact;
    await controlActuator(id, sw.checked ? 'on' : 'off', sw);
    try { state.nodes = await api('/api/nodes'); renderNodeDetail(); } catch (e) { /* keep optimistic state */ }
  }));
}

function renderMatrix() {
  const nodes = state.nodes;
  $('#matrix-head').innerHTML = '<th>Sensor</th>' + nodes.map(n => `<th class="num">${esc(n.id)}</th>`).join('')
    + (nodes.length > 1 ? '<th class="num">Fleet avg</th>' : '') + '<th>Target</th>';
  const targets = activeTargets();
  const sids = [...new Set(nodes.flatMap(n => Object.keys(n.sensors)))];
  const ordered = [...sids].sort((a, b) => (state.metaOrder.indexOf(a) + 999) - (state.metaOrder.indexOf(b) + 999));
  $('#matrix-body').innerHTML = ordered.map(sid => {
    const m = metaFor(sid), unit = unitLabel(m.unit);
    const vals = nodes.map(n => n.sensors[sid] ? n.sensors[sid].value : null);
    const known = vals.filter(v => v !== null);
    const avg = known.length ? known.reduce((a, b) => a + b, 0) / known.length : null;
    const t = targets[sid];
    return `<tr><td>${esc(displayName(sid))}${unit ? ` <span class="dim">(${esc(unit)})</span>` : ''}</td>`
      + vals.map(v => `<td class="num">${v === null ? '<span class="dim">—</span>' : v.toFixed(1)}</td>`).join('')
      + (nodes.length > 1 ? `<td class="num avg">${avg === null ? '—' : avg.toFixed(1)}</td>` : '')
      + `<td class="dim num" style="font-size:12px">${t ? `${t[0]}–${t[1]} ${esc(unit)}` : '—'}</td></tr>`;
  }).join('') || '<tr><td class="dim" colspan="5">No node readings yet</td></tr>';
}

// ── Profile view ────────────────────────────────────────────────────────────
async function loadProfile() {
  const [profile, sensors] = await Promise.all([api('/api/profile'), api('/api/sensors/latest')]);
  state.profile = profile; state.sensors = sensors;
  $('#pf-name').textContent = profile.name || 'No profile loaded';
  $('#pf-cultivar').textContent = [profile.cultivar, state.status && `${state.status.domain} domain`].filter(Boolean).join(' · ');
  $('#pf-notes').textContent = profile.notes || '';
  const stats = [];
  if (profile.started_at) stats.push(`Started <strong>${new Date(profile.started_at * 1000).toLocaleDateString()}</strong>`);
  stats.push(`Day <strong class="num">${profile.current_day}${profile.expected_duration_days ? ` of ${profile.expected_duration_days}` : ''}</strong>`);
  if (profile.current_phase) stats.push(`Phase <strong>${esc(profile.current_phase)}</strong>`);
  if (profile.harvest_days) stats.push(`Harvest at day <strong class="num">${profile.harvest_days}</strong>`);
  if (profile.days_until_next_phase) stats.push(`<strong class="num">${profile.days_until_next_phase}d</strong> until next phase`);
  $('#pf-stats').innerHTML = stats.map(s => `<span class="pf-stat">${s}</span>`).join('');

  $('#phases-list').innerHTML = (profile.phases || []).map(p => {
    const rows = Object.entries(p.targets || {}).map(([sid, r]) => {
      const m = metaFor(sid), unit = unitLabel(m.unit);
      const cur = sensors[sid];
      const lo = (m.min != null ? m.min : 0), hi = (m.max != null && m.max !== m.min ? m.max : 100);
      const sc = v => Math.min(Math.max((v - lo) / (hi - lo) * 100, 0), 100);
      const show = p.status === 'active' && cur;
      const st = cur ? (cur.value < r[0] ? 'low' : cur.value > r[1] ? 'high' : 'ok') : '';
      return `<div class="pt-row"><span>${esc(displayName(sid))}</span><span class="rng num">${r[0]}–${r[1]} ${esc(unit)}</span>
        <div class="rbar" style="margin:0"><div class="zone" style="left:${sc(r[0])}%;width:${sc(r[1]) - sc(r[0])}%"></div>
        ${show ? `<div class="val ${st === 'ok' ? '' : st === 'low' ? 'warn' : 'bad'}" style="left:${sc(cur.value)}%"></div>` : ''}</div>
        <span class="cur ${show ? st : ''} num">${show ? cur.value.toFixed(1) : ''}</span></div>`;
    }).join('');
    const hi = p.days[1] > 900 ? '∞' : p.days[1];
    return `<div class="phase-block ${p.status}"><div class="pb-head"><span class="pb-dot"></span>
      <span class="pb-name">${esc(p.name)}</span><span class="pb-days">day ${p.days[0]}–${hi}</span></div>
      ${p.notes ? `<div class="pb-notes">${esc(p.notes)}</div>` : ''}${rows}</div>`;
  }).join('') || '<div class="panel dim" style="text-align:center">No phases defined</div>';
}

// ── Chat ────────────────────────────────────────────────────────────────────
function renderMiniMd(text) {
  // Fail-closed subset: escape first, then inline code, bold, blocks.
  let h = esc(text);
  const codes = [];
  h = h.replace(/`([^`\n]+)`/g, (_, c) => { codes.push(`<code>${c}</code>`); return `\u0000${codes.length - 1}\u0000`; });
  h = h.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  const lines = h.split('\n');
  let out = '', inList = false;
  const closeList = () => { if (inList) { out += '</ul>'; inList = false; } };
  for (const ln of lines) {
    const t = ln.trim();
    if (/^-\s+/.test(t)) {
      if (!inList) { out += '<ul>'; inList = true; }
      out += `<li>${t.replace(/^-\s+/, '')}</li>`;
    } else if (/^#{1,3}\s+/.test(t)) {
      closeList();
      out += `<h3>${t.replace(/^#{1,3}\s+/, '')}</h3>`;
    } else if (/^&gt;\s?/.test(t)) {
      closeList();
      out += `<blockquote>${t.replace(/^&gt;\s?/, '')}</blockquote>`;
    } else if (t === '---') {
      closeList();
      out += '<hr>';
    } else if (t === '') {
      closeList();
    } else {
      closeList();
      out += `<p>${t}</p>`;
    }
  }
  closeList();
  return out.replace(/\u0000(\d+)\u0000/g, (_, i) => codes[+i]);
}

function renderChat() {
  const el = $('#chat-log');
  if (!state.chat.length) {
    el.innerHTML = '<div class="sys">No messages yet — note an observation or ask a question below</div>';
    return;
  }
  el.innerHTML = state.chat.map(m => {
    if (m.role !== 'user' && m.type === 'system') return `<div class="sys">${esc(m.text)}</div>`;
    const isUser = m.role === 'user';
    const label = isUser ? 'You' : `Muse${m.model ? ' · ' + esc(m.model) : ''}`;
    const body = isUser ? `<div class="bubble"><p>${esc(m.text)}</p></div>` : `<div class="bubble">${renderMiniMd(m.text)}</div>`;
    return `<div class="msg ${isUser ? 'u' : 'a'}"><div class="msg-meta"><span class="num">${esc(m.ts_str || '')}</span><span class="who">${label}</span></div>${body}</div>`;
  }).join('');
  el.scrollTop = el.scrollHeight;
}

async function loadChat() {
  try {
    state.chat = await api('/api/chat_history');
  } catch (e) {
    state.chat = [];
    $('#chat-log').innerHTML = `<div class="sys">Could not load history: ${esc(e.message)}</div>`;
    return;
  }
  renderChat();
}

async function sendChat() {
  const inp = $('#chat-in'), btn = $('#chat-send');
  const text = inp.value.trim();
  if (!text || inp.disabled) return;
  inp.value = ''; inp.disabled = true; btn.disabled = true;
  state.chat.push({ role: 'user', type: text.endsWith('?') ? 'question' : 'observation', text, ts_str: new Date().toLocaleString() });
  renderChat();
  const log = $('#chat-log');
  const tp = document.createElement('div');
  tp.className = 'msg a';
  tp.innerHTML = '<div class="bubble typing"><i></i><i></i><i></i></div>';
  log.appendChild(tp); log.scrollTop = log.scrollHeight;
  try {
    const resp = await api('/api/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: text }),
    });
    state.chat.push({ role: 'assistant', type: resp.type, text: resp.response, model: resp.model || '', ts_str: new Date().toLocaleString() });
  } catch (e) {
    state.chat.push({ role: 'assistant', type: 'system', text: `Error: ${e.message}`, ts_str: new Date().toLocaleString() });
  }
  renderChat();
  inp.disabled = false; btn.disabled = false; inp.focus();
}

// ── Decisions ───────────────────────────────────────────────────────────────
async function loadDecisions() {
  state.decisions = await api('/api/decisions?limit=30');
  renderDecisions();
}

function renderDecisions() {
  const rows = state.decisions.filter(d => state.decFilter === 'all' || d.was_overridden);
  $('#decisions-body').innerHTML = rows.map(d => {
    const cmds = Object.entries(d.commands).map(([k, v]) => cmdBadge(k, v)).join('');
    return `<tr class="${d.was_overridden ? 'hl' : ''}"><td class="ts">${fmtDateTime(d.ts)}</td>
      <td><span class="phase-tag">${esc(d.phase)}</span></td>
      <td><div class="cmds mini">${cmds}</div>${d.was_overridden ? '<span class="ovr">SAFETY OVERRIDE</span>' : ''}</td>
      <td><span class="rsn">${esc(d.reason)}</span></td>
      <td class="model-cell">${esc(d.model)}<br>${d.latency_ms}ms</td></tr>`;
  }).join('') || '<tr><td class="dim" colspan="5">No decisions recorded yet</td></tr>';
}

// ── Events ──────────────────────────────────────────────────────────────────
async function loadEvents() {
  state.events = await api('/api/events?limit=50&hours=48');
  renderEvents();
  updateEventBadge();
}

function renderEvents() {
  const rows = state.events.filter(e => state.evFilter === 'all' || e.severity === state.evFilter);
  $('#events-feed').innerHTML = rows.length ? rows.map(feedRow).join('')
    : '<div class="empty-block">No events match this filter</div>';
}

// ── Readings ────────────────────────────────────────────────────────────────
async function loadReadings() {
  const [domainSensors, latest] = await Promise.all([api('/api/domain/sensors'), api('/api/sensors/latest')]);
  const byPlugin = {};
  domainSensors.forEach(s => { (byPlugin[s.plugin] = byPlugin[s.plugin] || []).push(s); });
  const groups = Object.entries(byPlugin);
  if (!groups.length) {
    $('#readings-groups').innerHTML = '<div class="panel dim" style="text-align:center">No sensors configured.</div>';
    return;
  }
  $('#readings-groups').innerHTML = groups.map(([plugin, sensors], gi) => {
    const title = plugin.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    const rows = sensors.map(s => {
      const cur = latest[s.id];
      const curStr = cur ? `${cur.value.toFixed(1)} ${unitLabel(s.unit)} · ${timeAgo(cur.age_sec)}` : '—';
      const key = `${gi}-${s.id}`;
      return `<div class="read-row"><div><div class="read-name">${esc(s.name)} <span class="dim" style="font-weight:400">(${esc(unitLabel(s.unit))})</span></div>
          <div class="read-sub">current <span class="cur-now num" id="cur-${key}">${esc(curStr)}</span></div></div>
        <input class="read-in num" type="number" step="any"
          ${s.min != null ? `min="${s.min}"` : ''} ${s.max != null ? `max="${s.max}"` : ''}
          placeholder="${s.min != null && s.max != null ? `${s.min} – ${s.max}` : ''}"
          id="in-${key}" aria-label="${esc(s.name)}" data-sid="${esc(s.id)}" data-unit="${esc(unitLabel(s.unit))}">
        <button class="btn ghost" data-save="${key}" style="justify-content:center">Save</button>
        <div class="read-fb" id="fb-${key}"></div>
        ${s.hint ? `<div class="read-hint">${esc(s.hint)}</div>` : ''}</div>`;
    }).join('');
    return `<div class="panel read-group"><div class="panel-title"><h2 style="font-size:13px">${esc(title)}</h2></div>${rows}</div>`;
  }).join('');
  document.querySelectorAll('[data-save]').forEach(b => b.addEventListener('click', () => submitReading(b.dataset.save)));
  document.querySelectorAll('.read-in').forEach(inp => inp.addEventListener('keydown', e => {
    if (e.key === 'Enter') submitReading(inp.id.replace(/^in-/, ''));
  }));
}

async function submitReading(key) {
  const inp = $(`#in-${CSS.escape(key)}`), fb = $(`#fb-${key}`);
  const sid = inp.dataset.sid, unit = inp.dataset.unit;
  const v = parseFloat(inp.value);
  if (inp.value.trim() === '' || isNaN(v)) { fb.textContent = 'Enter a number'; fb.classList.add('err'); return; }
  inp.disabled = true;
  fb.classList.remove('err');
  fb.textContent = 'Saving…';
  try {
    const resp = await api(`/api/sensor/${sid}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value: v }),
    });
    if (resp.error) throw new Error(resp.error);
    const cur = $(`#cur-${CSS.escape(key)}`);
    if (cur) cur.textContent = `${v.toFixed(1)} ${unit} · just now`;
    inp.value = '';
    fb.textContent = '✓ Saved';
    setTimeout(() => { fb.textContent = ''; }, 3000);
  } catch (e) {
    fb.textContent = `Error: ${e.message}`;
    fb.classList.add('err');
  } finally {
    inp.disabled = false;
    inp.focus();
  }
}

// ── SSE live stream ─────────────────────────────────────────────────────────
function connectStream() {
  const dot = $('#conn-dot'), label = $('#conn-label');
  const es = new EventSource('/api/stream');
  es.onopen = () => { dot.classList.remove('off'); label.textContent = 'Live'; };
  es.onerror = () => { dot.classList.add('off'); label.textContent = 'Reconnecting…'; };
  es.onmessage = async e => {
    try {
      const payload = JSON.parse(e.data);
      if (payload.error) return;
      if (payload.sensors) state.sensors = payload.sensors;
      if (payload.status) {
        state.status = payload.status;
        $('#day-chip').textContent = `Day ${payload.status.day}`;
        $('#phase-chip').textContent = payload.status.phase;
      }
      if (state.view === 'dashboard' && payload.sensors) {
        renderSensorCards(payload.sensors);
        if (state.profile) renderProfileBanner(state.profile, payload.sensors);
        if (payload.status && payload.status.last_decision) renderLastDecision(payload.status.last_decision);
        if (payload.actuators) { state.actuators = payload.actuators; renderActuators(payload.actuators); }
        try {
          state.nodes = await api('/api/nodes');
          renderNodeSummary(state.nodes);
          const recent = await api('/api/events?limit=5&hours=48');
          renderRecentFeed(recent);
          updateEventBadge();
        } catch (err) { /* keep stale panels on transient failure */ }
      }
      if (state.view === 'nodes') {
        try {
          state.nodes = await api('/api/nodes');
          renderNodeList(); renderNodeDetail(); renderMatrix();
        } catch (err) { /* keep stale */ }
      }
    } catch { /* malformed message — ignore */ }
  };
}

// ── Init ────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  document.querySelectorAll('.nav-btn').forEach(b => b.addEventListener('click', () => goto(b.dataset.view)));
  document.querySelectorAll('[data-goto]').forEach(b => b.addEventListener('click', () => goto(b.dataset.goto)));
  document.querySelectorAll('#range-seg button').forEach(b => b.addEventListener('click', () => loadChart(+b.dataset.h)));
  document.querySelectorAll('#ev-filters .fchip').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('#ev-filters .fchip').forEach(x => x.classList.remove('active'));
    b.classList.add('active'); state.evFilter = b.dataset.f; renderEvents();
  }));
  document.querySelectorAll('#dec-filters .fchip').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('#dec-filters .fchip').forEach(x => x.classList.remove('active'));
    b.classList.add('active'); state.decFilter = b.dataset.f; renderDecisions();
  }));
  $('#chat-send').addEventListener('click', sendChat);
  $('#chat-in').addEventListener('keydown', e => { if (e.key === 'Enter') sendChat(); });
  window.addEventListener('resize', () => { if (state.view === 'dashboard') drawChart(); });

  try {
    const meta = await api('/api/domain/sensors');
    meta.forEach(s => {
      if (!state.sensorMeta[s.id]) {
        state.sensorMeta[s.id] = s;
        state.metaOrder.push(s.id);
      }
    });
  } catch (e) { console.error('Sensor metadata failed:', e); }

  connectStream();
  goto('dashboard');

  setInterval(() => { LOADERS[state.view]().catch(e => console.error('Refresh failed:', e)); }, 120_000);
});
