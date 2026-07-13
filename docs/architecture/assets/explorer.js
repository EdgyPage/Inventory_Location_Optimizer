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
  var crumbsEl = document.getElementById('ego-crumbs');
  var treeEl = document.getElementById('ego-tree');
  if (!stage) return;

  var Engine = (window.cytoscape && window.CytoEngine) ? window.CytoEngine : window.EgoSvg;
  var ids = Object.keys(G.nodes);
  var slugToId = {};
  ids.forEach(function (id) { slugToId[G.nodes[id].slug] = id; });

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function fileOf(id) { var i = id.indexOf('::'); return i >= 0 ? id.slice(0, i) : id; }
  function qualOf(id) { var i = id.indexOf('::'); return i >= 0 ? id.slice(i + 2) : null; }
  function baseName(f) { return f.split('/').pop(); }
  function parentId(id) {                       // enclosing scope id, or null for a module
    var q = qualOf(id); if (q === null) return null;
    var f = fileOf(id);
    return q.indexOf('.') >= 0 ? f + '::' + q.slice(0, q.lastIndexOf('.')) : f;
  }

  // ---- one O(n) pass: parent->children + a directory tree of modules ----------------------
  var childrenOf = {};
  ids.forEach(function (id) {
    var p = parentId(id);
    if (p !== null) (childrenOf[p] = childrenOf[p] || []).push(id);
  });
  Object.keys(childrenOf).forEach(function (p) {
    childrenOf[p].sort(function (a, b) { return G.nodes[a].name < G.nodes[b].name ? -1 : 1; });
  });
  var root = { name: '', path: '', dirs: {}, files: [] };
  ids.filter(function (id) { return G.nodes[id].kind === 'module'; }).forEach(function (mid) {
    var parts = G.nodes[mid].file.split('/'), cur = root, i;
    for (i = 0; i < parts.length - 1; i++) {
      if (!cur.dirs[parts[i]]) cur.dirs[parts[i]] =
        { name: parts[i], path: (cur.path ? cur.path + '/' : '') + parts[i], dirs: {}, files: [] };
      cur = cur.dirs[parts[i]];
    }
    cur.files.push(mid);
  });

  var expanded = {};        // 'd:<dirpath>' | 'n:<id>' -> true
  var focusId = null;

  // ---- navigation -------------------------------------------------------------------------
  function open(id) { var n = G.nodes[id]; if (n) location.href = 'nodes/' + n.slug + '.html'; }

  function center(id) {
    var n = G.nodes[id]; if (!n) return;
    focusId = id;
    stage.setAttribute('data-focus', n.slug);
    // auto-expand the path to this node in the tree
    var p = parentId(id);
    while (p !== null) { expanded['n:' + p] = true; var np = parentId(p); p = np; }
    var dparts = fileOf(id).split('/'), acc = '', i;
    for (i = 0; i < dparts.length - 1; i++) { acc = (acc ? acc + '/' : '') + dparts[i]; expanded['d:' + acc] = true; }
    renderCrumbs(id); renderDetail(id); renderTree();
    Engine.render(stage, G, id, { open: open, center: center });
  }

  function renderDetail(id) {
    if (!detail) return;
    var n = G.nodes[id], adj = G.adj[id] || {};
    detail.innerHTML =
      '<h3><code>' + esc(n.name) + '</code></h3>' +
      '<p class="dim">' + esc(n.kind) + ' · ' + esc(n.layer) + '</p>' +
      '<p class="dim">' + esc(n.file) + '</p>' +
      '<p class="dim">callers ' + ((adj.callers || []).length) +
      ' · callees ' + ((adj.callees || []).length) + '</p>' +
      '<p><a class="btn" href="nodes/' + n.slug + '.html">Open page ▸</a></p>' +
      '<p class="ego-hint">click a node to re-center · double-click (or the centre) to open its page</p>';
  }

  // ---- breadcrumb (layer › file › [Class ›] name) -----------------------------------------
  function renderCrumbs(id) {
    if (!crumbsEl) return;
    var n = G.nodes[id], parts = ['<a href="layers.html">' + esc(n.layer) + '</a>'];
    if (n.kind === 'module') {
      parts.push('<span class="here"><code>' + esc(n.file) + '</code></span>');
    } else {
      parts.push('<a href="#" data-center="' + esc(fileOf(id)) + '"><code>' + esc(baseName(n.file)) + '</code></a>');
      var pid = parentId(id);
      if (pid && G.nodes[pid] && G.nodes[pid].kind === 'class')
        parts.push('<a href="#" data-center="' + esc(pid) + '"><code>' + esc(G.nodes[pid].name) + '</code></a>');
      parts.push('<span class="here"><code>' + esc(n.name) + '</code></span>');
    }
    crumbsEl.innerHTML = parts.join(' <span class="sep">›</span> ');
  }
  if (crumbsEl) crumbsEl.addEventListener('click', function (ev) {
    var a = ev.target.closest('a[data-center]'); if (!a) return;
    ev.preventDefault(); center(a.getAttribute('data-center'));
  });

  // ---- code-tree navigator ('you are here') -----------------------------------------------
  function row(depth, tw, cls, dataAttrs, name) {
    return '<div class="tree-row ' + cls + '" style="padding-left:' + (6 + depth * 12) + 'px" ' + dataAttrs + '>' +
      '<span class="tree-tw">' + tw + '</span><span class="t-name">' + name + '</span></div>';
  }
  function twGlyph(canExp, open) { return canExp ? (open ? '▾' : '▸') : ''; }
  function renderSym(id, depth, out) {
    var n = G.nodes[id], kids = childrenOf[id] || [], open = !!expanded['n:' + id], canExp = kids.length > 0;
    var kc = n.kind === 'class' ? 'kc' : n.kind === 'const' ? 'kn' : 'kf';
    out.push(row(depth, twGlyph(canExp, open), 'sym ' + kc + (id === focusId ? ' here' : ''),
      'data-node="' + esc(id) + '" data-exp="' + (canExp ? 1 : 0) + '"', esc(n.name)));
    if (open) kids.forEach(function (k) { renderSym(k, depth + 1, out); });
  }
  function renderFile(mid, depth, out) {
    var n = G.nodes[mid], kids = childrenOf[mid] || [], open = !!expanded['n:' + mid], canExp = kids.length > 0;
    out.push(row(depth, twGlyph(canExp, open), 'file' + (mid === focusId ? ' here' : ''),
      'data-node="' + esc(mid) + '" data-exp="' + (canExp ? 1 : 0) + '"', esc(baseName(n.file))));
    if (open) kids.forEach(function (k) { renderSym(k, depth + 1, out); });
  }
  function renderDir(d, depth, out) {
    var open = !!expanded['d:' + d.path];
    out.push(row(depth, twGlyph(true, open), 'dir', 'data-dir="' + esc(d.path) + '"', esc(d.name) + '/'));
    if (open) renderDirChildren(d, depth + 1, out);
  }
  function renderDirChildren(d, depth, out) {
    Object.keys(d.dirs).sort().forEach(function (k) { renderDir(d.dirs[k], depth, out); });
    d.files.slice().sort(function (a, b) { return G.nodes[a].file < G.nodes[b].file ? -1 : 1; })
      .forEach(function (m) { renderFile(m, depth, out); });
  }
  function renderTree() {
    if (!treeEl) return;
    var out = []; renderDirChildren(root, 0, out); treeEl.innerHTML = out.join('');
    var here = treeEl.querySelector('.tree-row.here'); if (here) here.scrollIntoView({ block: 'nearest' });
  }
  if (treeEl) treeEl.addEventListener('click', function (ev) {
    var r = ev.target.closest('.tree-row'); if (!r) return;
    var dir = r.getAttribute('data-dir'), node = r.getAttribute('data-node');
    if (dir !== null) { expanded['d:' + dir] = !expanded['d:' + dir]; renderTree(); return; }
    if (node) {
      var canExp = r.getAttribute('data-exp') === '1';
      if (ev.target.classList.contains('tree-tw') && canExp) { expanded['n:' + node] = !expanded['n:' + node]; renderTree(); }
      else center(node);
    }
  });

  // ---- grouped, ranked, keyboard-navigable search -----------------------------------------
  var flatHits = [], activeIdx = -1;
  function score(name, v) {
    var ln = name.toLowerCase();
    return ln === v ? 0 : ln.indexOf(v) === 0 ? 1 : ln.indexOf(v) >= 0 ? 2 : 3;
  }
  function doSearch() {
    var v = search.value.toLowerCase().trim();
    results.innerHTML = ''; flatHits = []; activeIdx = -1;
    if (!v) return;
    var hits = ids.filter(function (id) {
      var n = G.nodes[id];
      return n.name.toLowerCase().indexOf(v) >= 0 || id.toLowerCase().indexOf(v) >= 0 || n.file.toLowerCase().indexOf(v) >= 0;
    });
    hits.sort(function (a, b) {
      var na = G.nodes[a], nb = G.nodes[b], sa = score(na.name, v), sb = score(nb.name, v);
      if (sa !== sb) return sa - sb;
      if (na.file !== nb.file) return na.file < nb.file ? -1 : 1;
      return na.name < nb.name ? -1 : 1;
    });
    var out = [], curFile = null;
    hits.slice(0, 60).forEach(function (id) {
      var n = G.nodes[id];
      if (n.file !== curFile) { curFile = n.file; out.push('<li class="grp">' + esc(n.file) + '</li>'); }
      var i = flatHits.length; flatHits.push(id);
      out.push('<li><a href="#" data-i="' + i + '"><code>' + esc(n.name) + '</code><span class="k">' + esc(n.kind) + '</span></a></li>');
    });
    results.innerHTML = out.join('');
  }
  function highlight() {
    Array.prototype.forEach.call(results.querySelectorAll('a[data-i]'), function (a) {
      a.classList.toggle('active', +a.getAttribute('data-i') === activeIdx);
    });
    var act = results.querySelector('a.active'); if (act) act.scrollIntoView({ block: 'nearest' });
  }
  if (search) {
    search.addEventListener('input', doSearch);
    search.addEventListener('keydown', function (ev) {
      if (ev.key === 'ArrowDown' && flatHits.length) { ev.preventDefault(); activeIdx = (activeIdx + 1) % flatHits.length; highlight(); }
      else if (ev.key === 'ArrowUp' && flatHits.length) { ev.preventDefault(); activeIdx = (activeIdx - 1 + flatHits.length) % flatHits.length; highlight(); }
      else if (ev.key === 'Enter') { ev.preventDefault(); if (flatHits.length) center(flatHits[activeIdx >= 0 ? activeIdx : 0]); }
      else if (ev.key === 'Escape') { search.value = ''; doSearch(); }
    });
  }
  if (results) results.addEventListener('click', function (ev) {
    var a = ev.target.closest('a[data-i]'); if (!a) return;
    ev.preventDefault(); center(flatHits[+a.getAttribute('data-i')]);
  });

  // ---- entry -------------------------------------------------------------------------------
  var m = location.search.match(/[?&]focus=([^&]+)/);
  var startId = m ? slugToId[decodeURIComponent(m[1])] : null;
  if (!startId) {
    var mains = ids.filter(function (id) { return G.nodes[id].name === 'main'; }).sort();
    startId = mains[0] || ids.slice().sort()[0];
  }
  center(startId);
})();
