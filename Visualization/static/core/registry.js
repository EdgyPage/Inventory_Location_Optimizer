// registry.js — views self-register on import; the shell knows nothing about any of them.
//
// A view module exports NOTHING. It calls register({...}) at import time and the shell picks it
// up. Adding a view is one file plus one import line in main.js — no switch statement, no
// central list to keep in sync.
//
// The contract:
//
//   id        stable key, used in the URL hash
//   title     tab label
//   needs     data verbs the shell resolves before render(); shared object identity across
//             every mounted view, so drilling in refetches nothing
//   watches   state keys this view cares about. Scrubbing `t` re-renders only views that
//             list 't'. This gating is why time scrubbing stays smooth.
//   requires  capability names; the tab is HIDDEN when the arm lacks them, rather than the
//             view rendering zeros for data that was never written
//   mount/render/hit/unmount
//
// `hit(x, y)` returns a drill TARGET ({kind:'aisle'|'bin', ...}) — never a state mutation. The
// shell owns the drill map (overview -> aisle -> bin), so no view knows its neighbours.

const views = [];

export function register(view) {
  for (const k of ['id', 'title', 'render']) {
    if (!view[k]) throw new Error(`view is missing required field: ${k}`);
  }
  if (views.some((v) => v.id === view.id)) throw new Error(`duplicate view id: ${view.id}`);
  views.push({
    needs: [], watches: [], requires: [],
    mount() {}, hit() { return null; }, unmount() {},
    ...view,
  });
  return view;
}

export function all() { return views.slice(); }

export function byId(id) { return views.find((v) => v.id === id) || null; }

/** Views this arm can actually show, given what the reader says it has. */
export function available(capabilities) {
  const caps = new Set(capabilities || []);
  return views.filter((v) => v.requires.every((c) => caps.has(c)));
}
