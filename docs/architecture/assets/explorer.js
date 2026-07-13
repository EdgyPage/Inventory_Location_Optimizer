/* explorer.js — code-graph ego explorer controller. Engine-agnostic: prefers a vendored
   cytoscape (window.CytoEngine) when present, else the built-in window.EgoSvg. Reads the
   inlined window.GRAPH (from assets/graph.js), supports ?focus=<slug>, search, re-centering
   on click, and opening a node's page. Node pages and this explorer mirror the SAME data. */
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
