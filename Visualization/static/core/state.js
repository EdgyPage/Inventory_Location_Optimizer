// state.js — the one mutable selection, and who is told when it changes.
//
// Views never assign to state. They call ctx.emit(patch), the shell diffs it, and only views
// whose `watches` list names a changed key re-render. That gating is what makes scrubbing time
// cheap: `t` changes 60x a second and only the two views watching it redraw.

const KEYS = [
  'run',          // the pane's arm id
  'compareRun',   // the second arm, and the COLOUR AUTHORITY (see palette.js)
  'batch',        // current batch
  't',            // seconds into the batch, or null for "batch start"
  'aisle',        // drilled-into aisle, or null
  'bin',          // {aisle, bayX, bayY} or null
  'view',         // active view id
  'colorMode',    // 'home' | 'fill' | 'layout' | 'sku'
  'topN',
  'playing',
];

const state = {
  run: null, compareRun: null, batch: 0, t: null, aisle: null, bin: null,
  view: 'overview', colorMode: 'home', topN: 25, playing: false,
};

const subscribers = new Set();

export function get() {
  return state;
}

export function emit(patch) {
  const changed = [];
  for (const [k, v] of Object.entries(patch)) {
    if (!KEYS.includes(k)) throw new Error(`unknown state key: ${k}`);
    // Compare by value; bin is a small object so a shallow JSON compare is honest and cheap.
    const same = (typeof v === 'object' && v !== null)
      ? JSON.stringify(state[k]) === JSON.stringify(v)
      : state[k] === v;
    if (!same) { state[k] = v; changed.push(k); }
  }
  if (changed.length) for (const fn of subscribers) fn(changed, state);
  return changed;
}

export function subscribe(fn) {
  subscribers.add(fn);
  return () => subscribers.delete(fn);
}
