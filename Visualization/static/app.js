'use strict';

// ── layout constants ──────────────────────────────────────────────────────────
const BIN_PX    = 13;
const BIN_STEP  = 14;   // bin + 1px gap
const AISLE_PAD = 4;
const LABEL_H   = 15;
const AISLE_GAP = 10;
const GRID_COLS = 6;
const MARGIN    = 16;

// ── simulation constants ──────────────────────────────────────────────────────
const CART_CAP       = 50 * 50 * 50;
const PICKER_COLORS  = ['#2196F3', '#FF5722', '#4CAF50'];
const STATUS_COLORS  = {
  traveling: '#aec6cf',
  picking:   '#ffad60',
  cart_swap: '#ff6b6b',
  idle:      '#888888',
};
const _STATUS_MAP = {
  task_start: 'traveling',
  arrive:     'picking',
  cart_swap:  'cart_swap',
  pick:       'traveling',
  task_end:   'traveling',
  done:       'idle',
};

// ── state ─────────────────────────────────────────────────────────────────────
let sim         = null;   // full API response
let aisleById   = {};     // aisle_id → aisle object
let initialBins = {};     // bin key → {sku, qty}
let pickerLocs  = {};     // pid → [{time, location}] (location-bearing events only)
let pickedKeys  = null;   // Set<string> of bin keys in any pick event
let currentT    = 0;
let playing     = false;
let lastTS      = null;
let animFrame   = null;
let speed       = 5;

// ── DOM refs ──────────────────────────────────────────────────────────────────
const svgEl       = document.getElementById('warehouse-svg');
const slider      = document.getElementById('time-slider');
const timeDisp    = document.getElementById('time-display');
const playBtn     = document.getElementById('play-btn');
const resetBtn    = document.getElementById('reset-btn');
const speedSel    = document.getElementById('speed-select');
const loadingMsg  = document.getElementById('loading-msg');
const pickerPanel = document.getElementById('picker-cards');

// ── bootstrap ─────────────────────────────────────────────────────────────────
fetch('/api/simulation')
  .then(r => r.json())
  .then(data => {
    sim = data;

    for (const a of sim.aisles) {
      aisleById[a.aisle_id] = a;
      for (const b of a.bins) {
        if (b.sku !== null) initialBins[b.key] = { sku: b.sku, qty: b.qty };
      }
    }

    pickerLocs = {};
    for (let pid = 0; pid < sim.num_pickers; pid++) pickerLocs[pid] = [];
    for (const e of sim.events) {
      if (e.location !== null) pickerLocs[e.picker_id].push(e);
    }

    pickedKeys = new Set(
      sim.events
        .filter(e => e.event_type === 'pick' && e.location)
        .map(e => e.location.join(','))
    );

    slider.max   = sim.max_time;
    slider.step  = 0.5;
    slider.value = 0;

    buildSVG();
    buildPickerCards();
    render(0);

    loadingMsg.textContent = `${sim.events.length} events · max t = ${sim.max_time.toFixed(1)}`;
  })
  .catch(err => {
    loadingMsg.textContent = 'Error loading simulation: ' + err.message;
  });

// ── SVG layout helpers ────────────────────────────────────────────────────────
function _maxBayX() { return Math.max(...sim.aisles.map(a => a.bay_x)); }
function _maxBayY() { return Math.max(...sim.aisles.map(a => a.bay_y)); }

function _cellW()  { return _maxBayX() * BIN_STEP + 2 * AISLE_PAD + AISLE_GAP; }
function _cellH()  { return LABEL_H + _maxBayY() * BIN_STEP + 2 * AISLE_PAD + AISLE_GAP; }

function _aisleOrigin(a) {
  return {
    x: MARGIN + a.grid_col * _cellW(),
    y: MARGIN + a.grid_row * _cellH(),
  };
}

