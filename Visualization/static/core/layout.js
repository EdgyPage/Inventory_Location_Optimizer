// layout.js — packing "up to 24 aisles" onto a screen.
//
// The count is a RESULT, not a constant. Aisles are 50 or 150 bays long on this warehouse, so a
// row of 150-bay aisles is 3x the width of a row of 50-bay ones; a fixed 6-column grid (what the
// old viewer used) gives 64 rows of ragged width and wastes most of the canvas.
//
// So: pack by aisle WIDTH, honour a legibility floor of ~2 px per bin column, and report how
// many fit. 384 aisles is 16+ pages of a 24-aisle band, which is why the mini-map is not
// optional — see minimap() below.

export const MAX_TILES = 24;
const MIN_PX_PER_BAY = 2;     // below this a bin column is not a thing you can see or click
const GAP = 6;
const HEADER = 14;

/**
 * Choose how many of `aisles` fit legibly, and where each goes.
 *
 * @returns {{tiles: Array, perPage: number, pages: number, page: number}}
 */
export function packAisles(aisles, W, H, page = 0) {
  if (!aisles.length || W <= 0 || H <= 0) {
    return { tiles: [], perPage: 0, pages: 0, page: 0 };
  }
  // Width is driven by the widest aisle on offer, so tiles stay comparable: a bin is the same
  // size in every tile, which is what makes two aisles visually diffable.
  const maxBayX = Math.max(...aisles.map((a) => a.bay_x || 1));
  const maxBayY = Math.max(...aisles.map((a) => a.bay_y || 1));
  const tileW = Math.max(maxBayX * MIN_PX_PER_BAY, 90);
  const cols = Math.max(1, Math.floor((W + GAP) / (tileW + GAP)));

  // Keep the bin aspect near square-ish, then let the row count fall out of the height.
  const tileH = Math.max(40, Math.min(H / 2, (tileW / maxBayX) * maxBayY + HEADER));
  const rows = Math.max(1, Math.floor((H + GAP) / (tileH + GAP)));

  const perPage = Math.min(MAX_TILES, cols * rows);
  const pages = Math.max(1, Math.ceil(aisles.length / perPage));
  const p = Math.max(0, Math.min(pages - 1, page));
  const slice = aisles.slice(p * perPage, p * perPage + perPage);

  const actualW = (W - GAP * (cols - 1)) / cols;
  const tiles = slice.map((a, i) => {
    const col = i % cols;
    const row = Math.floor(i / cols);
    return {
      aisle: a,
      box: {
        x: col * (actualW + GAP),
        y: row * (tileH + GAP) + HEADER,
        w: actualW,
        h: tileH - HEADER,
      },
      header: { x: col * (actualW + GAP), y: row * (tileH + GAP), w: actualW, h: HEADER },
    };
  });
  return { tiles, perPage, pages, page: p };
}

/**
 * The 384-cell overview strip: one rect per aisle, no bins.
 *
 * "Up to 24 aisles" is a detail BAND, not the warehouse — paging 16 screens is not an overview.
 * This is the thing that stays on screen and drives which 24 are in the band.
 */
export function minimap(aisles, W, H) {
  const n = aisles.length;
  if (!n || W <= 0) return { cells: [], cols: 0 };
  const cols = Math.max(1, Math.round(Math.sqrt(n * (W / Math.max(1, H)))));
  const rows = Math.ceil(n / cols);
  const cw = W / cols;
  const ch = Math.min(H / rows, 14);
  return {
    cols,
    cells: aisles.map((a, i) => ({
      aisle: a,
      x: (i % cols) * cw,
      y: Math.floor(i / cols) * ch,
      w: Math.max(1, cw - 1),
      h: Math.max(1, ch - 1),
    })),
    hit(px, py) {
      const c = Math.floor(px / cw);
      const r = Math.floor(py / ch);
      const i = r * cols + c;
      return (c >= 0 && c < cols && i >= 0 && i < n) ? aisles[i] : null;
    },
  };
}
