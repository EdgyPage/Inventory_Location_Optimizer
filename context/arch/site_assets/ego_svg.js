/* ego_svg.js — dependency-free inline-SVG ego-graph renderer (the guaranteed-offline
   engine). Implements window.EgoSvg.render(stageEl, GRAPH, nodeId, handlers), drawing the
   focus node centered with its callees (right), callers (left) and, for modules, imports
   (bottom). Click a ring node -> handlers.center(id); double-click or center -> open(id).
   cytoscape (if vendored) supersedes this via explorer.js. */
(function () {
  var NS = 'http://www.w3.org/2000/svg';
  var KIND_COLOR = { function: '#58a6ff', class: '#d2a8ff', module: '#7ee787', const: '#f0a020' };

  function el(tag, attrs) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }
  function trunc(s, n) { return s.length > n ? s.slice(0, n - 1) + '…' : s; }

  function placeArc(ids, cx, cy, r, a0, a1) {
    var out = [], n = ids.length;
    for (var i = 0; i < n; i++) {
      var t = n === 1 ? (a0 + a1) / 2 : a0 + (a1 - a0) * i / (n - 1);
      var rad = t * Math.PI / 180;
      out.push({ id: ids[i], x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) });
    }
    return out;
  }

  function render(stage, GRAPH, id, handlers) {
    stage.innerHTML = '';
    var W = 900, H = 640, cx = W / 2, cy = H / 2, R = 250;
    var svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, preserveAspectRatio: 'xMidYMid meet' });
    var node = GRAPH.nodes[id];
    if (!node) { stage.appendChild(svg); return; }
    var adj = GRAPH.adj[id] || { callers: [], callees: [], imports: [] };
    var callees = (adj.callees || []).slice(0, 20);
    var callers = (adj.callers || []).slice(0, 20);
    var imports = (node.kind === 'module' ? (adj.imports || []) : []).slice(0, 14);

    var ring = placeArc(callees, cx, cy, R, -70, 70)
      .concat(placeArc(callers, cx, cy, R, 110, 250))
      .concat(placeArc(imports, cx, cy, R * 0.72, 250, 290));

    ring.forEach(function (p) {
      svg.appendChild(el('line', { class: 'ego-edge', x1: cx, y1: cy, x2: p.x, y2: p.y }));
    });
    ring.forEach(function (p) {
      var nn = GRAPH.nodes[p.id]; if (!nn) return;
      var g = el('g', { class: 'ego-node', transform: 'translate(' + p.x + ',' + p.y + ')' });
      g.appendChild(el('circle', { r: 8, fill: KIND_COLOR[nn.kind] || '#888', stroke: '#0d1117', 'stroke-width': 1.5 }));
      var t = el('text', { x: 12, y: 4 }); t.textContent = trunc(nn.name, 18); g.appendChild(t);
      g.addEventListener('click', function () { handlers.center(p.id); });
      g.addEventListener('dblclick', function () { handlers.open(p.id); });
      svg.appendChild(g);
    });

    var fg = el('g', { class: 'ego-node', transform: 'translate(' + cx + ',' + cy + ')' });
    fg.appendChild(el('circle', { r: 15, fill: KIND_COLOR[node.kind] || '#888', stroke: '#c9d1d9', 'stroke-width': 2 }));
    var ft = el('text', { x: 0, y: 32, 'text-anchor': 'middle' }); ft.textContent = trunc(node.name, 28);
    fg.appendChild(ft);
    fg.addEventListener('click', function () { handlers.open(id); });
    svg.appendChild(fg);

    stage.appendChild(svg);
  }

  window.EgoSvg = { render: render, label: 'svg' };
})();