function binCentre(aisle_id, bx, by) {
  const a   = aisleById[aisle_id];
  const org = _aisleOrigin(a);
  return {
    cx: org.x + AISLE_PAD + (bx - 1) * BIN_STEP + BIN_PX / 2,
    cy: org.y + LABEL_H + AISLE_PAD + (by - 1) * BIN_STEP + BIN_PX / 2,
  };
}

// ── build SVG ─────────────────────────────────────────────────────────────────
function buildSVG() {
  const ns    = 'http://www.w3.org/2000/svg';
  const rows  = Math.ceil(sim.aisles.length / GRID_COLS);
  const W     = 2 * MARGIN + GRID_COLS * _cellW() - AISLE_GAP;
  const H     = 2 * MARGIN + rows * _cellH() - AISLE_GAP;

  svgEl.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svgEl.setAttribute('width',   W);
  svgEl.setAttribute('height',  H);

  const mkEl = (tag, attrs) => {
    const el = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    return el;
  };

  const binLayer    = mkEl('g', { id: 'bin-layer' });
  const pickerLayer = mkEl('g', { id: 'picker-layer' });

  for (const a of sim.aisles) {
    const org  = _aisleOrigin(a);
    const boxW = a.bay_x * BIN_STEP + 2 * AISLE_PAD;
    const boxH = LABEL_H + a.bay_y * BIN_STEP + 2 * AISLE_PAD;
    const bg   = a.handling_type === 'conveyable' ? '#1a2335' : '#231a35';

    // Aisle background
    binLayer.appendChild(mkEl('rect', {
      x: org.x, y: org.y, width: boxW, height: boxH,
      rx: 3, fill: bg, stroke: '#334', 'stroke-width': 1,
    }));

    // Aisle label: "A1 Food P" (storage_type + unit_type initial)
    const lbl = mkEl('text', {
      x: org.x + boxW / 2, y: org.y + 11,
      'text-anchor': 'middle', fill: '#6a7f99',
      'font-size': 8, 'font-family': 'monospace',
    });
    lbl.textContent =
      `A${a.aisle_id} ${a.storage_type.slice(0, 4)} ${a.unit_type[0].toUpperCase()}`;
    binLayer.appendChild(lbl);

    // Bins
    for (const b of a.bins) {
      const rx = org.x + AISLE_PAD + (b.x - 1) * BIN_STEP;
      const ry = org.y + LABEL_H + AISLE_PAD + (b.y - 1) * BIN_STEP;
      binLayer.appendChild(mkEl('rect', {
        id: 'bin-' + b.key,
        x: rx, y: ry, width: BIN_PX, height: BIN_PX,
        rx: 2,
        fill: b.sku !== null ? '#4CAF50' : '#2a2a3a',
      }));
    }
  }

  // Picker circles
  for (let pid = 0; pid < sim.num_pickers; pid++) {
    const g = mkEl('g', { id: `picker-${pid}` });
    g.style.display = 'none';

    const circle = mkEl('circle', {
      r: 8, cx: 0, cy: 0,
      fill: PICKER_COLORS[pid], stroke: 'white', 'stroke-width': 1.5,
    });
    const txt = mkEl('text', {
      'text-anchor': 'middle', dy: 4,
      'font-size': 8, 'font-weight': 'bold', fill: 'white',
    });
    txt.textContent = String(pid);

    g.appendChild(circle);
    g.appendChild(txt);
    pickerLayer.appendChild(g);
  }

  svgEl.appendChild(binLayer);
  svgEl.appendChild(pickerLayer);
}

