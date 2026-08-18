/* calltree viewer — renders window.CALLTREE_RUNS (from data.js).
 * Zero dependencies, no fetch, deterministic iteration (data.js is pre-sorted). */
'use strict';

const RUNS = window.CALLTREE_RUNS || [];
const SECTION_COLORS = ['#58a6ff', '#3fb950', '#e3b341', '#f85149', '#bc8cff',
                        '#39c5cf', '#d29922', '#ff7b72'];

const $ = (id) => document.getElementById(id);
const fmt = (x, d = 3) => (x == null ? '—' : x.toFixed(d));

let state = { run: 0, ab: -1, section: '', hideExt: true, sortKey: 'self_s',
              sortDir: -1, zoom: null };

// ── data helpers ─────────────────────────────────────────────────────────────
function flatten(doc, hideExt) {
  const out = new Map();
  (function walk(node, path) {
    for (const c of node.children || []) {
      if (hideExt && c.kind === 'ext') continue;
      const p = path + '/' + c.name;
      const prev = out.get(p);
      if (prev) { prev.calls += c.calls; prev.cum_s += c.cum_s; prev.self_s += c.self_s; }
      else out.set(p, { ...c, path: p });
      walk(c, p);
    }
  })(doc.tree, '');
  return out;
}

function sectionRoots(doc) {
  return (doc.tree.children || []).filter((c) => c.kind === 'section');
}

// ── header controls ──────────────────────────────────────────────────────────
function initControls() {
  const rs = $('run-select'), ab = $('ab-select'), ss = $('section-select');
  RUNS.forEach((r, i) => {
    const label = `${r.meta.scenario} s${r.meta.seed} ${r.meta.commit} (${r.file})`;
    rs.add(new Option(label, i));
    ab.add(new Option(label, i));
  });
  rs.onchange = () => { state.run = +rs.value; state.zoom = null; render(); };
  ab.onchange = () => { state.ab = ab.value === '' ? -1 : +ab.value; render(); };
  ss.onchange = () => { state.section = ss.value; state.zoom = null; render(); };
  $('hide-ext').onchange = (e) => { state.hideExt = e.target.checked; render(); };
  document.querySelectorAll('#fn-table th').forEach((th) => {
    th.onclick = () => {
      const k = th.dataset.k;
      state.sortDir = state.sortKey === k ? -state.sortDir : -1;
      state.sortKey = k;
      render();
    };
  });
}

function syncSectionOptions(doc) {
  const ss = $('section-select');
  const have = new Set([...ss.options].map((o) => o.value));
  for (const s of doc.sections) {
    if (!have.has(s.name)) ss.add(new Option(s.name, s.name));
  }
}

// ── panels ───────────────────────────────────────────────────────────────────
function renderMeta(doc) {
  const m = doc.meta, w = doc.wall_s;
  $('meta-panel').textContent =
    `sizes: ${JSON.stringify(m.sizes)}   batches: ${m.n_batches ?? '—'}   ` +
    `python ${m.python}   commit ${m.commit}   created ${m.created}\n` +
    `wall: untraced ${fmt(w.untraced)}s   traced ${w.traced == null ? '—' : fmt(w.traced) + 's'}` +
    `   overhead ${m.trace_overhead_x ?? '—'}x   fingerprint ${doc.counts_fingerprint.slice(0, 16)}`;
}

function renderSections(doc) {
  const bar = $('sections-bar');
  bar.textContent = '';
  const total = doc.sections.reduce((a, s) => a + s.wall_s, 0) || 1;
  doc.sections.forEach((s, i) => {
    if (s.wall_s <= 0) return;
    const d = document.createElement('div');
    d.style.width = (100 * s.wall_s / total) + '%';
    d.style.background = SECTION_COLORS[i % SECTION_COLORS.length];
    d.title = `${s.name}: ${fmt(s.wall_s)}s (${(100 * s.share).toFixed(1)}%)`;
    d.textContent = `${s.name} ${(100 * s.share).toFixed(0)}%`;
    bar.appendChild(d);
  });
}

