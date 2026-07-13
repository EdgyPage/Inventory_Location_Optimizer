/* explorer.js — code-graph ego explorer controller. Engine-agnostic: prefers a vendored
   cytoscape (window.CytoEngine) when present, else the built-in window.EgoSvg. Reads the
   inlined window.GRAPH (from assets/graph.js), supports ?focus=<slug>, search, re-centering
   on click, and opening a node's page. Node pages and this explorer mirror the SAME data. */

/* Optional richer engine — only used when assets/cytoscape.min.js is vendored. Same
   Engine.render(stageEl, GRAPH, nodeId, {open, center}) contract as window.EgoSvg. */
(function () {
  if (!window.cytoscape) return;
  var KIND = { function: '#58a6ff', class: '#d2a8ff', module: '#7ee787', const: '#f0a020' };
  var STYLE = [
    { selector: 'node', style: {
      'label': 'data(label)', 'color': '#c9d1d9', 'font-size': '9px',
      'text-valign': 'center', 'text-halign': 'right', 'width': 14, 'height': 14,
      'background-color': function (n) { return KIND[n.data('kind')] || '#8b949e'; },
      'text-wrap': 'ellipsis', 'text-max-width': '130px' } },
    { selector: 'node[focus="1"]', style: {
      'width': 26, 'height': 26, 'border-width': 2, 'border-color': '#c9d1d9', 'font-size': '11px' } },
    { selector: 'edge', style: {
      'width': 1, 'line-color': '#30363d', 'curve-style': 'bezier',
      'target-arrow-color': '#30363d', 'target-arrow-shape': 'triangle', 'arrow-scale': 0.7 } }
  ];

  function render(stage, GRAPH, id, handlers) {
    if (window.__egoCy) { try { window.__egoCy.destroy(); } catch (e) {} }
    var node = GRAPH.nodes[id];
    if (!node) return;
    var adj = GRAPH.adj[id] || { callers: [], callees: [], imports: [] };
    var els = [{ data: { id: id, label: node.name, kind: node.kind, focus: '1' } }];
    var seen = {}; seen[id] = 1;
    function add(list, dir) {
      (list || []).slice(0, 22).forEach(function (rid) {
        if (!GRAPH.nodes[rid] || seen[rid]) return;
        seen[rid] = 1;
        els.push({ data: { id: rid, label: GRAPH.nodes[rid].name, kind: GRAPH.nodes[rid].kind } });
        var e = dir === 'out' ? { source: id, target: rid } : { source: rid, target: id };
        e.id = 'e' + els.length;
        els.push({ data: e });
      });
    }
    add(adj.callees, 'out');
    add(adj.callers, 'in');
    if (node.kind === 'module') add(adj.imports, 'out');

    var cy = window.cytoscape({
      container: stage, elements: els, style: STYLE,
      layout: { name: 'concentric', concentric: function (n) { return n.data('focus') ? 2 : 1; },
                levelWidth: function () { return 1; }, minNodeSpacing: 36, animate: false },
      wheelSensitivity: 0.2
    });
    cy.on('tap', 'node', function (evt) {
      var nid = evt.target.id();
      if (nid === id) handlers.open(id); else handlers.center(nid);
    });
    window.__egoCy = cy;
  }
  window.CytoEngine = { render: render, label: 'cytoscape' };
})();

(function () {
  var G = window.GRAPH;
  if (!G) return;
  var stage = document.getElementById('ego');
  var detail = document.getElementById('ego-detail');
  var search = document.getElementById('ego-search');
  var results = document.getElementById('ego-results');
  if (!stage) return;

  var slugToId = {};
  Object.keys(G.nodes).forEach(function (id) { slugToId[G.nodes[id].slug] = id; });

  var Engine = (window.cytoscape && window.CytoEngine) ? window.CytoEngine : window.EgoSvg;

  function open(id) {
    var n = G.nodes[id];
    if (n) location.href = 'nodes/' + n.slug + '.html';
  }

  function center(id) {
    var n = G.nodes[id];
    if (!n) return;
    stage.setAttribute('data-focus', n.slug);
    if (detail) {
      var adj = G.adj[id] || {};
      detail.innerHTML =
        '<h3><code>' + n.name + '</code></h3>' +
        '<p class="dim">' + n.kind + ' · ' + n.layer + '</p>' +
        '<p class="dim">' + n.file + '</p>' +
        '<p class="dim">callers ' + ((adj.callers || []).length) +
        ' · callees ' + ((adj.callees || []).length) + '</p>' +
        '<p><a class="btn" href="nodes/' + n.slug + '.html">Open page ▸</a></p>' +
        '<p class="ego-hint">click a node to re-center · double-click (or the centre) to open its page</p>';
    }
    Engine.render(stage, G, id, { open: open, center: center });
  }

  function doSearch() {
    var v = search.value.toLowerCase().trim();
    results.innerHTML = '';
    if (!v) return;
    var ids = Object.keys(G.nodes).filter(function (id) {
      return G.nodes[id].name.toLowerCase().indexOf(v) >= 0;
    }).sort().slice(0, 40);
    ids.forEach(function (id) {
      var n = G.nodes[id];
      var li = document.createElement('li');
      var a = document.createElement('a');
      a.href = '#';
      a.innerHTML = '<code>' + n.name + '</code> <span class="dim">' + n.file + '</span>';
      a.addEventListener('click', function (ev) { ev.preventDefault(); center(id); });
      li.appendChild(a);
      results.appendChild(li);
    });
  }
  if (search) search.addEventListener('input', doSearch);

  var m = location.search.match(/[?&]focus=([^&]+)/);
  var startId = m ? slugToId[decodeURIComponent(m[1])] : null;
  if (!startId) {
    var mains = Object.keys(G.nodes).filter(function (id) { return G.nodes[id].name === 'main'; }).sort();
    startId = mains[0] || Object.keys(G.nodes).sort()[0];
  }
  center(startId);
})();