// ── build picker info cards ────────────────────────────────────────────────────
function buildPickerCards() {
  pickerPanel.innerHTML = '';
  for (let pid = 0; pid < sim.num_pickers; pid++) {
    const card = document.createElement('div');
    card.className = 'picker-card';
    card.innerHTML = `
      <div class="card-header">
        <span class="picker-dot" style="background:${PICKER_COLORS[pid]}"></span>
        <span class="picker-name">Picker ${pid}</span>
        <span class="status-badge" id="status-${pid}">idle</span>
      </div>
      <div class="card-body">
        <div class="bar-label">
          <span>Bins</span><span id="bins-txt-${pid}">0 / 0</span>
        </div>
        <div class="bar-track">
          <div class="bar-fill progress-bar" id="bins-bar-${pid}" style="width:0%"></div>
        </div>
        <div class="bar-label">
          <span>Cart</span><span id="cart-txt-${pid}">0%</span>
        </div>
        <div class="bar-track">
          <div class="bar-fill cart-bar" id="cart-bar-${pid}" style="width:0%"></div>
        </div>
        <div class="items-line" id="items-${pid}">Items: 0 / 0</div>
      </div>`;
    pickerPanel.appendChild(card);
  }
}

// ── render ────────────────────────────────────────────────────────────────────
function render(t) {
  const binQty = binStatesAt(t);
  const active = activeBinsAt(t);

  // Update only stocked bins that have ever been picked (plus active)
  for (const key of pickedKeys) {
    const el = document.getElementById('bin-' + key);
    if (!el) continue;
    const init = initialBins[key];
    if (!init) continue;
    el.setAttribute('fill',
      active.has(key) ? '#00E5FF' : binColor(binQty[key] ?? 0, init.qty));
  }
  // Highlight active bins that may not be in pickedKeys yet
  for (const key of active) {
    const el = document.getElementById('bin-' + key);
    if (el) el.setAttribute('fill', '#00E5FF');
  }

  // Pickers
  for (let pid = 0; pid < sim.num_pickers; pid++) {
    const pos  = pickerPosAt(pid, t);
    const g    = document.getElementById(`picker-${pid}`);
    if (pos) {
      g.style.display = '';
      g.setAttribute('transform', `translate(${pos.cx.toFixed(1)},${pos.cy.toFixed(1)})`);
    } else {
      g.style.display = 'none';
    }

    const info    = pickerInfoAt(pid, t);
    const fill    = cartFillAt(pid, t);
    const binsDone  = info.bins_completed;
    const binsTotal = info.total_bins || 1;
    const binsPct   = (binsDone / binsTotal * 100).toFixed(0);
    const cartPct   = (fill * 100).toFixed(0);

    const statusEl = document.getElementById(`status-${pid}`);
    statusEl.textContent   = info.status;
    statusEl.style.background = STATUS_COLORS[info.status] || '#888';

    document.getElementById(`bins-txt-${pid}`).textContent = `${binsDone} / ${info.total_bins}`;
    document.getElementById(`bins-bar-${pid}`).style.width = binsPct + '%';
    document.getElementById(`cart-txt-${pid}`).textContent = cartPct + '%';
    document.getElementById(`cart-bar-${pid}`).style.width = cartPct + '%';
    document.getElementById(`items-${pid}`).textContent    =
      `Items: ${info.items_picked} / ${info.total_items}`;
  }
}

// ── query helpers ──────────────────────────────────────────────────────────────

function binStatesAt(t) {
  const qty = {};
  for (const [key, info] of Object.entries(initialBins)) qty[key] = info.qty;
  for (const e of sim.events) {
    if (e.time > t) break;
    if (e.event_type === 'pick' && e.location) {
      const key = e.location.join(',');
      if (key in qty) qty[key] = Math.max(0, qty[key] - (e.quantity || 0));
    }
  }
  return qty;
}

function activeBinsAt(t) {
  const active = new Set();
  for (let pid = 0; pid < sim.num_pickers; pid++) {
    // Walk backwards to find last event with a location for this picker
    for (let i = sim.events.length - 1; i >= 0; i--) {
      const e = sim.events[i];
      if (e.time > t) continue;
      if (e.picker_id !== pid) continue;
      if (e.location) {
        if (e.event_type === 'arrive') active.add(e.location.join(','));
        break;
      }
      if (e.event_type === 'pick' || e.event_type === 'task_end') break;
    }
  }
  return active;
}