function renderIcicle(doc) {
  const svg = $('icicle');
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  const W = svg.width.baseVal.value, ROW = 26, MAXDEPTH = 15;

  let roots = sectionRoots(doc);
  if (state.section) roots = roots.filter((r) => r.name === state.section);
  if (!roots.length) roots = doc.tree.children || [];
  let frame = { name: '(all)', children: roots,
                cum_s: roots.reduce((a, c) => a + c.cum_s, 0) || 1 };
  if (state.zoom) frame = state.zoom;

  $('icicle-breadcrumb').textContent =
    state.zoom ? '⟵ ' + state.zoom.name + '  (click to reset)' : '';
  $('icicle-breadcrumb').onclick = () => { state.zoom = null; render(); };

  const ns = 'http://www.w3.org/2000/svg';
  (function draw(node, x0, x1, depth) {
    if (depth > MAXDEPTH || x1 - x0 < 1.5) return;
    let kids = (node.children || []).filter((c) => !(state.hideExt && c.kind === 'ext'));
    kids = kids.slice().sort((a, b) => b.cum_s - a.cum_s);
    const denom = node.cum_s || kids.reduce((a, c) => a + c.cum_s, 0) || 1;
    let x = x0;
    for (const [i, c] of kids.entries()) {
      const w = Math.max(0, (x1 - x0) * (c.cum_s / denom));
      if (w < 1.5) { continue; }
      const r = document.createElementNS(ns, 'rect');
      r.setAttribute('x', x); r.setAttribute('y', depth * ROW);
      r.setAttribute('width', w); r.setAttribute('height', ROW - 2);
      r.setAttribute('fill', SECTION_COLORS[(depth + i) % SECTION_COLORS.length]);
      const label = `${c.name}  cum ${fmt(c.cum_s)}s  self ${fmt(c.self_s)}s  calls ${c.calls.toLocaleString()}`;
      const t = document.createElementNS(ns, 'title');
      t.textContent = label;
      r.appendChild(t);
      r.addEventListener('click', () => { state.zoom = c; render(); });
      svg.appendChild(r);
      if (w > 60) {
        const tx = document.createElementNS(ns, 'text');
        tx.setAttribute('x', x + 4); tx.setAttribute('y', depth * ROW + 16);
        tx.textContent = c.name.length > w / 7 ? c.name.slice(0, w / 7) + '…' : c.name;
        svg.appendChild(tx);
      }
      draw(c, x, x + w, depth + 1);
      x += w;
    }
  })(frame, 0, W, 0);
}

function renderTable(doc, abDoc) {
  const tbody = document.querySelector('#fn-table tbody');
  tbody.textContent = '';
  const flat = flatten(doc, state.hideExt);
  const abFlat = abDoc ? flatten(abDoc, state.hideExt) : null;

  let rows = [...flat.values()];
  if (state.section) rows = rows.filter((r) => r.path.startsWith('/' + state.section + '/'));
  rows.forEach((r) => {
    r.percall = r.calls ? (1e6 * r.cum_s / r.calls) : 0;
    r.delta = abFlat ? ((abFlat.get(r.path)?.calls ?? 0) - r.calls) : null;
  });
  const k = state.sortKey;
  rows.sort((a, b) => state.sortDir * ((a[k] ?? -Infinity) > (b[k] ?? -Infinity) ? 1 : -1));

  for (const r of rows.slice(0, 400)) {
    const tr = document.createElement('tr');
    if (r.kind === 'ext') tr.className = 'ext';
    const dCls = r.delta > 0 ? 'pos' : r.delta < 0 ? 'neg' : '';
    tr.innerHTML =
      `<td class="name" title="${r.path}">${r.name}</td>` +
      `<td>${r.calls.toLocaleString()}</td><td>${fmt(r.cum_s)}</td>` +
      `<td>${fmt(r.self_s)}</td><td>${r.percall.toFixed(1)}</td>` +
      `<td class="${dCls}">${r.delta == null ? '—' : (r.delta > 0 ? '+' : '') + r.delta.toLocaleString()}</td>`;
    tbody.appendChild(tr);
  }
}

// ── main ─────────────────────────────────────────────────────────────────────
function render() {
  if (!RUNS.length) {
    document.body.insertAdjacentHTML('beforeend',
      '<section><b>No captures.</b> Run calltree_capture.py, then calltree_render.py.</section>');
    return;
  }
  const doc = RUNS[state.run];
  const ab = state.ab >= 0 ? RUNS[state.ab] : null;
  syncSectionOptions(doc);
  renderMeta(doc);
  renderSections(doc);
  renderIcicle(doc);
  renderTable(doc, ab);
}

initControls();
render();