function pickerPosAt(pid, t) {
  const evs = pickerLocs[pid];
  if (!evs || evs.length === 0) return null;

  // Before first known position: show at first position
  if (t <= evs[0].time) return _locPx(evs[0].location);

  // After last known position: stay at last
  if (t >= evs[evs.length - 1].time) return _locPx(evs[evs.length - 1].location);

  // Binary search for bounding events
  let lo = 0, hi = evs.length - 1;
  while (lo < hi - 1) {
    const mid = (lo + hi) >> 1;
    if (evs[mid].time <= t) lo = mid; else hi = mid;
  }
  const p0   = _locPx(evs[lo].location);
  const p1   = _locPx(evs[hi].location);
  const frac = (t - evs[lo].time) / (evs[hi].time - evs[lo].time);
  return { cx: p0.cx + (p1.cx - p0.cx) * frac, cy: p0.cy + (p1.cy - p0.cy) * frac };
}

function _locPx(loc) { return binCentre(loc[0], loc[1], loc[2]); }

function pickerInfoAt(pid, t) {
  let last = null;
  for (const e of sim.events) {
    if (e.time > t) break;
    if (e.picker_id === pid) last = e;
  }
  if (!last) return { status: 'idle', bins_completed: 0, total_bins: 0, items_picked: 0, total_items: 0 };
  return {
    status:         _STATUS_MAP[last.event_type] || 'idle',
    bins_completed: last.bins_completed,
    total_bins:     last.total_bins,
    items_picked:   last.items_picked,
    total_items:    last.total_items,
  };
}

function cartFillAt(pid, t) {
  let used = 0;
  for (const e of sim.events) {
    if (e.time > t) break;
    if (e.picker_id !== pid) continue;
    if (e.event_type === 'cart_swap') {
      used = 0;
    } else if (e.event_type === 'pick' && e.sku !== null) {
      used += (sim.sku_volumes[String(e.sku)] || 0) * (e.quantity || 0);
    }
  }
  return Math.min(used / CART_CAP, 1.0);
}

function binColor(qty, initialQty) {
  if (qty <= 0)            return '#3a2a2a';   // emptied
  if (qty >= initialQty)   return '#4CAF50';   // full
  if (qty / initialQty >= 0.5) return '#FF9800'; // partial ≥ 50%
  return '#f44336';                             // low < 50%
}

// ── controls ──────────────────────────────────────────────────────────────────
playBtn.addEventListener('click', () => {
  if (!sim) return;
  if (playing) {
    _pause();
  } else {
    if (currentT >= sim.max_time) currentT = 0;
    playing = true;
    playBtn.innerHTML = '&#9646;&#9646;';
    lastTS = null;
    animFrame = requestAnimationFrame(_animate);
  }
});

resetBtn.addEventListener('click', () => {
  _pause();
  currentT     = 0;
  slider.value = 0;
  timeDisp.textContent = 't = 0.0';
  if (sim) render(0);
});

slider.addEventListener('input', () => {
  currentT = parseFloat(slider.value);
  timeDisp.textContent = `t = ${currentT.toFixed(1)}`;
  if (!playing && sim) render(currentT);
});

speedSel.addEventListener('change', () => { speed = parseFloat(speedSel.value); });

function _pause() {
  playing = false;
  lastTS  = null;
  playBtn.innerHTML = '&#9654;';
  if (animFrame) { cancelAnimationFrame(animFrame); animFrame = null; }
}

function _animate(ts) {
  if (!playing) return;
  if (lastTS !== null) {
    currentT = Math.min(currentT + (ts - lastTS) / 1000 * speed, sim.max_time);
  }
  lastTS = ts;

  slider.value         = currentT;
  timeDisp.textContent = `t = ${currentT.toFixed(1)}`;
  render(currentT);

  if (currentT < sim.max_time) {
    animFrame = requestAnimationFrame(_animate);
  } else {
    _pause();
  }
}
